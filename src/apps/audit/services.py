import json
import uuid
from typing import Any, Dict, List, Optional

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.audit.models import AuditLog
from apps.budgets.models import BudgetReservation
from apps.contracts.models import (
    Contract,
)
from apps.core.middleware import get_client_ip, get_current_request_id, get_current_user
from apps.invoices.models import SupplierInvoice
from apps.orders.models import PurchaseOrder
from apps.receipts.models import GoodsReceipt
from apps.requisitions.models import PurchaseRequisition
from apps.sourcing.models import (
    SourcingEvent,
)
from apps.vendors.models import Vendor


def create_audit_log_service(
    actor: Optional[User] = None,
    action: str = AuditLog.ACTION_UPDATE,
    target_model: str = "",
    target_object_id: Any = "",
    previous_state: Optional[Dict[str, Any]] = None,
    new_state: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    request_id: Optional[str] = None,
    user_agent: str = "",
) -> AuditLog:
    """
    Centralized service to append an immutable AuditLog record across ProcureSphere 360.
    Automatically retrieves thread-local actor, request_id, and client IP if omitted.
    """
    if actor is None:
        actor = get_current_user()

    if request_id is None:
        request_id = get_current_request_id() or ""

    if ip_address is None:
        ip_address = get_client_ip()

    if target_object_id is not None:
        target_object_id = str(target_object_id)
    else:
        target_object_id = ""

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_model=target_model,
        target_object_id=target_object_id,
        previous_state=previous_state,
        new_state=new_state,
        ip_address=ip_address,
        request_id=request_id,
        user_agent=user_agent,
    )


def detect_entity_type(identifier: str) -> str:
    """
    Infers entity domain type from prefix or format.
    """
    id_upper = identifier.strip().upper()
    if id_upper.startswith("PR-"):
        return "PR"
    elif id_upper.startswith("PO-"):
        return "PO"
    elif id_upper.startswith("RFQ-") or id_upper.startswith("RFP-") or id_upper.startswith("SRC-"):
        return "SOURCING"
    elif id_upper.startswith("INV-") or id_upper.startswith("BILL-"):
        return "INVOICE"
    elif id_upper.startswith("GRN-") or id_upper.startswith("REC-"):
        return "RECEIPT"
    elif id_upper.startswith("CON-") or id_upper.startswith("CTR-"):
        return "CONTRACT"
    elif id_upper.startswith("VEND-") or id_upper.startswith("TIN-") or id_upper.startswith("TAX-"):
        return "VENDOR"
    return "UNKNOWN"


def get_transaction_lifecycle_service(  # noqa: C901
    entity_type: Optional[str] = None,
    entity_identifier: str = "",
) -> Dict[str, Any]:
    """
    Constructs an end-to-end Source-to-Pay (S2P) lifecycle graph from any starting node:
    Requisition -> Approvals -> Budget -> Sourcing -> Bids -> Evaluation -> Award ->
    Purchase Order -> Receipts -> Inspections -> Invoice -> 3-Way Match -> Payment ->
    Contract -> Vendor Governance & Complete Immutable Audit Trail.
    """
    raw_ident = entity_identifier.strip()
    if not entity_type or entity_type.upper() == "AUTO" or entity_type.upper() == "UNKNOWN":
        entity_type = detect_entity_type(raw_ident)

    entity_type = entity_type.upper()

    # Core container
    lifecycle = {
        "query_identifier": raw_ident,
        "resolved_entity_type": entity_type,
        "is_found": False,
        "root_entity": None,
        "requisition": None,
        "approvals": [],
        "budget_reservations": [],
        "sourcing_event": None,
        "bids": [],
        "award": None,
        "purchase_orders": [],
        "receipts": [],
        "invoices": [],
        "contracts": [],
        "vendor": None,
        "audit_logs": [],
        "stage_status": {
            "requisition": "NOT_STARTED",
            "approval": "NOT_STARTED",
            "sourcing": "NOT_STARTED",
            "po_issued": "NOT_STARTED",
            "receipt": "NOT_STARTED",
            "matching": "NOT_STARTED",
            "payment": "NOT_STARTED",
            "contract": "NOT_STARTED",
        },
        "flags": [],
        "generated_at": timezone.now().isoformat(),
    }

    # Tracking list of object IDs to gather aggregate audit trail
    tracked_object_ids: List[str] = []

    def safe_uuid_or_str(val):
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return None

    val_uuid = safe_uuid_or_str(raw_ident)

    pr_obj: Optional[PurchaseRequisition] = None
    po_objs: List[PurchaseOrder] = []
    src_obj: Optional[SourcingEvent] = None
    inv_objs: List[SupplierInvoice] = []
    grn_objs: List[GoodsReceipt] = []
    con_objs: List[Contract] = []
    vendor_obj: Optional[Vendor] = None

    # 1. Resolve Root Object
    if entity_type == "PR" or entity_type == "PURCHASE_REQUISITION":
        pr_query = Q(pr_number__iexact=raw_ident)
        if val_uuid:
            pr_query |= Q(id=val_uuid)
        pr_obj = (
            PurchaseRequisition.objects.filter(pr_query)
            .select_related("requester", "department", "cost_center")
            .prefetch_related("lines", "attachments")
            .first()
        )
        if pr_obj:
            lifecycle["is_found"] = True
            lifecycle["root_entity"] = f"Purchase Requisition ({pr_obj.pr_number})"

    elif entity_type == "PO" or entity_type == "PURCHASE_ORDER":
        po_query = Q(po_number__iexact=raw_ident)
        if val_uuid:
            po_query |= Q(id=val_uuid)
        po_first = (
            PurchaseOrder.objects.filter(po_query)
            .select_related(
                "vendor", "requisition", "sourcing_event", "cost_center", "acknowledged_by"
            )
            .prefetch_related("lines", "amendments", "delivery_schedules")
            .first()
        )
        if po_first:
            lifecycle["is_found"] = True
            po_objs.append(po_first)
            lifecycle["root_entity"] = f"Purchase Order ({po_first.po_number})"

    elif entity_type == "SOURCING" or entity_type == "SOURCING_EVENT":
        src_query = Q(event_number__iexact=raw_ident)
        if val_uuid:
            src_query |= Q(id=val_uuid)
        src_obj = (
            SourcingEvent.objects.filter(src_query)
            .select_related("requisition")
            .prefetch_related(
                "invitations__vendor",
                "bids__vendor",
                "bids__lines",
                "bids__versions",
                "evaluations__evaluator",
                "clarifications__vendor",
            )
            .first()
        )
        if src_obj:
            lifecycle["is_found"] = True
            lifecycle["root_entity"] = f"Sourcing Event ({src_obj.event_number})"

    elif entity_type == "INVOICE" or entity_type == "SUPPLIER_INVOICE":
        inv_query = Q(invoice_number__iexact=raw_ident)
        if val_uuid:
            inv_query |= Q(id=val_uuid)
        inv_first = (
            SupplierInvoice.objects.filter(inv_query)
            .select_related("vendor", "po", "po__requisition")
            .prefetch_related("lines", "exceptions__resolved_by", "match_results__performed_by")
            .first()
        )
        if inv_first:
            lifecycle["is_found"] = True
            inv_objs.append(inv_first)
            lifecycle["root_entity"] = f"Supplier Invoice ({inv_first.invoice_number})"

    elif entity_type == "RECEIPT" or entity_type == "GOODS_RECEIPT":
        grn_query = Q(grn_number__iexact=raw_ident)
        if val_uuid:
            grn_query |= Q(id=val_uuid)
        grn_first = (
            GoodsReceipt.objects.filter(grn_query)
            .select_related("po", "po__vendor", "received_by")
            .prefetch_related("lines__inspection", "lines__rejections")
            .first()
        )
        if grn_first:
            lifecycle["is_found"] = True
            grn_objs.append(grn_first)
            lifecycle["root_entity"] = f"Goods Receipt ({grn_first.grn_number})"

    elif entity_type == "CONTRACT":
        con_query = Q(contract_number__iexact=raw_ident)
        if val_uuid:
            con_query |= Q(id=val_uuid)
        con_first = (
            Contract.objects.filter(con_query)
            .select_related("vendor", "sourcing_event", "po", "contract_owner")
            .prefetch_related("versions", "milestones", "alerts", "obligations", "documents")
            .first()
        )
        if con_first:
            lifecycle["is_found"] = True
            con_objs.append(con_first)
            lifecycle["root_entity"] = f"Contract ({con_first.contract_number})"

    elif entity_type == "VENDOR":
        vend_query = Q(vendor_number__iexact=raw_ident) | Q(
            tax_identification_number__iexact=raw_ident
        )
        if val_uuid:
            vend_query |= Q(id=val_uuid)
        vendor_obj = (
            Vendor.objects.filter(vend_query)
            .select_related("category")
            .prefetch_related("contacts", "documents", "risk_records", "scorecards")
            .first()
        )
        if vendor_obj:
            lifecycle["is_found"] = True
            lifecycle["root_entity"] = f"Vendor ({vendor_obj.legal_name})"

    # Fallback Universal Scan if entity type was UNKNOWN or not found yet
    if not lifecycle["is_found"]:
        # Try PR
        pr_obj = (
            PurchaseRequisition.objects.filter(
                Q(pr_number__iexact=raw_ident)
                | (Q(id=val_uuid) if val_uuid else Q(pk__isnull=True))
            )
            .select_related("requester", "department", "cost_center")
            .prefetch_related("lines")
            .first()
        )
        if pr_obj:
            lifecycle["is_found"] = True
            lifecycle["resolved_entity_type"] = "PR"
            lifecycle["root_entity"] = f"Purchase Requisition ({pr_obj.pr_number})"

        if not lifecycle["is_found"]:
            po_first = (
                PurchaseOrder.objects.filter(
                    Q(po_number__iexact=raw_ident)
                    | (Q(id=val_uuid) if val_uuid else Q(pk__isnull=True))
                )
                .select_related("vendor", "requisition", "sourcing_event")
                .prefetch_related("lines")
                .first()
            )
            if po_first:
                lifecycle["is_found"] = True
                lifecycle["resolved_entity_type"] = "PO"
                po_objs.append(po_first)
                lifecycle["root_entity"] = f"Purchase Order ({po_first.po_number})"

        if not lifecycle["is_found"]:
            inv_first = (
                SupplierInvoice.objects.filter(
                    Q(invoice_number__iexact=raw_ident)
                    | (Q(id=val_uuid) if val_uuid else Q(pk__isnull=True))
                )
                .select_related("vendor", "po")
                .prefetch_related("lines")
                .first()
            )
            if inv_first:
                lifecycle["is_found"] = True
                lifecycle["resolved_entity_type"] = "INVOICE"
                inv_objs.append(inv_first)
                lifecycle["root_entity"] = f"Supplier Invoice ({inv_first.invoice_number})"

    # If still not found, return empty payload
    if not lifecycle["is_found"]:
        return lifecycle

    # 2. Traverse Graph Connections Across Modules (Bidirectional Expansion)
    for _ in range(3):  # Multi-pass expansion to connect all nodes across the S2P chain
        # From Invoices -> PO & Vendor
        for inv in list(inv_objs):
            if inv.po_id and not any(p.id == inv.po_id for p in po_objs):
                po = (
                    PurchaseOrder.objects.filter(id=inv.po_id)
                    .select_related(
                        "vendor", "requisition", "sourcing_event", "cost_center", "acknowledged_by"
                    )
                    .prefetch_related("lines", "amendments", "delivery_schedules")
                    .first()
                )
                if po:
                    po_objs.append(po)
            if not vendor_obj and inv.vendor:
                vendor_obj = inv.vendor

        # From Goods Receipts (GRN) -> PO
        for grn in list(grn_objs):
            if grn.po_id and not any(p.id == grn.po_id for p in po_objs):
                po = (
                    PurchaseOrder.objects.filter(id=grn.po_id)
                    .select_related(
                        "vendor", "requisition", "sourcing_event", "cost_center", "acknowledged_by"
                    )
                    .prefetch_related("lines", "amendments", "delivery_schedules")
                    .first()
                )
                if po:
                    po_objs.append(po)

        # From Contracts -> PO & Sourcing & Vendor
        for con in list(con_objs):
            if con.po_id and not any(p.id == con.po_id for p in po_objs):
                po = (
                    PurchaseOrder.objects.filter(id=con.po_id)
                    .select_related(
                        "vendor", "requisition", "sourcing_event", "cost_center", "acknowledged_by"
                    )
                    .prefetch_related("lines", "amendments", "delivery_schedules")
                    .first()
                )
                if po:
                    po_objs.append(po)
            if not src_obj and con.sourcing_event_id:
                src_obj = (
                    SourcingEvent.objects.filter(id=con.sourcing_event_id)
                    .select_related("requisition")
                    .prefetch_related("invitations", "bids", "evaluations")
                    .first()
                )
            if not vendor_obj and con.vendor:
                vendor_obj = con.vendor

        # From POs -> PR, Sourcing, Vendor, GRNs, Invoices, Contracts
        for po in list(po_objs):
            if not pr_obj and po.requisition_id:
                pr_obj = (
                    PurchaseRequisition.objects.filter(id=po.requisition_id)
                    .select_related("requester", "department", "cost_center")
                    .prefetch_related("lines", "attachments")
                    .first()
                )
            if not src_obj and po.sourcing_event_id:
                src_obj = (
                    SourcingEvent.objects.filter(id=po.sourcing_event_id)
                    .select_related("requisition")
                    .prefetch_related("invitations", "bids", "evaluations")
                    .first()
                )
            if not vendor_obj and po.vendor:
                vendor_obj = po.vendor

            # Fetch GRNs for this PO
            for grn in (
                GoodsReceipt.objects.filter(po=po)
                .select_related("received_by")
                .prefetch_related("lines__inspection", "lines__rejections")
            ):
                if not any(g.id == grn.id for g in grn_objs):
                    grn_objs.append(grn)

            # Fetch Invoices for this PO
            for inv in (
                SupplierInvoice.objects.filter(po=po)
                .select_related("vendor")
                .prefetch_related("lines", "exceptions", "match_results")
            ):
                if not any(i.id == inv.id for i in inv_objs):
                    inv_objs.append(inv)

            # Fetch Contracts linked to PO
            for con in (
                Contract.objects.filter(po=po)
                .select_related("vendor", "contract_owner")
                .prefetch_related("versions", "milestones", "obligations", "alerts")
            ):
                if not any(c.id == con.id for c in con_objs):
                    con_objs.append(con)

        # From Sourcing -> PR, Bids, POs, Contracts
        if src_obj:
            if not pr_obj and src_obj.requisition_id:
                pr_obj = (
                    PurchaseRequisition.objects.filter(id=src_obj.requisition_id)
                    .select_related("requester", "department", "cost_center")
                    .prefetch_related("lines", "attachments")
                    .first()
                )
            for po in (
                PurchaseOrder.objects.filter(sourcing_event=src_obj)
                .select_related("vendor", "cost_center")
                .prefetch_related("lines", "amendments")
            ):
                if not any(p.id == po.id for p in po_objs):
                    po_objs.append(po)
            for con in (
                Contract.objects.filter(sourcing_event=src_obj)
                .select_related("vendor")
                .prefetch_related("versions", "milestones")
            ):
                if not any(c.id == con.id for c in con_objs):
                    con_objs.append(con)

        # From PR -> Sourcing, POs
        if pr_obj:
            if not src_obj:
                src_obj = (
                    SourcingEvent.objects.filter(requisition=pr_obj)
                    .prefetch_related(
                        "invitations__vendor",
                        "bids__vendor",
                        "bids__versions",
                        "evaluations",
                        "clarifications",
                    )
                    .first()
                )
            for po in (
                PurchaseOrder.objects.filter(requisition=pr_obj)
                .select_related("vendor", "cost_center")
                .prefetch_related("lines", "amendments")
            ):
                if not any(p.id == po.id for p in po_objs):
                    po_objs.append(po)

    # Collect all tracked object IDs
    if pr_obj:
        tracked_object_ids.append(str(pr_obj.id))
    if src_obj:
        tracked_object_ids.append(str(src_obj.id))
    for po in po_objs:
        tracked_object_ids.append(str(po.id))
    for grn in grn_objs:
        tracked_object_ids.append(str(grn.id))
    for inv in inv_objs:
        tracked_object_ids.append(str(inv.id))
    for con in con_objs:
        tracked_object_ids.append(str(con.id))
    if vendor_obj:
        tracked_object_ids.append(str(vendor_obj.id))

    # 3. Build Detailed Data Payloads

    # A. Requisition payload
    if pr_obj:
        lifecycle["stage_status"]["requisition"] = pr_obj.status
        lifecycle["requisition"] = {
            "id": str(pr_obj.id),
            "pr_number": pr_obj.pr_number,
            "title": pr_obj.title,
            "justification": pr_obj.justification,
            "status": pr_obj.status,
            "total_amount": float(pr_obj.total_amount),
            "requested_delivery_date": str(pr_obj.requested_delivery_date),
            "requester": pr_obj.requester.email if pr_obj.requester else None,
            "department": pr_obj.department.name if pr_obj.department else None,
            "cost_center": pr_obj.cost_center.code if pr_obj.cost_center else None,
            "created_at": pr_obj.created_at.isoformat(),
            "lines": [
                {
                    "item_description": line.item_description,
                    "quantity": float(line.quantity),
                    "unit_of_measure": line.unit_of_measure,
                    "estimated_unit_price": float(line.estimated_unit_price),
                    "estimated_total": float(line.estimated_total),
                }
                for line in pr_obj.lines.all()
            ],
        }

        # Approvals for this PR
        app_actions = (
            ApprovalAction.objects.filter(target_object_id=pr_obj.id)
            .select_related("actor", "policy_step")
            .order_by("created_at")
        )
        for act in app_actions:
            lifecycle["approvals"].append(
                {
                    "id": str(act.id),
                    "actor": act.actor.email if act.actor else "System",
                    "action": act.action,
                    "comments": act.comments,
                    "previous_state": act.previous_state,
                    "new_state": act.new_state,
                    "step_description": act.policy_step.description if act.policy_step else None,
                    "timestamp": act.created_at.isoformat(),
                }
            )

        if pr_obj.status in ["APPROVED", "SOURCING", "PO_ISSUED"]:
            lifecycle["stage_status"]["approval"] = "APPROVED"
        elif pr_obj.status == "REJECTED":
            lifecycle["stage_status"]["approval"] = "REJECTED"
            lifecycle["flags"].append(
                {
                    "level": "WARNING",
                    "message": f"Purchase Requisition {pr_obj.pr_number} was rejected.",
                }
            )
        else:
            lifecycle["stage_status"]["approval"] = "IN_PROGRESS"

        # Budget reservations
        for b_res in BudgetReservation.objects.filter(requisition=pr_obj).select_related(
            "budget__cost_center", "budget__fiscal_period"
        ):
            lifecycle["budget_reservations"].append(
                {
                    "id": str(b_res.id),
                    "amount": float(b_res.amount),
                    "status": b_res.status,
                    "cost_center": b_res.budget.cost_center.code,
                    "fiscal_period": b_res.budget.fiscal_period.name,
                    "created_at": b_res.created_at.isoformat(),
                }
            )

    # B. Sourcing Event payload
    if src_obj:
        lifecycle["stage_status"]["sourcing"] = src_obj.status
        award_obj = getattr(src_obj, "award_decision", None)
        lifecycle["sourcing_event"] = {
            "id": str(src_obj.id),
            "event_number": src_obj.event_number,
            "title": src_obj.title,
            "event_type": src_obj.event_type,
            "status": src_obj.status,
            "is_sealed": src_obj.is_sealed,
            "bid_start_date": src_obj.bid_start_date.isoformat(),
            "bid_end_date": src_obj.bid_end_date.isoformat(),
            "invited_vendors_count": src_obj.invitations.count(),
            "total_bids_count": src_obj.bids.count(),
        }

        # Bids
        for bid in src_obj.bids.all().select_related("vendor"):
            tracked_object_ids.append(str(bid.id))
            lifecycle["bids"].append(
                {
                    "id": str(bid.id),
                    "bid_number": bid.bid_number,
                    "vendor": bid.vendor.legal_name,
                    "version": bid.version,
                    "status": bid.status,
                    "total_bid_amount": float(bid.total_bid_amount),
                    "submitted_at": bid.submitted_at.isoformat() if bid.submitted_at else None,
                    "versions_count": bid.versions.count(),
                }
            )

        # Award
        if award_obj:
            lifecycle["award"] = {
                "winning_vendor": award_obj.winning_bid.vendor.legal_name,
                "winning_bid_amount": float(award_obj.winning_bid.total_bid_amount),
                "award_reason": award_obj.award_reason,
                "approved_by": award_obj.approved_by.email if award_obj.approved_by else None,
                "awarded_at": award_obj.created_at.isoformat(),
            }

    # C. Purchase Order payload
    if po_objs:
        for po in po_objs:
            po_status = po.status
            lifecycle["stage_status"]["po_issued"] = po_status
            po_dict = {
                "id": str(po.id),
                "po_number": po.po_number,
                "version": po.version,
                "status": po.status,
                "vendor": po.vendor.legal_name,
                "total_amount": float(po.total_amount),
                "is_acknowledged": bool(po.acknowledged_at),
                "acknowledged_at": po.acknowledged_at.isoformat() if po.acknowledged_at else None,
                "lines": [
                    {
                        "item_description": line.item_description,
                        "quantity_ordered": float(line.quantity),
                        "quantity_received": float(line.quantity_received),
                        "unit_price": float(line.unit_price),
                        "line_total": float(line.line_total),
                    }
                    for line in po.lines.all()
                ],
                "amendments": [
                    {
                        "amendment_number": am.amendment_number,
                        "reason": am.reason,
                        "requested_by": am.requested_by.email if am.requested_by else None,
                        "created_at": am.created_at.isoformat(),
                    }
                    for am in po.amendments.all()
                ],
            }
            lifecycle["purchase_orders"].append(po_dict)

    # D. Goods Receipt & Inspection payload
    if grn_objs:
        all_passed = True
        has_rejections = False
        for grn in grn_objs:
            grn_dict = {
                "id": str(grn.id),
                "grn_number": grn.grn_number,
                "po_number": grn.po.po_number,
                "received_date": grn.received_date.isoformat(),
                "received_by": grn.received_by.email if grn.received_by else None,
                "lines": [],
            }
            for r_line in grn.lines.all():
                insp = getattr(r_line, "inspection", None)
                rejs = list(r_line.rejections.all())
                if rejs:
                    has_rejections = True
                if insp and not insp.passed:
                    all_passed = False

                grn_dict["lines"].append(
                    {
                        "item_description": r_line.po_line.item_description,
                        "quantity_received": float(r_line.quantity_received),
                        "quantity_accepted": float(r_line.quantity_accepted),
                        "quantity_rejected": float(r_line.quantity_rejected),
                        "inspection_status": (
                            "PASSED"
                            if (insp and insp.passed)
                            else ("FAILED" if insp else "PENDING")
                        ),
                        "rejections": [
                            {"qty": float(rj.rejected_quantity), "reason": rj.rejection_reason}
                            for rj in rejs
                        ],
                    }
                )
            lifecycle["receipts"].append(grn_dict)

        lifecycle["stage_status"]["receipt"] = (
            "COMPLETED"
            if (all_passed and not has_rejections)
            else ("EXCEPTION" if has_rejections else "PARTIAL")
        )
        if has_rejections:
            lifecycle["flags"].append(
                {
                    "level": "WARNING",
                    "message": "Goods Receipt records contain rejected quantities.",
                }
            )

    # E. Invoices, 3-Way Match & Payment payload
    if inv_objs:
        for inv in inv_objs:
            pay_obj = getattr(inv, "payment_record", None)
            match_res = inv.match_results.order_by("-created_at").first()
            open_exceptions = inv.exceptions.filter(status="OPEN")
            if open_exceptions.exists():
                lifecycle["flags"].append(
                    {
                        "level": "CRITICAL",
                        "message": f"Invoice {inv.invoice_number} has {open_exceptions.count()} open 3-Way Match exception(s).",
                    }
                )

            inv_dict = {
                "id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "status": inv.status,
                "invoice_date": str(inv.invoice_date),
                "due_date": str(inv.due_date),
                "total_amount": float(inv.total_amount),
                "match_status": (
                    "PASSED"
                    if (match_res and match_res.is_matched)
                    else ("FAILED" if match_res else "NOT_PERFORMED")
                ),
                "exceptions": [
                    {
                        "type": exc.exception_type,
                        "status": exc.status,
                        "description": exc.description,
                        "variance_amount": float(exc.variance_amount),
                        "resolved_by": exc.resolved_by.email if exc.resolved_by else None,
                    }
                    for exc in inv.exceptions.all()
                ],
                "payment": (
                    {
                        "payment_reference": pay_obj.payment_reference,
                        "payment_date": str(pay_obj.payment_date),
                        "amount_paid": float(pay_obj.amount_paid),
                        "method": pay_obj.payment_method,
                        "paid_by": pay_obj.paid_by.email if pay_obj.paid_by else None,
                    }
                    if pay_obj
                    else None
                ),
            }
            lifecycle["invoices"].append(inv_dict)

            if inv.status == "PAID":
                lifecycle["stage_status"]["payment"] = "PAID"
                lifecycle["stage_status"]["matching"] = "RESOLVED"
            elif inv.status == "READY_FOR_PAYMENT":
                lifecycle["stage_status"]["payment"] = "READY"
                lifecycle["stage_status"]["matching"] = "RESOLVED"
            elif inv.status == "EXCEPTION":
                lifecycle["stage_status"]["matching"] = "EXCEPTION"
            else:
                lifecycle["stage_status"]["matching"] = inv.status

    # F. Contracts payload
    if con_objs:
        for con in con_objs:
            lifecycle["stage_status"]["contract"] = con.status
            lifecycle["contracts"].append(
                {
                    "id": str(con.id),
                    "contract_number": con.contract_number,
                    "title": con.title,
                    "status": con.status,
                    "version": con.version,
                    "contract_value": float(con.contract_value),
                    "start_date": str(con.start_date),
                    "end_date": str(con.end_date),
                    "owner": con.contract_owner.email if con.contract_owner else None,
                    "milestones_count": con.milestones.count(),
                    "obligations_count": con.obligations.count(),
                    "alerts_count": con.alerts.filter(is_processed=False).count(),
                }
            )

    # G. Vendor Master payload
    if vendor_obj:
        latest_scorecard = vendor_obj.scorecards.order_by("-created_at").first()
        lifecycle["vendor"] = {
            "id": str(vendor_obj.id),
            "vendor_number": vendor_obj.vendor_number,
            "legal_name": vendor_obj.legal_name,
            "tax_identification_number": vendor_obj.tax_identification_number,
            "status": vendor_obj.status,
            "category": vendor_obj.category.name if vendor_obj.category else None,
            "email": vendor_obj.email,
            "risk_level": (
                vendor_obj.risk_records.order_by("-created_at").first().risk_level
                if vendor_obj.risk_records.exists()
                else "LOW"
            ),
            "composite_score": (
                float(latest_scorecard.composite_score)
                if latest_scorecard and latest_scorecard.composite_score is not None
                else None
            ),
            "kyc_documents_count": vendor_obj.documents.count(),
        }

        if vendor_obj.status in ["ON_HOLD", "SUSPENDED"]:
            lifecycle["flags"].append(
                {
                    "level": "CRITICAL",
                    "message": f"Vendor {vendor_obj.legal_name} is currently in {vendor_obj.status} status.",
                }
            )

    # 4. Aggregated Chronological Audit Trail Across All Graph Nodes
    if tracked_object_ids:
        audit_query = Q(target_object_id__in=tracked_object_ids)
        logs = (
            AuditLog.objects.filter(audit_query).select_related("actor").order_by("timestamp")[:100]
        )
        for log in logs:
            lifecycle["audit_logs"].append(
                {
                    "id": str(log.id),
                    "timestamp": log.timestamp.isoformat(),
                    "actor": log.actor.email if log.actor else "System",
                    "action": log.action,
                    "target_model": log.target_model,
                    "target_object_id": log.target_object_id,
                    "ip_address": log.ip_address,
                    "request_id": log.request_id,
                    "previous_state": log.previous_state,
                    "new_state": log.new_state,
                }
            )

    return lifecycle


def export_auditor_data_service(  # noqa: C901
    export_type: str,
    export_format: str = "csv",
    filters: Optional[Dict[str, Any]] = None,
    actor: Optional[Any] = None,
    request: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Generates structured export data (CSV or JSON) for Auditor Dashboard domains,
    and creates an immutable ACTION_EXPORT audit log record.
    """
    import csv
    import io

    from .selectors import (
        get_approval_history_audit,
        get_audit_logs,
        get_contract_changes_audit,
        get_invoice_exceptions_audit,
        get_po_changes_audit,
        get_security_events_audit,
        get_sourcing_activity_audit,
        get_vendor_compliance_audit,
    )

    filters = filters or {}
    export_format = export_format.lower()
    if export_format not in ["csv", "json", "xlsx"]:
        export_format = "csv"

    data_payload: List[Dict[str, Any]] = []
    headers: List[str] = []
    filename = f"auditor_export_{export_type}_{timezone.now().strftime('%Y%m%d_%H%M%S')}"

    if export_type == "audit_logs":
        logs_res = get_audit_logs(
            action=filters.get("action"),
            target_model=filters.get("target_model"),
            search_term=filters.get("search"),
            limit=500,
        )
        headers = [
            "ID",
            "Timestamp",
            "Actor",
            "Action",
            "Target Model",
            "Target ID",
            "IP Address",
            "Request ID",
        ]
        for log in logs_res["logs"]:
            data_payload.append(
                {
                    "ID": str(log.id),
                    "Timestamp": log.timestamp.isoformat(),
                    "Actor": log.actor.email if log.actor else "System",
                    "Action": log.action,
                    "Target Model": log.target_model,
                    "Target ID": log.target_object_id,
                    "IP Address": log.ip_address,
                    "Request ID": log.request_id,
                }
            )

    elif export_type == "vendor_compliance":
        vc_res = get_vendor_compliance_audit(
            status=filters.get("status"),
            kyc_status=filters.get("kyc_status"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "Vendor Number",
            "Legal Name",
            "Category",
            "Status",
            "Tax ID",
            "Email",
            "Verified Docs",
            "Total Docs",
            "Active Contracts",
        ]
        for item in vc_res["results"]:
            data_payload.append(
                {
                    "Vendor Number": item["vendor_number"],
                    "Legal Name": item["legal_name"],
                    "Category": item["category"] or "",
                    "Status": item["status"],
                    "Tax ID": item["tax_id"],
                    "Email": item["email"],
                    "Verified Docs": item["verified_documents"],
                    "Total Docs": item["total_documents"],
                    "Active Contracts": item["active_contracts_count"],
                }
            )

    elif export_type == "approval_history":
        app_res = get_approval_history_audit(
            target_model=filters.get("target_model"),
            action=filters.get("action"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "Timestamp",
            "Target Model",
            "Target ID",
            "Action",
            "Actor",
            "Role",
            "Policy Step",
            "Comments",
        ]
        for item in app_res["results"]:
            data_payload.append(
                {
                    "Timestamp": item["timestamp"],
                    "Target Model": item["target_model_name"],
                    "Target ID": item["target_object_id"],
                    "Action": item["action"],
                    "Actor": item["actor_email"],
                    "Role": item["actor_role"],
                    "Policy Step": item["policy_step"] or "",
                    "Comments": item["comments"] or "",
                }
            )

    elif export_type == "sourcing_activity":
        src_res = get_sourcing_activity_audit(
            status=filters.get("status"),
            event_type=filters.get("event_type"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "Event Number",
            "Title",
            "Type",
            "Status",
            "Is Sealed",
            "Total Bids",
            "Awarded Vendor",
            "Awarded Amount",
            "Bid End Date",
        ]
        for item in src_res["results"]:
            data_payload.append(
                {
                    "Event Number": item["event_number"],
                    "Title": item["title"],
                    "Type": item["event_type"],
                    "Status": item["status"],
                    "Is Sealed": item["is_sealed"],
                    "Total Bids": item["total_bids"],
                    "Awarded Vendor": item["awarded_vendor"] or "",
                    "Awarded Amount": item["awarded_amount"] or "",
                    "Bid End Date": item["bid_end_date"],
                }
            )

    elif export_type == "po_changes":
        po_res = get_po_changes_audit(
            status=filters.get("status"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "PO Number",
            "Version",
            "Vendor",
            "Status",
            "Total Amount",
            "Cost Center",
            "Amendments Count",
            "Acknowledged At",
        ]
        for item in po_res["results"]:
            data_payload.append(
                {
                    "PO Number": item["po_number"],
                    "Version": item["version"],
                    "Vendor": item["vendor_name"] or "",
                    "Status": item["status"],
                    "Total Amount": item["total_amount"],
                    "Cost Center": item["cost_center"] or "",
                    "Amendments Count": item["amendments_count"],
                    "Acknowledged At": item["acknowledged_at"] or "",
                }
            )

    elif export_type == "invoice_exceptions":
        exc_res = get_invoice_exceptions_audit(
            status=filters.get("status"),
            exception_type=filters.get("exception_type"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "Invoice Number",
            "PO Number",
            "Vendor",
            "Exception Type",
            "Status",
            "Variance Amount",
            "Description",
            "Resolved By",
        ]
        for item in exc_res["results"]:
            data_payload.append(
                {
                    "Invoice Number": item["invoice_number"] or "",
                    "PO Number": item["po_number"] or "",
                    "Vendor": item["vendor_name"] or "",
                    "Exception Type": item["exception_type_display"],
                    "Status": item["status"],
                    "Variance Amount": item["variance_amount"],
                    "Description": item["description"],
                    "Resolved By": item["resolved_by"] or "",
                }
            )

    elif export_type == "contract_changes":
        con_res = get_contract_changes_audit(
            status=filters.get("status"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "Contract Number",
            "Title",
            "Version",
            "Vendor",
            "Status",
            "Value",
            "Start Date",
            "End Date",
            "Owner",
            "Overdue Obligations",
        ]
        for item in con_res["results"]:
            data_payload.append(
                {
                    "Contract Number": item["contract_number"],
                    "Title": item["title"],
                    "Version": item["version"],
                    "Vendor": item["vendor_name"] or "",
                    "Status": item["status"],
                    "Value": item["contract_value"],
                    "Start Date": item["start_date"],
                    "End Date": item["end_date"],
                    "Owner": item["owner_email"] or "",
                    "Overdue Obligations": item["overdue_obligations"],
                }
            )

    elif export_type == "security_events":
        sec_res = get_security_events_audit(
            action=filters.get("action"),
            search=filters.get("search"),
            limit=500,
        )
        headers = [
            "Timestamp",
            "Actor",
            "Role",
            "Action",
            "Target Model",
            "Target ID",
            "IP Address",
            "Request ID",
        ]
        for item in sec_res["results"]:
            data_payload.append(
                {
                    "Timestamp": item["timestamp"],
                    "Actor": item["actor_email"],
                    "Role": item["actor_role"],
                    "Action": item["action"],
                    "Target Model": item["target_model"],
                    "Target ID": item["target_object_id"],
                    "IP Address": item["ip_address"] or "",
                    "Request ID": item["request_id"] or "",
                }
            )

    elif export_type == "lifecycle_trail":
        ident = filters.get("identifier", "")
        lifecycle_data = get_transaction_lifecycle_service(
            entity_type=filters.get("entity_type", "AUTO"),
            entity_identifier=ident,
        )
        headers = [
            "Timestamp",
            "Actor",
            "Action",
            "Target Model",
            "Target ID",
            "IP Address",
            "Request ID",
        ]
        for log in lifecycle_data.get("audit_logs", []):
            data_payload.append(
                {
                    "Timestamp": log["timestamp"],
                    "Actor": log["actor"],
                    "Action": log["action"],
                    "Target Model": log["target_model"],
                    "Target ID": log["target_object_id"],
                    "IP Address": log.get("ip_address") or "",
                    "Request ID": log.get("request_id") or "",
                }
            )

    # Render CSV / JSON
    # Render CSV / XLSX / JSON
    if export_format == "xlsx":
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Audit Export"
        ws.append(headers)
        for row in data_payload:
            ws.append([row.get(h, "") for h in headers])
        buffer = io.BytesIO()
        wb.save(buffer)
        content = buffer.getvalue()
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        file_extension = "xlsx"
    elif export_format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        for row in data_payload:
            writer.writerow(row)
        content = output.getvalue()
        content_type = "text/csv"
        file_extension = "csv"
    else:
        content = json.dumps(
            {
                "export_type": export_type,
                "exported_at": timezone.now().isoformat(),
                "total_records": len(data_payload),
                "data": data_payload,
            },
            indent=2,
            default=str,
        )
        content_type = "application/json"
        file_extension = "json"

    # Immutable export audit log creation
    ip_addr = request.META.get("REMOTE_ADDR") if request else None
    req_id = getattr(request, "request_id", None) or (
        request.META.get("HTTP_X_REQUEST_ID") if request else None
    )
    create_audit_log_service(
        actor=actor,
        action=AuditLog.ACTION_EXPORT,
        target_model="AuditorExport",
        target_object_id=export_type,
        new_state={
            "export_type": export_type,
            "export_format": export_format,
            "record_count": len(data_payload),
            "filters": {k: str(v) for k, v in filters.items()},
        },
        ip_address=ip_addr,
        request_id=req_id,
    )

    return {
        "filename": f"{filename}.{file_extension}",
        "content_type": content_type,
        "content": content,
        "record_count": len(data_payload),
    }

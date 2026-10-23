from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.budgets.models import Budget, SpendLedger
from apps.notifications.models import Notification
from apps.organization.models import CostCenter
from apps.vendors.models import Vendor

from .models import POAmendment, POLine, PurchaseOrder


@transaction.atomic
def generate_purchase_order_service(
    *,
    vendor: Vendor,
    cost_center: CostCenter,
    line_items: list,
    created_by_user: User,
    requisition=None,
    sourcing_event=None,
    terms_and_conditions: str = "Standard HPE Enterprise Procurement Terms & Conditions",
    tax_rate: Decimal = Decimal("0.10"),
    initial_status: str = PurchaseOrder.STATUS_ISSUED,
) -> PurchaseOrder:
    """
    Generates a new Purchase Order from requisition/award.
    Moves BudgetReservation to SpendLedger COMMITMENT when in ISSUED status.
    """
    from apps.core.services import generate_document_number

    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is suspended and cannot be issued POs."
        )

    po_number = generate_document_number(PurchaseOrder, "po_number", "PO")

    po = PurchaseOrder.objects.create(
        po_number=po_number,
        version=1,
        vendor=vendor,
        requisition=requisition,
        sourcing_event=sourcing_event,
        cost_center=cost_center,
        status=initial_status,
        terms_and_conditions=terms_and_conditions,
        subtotal=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("0.00"),
    )

    subtotal = Decimal("0.00")
    for item in line_items:
        line = POLine.objects.create(
            po=po,
            item_description=item["item_description"],
            quantity=Decimal(str(item["quantity"])),
            unit_price=Decimal(str(item["unit_price"])),
            unit_of_measure=item.get("unit_of_measure", "EA"),
        )
        subtotal += line.line_total

    tax = subtotal * tax_rate
    total = subtotal + tax

    po.subtotal = subtotal
    po.tax_amount = tax
    po.total_amount = total
    po.save(update_fields=["subtotal", "tax_amount", "total_amount", "updated_at"])

    # Move BudgetReservation to SpendLedger COMMITMENT only if PO is in ISSUED status
    if initial_status == PurchaseOrder.STATUS_ISSUED:
        budget = Budget.objects.filter(cost_center=cost_center).order_by("-created_at").first()
        if budget:
            budget.committed_amount += total
            budget.save(update_fields=["committed_amount", "updated_at"])

            SpendLedger.objects.create(
                budget=budget,
                entry_type=SpendLedger.ENTRY_COMMITMENT,
                amount=total,
                reference_number=po.po_number,
                description=f"PO Commitment for {vendor.legal_name}",
            )

        # Notify active vendor users
        for v_user in User.objects.filter(vendor=vendor, is_active=True):
            Notification.objects.create(
                recipient=v_user,
                notification_type=Notification.TYPE_PO_ISSUED,
                title=f"New Purchase Order Issued: {po.po_number}",
                message=f"A new Purchase Order {po.po_number} (${po.total_amount:,.2f}) has been issued to {vendor.legal_name}.",
                target_url=f"/orders/{po.id}/",
            )
        # Notify requisition requester if present
        if requisition and requisition.requester:
            Notification.objects.create(
                recipient=requisition.requester,
                notification_type=Notification.TYPE_PO_ISSUED,
                title=f"PO {po.po_number} Generated for PR {requisition.pr_number}",
                message=f"Purchase Order {po.po_number} has been generated from your approved requisition {requisition.pr_number}.",
                target_url=f"/orders/{po.id}/",
            )

    AuditLog.objects.create(
        actor=created_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        new_state={
            "po_number": po.po_number,
            "version": po.version,
            "total_amount": str(po.total_amount),
            "vendor": vendor.legal_name,
        },
    )

    return po


@transaction.atomic
def acknowledge_purchase_order_service(
    *, po: PurchaseOrder, vendor_user: User, acknowledgement_notes: str = ""
) -> PurchaseOrder:
    """
    Vendor acknowledges an issued PO. Transitions status to ACKNOWLEDGED.
    """
    if po.status != PurchaseOrder.STATUS_ISSUED:
        raise ValidationError(f"Cannot acknowledge PO in status '{po.status}'. Must be ISSUED.")

    previous_status = po.status
    po.status = PurchaseOrder.STATUS_ACKNOWLEDGED
    po.acknowledged_at = timezone.now()
    po.acknowledged_by = vendor_user
    po.acknowledgement_notes = acknowledgement_notes or po.acknowledgement_notes
    po.save(
        update_fields=[
            "status",
            "acknowledged_at",
            "acknowledged_by",
            "acknowledgement_notes",
            "updated_at",
        ]
    )

    AuditLog.objects.create(
        actor=vendor_user,
        action=AuditLog.ACTION_UPDATE,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        previous_state={"status": previous_status},
        new_state={
            "status": po.status,
            "acknowledged_at": str(po.acknowledged_at),
            "acknowledged_by": vendor_user.email,
            "acknowledgement_notes": po.acknowledgement_notes,
        },
    )

    if po.requisition and po.requisition.requester:
        Notification.objects.create(
            recipient=po.requisition.requester,
            notification_type=Notification.TYPE_PO_ACKNOWLEDGED,
            title=f"PO {po.po_number} Acknowledged by Vendor",
            message=f"Vendor {po.vendor.legal_name} has acknowledged Purchase Order {po.po_number}.",
            target_url=f"/orders/{po.id}/",
        )

    return po


@transaction.atomic
def amend_purchase_order_service(
    *,
    po: PurchaseOrder,
    reason: str,
    updated_line_items: list,
    requested_by_user: User,
    tax_rate: Decimal = Decimal("0.10"),
) -> POAmendment:
    """
    Creates a PO Change Order / Amendment:
    - Captures immutable JSON snapshot of prior PO state.
    - Increments PO version counter (e.g. V1 -> V2).
    - Preserves prior POLine history in POAmendment record.
    """
    if not reason:
        raise ValidationError("Amendment justification reason is required.")

    # 1. Take JSON snapshot of previous version
    snapshot = {
        "po_number": po.po_number,
        "version": po.version,
        "subtotal": str(po.subtotal),
        "tax_amount": str(po.tax_amount),
        "total_amount": str(po.total_amount),
        "status": po.status,
        "lines": [
            {
                "description": line.item_description,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
                "line_total": str(line.line_total),
            }
            for line in po.lines.all()
        ],
    }

    amendment_count = po.amendments.count() + 1
    amendment = POAmendment.objects.create(
        po=po,
        amendment_number=amendment_count,
        reason=reason,
        previous_version_snapshot=snapshot,
        requested_by=requested_by_user,
    )

    # 2. Update PO version and line items
    po.version += 1
    po.lines.all().delete()

    subtotal = Decimal("0.00")
    for item in updated_line_items:
        line = POLine.objects.create(
            po=po,
            item_description=item["item_description"],
            quantity=Decimal(str(item["quantity"])),
            unit_price=Decimal(str(item["unit_price"])),
            unit_of_measure=item.get("unit_of_measure", "EA"),
        )
        subtotal += line.line_total

    tax = subtotal * tax_rate
    total = subtotal + tax

    old_total = Decimal(str(snapshot["total_amount"]))
    diff = total - old_total

    po.subtotal = subtotal
    po.tax_amount = tax
    po.total_amount = total
    po.save(update_fields=["version", "subtotal", "tax_amount", "total_amount", "updated_at"])

    # Update Budget commitment with delta difference
    if diff != Decimal("0.00"):
        budget = Budget.objects.filter(cost_center=po.cost_center).order_by("-created_at").first()
        if budget:
            budget.committed_amount = max(Decimal("0.00"), budget.committed_amount + diff)
            budget.save(update_fields=["committed_amount", "updated_at"])

            SpendLedger.objects.create(
                budget=budget,
                entry_type=SpendLedger.ENTRY_COMMITMENT,
                amount=diff,
                reference_number=f"{po.po_number}-AMD{amendment_count}",
                description=f"PO Amendment #{amendment_count} for {po.vendor.legal_name} adjustment",
            )

    AuditLog.objects.create(
        actor=requested_by_user,
        action=AuditLog.ACTION_UPDATE,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        previous_state={"version": snapshot["version"], "total_amount": snapshot["total_amount"]},
        new_state={
            "version": po.version,
            "total_amount": str(po.total_amount),
            "amendment_number": amendment.amendment_number,
        },
    )

    for v_user in User.objects.filter(vendor=po.vendor, is_active=True):
        Notification.objects.create(
            recipient=v_user,
            notification_type=Notification.TYPE_PO_AMENDMENT,
            title=f"Purchase Order Amendment: {po.po_number} V{po.version}",
            message=f"Purchase Order {po.po_number} has been updated to Version {po.version} (Reason: {reason}).",
            target_url=f"/orders/{po.id}/",
        )
    if po.requisition and po.requisition.requester:
        Notification.objects.create(
            recipient=po.requisition.requester,
            notification_type=Notification.TYPE_PO_AMENDMENT,
            title=f"PO {po.po_number} Amended to V{po.version}",
            message=f"Purchase Order {po.po_number} linked to PR {po.requisition.pr_number} was amended to V{po.version}.",
            target_url=f"/orders/{po.id}/",
        )

    return amendment


@transaction.atomic
def cancel_purchase_order_governance_service(
    *, po: PurchaseOrder, actor: User, reason: str
) -> PurchaseOrder:
    """
    Procurement Manager governance action to cancel a Purchase Order.
    - Transitions PO status to CANCELLED.
    - Releases unfulfilled financial commitment back to the budget.
    - Creates a reversal SpendLedger entry.
    - Records an append-only AuditLog entry.
    """
    if not reason or not str(reason).strip():
        raise ValidationError("Cancellation reason / justification is mandatory.")

    if po.status == PurchaseOrder.STATUS_CANCELLED:
        raise ValidationError(f"Purchase Order {po.po_number} is already cancelled.")

    if po.status == PurchaseOrder.STATUS_COMPLETED:
        raise ValidationError(f"Cannot cancel completed/fulfilled Purchase Order {po.po_number}.")

    previous_status = po.status
    po.status = PurchaseOrder.STATUS_CANCELLED
    po.save(update_fields=["status", "updated_at"])

    # Calculate unfulfilled commitment amount to release
    total_received_value = Decimal("0.00")
    for line in po.lines.all():
        if line.quantity_received:
            total_received_value += line.quantity_received * line.unit_price

    unfulfilled_commitment = max(Decimal("0.00"), po.total_amount - total_received_value)

    if unfulfilled_commitment > Decimal("0.00"):
        budget = Budget.objects.filter(cost_center=po.cost_center).order_by("-created_at").first()
        if budget:
            budget.committed_amount = max(
                Decimal("0.00"), budget.committed_amount - unfulfilled_commitment
            )
            budget.save(update_fields=["committed_amount", "updated_at"])

            SpendLedger.objects.create(
                budget=budget,
                entry_type=SpendLedger.ENTRY_COMMITMENT,
                amount=-unfulfilled_commitment,
                reference_number=f"{po.po_number}-CANCEL",
                description=f"Commitment release on PO Cancellation: {po.po_number} ({reason})",
            )

    AuditLog.objects.create(
        actor=actor,
        action=AuditLog.ACTION_REJECT,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        previous_state={"status": previous_status, "total_amount": str(po.total_amount)},
        new_state={
            "status": po.status,
            "reason": reason,
            "released_commitment": str(unfulfilled_commitment),
        },
    )

    return po


@transaction.atomic
def submit_purchase_order_for_approval_service(
    *, po: PurchaseOrder, actor: User
) -> PurchaseOrder:
    """
    Submits a draft PO for managerial review and approval.
    Transitions DRAFT -> APPROVAL.
    """
    if po.status != PurchaseOrder.STATUS_DRAFT:
        raise ValidationError(
            f"Cannot submit PO in status '{po.status}' for approval. Must be DRAFT."
        )

    previous_status = po.status
    po.status = PurchaseOrder.STATUS_APPROVAL
    po.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=actor,
        action=AuditLog.ACTION_SUBMIT,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        previous_state={"status": previous_status},
        new_state={"status": po.status},
    )
    return po


@transaction.atomic
def approve_purchase_order_service(
    *, po: PurchaseOrder, approver: User, comments: str = ""
) -> PurchaseOrder:
    """
    Approves a PO and transitions to ISSUED state.
    Commits funds against the designated Cost Center budget.
    """
    if po.status not in [PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_APPROVAL]:
        raise ValidationError(
            f"Cannot approve PO in status '{po.status}'. Must be DRAFT or APPROVAL."
        )

    if po.vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{po.vendor.legal_name}' is suspended and cannot be issued POs."
        )

    previous_status = po.status
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status", "updated_at"])

    # Commit funds in SpendLedger if not already committed
    budget = Budget.objects.filter(cost_center=po.cost_center).order_by("-created_at").first()
    if budget:
        budget.committed_amount += po.total_amount
        budget.save(update_fields=["committed_amount", "updated_at"])

        SpendLedger.objects.create(
            budget=budget,
            entry_type=SpendLedger.ENTRY_COMMITMENT,
            amount=po.total_amount,
            reference_number=po.po_number,
            description=f"PO Commitment on Approval for {po.vendor.legal_name} ({comments or 'Approved'})",
        )

    AuditLog.objects.create(
        actor=approver,
        action=AuditLog.ACTION_APPROVE,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        previous_state={"status": previous_status},
        new_state={"status": po.status, "comments": comments},
    )
    return po


@transaction.atomic
def reject_purchase_order_service(
    *, po: PurchaseOrder, approver: User, reason: str
) -> PurchaseOrder:
    """
    Rejects a PO in approval, returning it to DRAFT state.
    """
    if not reason:
        raise ValidationError("Rejection reason is required.")

    if po.status != PurchaseOrder.STATUS_APPROVAL:
        raise ValidationError(f"Cannot reject PO in status '{po.status}'. Must be APPROVAL.")

    previous_status = po.status
    po.status = PurchaseOrder.STATUS_DRAFT
    po.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=approver,
        action=AuditLog.ACTION_REJECT,
        target_model="PurchaseOrder",
        target_object_id=str(po.id),
        previous_state={"status": previous_status},
        new_state={"status": po.status, "rejection_reason": reason},
    )
    return po


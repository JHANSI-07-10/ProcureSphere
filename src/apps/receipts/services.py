from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.orders.models import POLine, PurchaseOrder

from .models import (
    GoodsReceipt,
    InspectionRecord,
    ReceiptLine,
    RejectionRecord,
    StockHandoffRecord,
)


def _validate_and_create_receipt_line(
    *,
    receipt: GoodsReceipt,
    po: PurchaseOrder,
    received_by: User,
    item: dict,
) -> None:
    po_line_id = item.get("po_line_id") or (item["po_line"].id if "po_line" in item else None)
    if not po_line_id:
        raise ValidationError("Invalid receipt line: missing PO line reference.")

    try:
        po_line = POLine.objects.select_for_update().get(id=po_line_id)
    except (POLine.DoesNotExist, ValueError):
        raise ValidationError(f"PO line reference '{po_line_id}' is invalid or does not exist.")

    if po_line.po_id != po.id:
        raise ValidationError(
            f"PO line {po_line.id} does not belong to Purchase Order {po.po_number}."
        )

    try:
        qty_received = Decimal(str(item.get("quantity_received", "")))
    except Exception:
        raise ValidationError(
            f"Quantity received for '{po_line.item_description}' must be a valid numeric value."
        )

    if qty_received <= Decimal("0.00"):
        raise ValidationError(
            f"Quantity received for '{po_line.item_description}' must be greater than 0."
        )

    current_received = po_line.quantity_received or Decimal("0.00")
    remaining_qty = max(Decimal("0.00"), po_line.quantity - current_received)
    if qty_received > remaining_qty:
        raise ValidationError(
            f"Quantity received ({qty_received}) cannot exceed remaining quantity ({remaining_qty}) for '{po_line.item_description}'."
        )

    qty_accepted_raw = item.get("quantity_accepted")
    if qty_accepted_raw is not None and str(qty_accepted_raw).strip() != "":
        try:
            qty_accepted = Decimal(str(qty_accepted_raw))
        except Exception:
            raise ValidationError(
                f"Quantity accepted for '{po_line.item_description}' must be a valid numeric value."
            )
    else:
        qty_accepted = qty_received

    if qty_accepted < Decimal("0.00"):
        raise ValidationError(
            f"Quantity accepted for '{po_line.item_description}' cannot be negative."
        )
    if qty_accepted > qty_received:
        raise ValidationError(
            f"Quantity accepted ({qty_accepted}) cannot exceed quantity received ({qty_received}) for '{po_line.item_description}'."
        )

    qty_rejected = qty_received - qty_accepted

    line = ReceiptLine.objects.create(
        receipt=receipt,
        po_line=po_line,
        quantity_received=qty_received,
        quantity_accepted=qty_accepted,
        quantity_rejected=qty_rejected,
        notes=item.get("notes", ""),
    )

    if "inspection_notes" in item:
        InspectionRecord.objects.create(
            receipt_line=line,
            inspected_by=received_by,
            passed=(qty_rejected == Decimal("0.00")),
            inspection_notes=item["inspection_notes"],
        )

    if qty_rejected > Decimal("0.00"):
        RejectionRecord.objects.create(
            receipt_line=line,
            rejected_quantity=qty_rejected,
            rejection_reason=item.get("rejection_reason", "Quality non-conformance"),
        )

    po_line.quantity_received = current_received + qty_accepted
    po_line.save(update_fields=["quantity_received", "updated_at"])


def _check_stores_receiver_permission(
    user: User, action_desc: str = "perform stores operations"
) -> None:
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else None
    )
    is_authorized = user.is_superuser or role_code in [Role.STORES_RECEIVER, Role.SUPER_ADMIN]
    if not is_authorized:
        raise ValidationError(
            f"Unauthorized: Only Stores Receiver or Super Admin can {action_desc}."
        )


def _update_po_completion_status(po: PurchaseOrder) -> None:
    lines = list(POLine.objects.filter(po=po))
    if not lines:
        return

    all_completed = True
    any_received = False
    for line in lines:
        line_received = line.quantity_received or Decimal("0.00")
        if line_received < line.quantity:
            all_completed = False
        if line_received > Decimal("0.00"):
            any_received = True

    if all_completed:
        po.status = PurchaseOrder.STATUS_COMPLETED
    elif any_received:
        po.status = PurchaseOrder.STATUS_PARTIAL_RECEIPT
    else:
        po.status = (
            PurchaseOrder.STATUS_ACKNOWLEDGED if po.acknowledged_at else PurchaseOrder.STATUS_ISSUED
        )
    po.save(update_fields=["status", "updated_at"])


@transaction.atomic
def create_goods_receipt_service(
    *,
    po: PurchaseOrder,
    received_by: User,
    receipt_items: list,
    delivery_note_number: str = "",
    remarks: str = "",
) -> GoodsReceipt:
    """
    Records a Goods Receipt Note (GRN) against a Purchase Order.
    Supports partial receipts and multiple GRNs against one PO.
    Updates POLine.quantity_received and transitions PO status (PARTIAL_RECEIPT vs COMPLETED).
    """
    if not po:
        raise ValidationError("Purchase Order reference is mandatory.")
    if not received_by:
        raise ValidationError("Received by user reference is mandatory.")
    _check_stores_receiver_permission(received_by, "create goods receipts")

    if not receipt_items or not isinstance(receipt_items, list):
        raise ValidationError("Receipt must contain at least one line item.")

    po = PurchaseOrder.objects.select_for_update().get(id=po.id)

    if po.status not in [
        PurchaseOrder.STATUS_ISSUED,
        PurchaseOrder.STATUS_ACKNOWLEDGED,
        PurchaseOrder.STATUS_PARTIAL_RECEIPT,
    ]:
        raise ValidationError(f"Cannot record goods receipt against PO in status '{po.status}'.")

    from apps.core.services import generate_document_number
    from apps.notifications.models import Notification

    grn_number = generate_document_number(GoodsReceipt, "grn_number", "GRN")

    receipt = GoodsReceipt.objects.create(
        grn_number=grn_number,
        po=po,
        received_by=received_by,
        received_date=timezone.now(),
        delivery_note_number=delivery_note_number,
        remarks=remarks,
    )

    for item in receipt_items:
        _validate_and_create_receipt_line(
            receipt=receipt,
            po=po,
            received_by=received_by,
            item=item,
        )

    _update_po_completion_status(po)

    AuditLog.objects.create(
        actor=received_by,
        action=AuditLog.ACTION_CREATE,
        target_model="GoodsReceipt",
        target_object_id=str(receipt.id),
        new_state={
            "grn_number": receipt.grn_number,
            "po_number": po.po_number,
            "po_status": po.status,
        },
    )

    if po.requisition and po.requisition.requester:
        Notification.objects.create(
            recipient=po.requisition.requester,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title=f"Goods Receipt Recorded: {receipt.grn_number}",
            message=f"Goods/services have been received under receipt {receipt.grn_number} for PO {po.po_number}.",
            target_url=f"/receipts/{receipt.id}/",
        )

    return receipt


def _check_inspection_permission(user: User) -> None:
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else None
    )
    is_authorized = user.is_superuser or role_code in [Role.STORES_RECEIVER, Role.SUPER_ADMIN]
    if not is_authorized:
        raise ValidationError(
            "Unauthorized: Only Stores Receiver or Super Admin can perform quality inspections."
        )


def _sync_rejection_record(
    receipt_line: ReceiptLine,
    qty_rejected: Decimal,
    item: dict,
    inspection_notes: str,
) -> None:
    if qty_rejected > Decimal("0.00"):
        rejection_reason = (
            str(item.get("rejection_reason", "")).strip()
            or str(inspection_notes).strip()
            or f"Quality rejection of {qty_rejected} units"
        )
        returned_to_vendor = bool(item.get("returned_to_vendor", False))

        existing_rejection = RejectionRecord.objects.filter(receipt_line=receipt_line).first()
        if existing_rejection:
            existing_rejection.rejected_quantity = qty_rejected
            existing_rejection.rejection_reason = rejection_reason
            existing_rejection.returned_to_vendor = returned_to_vendor
            existing_rejection.save(
                update_fields=[
                    "rejected_quantity",
                    "rejection_reason",
                    "returned_to_vendor",
                    "updated_at",
                ]
            )
        else:
            RejectionRecord.objects.create(
                receipt_line=receipt_line,
                rejected_quantity=qty_rejected,
                rejection_reason=rejection_reason,
                returned_to_vendor=returned_to_vendor,
            )
    else:
        RejectionRecord.objects.filter(receipt_line=receipt_line).delete()


def _apply_single_line_inspection(
    *,
    receipt: GoodsReceipt,
    po: PurchaseOrder,
    inspected_by: User,
    item: dict,
) -> None:
    receipt_line_id = item.get("receipt_line_id") or (
        item["receipt_line"].id if "receipt_line" in item else None
    )
    if not receipt_line_id:
        raise ValidationError("Invalid inspection item: missing ReceiptLine reference.")

    try:
        receipt_line = (
            ReceiptLine.objects.select_for_update()
            .select_related("po_line")
            .get(id=receipt_line_id)
        )
    except ReceiptLine.DoesNotExist:
        raise ValidationError(f"Receipt line {receipt_line_id} does not exist.")

    if receipt_line.receipt_id != receipt.id or receipt_line.po_line.po_id != po.id:
        raise ValidationError(
            f"Receipt line {receipt_line.id} does not match Goods Receipt or PO reference."
        )

    passed = bool(item.get("passed", True))
    inspection_notes = str(item.get("inspection_notes", "")).strip()

    rejected_qty_raw = item.get("rejected_quantity")
    if rejected_qty_raw is not None and str(rejected_qty_raw).strip() != "":
        qty_rejected = Decimal(str(rejected_qty_raw))
    else:
        qty_rejected = Decimal("0.00") if passed else receipt_line.quantity_received

    if qty_rejected < Decimal("0.00"):
        raise ValidationError(
            f"Rejected quantity cannot be negative for '{receipt_line.po_line.item_description}'."
        )
    if qty_rejected > receipt_line.quantity_received:
        raise ValidationError(
            f"Rejected quantity ({qty_rejected}) cannot exceed received quantity ({receipt_line.quantity_received}) on '{receipt_line.po_line.item_description}'."
        )

    qty_accepted = receipt_line.quantity_received - qty_rejected
    if qty_rejected > Decimal("0.00"):
        passed = False
        rejection_reason = str(item.get("rejection_reason", "")).strip()
        if not rejection_reason and not inspection_notes:
            raise ValidationError(
                f"Rejection reason is required when rejecting quantities for '{receipt_line.po_line.item_description}'."
            )
    elif not passed and qty_rejected == Decimal("0.00"):
        # If marked failed without specifying rejected quantity, entire line is rejected
        qty_rejected = receipt_line.quantity_received
        qty_accepted = Decimal("0.00")

    old_accepted = receipt_line.quantity_accepted
    delta_accepted = qty_accepted - old_accepted

    if delta_accepted != Decimal("0.00"):
        po_line = POLine.objects.select_for_update().get(id=receipt_line.po_line_id)
        current_recv = po_line.quantity_received or Decimal("0.00")
        new_recv = max(Decimal("0.00"), current_recv + delta_accepted)
        if new_recv > po_line.quantity:
            raise ValidationError(
                f"Accepted quantity cannot cause received quantity ({new_recv}) to exceed ordered quantity ({po_line.quantity}) on '{po_line.item_description}'."
            )
        po_line.quantity_received = new_recv
        po_line.save(update_fields=["quantity_received", "updated_at"])

    receipt_line.quantity_accepted = qty_accepted
    receipt_line.quantity_rejected = qty_rejected
    receipt_line.save(update_fields=["quantity_accepted", "quantity_rejected", "updated_at"])

    InspectionRecord.objects.update_or_create(
        receipt_line=receipt_line,
        defaults={
            "inspected_by": inspected_by,
            "passed": passed,
            "inspection_notes": inspection_notes
            or f"Inspection {'PASSED' if passed else 'FAILED'}",
        },
    )

    _sync_rejection_record(receipt_line, qty_rejected, item, inspection_notes)

    if hasattr(receipt_line, "stock_handoff"):
        try:
            handoff = receipt_line.stock_handoff
            handoff.quantity_handed_off = qty_accepted
            handoff.save(update_fields=["quantity_handed_off", "updated_at"])
        except StockHandoffRecord.DoesNotExist:
            pass


@transaction.atomic
def record_inspection_service(
    *,
    receipt: GoodsReceipt,
    inspected_by: User,
    inspection_items: list,
) -> GoodsReceipt:
    """
    Records or updates quality inspection and rejection records for a Goods Receipt Note (GRN).
    Enforces server-side quantity validation, ownership verification, and updates PO line quantities.
    """
    _check_inspection_permission(inspected_by)

    if not inspection_items:
        raise ValidationError("Inspection must contain results for at least one line item.")

    po = PurchaseOrder.objects.select_for_update().get(id=receipt.po_id)

    # Capture previous state snapshot for audit
    previous_state = {
        "grn_number": receipt.grn_number,
        "po_number": po.po_number,
        "overall_inspection_status": receipt.overall_inspection_status,
        "lines": [
            {
                "line_id": str(line.id),
                "item_description": line.po_line.item_description,
                "quantity_received": str(line.quantity_received),
                "quantity_accepted": str(line.quantity_accepted),
                "quantity_rejected": str(line.quantity_rejected),
                "inspection_passed": (
                    line.inspection.passed if hasattr(line, "inspection") else None
                ),
            }
            for line in receipt.lines.select_related("po_line").all()
        ],
    }

    for item in inspection_items:
        _apply_single_line_inspection(
            receipt=receipt,
            po=po,
            inspected_by=inspected_by,
            item=item,
        )

    _update_po_completion_status(po)

    # Re-fetch receipt and lines for new state snapshot
    receipt.refresh_from_db()
    new_state = {
        "grn_number": receipt.grn_number,
        "action": "RECORD_INSPECTION",
        "po_number": po.po_number,
        "overall_inspection_status": receipt.overall_inspection_status,
        "inspected_lines_count": len(inspection_items),
        "lines": [
            {
                "line_id": str(line.id),
                "item_description": line.po_line.item_description,
                "quantity_received": str(line.quantity_received),
                "quantity_accepted": str(line.quantity_accepted),
                "quantity_rejected": str(line.quantity_rejected),
                "inspection_passed": (
                    line.inspection.passed if hasattr(line, "inspection") else None
                ),
                "inspection_notes": (
                    line.inspection.inspection_notes if hasattr(line, "inspection") else ""
                ),
                "rejection_reason": (
                    line.latest_rejection.rejection_reason if line.latest_rejection else ""
                ),
            }
            for line in receipt.lines.select_related("po_line").all()
        ],
    }

    AuditLog.objects.create(
        actor=inspected_by,
        action=AuditLog.ACTION_UPDATE,
        target_model="GoodsReceipt",
        target_object_id=str(receipt.id),
        previous_state=previous_state,
        new_state=new_state,
    )

    from apps.notifications.models import Notification

    has_rejected = any(line.quantity_rejected > Decimal("0.00") for line in receipt.lines.all())
    if has_rejected:
        recipients = set()
        if po.requisition and po.requisition.requester:
            recipients.add(po.requisition.requester)
        if po.created_by:
            recipients.add(po.created_by)

        for recipient in recipients:
            if not Notification.objects.filter(
                recipient=recipient,
                notification_type=Notification.TYPE_EXCEPTION_RAISED,
                target_url=f"/receipts/{receipt.id}/",
            ).exists():
                Notification.objects.create(
                    recipient=recipient,
                    notification_type=Notification.TYPE_EXCEPTION_RAISED,
                    title=f"Receipt Quality Rejection: {receipt.grn_number}",
                    message=(
                        f"Quality inspection recorded rejected items on receipt {receipt.grn_number} "
                        f"for PO {po.po_number}."
                    ),
                    target_url=f"/receipts/{receipt.id}/",
                )
    elif receipt.overall_inspection_status == "PASSED":
        if po.requisition and po.requisition.requester:
            if not Notification.objects.filter(
                recipient=po.requisition.requester,
                notification_type=Notification.TYPE_GOODS_RECEIVED,
                title__startswith="Receipt Inspection Passed",
                target_url=f"/receipts/{receipt.id}/",
            ).exists():
                Notification.objects.create(
                    recipient=po.requisition.requester,
                    notification_type=Notification.TYPE_GOODS_RECEIVED,
                    title=f"Receipt Inspection Passed: {receipt.grn_number}",
                    message=(
                        f"Quality inspection has passed for all items on receipt {receipt.grn_number} "
                        f"for PO {po.po_number}."
                    ),
                    target_url=f"/receipts/{receipt.id}/",
                )

    return receipt


def _apply_line_stock_handoff(
    *,
    line: ReceiptLine,
    handed_off_by: User,
    location: str,
    notes: str,
) -> StockHandoffRecord:
    if line.quantity_accepted <= Decimal("0.00"):
        raise ValidationError(
            f"Cannot hand off line '{line.po_line.item_description}' with 0 accepted quantity to stock."
        )

    handoff, _ = StockHandoffRecord.objects.update_or_create(
        receipt_line=line,
        defaults={
            "handed_off_by": handed_off_by,
            "quantity_handed_off": line.quantity_accepted,
            "storage_location": location or "MAIN-WH",
            "handoff_notes": notes or "",
        },
    )
    return handoff


@transaction.atomic
def handoff_goods_to_stock_service(
    *,
    receipt: GoodsReceipt,
    handed_off_by: User,
    storage_location: str = "MAIN-WH",
    handoff_notes: str = "",
    line_items: list = None,
) -> GoodsReceipt:
    """
    Hands accepted goods from a Goods Receipt Note into inventory/stock.
    Ensures:
    - Only authorized Stores Receiver can execute.
    - Only accepted quantities are handed off (rejected items excluded).
    - Idempotent: repeated submissions or re-runs do not double-post stock.
    - End-to-end traceability with storage location, notes, and audit log.
    """
    _check_stores_receiver_permission(handed_off_by)

    receipt = GoodsReceipt.objects.select_for_update().get(id=receipt.id)
    lines = list(
        ReceiptLine.objects.select_for_update().select_related("po_line").filter(receipt=receipt)
    )

    if not lines:
        raise ValidationError("Cannot hand off to stock: Receipt contains no line items.")

    accepted_lines = [rline for rline in lines if rline.quantity_accepted > Decimal("0.00")]
    if not accepted_lines:
        raise ValidationError(
            "Cannot hand off to stock: No accepted items available in this receipt."
        )

    lines_by_id = {str(rline.id): rline for rline in lines}
    processed_count = 0

    if line_items:
        for item in line_items:
            line_id = str(
                item.get("receipt_line_id")
                or (item["receipt_line"].id if "receipt_line" in item else "")
            )
            line = lines_by_id.get(line_id)
            if not line:
                raise ValidationError(
                    f"Receipt line {line_id} does not belong to Goods Receipt {receipt.grn_number}."
                )

            loc = str(item.get("storage_location") or storage_location or "MAIN-WH").strip()
            notes = str(item.get("handoff_notes") or handoff_notes or "").strip()
            _apply_line_stock_handoff(
                line=line,
                handed_off_by=handed_off_by,
                location=loc,
                notes=notes,
            )
            processed_count += 1
    else:
        loc = (storage_location or "MAIN-WH").strip()
        notes = (handoff_notes or "").strip()
        for line in accepted_lines:
            _apply_line_stock_handoff(
                line=line,
                handed_off_by=handed_off_by,
                location=loc,
                notes=notes,
            )
            processed_count += 1

    AuditLog.objects.create(
        actor=handed_off_by,
        action=AuditLog.ACTION_UPDATE,
        target_model="GoodsReceipt",
        target_object_id=str(receipt.id),
        new_state={
            "grn_number": receipt.grn_number,
            "action": "STOCK_HANDOFF",
            "storage_location": storage_location or "MAIN-WH",
            "lines_handed_off": processed_count,
        },
    )

    return receipt


@transaction.atomic
def process_vendor_return_service(
    *,
    returned_by: User,
    rejection_record: RejectionRecord = None,
    receipt_line: ReceiptLine = None,
    return_quantity: Decimal = None,
    tracking_reference: str = "",
    return_notes: str = "",
) -> RejectionRecord:
    """
    Processes the physical return of rejected goods to the vendor.
    Ensures:
    - Only authorized Stores Receiver or Super Admin can execute returns.
    - Cannot return more than the rejected quantity.
    - Does NOT alter accepted stock (never increases inventory).
    - Preserves return dispatch reference and full audit log.
    """
    _check_stores_receiver_permission(returned_by, "process vendor returns")

    if not rejection_record and not receipt_line:
        raise ValidationError("Either rejection_record or receipt_line must be provided.")

    if rejection_record:
        rejection_record = (
            RejectionRecord.objects.select_for_update()
            .select_related("receipt_line__receipt__po", "receipt_line__po_line")
            .get(id=rejection_record.id)
        )
        receipt_line = rejection_record.receipt_line
    else:
        receipt_line = (
            ReceiptLine.objects.select_for_update()
            .select_related("receipt__po", "po_line")
            .get(id=receipt_line.id)
        )
        rejection_record = RejectionRecord.objects.filter(receipt_line=receipt_line).first()
        if not rejection_record:
            raise ValidationError(
                f"No rejection record found for line '{receipt_line.po_line.item_description}'."
            )

    if receipt_line.quantity_rejected <= Decimal("0.00"):
        raise ValidationError(
            f"Cannot return line '{receipt_line.po_line.item_description}' with 0 rejected quantity."
        )

    max_returnable = rejection_record.rejected_quantity
    if return_quantity is not None:
        return_quantity = Decimal(str(return_quantity))
        if return_quantity <= Decimal("0.00"):
            raise ValidationError("Return quantity must be greater than 0.")
        if return_quantity > max_returnable:
            raise ValidationError(
                f"Return quantity ({return_quantity}) cannot exceed rejected quantity ({max_returnable}) for '{receipt_line.po_line.item_description}'."
            )
    else:
        return_quantity = max_returnable

    if rejection_record.returned_to_vendor:
        raise ValidationError(
            f"Rejected goods for line '{receipt_line.po_line.item_description}' have already been returned to vendor."
        )

    prev_returned = rejection_record.returned_to_vendor
    rejection_record.returned_to_vendor = True

    dispatch_info = []
    if tracking_reference:
        dispatch_info.append(f"Tracking Ref: {tracking_reference.strip()}")
    if return_notes:
        dispatch_info.append(f"Notes: {return_notes.strip()}")
    if dispatch_info:
        dispatch_str = (
            f" [Dispatched return of {return_quantity} units: {', '.join(dispatch_info)}]"
        )
        rejection_record.rejection_reason = f"{rejection_record.rejection_reason}{dispatch_str}"

    rejection_record.save(update_fields=["returned_to_vendor", "rejection_reason", "updated_at"])

    AuditLog.objects.create(
        actor=returned_by,
        action="RETURN_TO_VENDOR",
        target_model="RejectionRecord",
        target_object_id=str(rejection_record.id),
        previous_state={
            "returned_to_vendor": prev_returned,
            "rejected_quantity": str(max_returnable),
            "rejection_reason": rejection_record.rejection_reason,
        },
        new_state={
            "returned_to_vendor": True,
            "return_quantity": str(return_quantity),
            "tracking_reference": tracking_reference,
            "return_notes": return_notes,
            "grn_number": receipt_line.receipt.grn_number,
            "po_number": receipt_line.receipt.po.po_number,
            "item_description": receipt_line.po_line.item_description,
        },
    )

    return rejection_record


@transaction.atomic
def correct_receipt_line_quantity_service(
    *,
    receipt_line: ReceiptLine,
    corrected_by: User,
    new_quantity_received: Decimal,
    reason: str,
    new_quantity_accepted: Decimal = None,
    correction_notes: str = "",
) -> ReceiptLine:
    """
    Corrects quantities on a Goods Receipt line without destroying history.
    Enforces:
    - Mandatory reason for audit traceability.
    - Positive quantities.
    - Cannot reduce accepted quantity below already handed-off stock.
    - Recalculates PO line received quantity and PO completion status.
    - Preserves previous state in immutable AuditLog.
    """
    _check_stores_receiver_permission(corrected_by, "correct receipt quantities")

    reason_str = str(reason).strip() if reason else ""
    if not reason_str:
        raise ValidationError("Correction reason is mandatory to preserve audit trail.")

    try:
        new_qty_recv = Decimal(str(new_quantity_received))
    except Exception:
        raise ValidationError("Invalid new quantity received value.")

    if new_qty_recv <= Decimal("0.00"):
        raise ValidationError("Corrected quantity received must be greater than 0.")

    receipt_line = (
        ReceiptLine.objects.select_for_update()
        .select_related("receipt__po", "po_line")
        .get(id=receipt_line.id)
    )
    po_line = POLine.objects.select_for_update().get(id=receipt_line.po_line_id)
    po = PurchaseOrder.objects.select_for_update().get(id=receipt_line.receipt.po_id)

    if new_quantity_accepted is not None:
        try:
            new_qty_acc = Decimal(str(new_quantity_accepted))
        except Exception:
            raise ValidationError("Invalid new quantity accepted value.")
        if new_qty_acc < Decimal("0.00"):
            raise ValidationError("Quantity accepted cannot be negative.")
        if new_qty_acc > new_qty_recv:
            raise ValidationError(
                f"Quantity accepted ({new_qty_acc}) cannot exceed quantity received ({new_qty_recv})."
            )
    else:
        if receipt_line.quantity_rejected == Decimal("0.00"):
            new_qty_acc = new_qty_recv
        else:
            new_qty_rej_proportional = min(receipt_line.quantity_rejected, new_qty_recv)
            new_qty_acc = new_qty_recv - new_qty_rej_proportional

    new_qty_rej = new_qty_recv - new_qty_acc

    # Stock handoff guard: cannot accept less than already handed-off stock
    if hasattr(receipt_line, "stock_handoff"):
        try:
            handoff = receipt_line.stock_handoff
            if handoff and new_qty_acc < handoff.quantity_handed_off:
                raise ValidationError(
                    f"Corrected accepted quantity ({new_qty_acc}) cannot be less than already handed-off warehouse stock ({handoff.quantity_handed_off})."
                )
        except StockHandoffRecord.DoesNotExist:
            pass

    # Check PO limits
    delta_accepted = new_qty_acc - receipt_line.quantity_accepted
    current_po_recv = po_line.quantity_received or Decimal("0.00")
    new_po_recv = max(Decimal("0.00"), current_po_recv + delta_accepted)
    if new_po_recv > po_line.quantity:
        raise ValidationError(
            f"Corrected accepted quantity ({new_qty_acc}) causes total received on PO line ({new_po_recv}) to exceed ordered quantity ({po_line.quantity})."
        )

    # Check cross-GRN total received
    other_lines_recv = ReceiptLine.objects.filter(po_line=po_line).exclude(
        id=receipt_line.id
    ).aggregate(total=models.Sum("quantity_received"))["total"] or Decimal("0.00")
    if other_lines_recv + new_qty_recv > po_line.quantity:
        raise ValidationError(
            f"Corrected received quantity ({new_qty_recv}) causes total shipment quantity across all GRNs ({other_lines_recv + new_qty_recv}) to exceed ordered quantity ({po_line.quantity})."
        )

    previous_state = {
        "receipt_line_id": str(receipt_line.id),
        "grn_number": receipt_line.receipt.grn_number,
        "po_number": po.po_number,
        "item_description": po_line.item_description,
        "quantity_received": str(receipt_line.quantity_received),
        "quantity_accepted": str(receipt_line.quantity_accepted),
        "quantity_rejected": str(receipt_line.quantity_rejected),
        "po_line_quantity_received": str(current_po_recv),
        "po_status": po.status,
    }

    po_line.quantity_received = new_po_recv
    po_line.save(update_fields=["quantity_received", "updated_at"])

    receipt_line.quantity_received = new_qty_recv
    receipt_line.quantity_accepted = new_qty_acc
    receipt_line.quantity_rejected = new_qty_rej
    corr_note = f"[Correction: {reason_str}]"
    receipt_line.notes = f"{receipt_line.notes} {corr_note}".strip()
    receipt_line.save(
        update_fields=[
            "quantity_received",
            "quantity_accepted",
            "quantity_rejected",
            "notes",
            "updated_at",
        ]
    )

    # Sync RejectionRecord
    if new_qty_rej > Decimal("0.00"):
        rej = RejectionRecord.objects.filter(receipt_line=receipt_line).first()
        if rej:
            rej.rejected_quantity = new_qty_rej
            rej.rejection_reason = f"{rej.rejection_reason} [Corrected to {new_qty_rej}]"
            rej.save(update_fields=["rejected_quantity", "rejection_reason", "updated_at"])
        else:
            RejectionRecord.objects.create(
                receipt_line=receipt_line,
                rejected_quantity=new_qty_rej,
                rejection_reason=f"Defect logged during quantity correction: {reason_str}",
            )
    else:
        RejectionRecord.objects.filter(receipt_line=receipt_line).delete()

    _update_po_completion_status(po)
    po.refresh_from_db()

    AuditLog.objects.create(
        actor=corrected_by,
        action=AuditLog.ACTION_UPDATE,
        target_model="ReceiptLine",
        target_object_id=str(receipt_line.id),
        previous_state=previous_state,
        new_state={
            "action": "QUANTITY_CORRECTION",
            "reason": reason_str,
            "quantity_received": str(new_qty_recv),
            "quantity_accepted": str(new_qty_acc),
            "quantity_rejected": str(new_qty_rej),
            "po_line_quantity_received": str(new_po_recv),
            "po_status": po.status,
            "notes": correction_notes,
        },
    )

    return receipt_line

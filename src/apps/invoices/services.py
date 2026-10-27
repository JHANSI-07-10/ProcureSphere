from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.budgets.services import convert_commitment_to_actual_service
from apps.orders.models import PurchaseOrder
from apps.receipts.models import ReceiptLine

from .models import InvoiceLine, MatchException, SupplierInvoice


def _get_user_role_code(user):
    if not user:
        return None
    return getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )


@transaction.atomic
def create_supplier_invoice_service(
    *,
    vendor,
    po: PurchaseOrder,
    invoice_number: str,
    invoice_date,
    due_date,
    line_items: list,
    notes: str = "",
    created_by_user=None,
    tax_rate: Decimal = Decimal("0.10"),
) -> SupplierInvoice:
    """
    Creates a new Supplier Invoice in RECEIVED status with line items.
    Enforces unique invoice_number per vendor (duplicate detection).
    """
    if po.vendor_id != vendor.id:
        raise ValidationError(
            f"Purchase Order {po.po_number} does not belong to vendor '{vendor.legal_name}'."
        )

    if SupplierInvoice.objects.filter(vendor=vendor, invoice_number=invoice_number).exists():
        raise ValidationError(
            f"Duplicate invoice detected: Invoice '{invoice_number}' already exists for vendor '{vendor.legal_name}'."
        )

    subtotal = Decimal("0.00")
    invoice = SupplierInvoice.objects.create(
        vendor=vendor,
        po=po,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        status=SupplierInvoice.STATUS_RECEIVED,
        subtotal=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("0.00"),
        notes=notes,
    )

    for item in line_items:
        po_line = item.get("po_line")
        qty = Decimal(str(item["quantity"]))
        unit_price = Decimal(str(item["unit_price"]))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            po_line=po_line,
            item_description=item.get(
                "item_description", getattr(po_line, "item_description", "Line Item")
            ),
            quantity=qty,
            unit_price=unit_price,
        )
        subtotal += line.line_total

    tax = subtotal * tax_rate
    total = subtotal + tax

    invoice.subtotal = subtotal
    invoice.tax_amount = tax
    invoice.total_amount = total
    invoice.save(update_fields=["subtotal", "tax_amount", "total_amount", "updated_at"])

    AuditLog.objects.create(
        actor=created_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        new_state={
            "invoice_number": invoice.invoice_number,
            "vendor": vendor.legal_name,
            "po_number": po.po_number,
            "total_amount": str(total),
        },
    )

    return invoice


@transaction.atomic
def run_3_way_match_service(
    *,
    invoice: SupplierInvoice,
    price_tolerance_pct: Decimal = None,
    qty_tolerance_pct: Decimal = None,
    user=None,
) -> tuple[SupplierInvoice, list[MatchException]]:
    """
    Executes automated 3-way match: PO line vs Goods Receipt line vs Supplier Invoice line.
    Generates explicit MatchException records if variance exceeds tolerance thresholds.
    """
    from apps.invoices.models import MatchTolerancePolicy

    policy = MatchTolerancePolicy.get_current()
    if price_tolerance_pct is None:
        price_tolerance_pct = policy.price_tolerance_pct
    if qty_tolerance_pct is None:
        qty_tolerance_pct = policy.qty_tolerance_pct

    invoice.status = SupplierInvoice.STATUS_MATCHING
    invoice.save(update_fields=["status", "updated_at"])

    exceptions = []

    for inv_line in invoice.lines.all():
        po_line = inv_line.po_line
        if not po_line:
            exc = MatchException.objects.create(
                invoice=invoice,
                exception_type=MatchException.TYPE_UNMATCHED_LINE,
                description=f"Invoice line '{inv_line.item_description}' has no linked PO line.",
                variance_amount=inv_line.line_total,
            )
            exceptions.append(exc)
            continue

        # 1. Quantity Variance check (Billed Qty vs Goods Receipt Accepted Qty)
        accepted_qty_agg = ReceiptLine.objects.filter(po_line=po_line).aggregate(
            total_accepted=models.Sum("quantity_accepted")
        )["total_accepted"] or Decimal("0.00")

        max_allowed_qty = accepted_qty_agg * (Decimal("1.00") + qty_tolerance_pct)
        if inv_line.quantity > max_allowed_qty:
            qty_diff = inv_line.quantity - accepted_qty_agg
            variance = qty_diff * inv_line.unit_price
            exc = MatchException.objects.create(
                invoice=invoice,
                exception_type=MatchException.TYPE_QUANTITY_VARIANCE,
                description=(
                    f"Billed qty {inv_line.quantity} exceeds accepted GRN qty {accepted_qty_agg} "
                    f"for item '{po_line.item_description}' (Tolerance: {qty_tolerance_pct * 100}%)."
                ),
                variance_amount=variance,
            )
            exceptions.append(exc)

        # 2. Price Variance check (Billed Unit Price vs PO Line Unit Price)
        max_allowed_price = po_line.unit_price * (Decimal("1.00") + price_tolerance_pct)
        if inv_line.unit_price > max_allowed_price:
            unit_diff = inv_line.unit_price - po_line.unit_price
            variance = unit_diff * inv_line.quantity
            exc = MatchException.objects.create(
                invoice=invoice,
                exception_type=MatchException.TYPE_PRICE_VARIANCE,
                description=(
                    f"Billed unit price ${inv_line.unit_price} exceeds PO price ${po_line.unit_price} "
                    f"for item '{po_line.item_description}' (Tolerance: {price_tolerance_pct * 100}%)."
                ),
                variance_amount=variance,
            )
            exceptions.append(exc)

    if exceptions:
        invoice.status = SupplierInvoice.STATUS_EXCEPTION
    else:
        invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT

    invoice.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        new_state={
            "status": invoice.status,
            "exception_count": len(exceptions),
        },
    )

    return invoice, exceptions


@transaction.atomic
def resolve_match_exception_service(
    *,
    match_exception: MatchException,
    resolved_by_user,
    resolution_notes: str,
    action: str = "RESOLVE",
) -> MatchException:
    """
    Finance exception resolution workflow for 3-way match variances.
    """
    role_code = _get_user_role_code(resolved_by_user)
    if not (
        getattr(resolved_by_user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can resolve invoice match exceptions."
        )

    if match_exception.status != MatchException.STATUS_OPEN:
        raise ValidationError(f"Match exception is already in status '{match_exception.status}'.")

    if action == "RESOLVE":
        match_exception.status = MatchException.STATUS_RESOLVED
        match_exception.resolved_by = resolved_by_user
        match_exception.resolution_notes = resolution_notes
        match_exception.save(
            update_fields=["status", "resolved_by", "resolution_notes", "updated_at"]
        )

        # If all exceptions on invoice are resolved, promote invoice to READY_FOR_PAYMENT
        invoice = match_exception.invoice
        remaining_open = invoice.exceptions.filter(status=MatchException.STATUS_OPEN).count()
        if remaining_open == 0:
            invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
            invoice.save(update_fields=["status", "updated_at"])

    elif action == "REJECT":
        match_exception.status = MatchException.STATUS_REJECTED
        match_exception.resolved_by = resolved_by_user
        match_exception.resolution_notes = resolution_notes
        match_exception.save(
            update_fields=["status", "resolved_by", "resolution_notes", "updated_at"]
        )

        invoice = match_exception.invoice
        invoice.status = SupplierInvoice.STATUS_REJECTED
        invoice.save(update_fields=["status", "updated_at"])
    else:
        raise ValidationError(
            f"Invalid exception resolution action '{action}'. Must be RESOLVE or REJECT."
        )

    AuditLog.objects.create(
        actor=resolved_by_user,
        action=AuditLog.ACTION_APPROVE if action == "RESOLVE" else AuditLog.ACTION_REJECT,
        target_model="MatchException",
        target_object_id=str(match_exception.id),
        new_state={
            "status": match_exception.status,
            "resolution_notes": resolution_notes,
            "invoice_status": match_exception.invoice.status,
        },
    )

    return match_exception


@transaction.atomic
def mark_invoice_paid_service(*, invoice: SupplierInvoice, user) -> SupplierInvoice:
    """
    Marks invoice as PAID and transitions SpendLedger from COMMITMENT to ACTUAL.
    """
    role_code = _get_user_role_code(user)
    if not (
        getattr(user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can execute invoice payments."
        )

    if invoice.status not in [
        SupplierInvoice.STATUS_READY_FOR_PAYMENT,
        SupplierInvoice.STATUS_APPROVAL,
    ]:
        raise ValidationError(
            f"Invoice '{invoice.invoice_number}' in status '{invoice.status}' is not ready for payment."
        )

    previous_status = invoice.status
    invoice.status = SupplierInvoice.STATUS_PAID
    invoice.save(update_fields=["status", "updated_at"])

    # Convert spend ledger commitment -> actual
    convert_commitment_to_actual_service(po=invoice.po, invoice=invoice, user=user)

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        previous_state={"status": previous_status},
        new_state={"status": invoice.status},
    )

    return invoice

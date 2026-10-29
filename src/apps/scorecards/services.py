from decimal import Decimal

from django.db import models, transaction
from django.db.models import Min, Sum
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log_service
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.vendors.models import Vendor

from .models import VendorScorecard

HUNDRED = Decimal("100.00")

# Existing approved compliance mapping from vendor governance status.
COMPLIANCE_BY_STATUS = {
    Vendor.STATUS_ACTIVE: Decimal("100.00"),
    Vendor.STATUS_ON_HOLD: Decimal("60.00"),
    Vendor.STATUS_SUSPENDED: Decimal("0.00"),
}
COMPLIANCE_DEFAULT = Decimal("80.00")


def _pct(numerator, denominator):
    if not denominator:
        return None
    return round((Decimal(numerator) / Decimal(denominator)) * HUNDRED, 2)


def compute_quality_score(vendor):
    """Accepted quantity / received quantity across all GRN lines for the vendor's POs."""
    totals = ReceiptLine.objects.filter(receipt__po__vendor=vendor).aggregate(
        received=Sum("quantity_received"), accepted=Sum("quantity_accepted")
    )
    return _pct(totals["accepted"] or 0, totals["received"] or 0)


def compute_delivery_score(vendor):
    """
    Share of GRNs received on/before the expected date. Expected date = earliest PO delivery
    schedule date, falling back to the requisition's requested delivery date. GRNs with no
    reference date are excluded rather than assumed on time.
    """
    receipts = GoodsReceipt.objects.filter(po__vendor=vendor).select_related("po__requisition")
    receipts = receipts.annotate(expected=Min("po__delivery_schedules__expected_delivery_date"))
    evaluated = on_time = 0
    for receipt in receipts:
        expected = receipt.expected or (
            receipt.po.requisition.requested_delivery_date if receipt.po.requisition_id else None
        )
        if expected is None:
            continue
        evaluated += 1
        if timezone.localdate(receipt.received_date) <= expected:
            on_time += 1
    return _pct(on_time, evaluated)


def compute_price_score(vendor):
    """
    Price adherence: invoiced value vs PO value per PO (non-rejected invoices). Billing at or
    below PO value scores 100; over-billing reduces the score by the over-billed percentage.
    """
    from apps.invoices.models import SupplierInvoice
    from apps.orders.models import PurchaseOrder

    invoiced = (
        SupplierInvoice.objects.filter(vendor=vendor, po__isnull=False)
        .exclude(status=SupplierInvoice.STATUS_REJECTED)
        .values("po_id")
        .annotate(total=Sum("total_amount"))
    )
    po_totals = dict(
        PurchaseOrder.objects.filter(id__in=[row["po_id"] for row in invoiced]).values_list(
            "id", "total_amount"
        )
    )
    scores = []
    for row in invoiced:
        po_total = po_totals.get(row["po_id"])
        if not po_total:
            continue
        over = max(Decimal("0"), row["total"] - po_total) / po_total * HUNDRED
        scores.append(max(Decimal("0"), HUNDRED - over))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def compute_responsiveness_score(vendor):
    """
    Average of (a) RFQ/RFP invitation response rate for events whose bid window has closed and
    (b) PO acknowledgement rate for POs issued to the vendor.
    """
    from apps.orders.models import PurchaseOrder
    from apps.sourcing.models import BidInvite, SourcingEvent

    closed_invites = BidInvite.objects.filter(vendor=vendor).exclude(
        event__status__in=[
            SourcingEvent.STATUS_DRAFT,
            SourcingEvent.STATUS_PUBLISHED,
            SourcingEvent.STATUS_BID_WINDOW,
        ]
    )
    components = []
    invite_rate = _pct(closed_invites.filter(is_responded=True).count(), closed_invites.count())
    if invite_rate is not None:
        components.append(invite_rate)

    issued_pos = PurchaseOrder.objects.filter(vendor=vendor).exclude(
        status__in=[PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_APPROVAL]
    )
    ack_rate = _pct(issued_pos.filter(acknowledged_at__isnull=False).count(), issued_pos.count())
    if ack_rate is not None:
        components.append(ack_rate)

    if not components:
        return None
    return round(sum(components) / len(components), 2)


def compute_sla_score(vendor):
    """Vendor-owned contract obligations that are due: fulfilled on/before the due date."""
    from apps.contracts.models import ContractObligation

    due = ContractObligation.objects.filter(
        contract__vendor=vendor,
        responsible_party__iexact="VENDOR",
        due_date__lte=timezone.localdate(),
    )
    total = due.count()
    met = sum(
        1
        for ob in due.only("due_date", "is_fulfilled", "fulfilled_at")
        if ob.is_fulfilled
        and (ob.fulfilled_at is None or timezone.localdate(ob.fulfilled_at) <= ob.due_date)
    )
    return _pct(met, total)


def compute_compliance_score(vendor):
    return COMPLIANCE_BY_STATUS.get(vendor.status, COMPLIANCE_DEFAULT)


@transaction.atomic
def calculate_vendor_scorecard_service(
    *,
    vendor: Vendor,
    evaluation_period: str,
    evaluated_by_user,
    comments: str = "",
) -> VendorScorecard:
    """
    Computes a supplier performance scorecard from transactional indicators (PRD 3.3:
    delivery, quality, price, responsiveness, compliance and SLA). An indicator with no
    underlying data is stored as NULL — never a fabricated score. Each run is a new record,
    so trend history is preserved.
    """
    evaluation_period = (evaluation_period or "").strip()
    if not evaluation_period:
        from django.core.exceptions import ValidationError

        raise ValidationError("Evaluation period is required.")

    scorecard = VendorScorecard.objects.create(
        vendor=vendor,
        evaluation_period=evaluation_period,
        delivery_score=compute_delivery_score(vendor),
        quality_score=compute_quality_score(vendor),
        price_score=compute_price_score(vendor),
        responsiveness_score=compute_responsiveness_score(vendor),
        compliance_score=compute_compliance_score(vendor),
        sla_score=compute_sla_score(vendor),
        evaluator_comments=comments
        or f"Automated performance evaluation for period {evaluation_period}",
        evaluated_by=evaluated_by_user,
        created_by=evaluated_by_user,
    )

    def _s(value):
        return None if value is None else str(value)

    create_audit_log_service(
        actor=evaluated_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="VendorScorecard",
        target_object_id=scorecard.id,
        new_state={
            "vendor": vendor.legal_name,
            "period": scorecard.evaluation_period,
            "composite_score": _s(scorecard.composite_score),
            "delivery_score": _s(scorecard.delivery_score),
            "quality_score": _s(scorecard.quality_score),
            "price_score": _s(scorecard.price_score),
            "responsiveness_score": _s(scorecard.responsiveness_score),
            "compliance_score": _s(scorecard.compliance_score),
            "sla_score": _s(scorecard.sla_score),
        },
    )

    return scorecard


def latest_scorecards_queryset():
    """One row per vendor: the most recent scorecard (PostgreSQL DISTINCT ON or DB fallback)."""
    from django.db import connection

    if connection.vendor == "postgresql":
        ids = (
            VendorScorecard.objects.order_by("vendor_id", "-created_at")
            .distinct("vendor_id")
            .values("id")
        )
    else:
        ids = (
            VendorScorecard.objects.values("vendor_id")
            .annotate(latest_id=models.Max("id"))
            .values("latest_id")
        )
    return VendorScorecard.objects.filter(id__in=models.Subquery(ids)).select_related(
        "vendor", "vendor__category"
    )

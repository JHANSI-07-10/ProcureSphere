from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import BidAttachment, BidEvaluation, BidInvite, SourcingEvent, VendorBid
from .permissions import can_read_bids, can_view_events

OPEN_EVENT_STATUSES = [
    SourcingEvent.STATUS_PUBLISHED,
    SourcingEvent.STATUS_BID_WINDOW,
    SourcingEvent.STATUS_TECHNICAL_REVIEW,
    SourcingEvent.STATUS_COMMERCIAL_REVIEW,
    SourcingEvent.STATUS_AWARD_APPROVAL,
]


def get_all_sourcing_events():
    return (
        SourcingEvent.objects.select_related("requisition")
        .annotate(
            invite_count=Count("invitations", distinct=True),
            submitted_bid_count=Count(
                "bids",
                filter=Q(bids__status__in=[VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED]),
                distinct=True,
            ),
        )
        .order_by("-created_at")
    )


def get_sourcing_events_for_user(user):
    """
    Object-level scoping: internal readers see all events; vendor users see only events they
    are invited to and that have been published (drafts are never exposed to vendors).
    """
    qs = get_all_sourcing_events()
    if user.is_vendor:
        if not user.vendor_id:
            return qs.none()
        return qs.filter(invitations__vendor_id=user.vendor_id).exclude(
            status=SourcingEvent.STATUS_DRAFT
        )
    if can_view_events(user):
        if not user.is_superuser:
            user_dept = getattr(user, "department", None)
            user_org = getattr(user_dept, "organization", None) if user_dept else None
            if user_org:
                qs = qs.filter(
                    Q(requisition__department__organization=user_org)
                    | Q(requisition__isnull=True, created_by__department__organization=user_org)
                )
        return qs
    return qs.none()


def get_sourcing_event_by_id(event_id):
    return (
        SourcingEvent.objects.select_related("requisition")
        .prefetch_related("invitations", "bids")
        .filter(id=event_id)
        .first()
    )


def search_sourcing_events(queryset, params):
    """Server-side search / filter for the RFQ/RFP register and API."""
    q = (params.get("q") or params.get("search") or "").strip()
    if q:
        queryset = queryset.filter(
            Q(event_number__icontains=q)
            | Q(title__icontains=q)
            | Q(invitations__vendor__legal_name__icontains=q)
            | Q(invitations__vendor__vendor_number__icontains=q)
        ).distinct()
    status = params.get("status")
    if status == "OPEN":
        queryset = queryset.filter(status__in=OPEN_EVENT_STATUSES)
    elif status == "CLOSING_SOON":
        now = timezone.now()
        queryset = queryset.filter(
            status=SourcingEvent.STATUS_BID_WINDOW,
            bid_end_date__gt=now,
            bid_end_date__lte=now + timedelta(days=3),
        )
    elif status in dict(SourcingEvent.STATUS_CHOICES):
        queryset = queryset.filter(status=status)
    event_type = params.get("event_type")
    if event_type in dict(SourcingEvent.EVENT_TYPE_CHOICES):
        queryset = queryset.filter(event_type=event_type)
    date_from = params.get("deadline_from")
    if date_from:
        queryset = queryset.filter(bid_end_date__date__gte=date_from)
    date_to = params.get("deadline_to")
    if date_to:
        queryset = queryset.filter(bid_end_date__date__lte=date_to)
    category = params.get("category")
    if category:
        queryset = queryset.filter(invitations__vendor__category_id=category).distinct()
    return queryset


def get_sourcing_dashboard_metrics() -> dict:
    """RFQ/RFP KPIs for the Procurement Executive dashboard (one aggregate query)."""
    now = timezone.now()
    return SourcingEvent.objects.aggregate(
        total=Count("id"),
        draft=Count("id", filter=Q(status=SourcingEvent.STATUS_DRAFT)),
        published=Count("id", filter=Q(status=SourcingEvent.STATUS_PUBLISHED)),
        bid_window=Count("id", filter=Q(status=SourcingEvent.STATUS_BID_WINDOW)),
        closing_soon=Count(
            "id",
            filter=Q(
                status=SourcingEvent.STATUS_BID_WINDOW,
                bid_end_date__gt=now,
                bid_end_date__lte=now + timedelta(days=3),
            ),
        ),
        awaiting_evaluation=Count(
            "id",
            filter=Q(
                status__in=[
                    SourcingEvent.STATUS_TECHNICAL_REVIEW,
                    SourcingEvent.STATUS_COMMERCIAL_REVIEW,
                ]
            ),
        ),
        awaiting_award=Count("id", filter=Q(status=SourcingEvent.STATUS_AWARD_APPROVAL)),
        awarded=Count("id", filter=Q(status=SourcingEvent.STATUS_AWARDED)),
        cancelled=Count("id", filter=Q(status=SourcingEvent.STATUS_CANCELLED)),
        active=Count("id", filter=Q(status__in=OPEN_EVENT_STATUSES)),
    )


def get_sealed_vendor_bids(event: SourcingEvent, requesting_user):
    """
    Selector that enforces sealed bid privacy controls:
    1. Vendor users see ONLY their own bids.
    2. Internal evaluators see NO bids while the event is DRAFT/PUBLISHED/BID_WINDOW/CANCELLED.
    3. After closure only roles with bid-read rights (docs/rbac_matrix.md) see submitted bids;
       drafts are never visible to evaluators.
    """
    queryset = (
        VendorBid.objects.select_related("vendor", "event")
        .prefetch_related("lines")
        .filter(event=event)
    )

    if requesting_user.is_vendor:
        return queryset.filter(vendor_id=requesting_user.vendor_id)

    if not can_read_bids(requesting_user) or not event.bids_visible_to_evaluators:
        return VendorBid.objects.none()

    return queryset.exclude(status=VendorBid.STATUS_DRAFT).order_by("total_bid_amount")


def get_bid_attachments_for_evaluator(event: SourcingEvent, user):
    """Technical/compliance attachments after closure; commercial only from COMMERCIAL_REVIEW."""
    if user.is_vendor or not can_read_bids(user) or not event.bids_visible_to_evaluators:
        return BidAttachment.objects.none()
    qs = BidAttachment.objects.filter(bid__event=event).exclude(bid__status=VendorBid.STATUS_DRAFT)
    if not event.commercial_visible_to_evaluators:
        qs = qs.exclude(document_type=BidAttachment.DOC_TYPE_COMMERCIAL)
    return qs.select_related("bid", "bid__vendor")


def build_evaluation_rows(event: SourcingEvent, user):
    """
    Bid comparison rows for the evaluator UI. Commercial data (amount, lines, commercial
    proposal, commercial score) is only included once the event reaches COMMERCIAL_REVIEW.
    """
    bids = list(get_sealed_vendor_bids(event, user))
    show_commercial = event.commercial_visible_to_evaluators
    evaluations = {}
    for ev in BidEvaluation.objects.filter(event=event).select_related("evaluator"):
        evaluations.setdefault(ev.bid_id, []).append(ev)
    attachments = {}
    for att in get_bid_attachments_for_evaluator(event, user):
        attachments.setdefault(att.bid_id, []).append(att)

    rows = []
    for bid in bids:
        evs = evaluations.get(bid.id, [])
        tech_scores = [e.technical_score for e in evs if e.technical_evaluated_at]
        comm_scores = [e.commercial_score for e in evs if e.commercial_evaluated_at]
        weighted = [e.weighted_total_score for e in evs if e.commercial_evaluated_at]
        mine = next((e for e in evs if e.evaluator_id == user.id), None)
        rows.append(
            {
                "bid": bid,
                "vendor": bid.vendor,
                "technical_avg": (sum(tech_scores) / len(tech_scores)) if tech_scores else None,
                "commercial_avg": (
                    (sum(comm_scores) / len(comm_scores))
                    if comm_scores and show_commercial
                    else None
                ),
                "weighted_avg": (
                    (sum(weighted) / len(weighted)) if weighted and show_commercial else None
                ),
                "evaluation_count": len(evs),
                "my_evaluation": mine,
                "amount": bid.total_bid_amount if show_commercial else None,
                "lines": list(bid.lines.all()) if show_commercial else [],
                "commercial_proposal": bid.commercial_proposal if show_commercial else None,
                "attachments": attachments.get(bid.id, []),
            }
        )
    if show_commercial:
        rows.sort(key=lambda r: (r["weighted_avg"] is None, -(r["weighted_avg"] or 0)))
    return rows


def get_invitation_rows(event: SourcingEvent):
    """Invitation status per vendor (internal view; never shows bid content)."""
    bid_status = dict(event.bids.values_list("vendor_id", "status"))
    rows = []
    for invite in event.invitations.select_related("vendor", "vendor__category").order_by(
        "vendor__legal_name"
    ):
        rows.append(
            {
                "invite": invite,
                "vendor": invite.vendor,
                "bid_status": bid_status.get(invite.vendor_id),
            }
        )
    return rows


def get_eligible_vendors_for_event(event: SourcingEvent, category_id=None, q=""):
    from apps.vendors.models import Vendor

    from .services import ELIGIBLE_VENDOR_STATUSES

    invited = BidInvite.objects.filter(event=event).values("vendor_id")
    qs = (
        Vendor.objects.filter(status__in=ELIGIBLE_VENDOR_STATUSES)
        .exclude(id__in=invited)
        .select_related("category")
        .order_by("legal_name")
    )
    if category_id:
        qs = qs.filter(category_id=category_id)
    if q:
        qs = qs.filter(
            Q(legal_name__icontains=q) | Q(vendor_number__icontains=q) | Q(trade_name__icontains=q)
        )
    return qs


PENDING_ONBOARDING_STATUSES = ["DRAFT", "SUBMITTED", "KYC_REVIEW"]


def get_pending_onboarding_vendors(event: SourcingEvent, category_id=None, q=""):
    """Newly registered vendors that cannot be invited yet (KYC not approved). Display only."""
    from apps.vendors.models import Vendor

    invited = BidInvite.objects.filter(event=event).values("vendor_id")
    qs = (
        Vendor.objects.filter(status__in=PENDING_ONBOARDING_STATUSES)
        .exclude(id__in=invited)
        .select_related("category")
        .order_by("legal_name")
    )
    if category_id:
        qs = qs.filter(category_id=category_id)
    if q:
        qs = qs.filter(
            Q(legal_name__icontains=q) | Q(vendor_number__icontains=q) | Q(trade_name__icontains=q)
        )
    return qs

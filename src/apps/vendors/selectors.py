from datetime import timedelta

from django.utils import timezone

from .models import Vendor, VendorDocument


def get_all_vendors():
    return Vendor.objects.select_related("category").order_by("-created_at")


def get_all_active_vendors():
    return (
        Vendor.objects.select_related("category")
        .filter(status=Vendor.STATUS_ACTIVE)
        .order_by("legal_name")
    )


def get_vendor_by_id(vendor_id):
    return (
        Vendor.objects.select_related("category")
        .prefetch_related("documents", "contacts", "risk_records")
        .filter(id=vendor_id)
        .first()
    )


def get_vendors_by_status(status_code):
    return (
        Vendor.objects.select_related("category").filter(status=status_code).order_by("-created_at")
    )


def get_vendor_documents(vendor_id):
    return (
        VendorDocument.objects.select_related("verified_by")
        .filter(vendor_id=vendor_id)
        .order_by("-created_at")
    )


def get_vendor_dashboard_metrics(vendor, user=None):
    """
    Computes real backend metrics for the Vendor Dashboard.
    Zero hardcoded values.
    """
    from apps.notifications.models import Notification
    from apps.orders.models import PurchaseOrder
    from apps.sourcing.models import BidInvite, Clarification, SourcingEvent, VendorBid

    if not vendor:
        return {
            "active_rfq_invitations": 0,
            "bids_in_draft": 0,
            "submitted_bids": 0,
            "upcoming_deadlines": 0,
            "pending_clarifications": 0,
            "pos_requiring_ack": 0,
            "active_pos": 0,
            "kyc_compliance_status": "DRAFT",
            "unread_notifications": 0,
        }

    now = timezone.now()

    # Active RFQ/RFP Invitations: Invited and currently in BID_WINDOW
    active_invitations_count = BidInvite.objects.filter(
        vendor=vendor,
        event__status=SourcingEvent.STATUS_BID_WINDOW,
    ).count()

    # Bids in Draft
    bids_in_draft_count = VendorBid.objects.filter(
        vendor=vendor,
        status=VendorBid.STATUS_DRAFT,
    ).count()

    # Submitted Bids (Submitted or Amended)
    submitted_bids_count = VendorBid.objects.filter(
        vendor=vendor,
        status__in=[VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED],
    ).count()

    # Upcoming Deadlines: Open events closing in next 7 days
    upcoming_deadlines_count = BidInvite.objects.filter(
        vendor=vendor,
        event__status=SourcingEvent.STATUS_BID_WINDOW,
        event__bid_end_date__gte=now,
        event__bid_end_date__lte=now + timedelta(days=7),
    ).count()

    # Pending Clarifications
    pending_clarifications_count = Clarification.objects.filter(
        vendor=vendor,
        status="PENDING",
    ).count()

    # POs Requiring Acknowledgement (ISSUED status)
    pos_requiring_ack_count = PurchaseOrder.objects.filter(
        vendor=vendor,
        status=PurchaseOrder.STATUS_ISSUED,
    ).count()

    # Active POs (ISSUED, ACKNOWLEDGED, PARTIAL_RECEIPT)
    active_pos_count = PurchaseOrder.objects.filter(
        vendor=vendor,
        status__in=[
            PurchaseOrder.STATUS_ISSUED,
            PurchaseOrder.STATUS_ACKNOWLEDGED,
            PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        ],
    ).count()

    # Notifications
    unread_notifications = 0
    if user and user.is_authenticated:
        unread_notifications = Notification.objects.filter(
            recipient=user,
            is_read=False,
        ).count()

    return {
        "active_rfq_invitations": active_invitations_count,
        "bids_in_draft": bids_in_draft_count,
        "submitted_bids": submitted_bids_count,
        "upcoming_deadlines": upcoming_deadlines_count,
        "pending_clarifications": pending_clarifications_count,
        "pos_requiring_ack": pos_requiring_ack_count,
        "active_pos": active_pos_count,
        "kyc_compliance_status": vendor.status,
        "unread_notifications": unread_notifications,
    }


# ==============================================================================
# VENDOR GOVERNANCE (internal procurement view)
# ==============================================================================


def get_governance_vendor_queryset():
    from .filters import annotate_governance

    return annotate_governance(Vendor.objects.select_related("category")).order_by("-updated_at")


def get_governance_metrics() -> dict:
    """Database-backed governance KPIs (single aggregate query + two subquery counts)."""
    from django.db.models import Count, Q

    from .filters import PERFORMANCE_ISSUE_THRESHOLD, annotate_governance
    from .models import VendorRiskRecord

    counts = Vendor.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=Vendor.STATUS_ACTIVE)),
        pending_review=Count(
            "id",
            filter=Q(
                status__in=[
                    Vendor.STATUS_SUBMITTED,
                    Vendor.STATUS_KYC_REVIEW,
                    Vendor.STATUS_APPROVED,
                ]
            ),
        ),
        on_hold=Count("id", filter=Q(status=Vendor.STATUS_ON_HOLD)),
        suspended=Count("id", filter=Q(status=Vendor.STATUS_SUSPENDED)),
    )
    annotated = annotate_governance(Vendor.objects.all())
    high_risk = annotated.filter(latest_risk_level=VendorRiskRecord.RISK_LEVEL_HIGH)
    perf_issues = annotated.filter(latest_composite_score__lt=PERFORMANCE_ISSUE_THRESHOLD)
    today = timezone.now().date()
    expired_docs = Vendor.objects.filter(
        status=Vendor.STATUS_ACTIVE, documents__expiry_date__lt=today
    )
    attention_ids = (
        set(high_risk.values_list("id", flat=True))
        | set(perf_issues.values_list("id", flat=True))
        | set(expired_docs.values_list("id", flat=True))
        | set(Vendor.objects.filter(status=Vendor.STATUS_ON_HOLD).values_list("id", flat=True))
    )
    counts.update(
        {
            "high_risk": high_risk.count(),
            "performance_issues": perf_issues.count(),
            "requiring_attention": len(attention_ids),
        }
    )
    return counts


def get_vendor_open_transactions(vendor) -> dict:
    """
    Open sourcing / bid / PO exposure for a vendor; shown before hold/suspension
    (PRD 4.2 edge case: suspension while open sourcing or PO transactions exist).
    """
    from apps.orders.models import PurchaseOrder
    from apps.sourcing.models import BidInvite, SourcingEvent, VendorBid

    open_event_statuses = [
        SourcingEvent.STATUS_DRAFT,
        SourcingEvent.STATUS_PUBLISHED,
        SourcingEvent.STATUS_BID_WINDOW,
        SourcingEvent.STATUS_TECHNICAL_REVIEW,
        SourcingEvent.STATUS_COMMERCIAL_REVIEW,
        SourcingEvent.STATUS_AWARD_APPROVAL,
    ]
    invites = BidInvite.objects.filter(
        vendor=vendor, event__status__in=open_event_statuses
    ).select_related("event")
    bids = VendorBid.objects.filter(
        vendor=vendor,
        event__status__in=open_event_statuses,
        status__in=[VendorBid.STATUS_DRAFT, VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED],
    ).select_related("event")
    pending_awards = SourcingEvent.objects.filter(
        status=SourcingEvent.STATUS_AWARD_APPROVAL,
        award_decisions__status="PENDING",
        award_decisions__winning_bid__vendor=vendor,
    )
    open_pos = PurchaseOrder.objects.filter(
        vendor=vendor,
        status__in=[
            PurchaseOrder.STATUS_DRAFT,
            PurchaseOrder.STATUS_APPROVAL,
            PurchaseOrder.STATUS_ISSUED,
            PurchaseOrder.STATUS_ACKNOWLEDGED,
            PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        ],
    )
    return {
        "open_invitations": list(invites),
        "open_bids": list(bids),
        "pending_awards": list(pending_awards),
        "open_pos": list(open_pos),
        "summary": {
            "open_invitations": invites.count(),
            "open_bids": bids.count(),
            "pending_awards": pending_awards.count(),
            "open_pos": open_pos.count(),
        },
    }


def get_vendor_change_history(vendor, limit=100):
    """AuditLog entries for the vendor and its documents / risk records / scorecards."""
    from django.db.models import Q

    from apps.audit.models import AuditLog

    doc_ids = [str(i) for i in vendor.documents.values_list("id", flat=True)]
    risk_ids = [str(i) for i in vendor.risk_records.values_list("id", flat=True)]
    score_ids = [str(i) for i in vendor.scorecards.values_list("id", flat=True)]
    return (
        AuditLog.objects.filter(
            Q(target_model="Vendor", target_object_id=str(vendor.id))
            | Q(target_model="VendorDocument", target_object_id__in=doc_ids)
            | Q(target_model="VendorRiskRecord", target_object_id__in=risk_ids)
            | Q(target_model="VendorScorecard", target_object_id__in=score_ids)
        )
        .select_related("actor", "actor__role")
        .order_by("-timestamp")[:limit]
    )


def get_vendor_sourcing_history(vendor, limit=20):
    from apps.sourcing.models import BidInvite

    return (
        BidInvite.objects.filter(vendor=vendor)
        .select_related("event")
        .order_by("-created_at")[:limit]
    )


def get_vendor_purchase_orders(vendor, limit=20):
    from apps.orders.models import PurchaseOrder

    return PurchaseOrder.objects.filter(vendor=vendor).order_by("-created_at")[:limit]


def get_vendor_onboarding_progress(vendor):
    """
    Computes step-by-step onboarding progress for restricted self-service vendor onboarding.
    Returns checklist items, completed count, total count, progress percentage, and current stage.
    """
    if not vendor:
        return {
            "steps": [],
            "completed_count": 0,
            "total_count": 5,
            "percentage": 0,
            "is_complete": False,
            "status": "DRAFT",
            "can_submit": False,
        }

    # Step 1: Corporate Profile
    step1_done = bool(vendor.legal_name and vendor.address and vendor.phone)
    step1 = {
        "id": "profile",
        "step_number": 1,
        "title": "Corporate Profile",
        "description": "Company registration, legal address, and verified phone contact (+91).",
        "is_complete": step1_done,
        "url": "/vendor/profile/#company-info",
        "icon": "fa-building",
    }

    # Step 2: Banking & Remittance Settlement
    step2_done = bool(vendor.bank_name and vendor.bank_account_number and vendor.bank_routing_code)
    step2 = {
        "id": "banking",
        "step_number": 2,
        "title": "Bank Settlement Details",
        "description": "Remittance bank name, account number/IBAN, and routing code/IFSC.",
        "is_complete": step2_done,
        "url": "/vendor/profile/#bank-info",
        "icon": "fa-landmark",
    }

    # Step 3: Authorized Contacts
    contacts_count = vendor.contacts.count()
    step3_done = contacts_count > 0
    step3 = {
        "id": "contacts",
        "step_number": 3,
        "title": "Authorized Contacts",
        "description": f"{contacts_count} contact(s) registered. Primary and secondary contact management.",
        "is_complete": step3_done,
        "url": "/vendor/profile/#contacts",
        "icon": "fa-address-book",
    }

    # Step 4: Mandatory KYC Documents
    docs_count = vendor.documents.count()
    verified_docs_count = vendor.documents.filter(is_verified=True).count()
    step4_done = docs_count > 0
    step4 = {
        "id": "documents",
        "step_number": 4,
        "title": "KYC Verification Documents",
        "description": f"{docs_count} document(s) uploaded ({verified_docs_count} verified).",
        "is_complete": step4_done,
        "url": "/vendor/profile/#kyc-docs",
        "icon": "fa-folder-open",
    }

    # Step 5: Procurement Review & Activation
    is_active = vendor.status == Vendor.STATUS_ACTIVE
    step5_done = is_active
    step5_label = (
        "Active & Approved"
        if is_active
        else (
            "Under Review"
            if vendor.status in (Vendor.STATUS_SUBMITTED, Vendor.STATUS_KYC_REVIEW)
            else (
                "Changes Required"
                if vendor.status == Vendor.STATUS_CHANGES_REQUIRED
                else ("Rejected" if vendor.status == Vendor.STATUS_REJECTED else "Pending Submission")
            )
        )
    )
    step5 = {
        "id": "review",
        "step_number": 5,
        "title": "Procurement Review & Activation",
        "description": f"Status: {step5_label}.",
        "is_complete": step5_done,
        "in_progress": vendor.status in (Vendor.STATUS_SUBMITTED, Vendor.STATUS_KYC_REVIEW),
        "changes_required": vendor.status == Vendor.STATUS_CHANGES_REQUIRED,
        "url": "/vendor/profile/",
        "icon": "fa-shield-halved",
    }

    steps = [step1, step2, step3, step4, step5]
    completed_count = sum(1 for s in steps if s["is_complete"])
    percentage = int((completed_count / len(steps)) * 100)
    can_submit = (
        vendor.status in (Vendor.STATUS_DRAFT, Vendor.STATUS_CHANGES_REQUIRED)
        and step1_done
        and step4_done
    )

    return {
        "steps": steps,
        "completed_count": completed_count,
        "total_count": len(steps),
        "percentage": percentage,
        "is_complete": is_active,
        "status": vendor.status,
        "status_display": vendor.get_status_display(),
        "status_notes": vendor.status_notes,
        "changes_required": vendor.status == Vendor.STATUS_CHANGES_REQUIRED,
        "can_submit": can_submit,
    }


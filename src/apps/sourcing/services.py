from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log_service
from apps.core.validators import validate_file_upload
from apps.notifications.models import Notification
from apps.notifications.services import notify_role_users, notify_vendor_users
from apps.vendors.models import Vendor

from .models import (
    AwardDecision,
    BidAttachment,
    BidEvaluation,
    BidInvite,
    BidLine,
    BidVersion,
    Clarification,
    NegotiationNote,
    SourcingEvent,
    VendorBid,
)

# ==============================================================================
# LIFECYCLE (PRD 4: DRAFT -> PUBLISHED -> BID WINDOW -> TECHNICAL REVIEW ->
#                   COMMERCIAL REVIEW -> AWARD APPROVAL -> AWARDED / CANCELLED)
# ==============================================================================

SOURCING_TRANSITIONS = {
    SourcingEvent.STATUS_DRAFT: {SourcingEvent.STATUS_PUBLISHED, SourcingEvent.STATUS_CANCELLED},
    SourcingEvent.STATUS_PUBLISHED: {
        SourcingEvent.STATUS_BID_WINDOW,
        SourcingEvent.STATUS_CANCELLED,
    },
    SourcingEvent.STATUS_BID_WINDOW: {
        SourcingEvent.STATUS_TECHNICAL_REVIEW,
        SourcingEvent.STATUS_CANCELLED,
    },
    SourcingEvent.STATUS_TECHNICAL_REVIEW: {
        SourcingEvent.STATUS_COMMERCIAL_REVIEW,
        SourcingEvent.STATUS_CANCELLED,
    },
    SourcingEvent.STATUS_COMMERCIAL_REVIEW: {
        SourcingEvent.STATUS_AWARD_APPROVAL,
        SourcingEvent.STATUS_CANCELLED,
    },
    SourcingEvent.STATUS_AWARD_APPROVAL: {
        SourcingEvent.STATUS_AWARDED,
        SourcingEvent.STATUS_COMMERCIAL_REVIEW,  # award recommendation rejected
        SourcingEvent.STATUS_CANCELLED,
    },
    SourcingEvent.STATUS_AWARDED: set(),
    SourcingEvent.STATUS_CANCELLED: set(),
}

# Bids that count as a real response (drafts and withdrawals are excluded from evaluation).
EVALUABLE_BID_STATUSES = [VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED]

# Only ACTIVE vendors (approved through KYC and not on hold/suspended) may be invited.
ELIGIBLE_VENDOR_STATUSES = [Vendor.STATUS_ACTIVE]


def _lock_and_refresh(instance):
    """
    Row-locks the instance for the current transaction and reloads it in place, so callers
    holding the object see the post-transition state.
    """
    type(instance).objects.select_for_update().filter(pk=instance.pk).first()
    instance.refresh_from_db()
    return instance


def _transition_event(
    *, event: SourcingEvent, to_status: str, actor, action=AuditLog.ACTION_UPDATE, extra=None
):
    """Validated, audited state change for a sourcing event."""
    allowed = SOURCING_TRANSITIONS.get(event.status, set())
    if to_status not in allowed:
        raise ValidationError(
            f"Invalid sourcing transition {event.status} -> {to_status} for {event.event_number}."
        )
    previous_status = event.status
    event.status = to_status
    event.save(update_fields=["status", "updated_at"])
    new_state = {"status": to_status, "event_number": event.event_number}
    if extra:
        new_state.update(extra)
    if actor is None:
        # System (clock-driven) transition: recorded with no actor, never attributed to
        # whichever user's request happened to trigger the sync.
        AuditLog.objects.create(
            actor=None,
            action=action,
            target_model="SourcingEvent",
            target_object_id=str(event.id),
            previous_state={"status": previous_status},
            new_state=new_state,
        )
    else:
        create_audit_log_service(
            actor=actor,
            action=action,
            target_model="SourcingEvent",
            target_object_id=event.id,
            previous_state={"status": previous_status},
            new_state=new_state,
        )
    return event


def _next_document_number(model, field: str, prefix: str) -> str:
    """Sequential document number unique for the given prefix, generated inside the transaction."""
    year = timezone.now().strftime("%Y")
    seq = model.objects.count() + 1
    while True:
        candidate = f"{prefix}-{year}-{seq:05d}"
        if not model.objects.filter(**{field: candidate}).exists():
            return candidate
        seq += 1


def _parse_weight(value, label):
    try:
        weight = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValidationError(f"{label} must be a number between 0 and 100.")
    if weight < 0 or weight > 100:
        raise ValidationError(f"{label} must be between 0 and 100.")
    return weight


def _validate_event_fields(
    *, title, event_type, bid_start_date, bid_end_date, description, weights
):
    errors = []
    if not (title or "").strip():
        errors.append("Title is required.")
    if event_type not in (SourcingEvent.TYPE_RFQ, SourcingEvent.TYPE_RFP):
        errors.append("Event type must be RFQ or RFP.")
    if not (description or "").strip():
        errors.append("Description / scope of requirement is required.")
    if not bid_start_date or not bid_end_date:
        errors.append("Bid start and bid deadline are required.")
    elif bid_start_date >= bid_end_date:
        errors.append("Bid start must be earlier than the bid deadline.")
    tech_w, comm_w = weights
    if tech_w + comm_w != Decimal("100"):
        errors.append("Technical and commercial weights must add up to 100.")
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def create_sourcing_event_service(
    *,
    title: str,
    event_type: str,
    bid_start_date,
    bid_end_date,
    description: str,
    technical_requirements: str = "",
    commercial_requirements: str = "",
    required_documents: str = "",
    requisition=None,
    technical_weight=Decimal("50.00"),
    commercial_weight=Decimal("50.00"),
    created_by_user: User = None,
) -> SourcingEvent:
    """
    Creates a new RFQ/RFP sourcing event in DRAFT status.
    """
    tech_w = _parse_weight(technical_weight, "Technical weight")
    comm_w = _parse_weight(commercial_weight, "Commercial weight")
    _validate_event_fields(
        title=title,
        event_type=event_type,
        bid_start_date=bid_start_date,
        bid_end_date=bid_end_date,
        description=description,
        weights=(tech_w, comm_w),
    )

    if requisition:
        from apps.requisitions.models import PurchaseRequisition

        if requisition.status != PurchaseRequisition.STATUS_APPROVED:
            raise ValidationError(
                f"Cannot link Purchase Requisition '{requisition.pr_number}' in status '{requisition.status}'. "
                "Only APPROVED requisitions may be sourced."
            )

    prefix = "RFQ" if event_type == SourcingEvent.TYPE_RFQ else "RFP"
    event_number = _next_document_number(SourcingEvent, "event_number", prefix)

    event = SourcingEvent.objects.create(
        event_number=event_number,
        title=title.strip(),
        event_type=event_type,
        requisition=requisition,
        status=SourcingEvent.STATUS_DRAFT,
        bid_start_date=bid_start_date,
        bid_end_date=bid_end_date,
        description=description.strip(),
        technical_requirements=technical_requirements or "",
        commercial_requirements=commercial_requirements or "",
        required_documents=required_documents or "",
        technical_weight=tech_w,
        commercial_weight=comm_w,
        is_sealed=True,
        created_by=created_by_user,
    )

    create_audit_log_service(
        actor=created_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="SourcingEvent",
        target_object_id=event.id,
        new_state={
            "event_number": event.event_number,
            "status": event.status,
            "event_type": event.event_type,
        },
    )

    return event


EDITABLE_DRAFT_FIELDS = [
    "title",
    "event_type",
    "bid_start_date",
    "bid_end_date",
    "description",
    "technical_requirements",
    "commercial_requirements",
    "required_documents",
    "requisition",
    "technical_weight",
    "commercial_weight",
]


@transaction.atomic
def update_draft_sourcing_event_service(*, event: SourcingEvent, user: User, data: dict):
    """
    Edits a DRAFT event. Published events are immutable (vendors may already be bidding).
    """
    _lock_and_refresh(event)
    if event.status != SourcingEvent.STATUS_DRAFT:
        raise ValidationError(f"Only DRAFT events can be edited (current: {event.status}).")

    def _snap(obj):
        return {
            f: (str(getattr(obj, f + "_id")) if f == "requisition" else str(getattr(obj, f)))
            for f in EDITABLE_DRAFT_FIELDS
        }

    previous = _snap(event)
    for field in EDITABLE_DRAFT_FIELDS:
        if field in data:
            setattr(event, field, data[field])

    event.technical_weight = _parse_weight(event.technical_weight, "Technical weight")
    event.commercial_weight = _parse_weight(event.commercial_weight, "Commercial weight")
    _validate_event_fields(
        title=event.title,
        event_type=event.event_type,
        bid_start_date=event.bid_start_date,
        bid_end_date=event.bid_end_date,
        description=event.description,
        weights=(event.technical_weight, event.commercial_weight),
    )
    event.title = event.title.strip()
    if event.requisition:
        from apps.requisitions.models import PurchaseRequisition

        if event.requisition.status != PurchaseRequisition.STATUS_APPROVED:
            raise ValidationError(
                f"Cannot link Purchase Requisition '{event.requisition.pr_number}' in status '{event.requisition.status}'. "
                "Only APPROVED requisitions may be sourced."
            )
    event.save()

    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="SourcingEvent",
        target_object_id=event.id,
        previous_state=previous,
        new_state=_snap(event),
    )
    return event


def get_publish_validation_errors(event: SourcingEvent) -> list:
    """Server-side publish readiness checks. Empty list == publishable."""
    errors = []
    now = timezone.now()
    if event.status != SourcingEvent.STATUS_DRAFT:
        errors.append(f"Only DRAFT events can be published (current: {event.status}).")
    if not event.title.strip():
        errors.append("Title is required.")
    if event.event_type not in (SourcingEvent.TYPE_RFQ, SourcingEvent.TYPE_RFP):
        errors.append("Event type must be RFQ or RFP.")
    if not event.description.strip():
        errors.append("Description / scope of requirement is required.")
    if event.bid_start_date >= event.bid_end_date:
        errors.append("Bid start must be earlier than the bid deadline.")
    if event.bid_end_date <= now:
        errors.append("Bid deadline is in the past; adjust the bid window before publishing.")
    if event.technical_weight + event.commercial_weight != Decimal("100"):
        errors.append("Technical and commercial weights must add up to 100.")
    return errors + _invitation_errors(event)


def _invitation_errors(event: SourcingEvent) -> list:
    invites = list(event.invitations.select_related("vendor"))
    if not invites:
        return ["Invite at least one eligible vendor before publishing."]
    errors = []
    for invite in invites:
        v = invite.vendor
        if v.status not in ELIGIBLE_VENDOR_STATUSES:
            errors.append(
                f"Invited vendor '{v.legal_name}' is no longer eligible "
                f"(status {v.status}); remove the invitation."
            )
        elif not bool(getattr(v, "is_event_eligible", False)):
            errors.append(
                f"Invited vendor '{v.legal_name}' is not legally eligible for events "
                f"(eligibility: {getattr(v, 'event_eligibility_status', 'INELIGIBLE')}); remove the invitation."
            )
    return errors


def _notify_invited_vendor(event: SourcingEvent, vendor: Vendor):
    notify_vendor_users(
        vendor=vendor,
        notification_type=Notification.TYPE_RFQ_INVITATION,
        title=f"Invitation: {event.event_number} — {event.title}",
        message=(
            f"You are invited to respond to {event.get_event_type_display()} {event.event_number}. "
            f"Bid window: {event.bid_start_date:%Y-%m-%d %H:%M} to "
            f"{event.bid_end_date:%Y-%m-%d %H:%M}."
        ),
        target_url=f"/vendor/sourcing/{event.id}/",
    )


@transaction.atomic
def publish_sourcing_event_service(*, event: SourcingEvent, user: User) -> SourcingEvent:
    """
    Validates and publishes a DRAFT event (DRAFT -> PUBLISHED), notifies invited vendors and
    opens the bid window immediately when the bid start time has already been reached.
    """
    _lock_and_refresh(event)
    errors = get_publish_validation_errors(event)
    if errors:
        raise ValidationError(errors)

    _transition_event(event=event, to_status=SourcingEvent.STATUS_PUBLISHED, actor=user)

    for invite in event.invitations.select_related("vendor"):
        _notify_invited_vendor(event, invite.vendor)

    sync_event_window_status(event=event)
    return event


def sync_event_window_status(*, event: SourcingEvent, now=None) -> SourcingEvent:
    """
    Server-authoritative bid-window clock:
    PUBLISHED -> BID_WINDOW once bid_start_date is reached;
    BID_WINDOW -> TECHNICAL_REVIEW once bid_end_date has passed (bid closure).
    Called from views/selectors on access and from the Celery Beat task.
    """
    now = now or timezone.now()
    with transaction.atomic():
        _lock_and_refresh(event)
        if event.status == SourcingEvent.STATUS_PUBLISHED and event.bid_start_date <= now:
            _transition_event(
                event=event,
                to_status=SourcingEvent.STATUS_BID_WINDOW,
                actor=None,
                extra={"trigger": "bid_start_reached"},
            )
        if event.status == SourcingEvent.STATUS_BID_WINDOW and event.bid_end_date < now:
            _transition_event(
                event=event,
                to_status=SourcingEvent.STATUS_TECHNICAL_REVIEW,
                actor=None,
                extra={"trigger": "bid_deadline_passed"},
            )
            notify_role_users(
                role_codes=[Role.PROC_EXEC, Role.PROC_MGR, Role.SUPER_ADMIN],
                notification_type=Notification.TYPE_BID_DEADLINE,
                title=f"Bid Deadline Passed: {event.event_number}",
                message=f"Bidding window has closed for {event.event_number} ({event.title}). Sourcing event is now in Technical Review.",
                target_url=f"/sourcing/{event.id}/",
            )
    return event


def sync_all_event_windows(now=None) -> int:
    """Applies sync_event_window_status to every event whose window state may be stale."""
    now = now or timezone.now()
    due = SourcingEvent.objects.filter(
        status=SourcingEvent.STATUS_PUBLISHED, bid_start_date__lte=now
    ) | SourcingEvent.objects.filter(status=SourcingEvent.STATUS_BID_WINDOW, bid_end_date__lt=now)
    changed = 0
    for event in due:
        before = event.status
        sync_event_window_status(event=event, now=now)
        if event.status != before:
            changed += 1
    return changed


@transaction.atomic
def close_bid_window_service(*, event: SourcingEvent, user: User) -> SourcingEvent:
    """
    Bid closure (BID_WINDOW -> TECHNICAL_REVIEW). Only permitted once the server-side deadline
    has passed; early closure is not supported (docs/assumptions.md ASSUMP-010).
    """
    _lock_and_refresh(event)
    if event.status != SourcingEvent.STATUS_BID_WINDOW:
        raise ValidationError(f"Bid window is not open (current: {event.status}).")
    if timezone.now() <= event.bid_end_date:
        raise ValidationError(
            f"Bid window cannot be closed before the deadline "
            f"({event.bid_end_date:%Y-%m-%d %H:%M})."
        )
    return _transition_event(
        event=event,
        to_status=SourcingEvent.STATUS_TECHNICAL_REVIEW,
        actor=user,
        extra={"trigger": "manual_close_after_deadline"},
    )


def _invite_error(event: SourcingEvent, vendor_id, vendor):
    if vendor is None:
        return f"Vendor '{vendor_id}' does not exist."
    if vendor.status not in ELIGIBLE_VENDOR_STATUSES:
        return (
            f"Vendor '{vendor.legal_name}' is not eligible for invitation "
            f"(status: {vendor.get_status_display()})."
        )
    if not bool(getattr(vendor, "is_event_eligible", False)):
        return (
            f"Vendor '{vendor.legal_name}' is not legally eligible to participate in events "
            f"(eligibility: {getattr(vendor, 'event_eligibility_status', 'INELIGIBLE')})."
        )
    if BidInvite.objects.filter(event=event, vendor=vendor).exists():
        return f"Vendor '{vendor.legal_name}' is already invited to this event."
    return None


@transaction.atomic
def invite_vendors_to_event_service(
    *, event: SourcingEvent, vendor_ids: list, invited_by: User
) -> list:
    """
    Invites eligible (ACTIVE) vendors to a sourcing event before bid closure.
    Rejects ineligible vendors (draft/KYC/rejected/on hold/suspended) and duplicate invitations.
    Vendors invited after publication are notified immediately.
    """
    _lock_and_refresh(event)
    if event.status not in (
        SourcingEvent.STATUS_DRAFT,
        SourcingEvent.STATUS_PUBLISHED,
        SourcingEvent.STATUS_BID_WINDOW,
    ):
        raise ValidationError(f"Vendors cannot be invited when the event is {event.status}.")
    if event.status != SourcingEvent.STATUS_DRAFT and timezone.now() > event.bid_end_date:
        raise ValidationError("Vendors cannot be invited after the bid deadline.")
    if not vendor_ids:
        raise ValidationError("Select at least one vendor to invite.")

    vendors = {str(v.id): v for v in Vendor.objects.filter(id__in=vendor_ids)}
    errors = [_invite_error(event, vid, vendors.get(str(vid))) for vid in vendor_ids]
    errors = [e for e in errors if e]
    if errors:
        raise ValidationError(errors)

    invitations = []
    for vid in dict.fromkeys(str(v) for v in vendor_ids):
        vendor = vendors[vid]
        invite = BidInvite.objects.create(event=event, vendor=vendor, created_by=invited_by)
        invitations.append(invite)
        create_audit_log_service(
            actor=invited_by,
            action=AuditLog.ACTION_CREATE,
            target_model="BidInvite",
            target_object_id=invite.id,
            new_state={
                "event_number": event.event_number,
                "vendor": vendor.legal_name,
                "vendor_number": vendor.vendor_number,
            },
        )
        if event.status != SourcingEvent.STATUS_DRAFT:
            _notify_invited_vendor(event, vendor)

    return invitations


@transaction.atomic
def revoke_invitation_service(*, invite: BidInvite, user: User):
    """Removes an invitation while the event is still a DRAFT (nothing has been sent yet)."""
    if invite.event.status != SourcingEvent.STATUS_DRAFT:
        raise ValidationError("Invitations can only be removed while the event is DRAFT.")
    snapshot = {"event_number": invite.event.event_number, "vendor": invite.vendor.legal_name}
    invite_id = invite.id
    invite.delete()
    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_DELETE,
        target_model="BidInvite",
        target_object_id=invite_id,
        previous_state=snapshot,
    )


def _reject_if_deadline_passed(event: SourcingEvent):
    """Deadline check before the window sync, so late vendors get a deadline-specific error."""
    if (
        event.status
        in (
            SourcingEvent.STATUS_PUBLISHED,
            SourcingEvent.STATUS_BID_WINDOW,
        )
        and timezone.now() > event.bid_end_date
    ):
        raise ValidationError(
            f"Bid deadline passed on {event.bid_end_date:%Y-%m-%d %H:%M}; "
            "submissions and amendments are closed."
        )


def _assert_bid_window_open(event: SourcingEvent, verb: str):
    now = timezone.now()
    if event.status != SourcingEvent.STATUS_BID_WINDOW:
        raise ValidationError(
            f"Cannot {verb}: event '{event.event_number}' is not accepting bids "
            f"(status: {event.status})."
        )
    if now < event.bid_start_date:
        raise ValidationError(f"Cannot {verb}: the bid window has not opened yet.")
    if now > event.bid_end_date:
        raise ValidationError(
            f"Cannot {verb}: bid deadline passed on {event.bid_end_date:%Y-%m-%d %H:%M}."
        )


@transaction.atomic
def withdraw_vendor_bid_service(*, bid: VendorBid, user: User, reason: str) -> VendorBid:
    """
    Vendor withdraws its bid before event close (PRD 4.2 edge case). History is preserved:
    the current content is snapshotted into BidVersion and the bid is marked WITHDRAWN.
    """
    _lock_and_refresh(bid)
    _assert_bid_window_open(bid.event, "withdraw bid")
    if user and getattr(user, "vendor_id", None):
        if user.vendor_id != bid.vendor_id:
            raise PermissionDenied(
                f"Unauthorized: User {user.email} from vendor '{user.vendor.legal_name}' "
                f"cannot withdraw bids for vendor '{bid.vendor.legal_name}'."
            )
    if bid.status == VendorBid.STATUS_WITHDRAWN:
        raise ValidationError("Bid is already withdrawn.")
    if not (reason or "").strip():
        raise ValidationError("A withdrawal reason is required.")

    previous_status = bid.status
    if not BidVersion.objects.filter(bid=bid, version_number=bid.version).exists():
        BidVersion.objects.create(
            bid=bid,
            version_number=bid.version,
            status=bid.status,
            total_bid_amount=bid.total_bid_amount,
            proposal_summary=bid.proposal_summary,
            technical_proposal=bid.technical_proposal,
            commercial_proposal=bid.commercial_proposal,
            submitted_at=bid.submitted_at,
            snapshot_data={
                "lines": [
                    {
                        "item_description": line.item_description,
                        "quantity": str(line.quantity),
                        "quoted_unit_price": str(line.quoted_unit_price),
                        "quoted_total_price": str(line.quoted_total_price),
                    }
                    for line in bid.lines.all()
                ]
            },
        )
    bid.status = VendorBid.STATUS_WITHDRAWN
    bid.save(update_fields=["status", "updated_at"])

    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_CANCEL,
        target_model="VendorBid",
        target_object_id=bid.id,
        previous_state={"status": previous_status, "version": bid.version},
        new_state={"status": bid.status, "reason": reason.strip()},
    )
    return bid


def validate_bid_service(*, bid: VendorBid) -> dict:
    """
    Authoritative server-side bid validation.
    Checks authorization, status, deadline, completeness, line item pricing, and attachments.
    """
    errors = []
    warnings = []
    event = bid.event

    if event.status != SourcingEvent.STATUS_BID_WINDOW:
        errors.append(
            f"Event '{event.event_number}' is not currently accepting bids (Status: {event.status})."
        )

    if timezone.now() > event.bid_end_date:
        errors.append(
            f"Bid submission deadline passed on {event.bid_end_date.strftime('%Y-%m-%d %H:%M')}."
        )

    if bid.vendor.status == Vendor.STATUS_SUSPENDED:
        errors.append(
            "Vendor account is currently suspended and cannot participate in procurement."
        )

    if not bool(getattr(bid.vendor, "is_event_eligible", False)):
        errors.append(
            f"Vendor account is not legally eligible to participate in events (Status: {getattr(bid.vendor, 'event_eligibility_status', 'INELIGIBLE')})."
        )

    if not bid.technical_proposal.strip() and not bid.proposal_summary.strip():
        errors.append("Technical response or proposal summary must be provided.")

    lines = list(bid.lines.all())
    if not lines:
        errors.append(
            "At least one commercial line item with quantity and quoted price is required."
        )
    else:
        for idx, line in enumerate(lines, 1):
            if line.quantity <= Decimal("0.00"):
                errors.append(
                    f"Line {idx} ('{line.item_description}'): Quantity must be greater than zero."
                )
            if line.quoted_unit_price <= Decimal("0.00"):
                errors.append(
                    f"Line {idx} ('{line.item_description}'): Quoted unit price must be greater than zero."
                )

    if bid.total_bid_amount <= Decimal("0.00"):
        errors.append("Total bid amount must be greater than $0.00.")

    if event.required_documents and not bid.attachments.exists():
        warnings.append(
            "Event specifies required documents, but no attachments have been uploaded."
        )

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "event_number": event.event_number,
        "bid_number": bid.bid_number,
        "version": bid.version,
        "total_amount": str(bid.total_bid_amount),
        "deadline": event.bid_end_date.strftime("%Y-%m-%d %H:%M"),
    }


@transaction.atomic
def save_draft_bid_service(
    *,
    event: SourcingEvent,
    vendor: Vendor,
    user: User,
    line_items: list = None,
    proposal_summary: str = "",
    technical_proposal: str = "",
    commercial_proposal: str = "",
) -> VendorBid:
    """
    Saves or updates a vendor's bid in DRAFT status.
    """
    _reject_if_deadline_passed(event)
    sync_event_window_status(event=event)
    if event.status != SourcingEvent.STATUS_BID_WINDOW:
        raise ValidationError(
            f"Bidding is closed for event '{event.event_number}'. Status: {event.status}"
        )

    if timezone.now() < event.bid_start_date:
        raise ValidationError("Cannot prepare bid: the bid window has not opened yet.")

    if timezone.now() > event.bid_end_date:
        raise ValidationError("Cannot prepare bid: Sourcing event bid deadline has passed.")

    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(f"Vendor '{vendor.legal_name}' is suspended and cannot prepare bids.")

    if user and getattr(user, "vendor_id", None):
        if user.vendor_id != vendor.id:
            raise PermissionDenied(
                f"Unauthorized: User {user.email} from vendor '{user.vendor.legal_name}' "
                f"cannot save draft bids for vendor '{vendor.legal_name}'."
            )

    if not bool(getattr(vendor, "is_event_eligible", False)):
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is not legally eligible to participate in events."
        )

    if not BidInvite.objects.filter(event=event, vendor=vendor).exists():
        raise ValidationError("Vendor does not have a valid invitation to this sourcing event.")

    bid = VendorBid.objects.filter(event=event, vendor=vendor).first()
    if not bid:
        bid_count = VendorBid.objects.count() + 1
        bid_number = f"BID-{timezone.now().strftime('%Y')}-{bid_count:05d}"
        bid = VendorBid.objects.create(
            event=event,
            vendor=vendor,
            bid_number=bid_number,
            version=1,
            status=VendorBid.STATUS_DRAFT,
            proposal_summary=proposal_summary,
            technical_proposal=technical_proposal,
            commercial_proposal=commercial_proposal,
            total_bid_amount=Decimal("0.00"),
        )
    else:
        if bid.status not in [VendorBid.STATUS_DRAFT]:
            raise ValidationError(
                f"Bid is already in '{bid.status}' status. Use Amendment to modify."
            )
        bid.proposal_summary = proposal_summary
        bid.technical_proposal = technical_proposal
        bid.commercial_proposal = commercial_proposal

    if line_items is not None:
        bid.lines.all().delete()
        total = Decimal("0.00")
        for item in line_items:
            qty = Decimal(str(item.get("quantity", 1)))
            unit_price = Decimal(str(item.get("quoted_unit_price", 0)))
            line = BidLine.objects.create(
                bid=bid,
                pr_line=item.get("pr_line"),
                item_description=item.get("item_description", "Item"),
                quantity=qty,
                quoted_unit_price=unit_price,
            )
            total += line.quoted_total_price
        bid.total_bid_amount = total

    bid.save()

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE if bid.id else AuditLog.ACTION_CREATE,
        target_model="VendorBid",
        target_object_id=str(bid.id),
        new_state={
            "bid_number": bid.bid_number,
            "status": bid.status,
            "total_amount": str(bid.total_bid_amount),
        },
    )
    return bid


@transaction.atomic
def submit_vendor_bid_service(
    *,
    event: SourcingEvent,
    vendor: Vendor,
    line_items: list,
    proposal_summary: str = "",
    technical_proposal: str = "",
    commercial_proposal: str = "",
    submitted_by_user: User = None,
) -> VendorBid:
    """
    Submits a sealed bid for a vendor during the BID_WINDOW.
    Enforces server-side deadline, authorization, atomic transaction, versioning, and audit log.
    """
    _lock_and_refresh(event)
    _reject_if_deadline_passed(event)
    sync_event_window_status(event=event)
    if event.status != SourcingEvent.STATUS_BID_WINDOW:
        raise ValidationError(
            f"Bidding is closed for event '{event.event_number}'. Current status: {event.status}"
        )

    if timezone.now() < event.bid_start_date:
        raise ValidationError("Cannot submit bid: the bid window has not opened yet.")

    if timezone.now() > event.bid_end_date:
        raise ValidationError(
            f"Cannot submit bid: Sourcing event deadline passed on {event.bid_end_date.strftime('%Y-%m-%d %H:%M')}."
        )

    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(f"Vendor '{vendor.legal_name}' is suspended and cannot submit bids.")

    if submitted_by_user and getattr(submitted_by_user, "vendor_id", None):
        if submitted_by_user.vendor_id != vendor.id:
            raise PermissionDenied(
                f"Unauthorized: User {submitted_by_user.email} from vendor '{submitted_by_user.vendor.legal_name}' "
                f"cannot submit bids for vendor '{vendor.legal_name}'."
            )

    if not bool(getattr(vendor, "is_event_eligible", False)):
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is not legally eligible to participate in events."
        )

    # Verify vendor has invitation
    if not BidInvite.objects.filter(event=event, vendor=vendor).exists():
        raise ValidationError(f"Vendor '{vendor.legal_name}' has not been invited to this event.")

    bid = VendorBid.objects.select_for_update().filter(event=event, vendor=vendor).first()
    created = False
    if not bid:
        created = True
        bid_count = VendorBid.objects.count() + 1
        bid_number = f"BID-{timezone.now().strftime('%Y')}-{bid_count:05d}"
        bid = VendorBid.objects.create(
            event=event,
            vendor=vendor,
            bid_number=bid_number,
            version=1,
            status=VendorBid.STATUS_SUBMITTED,
            proposal_summary=proposal_summary,
            technical_proposal=technical_proposal,
            commercial_proposal=commercial_proposal,
            total_bid_amount=Decimal("0.00"),
            submitted_at=timezone.now(),
        )
    else:
        if bid.status == VendorBid.STATUS_WITHDRAWN:
            raise ValidationError(
                "Cannot submit: This bid has already been withdrawn and cannot be re-submitted."
            )
        bid.status = VendorBid.STATUS_SUBMITTED
        bid.proposal_summary = proposal_summary
        bid.technical_proposal = technical_proposal or bid.technical_proposal
        bid.commercial_proposal = commercial_proposal or bid.commercial_proposal
        bid.submitted_at = timezone.now()

    if line_items:
        bid.lines.all().delete()
        total = Decimal("0.00")
        snapshot_lines = []
        for item in line_items:
            qty = Decimal(str(item["quantity"]))
            unit_price = Decimal(str(item["quoted_unit_price"]))
            if qty <= Decimal("0.00"):
                raise ValidationError(
                    f"Quantity for line item '{item.get('item_description', '')}' must be greater than zero."
                )
            if unit_price < Decimal("0.00"):
                raise ValidationError(
                    f"Quoted unit price for line item '{item.get('item_description', '')}' cannot be negative."
                )
            line = BidLine.objects.create(
                bid=bid,
                pr_line=item.get("pr_line"),
                item_description=item["item_description"],
                quantity=qty,
                quoted_unit_price=unit_price,
            )
            total += line.quoted_total_price
            snapshot_lines.append(
                {
                    "item_description": line.item_description,
                    "quantity": str(line.quantity),
                    "quoted_unit_price": str(line.quoted_unit_price),
                    "quoted_total_price": str(line.quoted_total_price),
                }
            )
        bid.total_bid_amount = total
    else:
        snapshot_lines = [
            {
                "item_description": line.item_description,
                "quantity": str(line.quantity),
                "quoted_unit_price": str(line.quoted_unit_price),
                "quoted_total_price": str(line.quoted_total_price),
            }
            for line in bid.lines.all()
        ]

    bid.save()

    # Capture initial immutable BidVersion (Version 1)
    BidVersion.objects.get_or_create(
        bid=bid,
        version_number=bid.version,
        defaults={
            "status": VendorBid.STATUS_SUBMITTED,
            "total_bid_amount": bid.total_bid_amount,
            "proposal_summary": bid.proposal_summary,
            "technical_proposal": bid.technical_proposal,
            "commercial_proposal": bid.commercial_proposal,
            "submitted_at": bid.submitted_at,
            "snapshot_data": {"lines": snapshot_lines},
        },
    )

    # Update invite status
    BidInvite.objects.filter(event=event, vendor=vendor).update(is_responded=True)

    AuditLog.objects.create(
        actor=submitted_by_user,
        action=AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE,
        target_model="VendorBid",
        target_object_id=str(bid.id),
        new_state={
            "bid_number": bid.bid_number,
            "version": bid.version,
            "status": bid.status,
            "total_amount": str(bid.total_bid_amount),
            "vendor": vendor.legal_name,
        },
    )

    return bid


@transaction.atomic
def amend_vendor_bid_service(
    *,
    bid: VendorBid,
    vendor_user: User,
    amendment_reason: str,
    line_items: list,
    proposal_summary: str = "",
    technical_proposal: str = "",
    commercial_proposal: str = "",
) -> VendorBid:
    """
    Amends a submitted bid while the event is still open.
    Preserves prior version as an immutable BidVersion snapshot, creates new version, and audits.
    Rejects amendment if deadline has passed or event is not in BID_WINDOW.
    """
    event = bid.event
    _lock_and_refresh(bid)
    _lock_and_refresh(event)
    _reject_if_deadline_passed(event)
    sync_event_window_status(event=event)
    if event.status != SourcingEvent.STATUS_BID_WINDOW:
        raise ValidationError(
            f"Cannot amend bid: Sourcing event is '{event.status}', not BID_WINDOW."
        )

    if timezone.now() > event.bid_end_date:
        raise ValidationError(
            f"Cannot amend bid: Sourcing event deadline passed on {event.bid_end_date.strftime('%Y-%m-%d %H:%M')}."
        )

    if bid.status not in (VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED):
        raise ValidationError(f"Only submitted bids can be amended (current: {bid.status}).")

    if vendor_user and getattr(vendor_user, "vendor_id", None):
        if vendor_user.vendor_id != bid.vendor_id:
            raise PermissionDenied(
                f"Unauthorized: User {vendor_user.email} from vendor '{vendor_user.vendor.legal_name}' "
                f"cannot amend bids for vendor '{bid.vendor.legal_name}'."
            )

    if not amendment_reason or not amendment_reason.strip():
        raise ValidationError("Amendment justification/reason is required for bid amendment.")

    # 1. Ensure prior version is snapshotted into BidVersion
    if not BidVersion.objects.filter(bid=bid, version_number=bid.version).exists():
        prior_lines = [
            {
                "item_description": line.item_description,
                "quantity": str(line.quantity),
                "quoted_unit_price": str(line.quoted_unit_price),
                "quoted_total_price": str(line.quoted_total_price),
            }
            for line in bid.lines.all()
        ]
        BidVersion.objects.create(
            bid=bid,
            version_number=bid.version,
            status=bid.status,
            total_bid_amount=bid.total_bid_amount,
            proposal_summary=bid.proposal_summary,
            technical_proposal=bid.technical_proposal,
            commercial_proposal=bid.commercial_proposal,
            submitted_at=bid.submitted_at or timezone.now(),
            snapshot_data={"lines": prior_lines},
        )

    # 2. Increment version and update current bid
    new_version_num = bid.version + 1
    previous_version_num = bid.version

    bid.version = new_version_num
    bid.status = VendorBid.STATUS_AMENDED
    bid.proposal_summary = proposal_summary or bid.proposal_summary
    bid.technical_proposal = technical_proposal or bid.technical_proposal
    bid.commercial_proposal = commercial_proposal or bid.commercial_proposal
    bid.submitted_at = timezone.now()

    # 3. Update line items
    bid.lines.all().delete()
    total = Decimal("0.00")
    new_lines_snapshot = []
    for item in line_items:
        qty = Decimal(str(item["quantity"]))
        unit_price = Decimal(str(item["quoted_unit_price"]))
        line = BidLine.objects.create(
            bid=bid,
            pr_line=item.get("pr_line"),
            item_description=item["item_description"],
            quantity=qty,
            quoted_unit_price=unit_price,
        )
        total += line.quoted_total_price
        new_lines_snapshot.append(
            {
                "item_description": line.item_description,
                "quantity": str(line.quantity),
                "quoted_unit_price": str(line.quoted_unit_price),
                "quoted_total_price": str(line.quoted_total_price),
            }
        )

    bid.total_bid_amount = total
    bid.save()

    # 4. Create immutable BidVersion record for this amendment
    BidVersion.objects.create(
        bid=bid,
        version_number=new_version_num,
        status=VendorBid.STATUS_AMENDED,
        total_bid_amount=bid.total_bid_amount,
        proposal_summary=bid.proposal_summary,
        technical_proposal=bid.technical_proposal,
        commercial_proposal=bid.commercial_proposal,
        amendment_reason=amendment_reason.strip(),
        submitted_at=bid.submitted_at,
        snapshot_data={"lines": new_lines_snapshot, "amendment_reason": amendment_reason.strip()},
    )

    # 5. Append-only AuditLog
    AuditLog.objects.create(
        actor=vendor_user,
        action=AuditLog.ACTION_UPDATE,
        target_model="VendorBid",
        target_object_id=str(bid.id),
        previous_state={"version": previous_version_num, "status": "SUBMITTED"},
        new_state={
            "version": new_version_num,
            "status": bid.status,
            "total_amount": str(bid.total_bid_amount),
            "amendment_reason": amendment_reason.strip(),
        },
    )

    return bid


@transaction.atomic
def ask_clarification_service(
    *,
    event: SourcingEvent,
    vendor: Vendor,
    user: User,
    question: str,
) -> Clarification:
    """
    Submits a clarification question from an invited vendor for a sourcing event.
    """
    if not question or not question.strip():
        raise ValidationError("Clarification question cannot be blank.")

    if not BidInvite.objects.filter(event=event, vendor=vendor).exists():
        raise ValidationError("Vendor does not have a valid invitation for this sourcing event.")
    if event.status in (
        SourcingEvent.STATUS_DRAFT,
        SourcingEvent.STATUS_AWARDED,
        SourcingEvent.STATUS_CANCELLED,
    ):
        raise ValidationError(f"Clarifications are not accepted for a {event.status} event.")
    if event.bid_end_date and timezone.now() > event.bid_end_date:
        raise ValidationError(
            f"Clarifications are closed: Sourcing event deadline passed on {event.bid_end_date.strftime('%Y-%m-%d %H:%M')}."
        )

    clarification = Clarification.objects.create(
        event=event,
        vendor=vendor,
        question=question.strip(),
        status="PENDING",
    )

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_CREATE,
        target_model="Clarification",
        target_object_id=str(clarification.id),
        new_state={
            "event_number": event.event_number,
            "vendor": vendor.legal_name,
            "question": clarification.question[:100],
        },
    )

    return clarification


@transaction.atomic
def upload_bid_attachment_service(
    *,
    bid: VendorBid,
    user: User,
    file,
    title: str = "",
    document_type: str = "TECHNICAL",
) -> BidAttachment:
    """
    Attaches a validated document to a vendor bid.
    """
    _assert_bid_window_open(bid.event, "upload attachment")
    if bid.vendor_id != getattr(user, "vendor_id", None) and not user.is_superuser:
        raise ValidationError("Only the bidding vendor can attach documents to this bid.")
    if document_type not in dict(BidAttachment.DOC_CHOICES):
        raise ValidationError(f"Invalid attachment type '{document_type}'.")
    validate_file_upload(file)
    if not title or not title.strip():
        title = getattr(file, "name", "Proposal Attachment")

    attachment = BidAttachment.objects.create(
        bid=bid,
        title=title.strip(),
        document_type=document_type,
        file=file,
        file_size=getattr(file, "size", 0),
    )

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_CREATE,
        target_model="BidAttachment",
        target_object_id=str(attachment.id),
        new_state={
            "bid_number": bid.bid_number,
            "title": attachment.title,
            "type": attachment.document_type,
        },
    )

    return attachment


@transaction.atomic
def answer_clarification_service(
    *, clarification: Clarification, user: User, answer: str
) -> Clarification:
    """
    Procurement answers a vendor clarification. Answers are visible only to the asking vendor
    (existing behaviour; broadcast visibility is an OPEN DECISION, ASSUMP-011).
    """
    _lock_and_refresh(clarification)
    event = clarification.event
    if event.status in (SourcingEvent.STATUS_AWARDED, SourcingEvent.STATUS_CANCELLED):
        raise ValidationError(f"Clarifications are closed for a {event.status} event.")
    if not (answer or "").strip():
        raise ValidationError("Answer cannot be blank.")

    previous = {"status": clarification.status, "answer": clarification.answer}
    clarification.answer = answer.strip()
    clarification.answered_by = user
    clarification.answered_at = timezone.now()
    clarification.status = "ANSWERED"
    clarification.save()

    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="Clarification",
        target_object_id=clarification.id,
        previous_state=previous,
        new_state={"status": clarification.status, "event_number": event.event_number},
    )
    notify_vendor_users(
        vendor=clarification.vendor,
        notification_type=Notification.TYPE_CLARIFICATION_RESPONSE,
        title=f"Clarification answered: {event.event_number}",
        message=clarification.answer[:500],
        target_url=f"/vendor/sourcing/{event.id}/",
    )
    return clarification


# ==============================================================================
# EVALUATION (technical envelope first, commercial envelope second)
# ==============================================================================


def _get_evaluable_bid(event: SourcingEvent, bid: VendorBid) -> VendorBid:
    if bid.event_id != event.id:
        raise ValidationError("Bid does not belong to this sourcing event.")
    if bid.status not in EVALUABLE_BID_STATUSES:
        raise ValidationError(f"Bid {bid.bid_number} is {bid.status} and cannot be evaluated.")
    return bid


def _parse_score(value, label):
    try:
        score = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValidationError(f"{label} must be a number between 0 and 100.")
    if score < 0 or score > 100:
        raise ValidationError(f"{label} must be between 0 and 100.")
    return score


@transaction.atomic
def record_technical_evaluation_service(
    *, event: SourcingEvent, bid: VendorBid, evaluator: User, score, comments: str = ""
) -> BidEvaluation:
    if event.status != SourcingEvent.STATUS_TECHNICAL_REVIEW:
        raise ValidationError("Technical scores can only be recorded during TECHNICAL REVIEW.")
    _get_evaluable_bid(event, bid)
    score = _parse_score(score, "Technical score")

    evaluation, created = BidEvaluation.objects.select_for_update().get_or_create(
        bid=bid, evaluator=evaluator, defaults={"event": event}
    )
    previous = None if created else {"technical_score": str(evaluation.technical_score)}
    evaluation.technical_score = score
    evaluation.technical_evaluated_at = timezone.now()
    if comments:
        evaluation.comments = comments.strip()
    evaluation.save()

    create_audit_log_service(
        actor=evaluator,
        action=AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE,
        target_model="BidEvaluation",
        target_object_id=evaluation.id,
        previous_state=previous,
        new_state={
            "stage": "TECHNICAL",
            "bid_number": bid.bid_number,
            "technical_score": str(score),
        },
    )
    return evaluation


@transaction.atomic
def record_commercial_evaluation_service(
    *, event: SourcingEvent, bid: VendorBid, evaluator: User, score, comments: str = ""
) -> BidEvaluation:
    if event.status != SourcingEvent.STATUS_COMMERCIAL_REVIEW:
        raise ValidationError("Commercial scores can only be recorded during COMMERCIAL REVIEW.")
    _get_evaluable_bid(event, bid)
    score = _parse_score(score, "Commercial score")

    evaluation, created = BidEvaluation.objects.select_for_update().get_or_create(
        bid=bid, evaluator=evaluator, defaults={"event": event}
    )
    previous = None if created else {"commercial_score": str(evaluation.commercial_score)}
    evaluation.commercial_score = score
    evaluation.commercial_evaluated_at = timezone.now()
    if comments:
        evaluation.comments = comments.strip()
    evaluation.save()

    create_audit_log_service(
        actor=evaluator,
        action=AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE,
        target_model="BidEvaluation",
        target_object_id=evaluation.id,
        previous_state=previous,
        new_state={
            "stage": "COMMERCIAL",
            "bid_number": bid.bid_number,
            "commercial_score": str(score),
            "weighted_total_score": str(evaluation.weighted_total_score),
        },
    )
    return evaluation


@transaction.atomic
def advance_to_commercial_review_service(*, event: SourcingEvent, user: User) -> SourcingEvent:
    """
    TECHNICAL_REVIEW -> COMMERCIAL_REVIEW. Requires every evaluable bid to have a technical score;
    only after this transition is pricing (the commercial envelope) unsealed to evaluators.
    """
    _lock_and_refresh(event)
    bids = list(event.bids.filter(status__in=EVALUABLE_BID_STATUSES))
    if not bids:
        raise ValidationError("No submitted bids to evaluate. Cancel the event or re-run sourcing.")
    scored = set(
        BidEvaluation.objects.filter(event=event, technical_evaluated_at__isnull=False).values_list(
            "bid_id", flat=True
        )
    )
    missing = [b.bid_number for b in bids if b.id not in scored]
    if missing:
        raise ValidationError(f"Technical evaluation pending for: {', '.join(missing)}.")
    return _transition_event(
        event=event, to_status=SourcingEvent.STATUS_COMMERCIAL_REVIEW, actor=user
    )


@transaction.atomic
def add_negotiation_note_service(
    *, event: SourcingEvent, bid: VendorBid, author: User, note: str
) -> NegotiationNote:
    if event.status not in (
        SourcingEvent.STATUS_COMMERCIAL_REVIEW,
        SourcingEvent.STATUS_AWARD_APPROVAL,
    ):
        raise ValidationError(
            "Negotiation notes can be recorded during COMMERCIAL REVIEW or AWARD APPROVAL."
        )
    _get_evaluable_bid(event, bid)
    if not (note or "").strip():
        raise ValidationError("Negotiation note cannot be blank.")
    entry = NegotiationNote.objects.create(
        event=event, bid=bid, author=author, note=note.strip(), created_by=author
    )
    create_audit_log_service(
        actor=author,
        action=AuditLog.ACTION_CREATE,
        target_model="NegotiationNote",
        target_object_id=entry.id,
        new_state={"event_number": event.event_number, "bid_number": bid.bid_number},
    )
    return entry


# ==============================================================================
# AWARD (Procurement Executive recommends -> Procurement Manager approves)
# ==============================================================================


def _assert_vendor_awardable(vendor: Vendor):
    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is suspended/blacklisted and cannot be awarded."
        )
    if vendor.status != Vendor.STATUS_ACTIVE:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is {vendor.get_status_display()} and cannot be awarded."
        )
    if not bool(getattr(vendor, "is_event_eligible", False)):
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is not legally eligible to participate in events."
        )


@transaction.atomic
def recommend_award_service(
    *, event: SourcingEvent, winning_bid: VendorBid, recommended_by: User, award_reason: str
) -> AwardDecision:
    """COMMERCIAL_REVIEW -> AWARD_APPROVAL with a PENDING AwardDecision."""
    _lock_and_refresh(event)
    if event.status != SourcingEvent.STATUS_COMMERCIAL_REVIEW:
        raise ValidationError("An award can only be recommended during COMMERCIAL REVIEW.")
    _get_evaluable_bid(event, winning_bid)
    _assert_vendor_awardable(winning_bid.vendor)
    if not (award_reason or "").strip():
        raise ValidationError("Award justification is required.")
    if not BidEvaluation.objects.filter(
        bid=winning_bid, commercial_evaluated_at__isnull=False
    ).exists():
        raise ValidationError(
            f"Bid {winning_bid.bid_number} has no commercial evaluation; score it before award."
        )

    decision = AwardDecision.objects.create(
        event=event,
        winning_bid=winning_bid,
        award_reason=award_reason.strip(),
        status=AwardDecision.STATUS_PENDING,
        recommended_by=recommended_by,
        created_by=recommended_by,
    )
    create_audit_log_service(
        actor=recommended_by,
        action=AuditLog.ACTION_SUBMIT,
        target_model="AwardDecision",
        target_object_id=decision.id,
        new_state={
            "status": decision.status,
            "event_number": event.event_number,
            "recommended_vendor": winning_bid.vendor.legal_name,
            "bid_number": winning_bid.bid_number,
            "amount": str(winning_bid.total_bid_amount),
        },
    )
    _transition_event(
        event=event, to_status=SourcingEvent.STATUS_AWARD_APPROVAL, actor=recommended_by
    )
    notify_role_users(
        role_codes=[Role.PROC_MGR],
        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
        title=f"Award approval required: {event.event_number}",
        message=(
            f"Recommended award to {winning_bid.vendor.legal_name} "
            f"({winning_bid.total_bid_amount}). Reason: {decision.award_reason[:300]}"
        ),
        target_url=f"/sourcing-events/{event.id}/",
    )
    return decision


def _pending_decision(event: SourcingEvent) -> AwardDecision:
    decision = (
        AwardDecision.objects.select_for_update()
        .select_related("winning_bid__vendor")
        .filter(event=event, status=AwardDecision.STATUS_PENDING)
        .first()
    )
    if not decision:
        raise ValidationError("There is no pending award recommendation for this event.")
    return decision


@transaction.atomic
def approve_award_service(
    *, event: SourcingEvent, approver: User, comments: str = ""
) -> AwardDecision:
    """AWARD_APPROVAL -> AWARDED. Re-checks vendor eligibility at decision time."""
    _lock_and_refresh(event)
    if event.status != SourcingEvent.STATUS_AWARD_APPROVAL:
        raise ValidationError("Event is not awaiting award approval.")
    decision = _pending_decision(event)
    _assert_vendor_awardable(decision.winning_bid.vendor)

    decision.status = AwardDecision.STATUS_APPROVED
    decision.approved_by = approver
    decision.decided_at = timezone.now()
    decision.decision_comments = (comments or "").strip()
    decision.save()

    create_audit_log_service(
        actor=approver,
        action=AuditLog.ACTION_APPROVE,
        target_model="AwardDecision",
        target_object_id=decision.id,
        previous_state={"status": AwardDecision.STATUS_PENDING},
        new_state={
            "status": decision.status,
            "event_number": event.event_number,
            "winner": decision.winning_bid.vendor.legal_name,
            "comments": decision.decision_comments,
        },
    )
    _transition_event(
        event=event,
        to_status=SourcingEvent.STATUS_AWARDED,
        actor=approver,
        action=AuditLog.ACTION_APPROVE,
        extra={"winner": decision.winning_bid.vendor.legal_name},
    )
    from apps.notifications.services import notify_vendor_users

    notify_vendor_users(
        vendor=decision.winning_bid.vendor,
        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
        title=f"Award Confirmed: {event.event_number} — {event.title}",
        message=(
            f"Congratulations! Your proposal for {event.event_number} has been approved and awarded "
            f"by the Procurement Board."
        ),
        target_url=f"/vendor/sourcing/{event.id}/",
    )
    return decision


@transaction.atomic
def reject_award_service(*, event: SourcingEvent, approver: User, comments: str) -> AwardDecision:
    """AWARD_APPROVAL -> COMMERCIAL_REVIEW; the rejected decision is retained as history."""
    _lock_and_refresh(event)
    if event.status != SourcingEvent.STATUS_AWARD_APPROVAL:
        raise ValidationError("Event is not awaiting award approval.")
    if not (comments or "").strip():
        raise ValidationError("A reason is required to reject an award recommendation.")
    decision = _pending_decision(event)
    decision.status = AwardDecision.STATUS_REJECTED
    decision.approved_by = approver
    decision.decided_at = timezone.now()
    decision.decision_comments = comments.strip()
    decision.save()

    create_audit_log_service(
        actor=approver,
        action=AuditLog.ACTION_REJECT,
        target_model="AwardDecision",
        target_object_id=decision.id,
        previous_state={"status": AwardDecision.STATUS_PENDING},
        new_state={"status": decision.status, "comments": decision.decision_comments},
    )
    _transition_event(
        event=event,
        to_status=SourcingEvent.STATUS_COMMERCIAL_REVIEW,
        actor=approver,
        action=AuditLog.ACTION_REJECT,
    )
    if decision.recommended_by_id:
        from apps.notifications.services import notify_users

        notify_users(
            users=[decision.recommended_by],
            notification_type=Notification.TYPE_APPROVAL_REQUIRED,
            title=f"Award recommendation rejected: {event.event_number}",
            message=decision.decision_comments[:500],
            target_url=f"/sourcing-events/{event.id}/",
        )
    return decision


@transaction.atomic
def evaluate_and_award_sourcing_event_service(
    *, event: SourcingEvent, winning_bid: VendorBid, award_reason: str, approved_by_user: User
) -> AwardDecision:
    """
    Direct award by an approver (Procurement Manager): records the recommendation and its
    approval in one transaction. Same guards as the two-step path except the commercial-score
    requirement, which the approver explicitly takes responsibility for.
    """
    if winning_bid.event_id != event.id:
        raise ValidationError("Winning bid does not belong to target sourcing event.")
    _lock_and_refresh(event)
    _get_evaluable_bid(event, winning_bid)
    _assert_vendor_awardable(winning_bid.vendor)
    if not (award_reason or "").strip():
        raise ValidationError("Award justification is required.")

    if event.status == SourcingEvent.STATUS_COMMERCIAL_REVIEW:
        decision = AwardDecision.objects.create(
            event=event,
            winning_bid=winning_bid,
            award_reason=award_reason.strip(),
            status=AwardDecision.STATUS_PENDING,
            recommended_by=approved_by_user,
            created_by=approved_by_user,
        )
        _transition_event(
            event=event, to_status=SourcingEvent.STATUS_AWARD_APPROVAL, actor=approved_by_user
        )
    elif event.status == SourcingEvent.STATUS_AWARD_APPROVAL:
        pending = (
            AwardDecision.objects.select_for_update()
            .filter(event=event, status=AwardDecision.STATUS_PENDING)
            .first()
        )
        if not pending:
            AwardDecision.objects.create(
                event=event,
                winning_bid=winning_bid,
                award_reason=award_reason.strip(),
                status=AwardDecision.STATUS_PENDING,
                recommended_by=approved_by_user,
                created_by=approved_by_user,
            )
    else:
        raise ValidationError(f"Event cannot be awarded from status {event.status}.")

    decision = approve_award_service(event=event, approver=approved_by_user, comments=award_reason)
    event.refresh_from_db()
    return decision


@transaction.atomic
def cancel_sourcing_event_service(
    *, event: SourcingEvent, user: User, reason: str
) -> SourcingEvent:
    _lock_and_refresh(event)
    if not (reason or "").strip():
        raise ValidationError("A cancellation reason is required.")
    AwardDecision.objects.filter(event=event, status=AwardDecision.STATUS_PENDING).update(
        status=AwardDecision.STATUS_REJECTED,
        decided_at=timezone.now(),
        decision_comments=f"Event cancelled: {reason.strip()}",
    )
    _transition_event(
        event=event,
        to_status=SourcingEvent.STATUS_CANCELLED,
        actor=user,
        action=AuditLog.ACTION_CANCEL,
        extra={"reason": reason.strip()},
    )
    for invite in event.invitations.select_related("vendor"):
        notify_vendor_users(
            vendor=invite.vendor,
            notification_type=Notification.TYPE_BID_DEADLINE,
            title=f"Sourcing event cancelled: {event.event_number}",
            message=f"{event.title} has been cancelled. Reason: {reason.strip()[:300]}",
            target_url=f"/vendor/sourcing/{event.id}/",
        )
    return event


# ==============================================================================
# AWARD -> PURCHASE ORDER
# ==============================================================================


@transaction.atomic
def generate_po_from_award_service(*, event: SourcingEvent, user: User, cost_center=None):
    """
    Creates the Purchase Order for an AWARDED event from the approved winning bid lines,
    reusing orders.generate_purchase_order_service (budget commitment, audit, numbering).
    """
    from apps.orders.models import PurchaseOrder
    from apps.orders.services import generate_purchase_order_service

    _lock_and_refresh(event)
    if event.status != SourcingEvent.STATUS_AWARDED:
        raise ValidationError("A Purchase Order can only be generated for an AWARDED event.")
    decision = event.award_decision
    if not decision or decision.status != AwardDecision.STATUS_APPROVED:
        raise ValidationError("No approved award decision exists for this event.")
    if (
        PurchaseOrder.objects.filter(sourcing_event=event)
        .exclude(status=PurchaseOrder.STATUS_CANCELLED)
        .exists()
    ):
        raise ValidationError("A Purchase Order already exists for this sourcing event.")

    if cost_center is None and event.requisition_id:
        cost_center = event.requisition.cost_center
    if cost_center is None:
        raise ValidationError("Select a cost center for the Purchase Order.")

    bid = decision.winning_bid
    lines = [
        {
            "item_description": line.item_description,
            "quantity": line.quantity,
            "unit_price": line.quoted_unit_price,
        }
        for line in bid.lines.all()
    ]
    if not lines:
        raise ValidationError("The winning bid has no priced line items to order.")

    return generate_purchase_order_service(
        vendor=bid.vendor,
        cost_center=cost_center,
        line_items=lines,
        created_by_user=user,
        requisition=event.requisition,
        sourcing_event=event,
    )

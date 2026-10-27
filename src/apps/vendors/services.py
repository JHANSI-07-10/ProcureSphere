from datetime import timedelta
import logging
import secrets
import string
import uuid

try:
    import requests
except ImportError:
    requests = None
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log_service
from apps.core.validators import (
    check_upload_rate_limit,
    extract_pan_from_gstin,
    is_valid_gstin,
    is_valid_pan,
    normalize_tax_identifier,
    validate_file_upload,
    validate_gstin,
    validate_pan_gstin_consistency,
    validate_pan_number,
)
from apps.notifications.models import Notification
from apps.notifications.services import notify_users, notify_vendor_users

from .brevo_email import build_credentials_html
from .kyc_policy import ProvisionalKYCPolicy
from .models import (
    Vendor,
    VendorCategory,
    VendorContact,
    VendorCredentialDispatch,
    VendorDocument,
    VendorDocumentReviewHistory,
    VendorRiskRecord,
)
from .permissions import can_legal_verify_vendor, can_operate_governance

logger = logging.getLogger(__name__)


@transaction.atomic
def register_vendor_service(
    *,
    legal_name: str,
    tax_identification_number: str,
    category: VendorCategory,
    email: str,
    address: str,
    trade_name: str = "",
    registration_number: str = "",
    phone: str = "",
    bank_name: str = "",
    bank_account_number: str = "",
    bank_routing_code: str = "",
    created_by_user: User = None,
    pan_number: str = "",
    gstin: str = "",
) -> Vendor:
    """
    Registers a new vendor in DRAFT status with unique vendor number.
    Safely validates and normalizes optional dedicated PAN and GSTIN fields.
    """
    if Vendor.objects.filter(tax_identification_number=tax_identification_number).exists():
        raise ValidationError(f"Vendor with tax ID '{tax_identification_number}' already exists.")

    norm_pan = normalize_tax_identifier(pan_number)
    norm_gstin = normalize_tax_identifier(gstin)

    if norm_pan:
        validate_pan_number(norm_pan)
    if norm_gstin:
        validate_gstin(norm_gstin)
    if norm_pan and norm_gstin:
        validate_pan_gstin_consistency(norm_pan, norm_gstin)

    # Derive recognizable PAN or GSTIN from legacy tax_identification_number if not provided
    clean_tin = normalize_tax_identifier(tax_identification_number)
    if not norm_pan and is_valid_pan(clean_tin):
        norm_pan = clean_tin
    if not norm_gstin and is_valid_gstin(clean_tin):
        norm_gstin = clean_tin
        if not norm_pan:
            norm_pan = extract_pan_from_gstin(clean_tin) or ""
    elif norm_gstin and not norm_pan:
        norm_pan = extract_pan_from_gstin(norm_gstin) or ""

    from apps.core.services import generate_document_number

    vendor_number = generate_document_number(Vendor, "vendor_number", "VND")

    vendor = Vendor.objects.create(
        legal_name=legal_name,
        trade_name=trade_name,
        vendor_number=vendor_number,
        tax_identification_number=tax_identification_number,
        pan_number=norm_pan,
        gstin=norm_gstin,
        registration_number=registration_number,
        category=category,
        email=email,
        phone=phone,
        address=address,
        bank_name=bank_name,
        bank_account_number=bank_account_number,
        bank_routing_code=bank_routing_code,
        status=Vendor.STATUS_DRAFT,
        created_by=created_by_user,
    )

    AuditLog.objects.create(
        actor=created_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        new_state={
            "status": vendor.status,
            "vendor_number": vendor.vendor_number,
            "legal_name": vendor.legal_name,
            "pan_number": vendor.pan_number,
            "gstin": vendor.gstin,
        },
    )

    return vendor


@transaction.atomic
def update_vendor_service(
    *,
    vendor: Vendor,
    actor: User,
    legal_name: str,
    trade_name: str,
    email: str,
    phone: str,
    address: str,
    category: VendorCategory,
    bank_name: str,
    bank_account_number: str,
    bank_routing_code: str,
) -> Vendor:
    """
    Updates vendor master information. Allowed for Procurement Executives/Managers only.
    Audit-logged with previous and new state.
    """
    if not can_operate_governance(actor):
        raise PermissionDenied("Only Procurement Executives/Managers can edit vendor information.")

    previous_state = {
        "legal_name": vendor.legal_name,
        "trade_name": vendor.trade_name,
        "email": vendor.email,
        "phone": vendor.phone,
        "address": vendor.address,
        "category": str(vendor.category_id),
        "bank_name": vendor.bank_name,
        "bank_account_number": vendor.bank_account_number,
        "bank_routing_code": vendor.bank_routing_code,
    }

    vendor.legal_name = legal_name
    vendor.trade_name = trade_name
    vendor.email = email
    vendor.phone = phone
    vendor.address = address
    vendor.category = category
    vendor.bank_name = bank_name
    vendor.bank_account_number = bank_account_number
    vendor.bank_routing_code = bank_routing_code
    vendor.save(
        update_fields=[
            "legal_name",
            "trade_name",
            "email",
            "phone",
            "address",
            "category",
            "bank_name",
            "bank_account_number",
            "bank_routing_code",
            "updated_at",
        ]
    )

    create_audit_log_service(
        actor=actor,
        action=AuditLog.ACTION_UPDATE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        previous_state=previous_state,
        new_state={
            "legal_name": vendor.legal_name,
            "trade_name": vendor.trade_name,
            "email": vendor.email,
            "phone": vendor.phone,
            "address": vendor.address,
            "category": str(vendor.category_id),
            "bank_name": vendor.bank_name,
            "bank_account_number": vendor.bank_account_number,
            "bank_routing_code": vendor.bank_routing_code,
        },
    )
    return vendor


# Statuses that are safe to delete (no workflow impact)
_DELETABLE_STATUSES = {Vendor.STATUS_DRAFT, Vendor.STATUS_REJECTED}


@transaction.atomic
def delete_vendor_service(*, vendor: Vendor, actor: User) -> None:
    """
    Permanently deletes a vendor record. Only allowed for DRAFT or REJECTED vendors
    that have no open sourcing events, POs or invoices. Writes an audit entry before deletion.
    """
    if not can_operate_governance(actor):
        raise PermissionDenied("Only Procurement Executives/Managers can delete vendor records.")

    if vendor.status not in _DELETABLE_STATUSES:
        raise ValidationError(
            f"Only DRAFT or REJECTED vendors can be deleted. "
            f"'{vendor.vendor_number}' is currently {vendor.get_status_display()}."
        )

    # Block deletion if there are any linked POs or sourcing invitations
    from apps.orders.models import PurchaseOrder  # local import to avoid circular

    if PurchaseOrder.objects.filter(vendor=vendor).exists():
        raise ValidationError(
            f"Cannot delete '{vendor.vendor_number}': purchase orders are linked to this vendor."
        )
    if vendor.bid_invitations.exists():
        raise ValidationError(
            f"Cannot delete '{vendor.vendor_number}': sourcing invitations are linked to this vendor."
        )

    # Portal logins: users.vendor is SET_NULL, which would leave a live account with no vendor.
    # Deactivate (not delete) them; other records and the audit trail may reference these users.
    vendor_users = list(vendor.vendor_users.all())
    # Credential dispatch rows are PROTECT-ed evidence of emails sent to this (never-activated)
    # vendor; they are removed with it, with their count preserved in the audit entry below.
    dispatch_count = vendor.credential_dispatches.count()

    # Write audit log BEFORE deletion so the record is preserved
    create_audit_log_service(
        actor=actor,
        action=AuditLog.ACTION_DELETE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        previous_state={
            "vendor_number": vendor.vendor_number,
            "legal_name": vendor.legal_name,
            "status": vendor.status,
            "email": vendor.email,
        },
        new_state={
            "deleted": True,
            "credential_dispatches_removed": dispatch_count,
            "portal_users_deactivated": [u.email for u in vendor_users],
        },
    )
    for portal_user in vendor_users:
        portal_user.is_active = False
        portal_user.must_change_password = True
        portal_user.save(update_fields=["is_active", "must_change_password"])
    vendor.credential_dispatches.all().delete()
    vendor.delete()


@transaction.atomic
def submit_vendor_kyc_service(*, vendor: Vendor, user: User) -> Vendor:
    """
    Transitions Vendor state from DRAFT or CHANGES_REQUIRED to SUBMITTED.
    """
    if vendor.status not in (Vendor.STATUS_DRAFT, Vendor.STATUS_CHANGES_REQUIRED):
        raise ValidationError(
            f"Cannot submit vendor in status '{vendor.status}'. Must be DRAFT or CHANGES_REQUIRED."
        )

    if not vendor.documents.exists():
        raise ValidationError(
            "At least one KYC document must be uploaded before submitting registration."
        )

    previous_status = vendor.status
    vendor.status = Vendor.STATUS_SUBMITTED
    vendor.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        previous_state={"status": previous_status},
        new_state={"status": vendor.status},
    )
    return vendor


@transaction.atomic
def start_kyc_review_service(*, vendor: Vendor, reviewer: User) -> Vendor:
    """
    Transitions Vendor state to KYC_REVIEW.
    """
    if vendor.status != Vendor.STATUS_SUBMITTED:
        raise ValidationError(
            f"Cannot start KYC review for vendor in status '{vendor.status}'. Must be SUBMITTED."
        )

    previous_status = vendor.status
    vendor.status = Vendor.STATUS_KYC_REVIEW
    vendor.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=reviewer,
        action=AuditLog.ACTION_UPDATE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        previous_state={"status": previous_status},
        new_state={"status": vendor.status},
    )
    return vendor


KYC_DOC_REVIEWABLE_STATUSES = (Vendor.STATUS_SUBMITTED, Vendor.STATUS_KYC_REVIEW)


def _lock_reviewable_document(document: VendorDocument, reviewer: User) -> VendorDocument:
    """Row-locks the document and checks reviewer authority and vendor state."""
    from .permissions import can_review_kyc

    if not can_review_kyc(reviewer):
        raise PermissionDenied("Your role is not authorised to review KYC documents.")
    document = VendorDocument.objects.select_for_update().select_related("vendor").get(
        pk=document.pk
    )
    if document.vendor.status not in KYC_DOC_REVIEWABLE_STATUSES:
        raise ValidationError(
            "KYC documents can only be reviewed after the vendor has submitted them "
            f"(vendor status is {document.vendor.get_status_display()})."
        )
    if document.review_status != VendorDocument.REVIEW_PENDING:
        raise ValidationError(
            f"Document has already been reviewed ({document.get_review_status_display()})."
        )
    return document



def verify_vendor_document_decision_service(
    *,
    document: VendorDocument = None,
    document_id=None,
    reviewer: User,
    decision: str,
    notes: str = "",
    reviewer_notes: str = "",
) -> VendorDocument:
    """
    Records a document-level decision by Legal Manager or Super Admin:
    - VERIFY: Marks document verified and updates verification timestamp.
    - REJECT: Marks document rejected with reviewer comments.
    - REQUEST_CORRECTION: Marks document correction required with reviewer feedback.
    Appends to VendorDocumentReviewHistory and logs AuditLog.
    Re-evaluates vendor event eligibility if a mandatory document status changes.
    """
    if document is None:
        if document_id is None:
            raise ValidationError("Either document or document_id is required.")
        document = VendorDocument.objects.select_for_update().get(pk=document_id)

    if not can_legal_verify_vendor(reviewer) and not can_operate_governance(reviewer):
        raise PermissionDenied("You do not have permission to verify or review KYC documents.")

    resolved_notes = (notes or reviewer_notes or "").strip()
    valid_decisions = {
        "VERIFY": (VendorDocument.STATUS_VERIFIED, AuditLog.ACTION_VERIFY),
        "REJECT": (VendorDocument.STATUS_REJECTED, AuditLog.ACTION_REJECT),
        "REQUEST_CORRECTION": (VendorDocument.STATUS_CHANGES_REQUIRED, AuditLog.ACTION_UPDATE),
    }
    if decision not in valid_decisions:
        raise ValidationError(
            f"Invalid decision '{decision}'. Must be one of: {list(valid_decisions.keys())}"
        )

    if decision in ["REJECT", "REQUEST_CORRECTION"] and not resolved_notes:
        raise ValidationError(
            "Reviewer notes are mandatory when rejecting or requesting corrections on a document."
        )

    new_status, audit_action = valid_decisions[decision]
    previous_status = document.status
    previous_is_verified = document.is_verified

    document.status = new_status
    document.is_verified = bool(new_status == VendorDocument.STATUS_VERIFIED)
    document.verified_by = reviewer
    if decision == "VERIFY":
        document.verified_at = timezone.now()
    document.reviewer_notes = resolved_notes
    document.save(
        update_fields=[
            "status",
            "is_verified",
            "verified_by",
            "verified_at",
            "reviewer_notes",
            "updated_at",
        ]
    )

    VendorDocumentReviewHistory.objects.create(
        document=document,
        reviewer=reviewer,
        action=decision,
        notes=resolved_notes,
        previous_status=previous_status,
        new_status=new_status,
    )

    create_audit_log_service(
        actor=reviewer,
        action=audit_action,
        target_model="VendorDocument",
        target_object_id=str(document.id),
        previous_state={
            "status": previous_status,
            "is_verified": previous_is_verified,
            "document_type": document.document_type,
            "vendor": document.vendor.legal_name,
        },
        new_state={
            "status": new_status,
            "is_verified": document.is_verified,
            "decision": decision,
            "notes": resolved_notes,
            "vendor": document.vendor.legal_name,
        },
    )

    vendor = document.vendor
    eval_result = ProvisionalKYCPolicy.evaluate_vendor_kyc_compliance(vendor)
    if not eval_result["is_compliant"] and vendor.is_event_eligible:
        previous_eligibility = vendor.event_eligibility_status
        vendor.is_event_eligible = False
        vendor.event_eligibility_status = Vendor.ELIGIBILITY_INELIGIBLE
        vendor.save(update_fields=["is_event_eligible", "event_eligibility_status", "updated_at"])
        create_audit_log_service(
            actor=reviewer,
            action=AuditLog.ACTION_UPDATE,
            target_model="Vendor",
            target_object_id=str(vendor.id),
            previous_state={"is_event_eligible": True, "event_eligibility_status": previous_eligibility},
            new_state={
                "is_event_eligible": False,
                "event_eligibility_status": Vendor.ELIGIBILITY_INELIGIBLE,
                "reason": f"Document compliance failure ({document.title} marked {new_status})",
            },
        )
    elif eval_result["is_compliant"] and not vendor.is_event_eligible and vendor.status == Vendor.STATUS_ACTIVE:
        previous_eligibility = vendor.event_eligibility_status
        vendor.is_event_eligible = True
        vendor.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
        vendor.save(update_fields=["is_event_eligible", "event_eligibility_status", "updated_at"])
        create_audit_log_service(
            actor=reviewer,
            action=AuditLog.ACTION_UPDATE,
            target_model="Vendor",
            target_object_id=str(vendor.id),
            previous_state={"is_event_eligible": False, "event_eligibility_status": previous_eligibility},
            new_state={
                "is_event_eligible": True,
                "event_eligibility_status": Vendor.ELIGIBILITY_ELIGIBLE,
                "reason": "All KYC compliance documents verified and valid; eligibility reinstated",
            },
        )
        notify_vendor_users(
            vendor=vendor,
            notification_type=Notification.TYPE_KYC_REQUEST,
            title="Event Participation Eligibility Restored",
            message=(
                f"All mandatory KYC documents for {vendor.legal_name} have been reviewed and verified. "
                "Your event participation eligibility has been restored."
            ),
            target_url="/vendor/documents/",
        )
        pe_user = vendor.created_by
        if pe_user and pe_user.is_active:
            notify_users(
                users=[pe_user],
                notification_type=Notification.TYPE_KYC_REQUEST,
                title=f"Vendor Reinstated: {vendor.legal_name} is Event-Eligible",
                message=(
                    f"Replacement/renewed KYC documents for vendor '{vendor.legal_name}' have been verified by Legal. "
                    "Event eligibility has been restored."
                ),
                target_url=f"/vendors/{vendor.id}/",
            )

    if decision in ["REJECT", "REQUEST_CORRECTION"]:
        notify_vendor_users(
            vendor=vendor,
            notification_type=Notification.TYPE_KYC_REQUEST,
            title=f"KYC Document Feedback: {document.title}",
            message=(
                f"Document '{document.title}' ({document.get_document_type_display()}) status: "
                f"{document.get_status_display()}. Reviewer feedback: {document.reviewer_notes}. "
                "Please upload a corrected or replacement document in your vendor portal."
            ),
            target_url="/vendor/documents/",
        )

    return document


@transaction.atomic
def verify_vendor_document_service(
    *, document: VendorDocument, verifier: User, notes: str = ""
) -> VendorDocument:
    """Marks a KYC document as verified (delegates to verify_vendor_document_decision_service)."""
    if document.is_verified:
        raise ValidationError("Document is already verified.")
    return verify_vendor_document_decision_service(
        document=document,
        reviewer=verifier,
        decision="VERIFY",
        notes=notes or "Verified by governance reviewer.",
    )


@transaction.atomic
def reject_vendor_document_service(
    *, document: VendorDocument, reviewer: User, reason: str
) -> VendorDocument:
    """Rejects a pending KYC document with a mandatory reason (delegates to verify_vendor_document_decision_service)."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to reject a document.")
    return verify_vendor_document_decision_service(
        document=document,
        reviewer=reviewer,
        decision="REJECT",
        notes=reason,
    )


@transaction.atomic
def approve_vendor_service(*, vendor: Vendor, manager: User, notes: str = "") -> Vendor:
    """
    Activates a vendor (SUBMITTED / KYC_REVIEW -> ACTIVE). Only Procurement/Admin roles may
    activate, and only when every KYC gate passes (get_vendor_kyc_summary): documents present,
    mandatory types present, all current documents verified, none rejected or pending.
    The vendor row is locked so a replayed or concurrent approval cannot double-transition.
    """
    from .permissions import can_operate_governance
    from .selectors import get_vendor_kyc_summary

    if not can_operate_governance(manager):
        raise PermissionDenied("Your role is not authorised to activate vendors.")
    vendor = Vendor.objects.select_for_update().get(pk=vendor.pk)
    summary = get_vendor_kyc_summary(vendor)
    if vendor.status == Vendor.STATUS_ACTIVE:
        raise ValidationError("Vendor is already active.")
    if not summary["can_activate"]:
        raise ValidationError("Cannot activate vendor: " + " ".join(summary["blockers"]))

    previous_status = vendor.status
    vendor.status = Vendor.STATUS_ACTIVE
    vendor.status_notes = notes
    vendor.save(update_fields=["status", "status_notes", "updated_at"])

    create_audit_log_service(
        actor=manager,
        action=AuditLog.ACTION_APPROVE,
        target_model="Vendor",
        target_object_id=vendor.id,
        previous_state={"status": previous_status},
        new_state={
            "status": vendor.status,
            "notes": notes,
            "documents_verified": summary["documents_verified"],
        },
    )
    notify_vendor_users(
        vendor=vendor,
        notification_type=Notification.TYPE_KYC_REQUEST,
        title="Your vendor account is now active",
        message=(notes or "KYC approved.")[:500],
        target_url="/vendor/",
    )
    return vendor


# (from_status, to_status) -> capability required. Anything not listed is an invalid transition.
VENDOR_GOVERNANCE_TRANSITIONS = {
    (Vendor.STATUS_ACTIVE, Vendor.STATUS_ON_HOLD): "operate",
    (Vendor.STATUS_ON_HOLD, Vendor.STATUS_ACTIVE): "operate",
    (Vendor.STATUS_ACTIVE, Vendor.STATUS_SUSPENDED): "suspend",
    (Vendor.STATUS_ON_HOLD, Vendor.STATUS_SUSPENDED): "suspend",
    (Vendor.STATUS_SUSPENDED, Vendor.STATUS_ACTIVE): "suspend",
    (Vendor.STATUS_SUBMITTED, Vendor.STATUS_REJECTED): "operate",
    (Vendor.STATUS_KYC_REVIEW, Vendor.STATUS_REJECTED): "operate",
    (Vendor.STATUS_SUBMITTED, Vendor.STATUS_CHANGES_REQUIRED): "operate",
    (Vendor.STATUS_KYC_REVIEW, Vendor.STATUS_CHANGES_REQUIRED): "operate",
    (Vendor.STATUS_CHANGES_REQUIRED, Vendor.STATUS_REJECTED): "operate",
}


def allowed_governance_transitions(vendor: Vendor, user: User) -> list:
    """Target statuses the given user may move this vendor to (drives the UI and the API)."""
    from .permissions import can_operate_governance, can_suspend_vendor

    checks = {"operate": can_operate_governance(user), "suspend": can_suspend_vendor(user)}
    return [
        to_status
        for (from_status, to_status), capability in VENDOR_GOVERNANCE_TRANSITIONS.items()
        if from_status == vendor.status and checks[capability]
    ]


@transaction.atomic
def set_vendor_status_governance_service(
    *, vendor: Vendor, actor: User, new_status: str, notes: str
) -> Vendor:
    """
    Governance status change (hold / release / suspend / reinstate / reject) through the
    VENDOR_GOVERNANCE_TRANSITIONS table. Records the vendor's open sourcing/PO exposure in the
    audit trail. Open transactions are NOT auto-cancelled (OPEN DECISION, ASSUMP-009); a
    suspended vendor is blocked from new invitations, bids, awards and POs by those services.
    """
    from .permissions import can_operate_governance, can_suspend_vendor
    from .selectors import get_vendor_open_transactions

    Vendor.objects.select_for_update().filter(pk=vendor.pk).first()
    vendor.refresh_from_db()
    if vendor.status == new_status:
        if (notes or "").strip():
            vendor.status_notes = notes.strip()
            vendor.save(update_fields=["status_notes", "updated_at"])
        return vendor
    capability = VENDOR_GOVERNANCE_TRANSITIONS.get((vendor.status, new_status))
    if capability is None:
        raise ValidationError(f"Invalid vendor status transition {vendor.status} -> {new_status}.")
    if new_status == Vendor.STATUS_CHANGES_REQUIRED:
        from .permissions import can_review_kyc

        allowed = can_review_kyc(actor)
    else:
        allowed = (
            can_suspend_vendor(actor) if capability == "suspend" else can_operate_governance(actor)
        )
    if not allowed:
        raise PermissionDenied(
            "Your role is not authorised to perform this vendor governance action."
        )
    if not (notes or "").strip():
        raise ValidationError("A governance reason / note is required.")

    exposure = get_vendor_open_transactions(vendor)["summary"]
    previous_status = vendor.status
    vendor.status = new_status
    vendor.status_notes = notes.strip()
    vendor.save(update_fields=["status", "status_notes", "updated_at"])

    create_audit_log_service(
        actor=actor,
        action=(
            AuditLog.ACTION_REJECT
            if new_status == Vendor.STATUS_REJECTED
            else AuditLog.ACTION_UPDATE
        ),
        target_model="Vendor",
        target_object_id=vendor.id,
        previous_state={"status": previous_status},
        new_state={
            "status": vendor.status,
            "notes": vendor.status_notes,
            "open_transactions_at_change": exposure,
        },
    )
    notify_vendor_users(
        vendor=vendor,
        notification_type=Notification.TYPE_KYC_REQUEST,
        title=f"Vendor status changed to {vendor.get_status_display()}",
        message=vendor.status_notes[:500],
        target_url="/vendor/profile/",
    )
    return vendor


@transaction.atomic
def record_vendor_risk_assessment_service(
    *, vendor: Vendor, assessor: User, risk_level: str, risk_flags: list, notes: str
) -> VendorRiskRecord:
    """Appends a new risk assessment (risk history is never overwritten)."""
    from .permissions import can_operate_governance

    if not can_operate_governance(assessor):
        raise PermissionDenied("Your role is not authorised to record vendor risk assessments.")
    valid_levels = {code for code, _ in VendorRiskRecord.RISK_CHOICES}
    if risk_level not in valid_levels:
        raise ValidationError(f"Invalid risk level '{risk_level}'.")
    valid_flags = {code for code, _ in VendorRiskRecord.RISK_FLAG_CHOICES}
    risk_flags = list(dict.fromkeys(risk_flags or []))
    unknown = [f for f in risk_flags if f not in valid_flags]
    if unknown:
        raise ValidationError(f"Unknown risk flag(s): {', '.join(unknown)}.")
    if not (notes or "").strip():
        raise ValidationError("Assessment notes are required.")

    previous = vendor.risk_records.order_by("-created_at").first()
    record = VendorRiskRecord.objects.create(
        vendor=vendor,
        risk_level=risk_level,
        risk_flags=risk_flags,
        assessment_notes=notes.strip(),
        assessed_by=assessor,
        created_by=assessor,
    )
    if previous and record.created_at <= previous.created_at:
        VendorRiskRecord.objects.filter(pk=record.pk).update(
            created_at=previous.created_at + timedelta(milliseconds=10)
        )
        record.refresh_from_db()
    create_audit_log_service(
        actor=assessor,
        action=AuditLog.ACTION_CREATE,
        target_model="VendorRiskRecord",
        target_object_id=record.id,
        previous_state=(
            {"risk_level": previous.risk_level, "risk_flags": previous.risk_flags}
            if previous
            else None
        ),
        new_state={
            "vendor": vendor.legal_name,
            "risk_level": record.risk_level,
            "risk_flags": record.risk_flags,
        },
    )
    return record


@transaction.atomic
def update_vendor_profile_service(
    *,
    vendor: Vendor,
    user: User,
    data: dict,
) -> Vendor:
    """
    Vendor self-service profile update: updates trade name, address, phone, banking info, and tax fields.
    Legal name, TIN, vendor number, and category are locked and require governance approval.
    """
    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is suspended. Self-service updates are frozen."
        )

    previous_state = {
        "trade_name": vendor.trade_name,
        "address": vendor.address,
        "phone": vendor.phone,
        "bank_name": vendor.bank_name,
        "bank_account_number": vendor.bank_account_number,
        "bank_routing_code": vendor.bank_routing_code,
        "pan_number": vendor.pan_number,
        "gstin": vendor.gstin,
    }

    update_fields = ["updated_at"]

    if "trade_name" in data:
        vendor.trade_name = data["trade_name"]
        update_fields.append("trade_name")
    if "address" in data:
        vendor.address = data["address"]
        update_fields.append("address")
    if "phone" in data:
        vendor.phone = data["phone"]
        update_fields.append("phone")
    if "bank_name" in data:
        vendor.bank_name = data["bank_name"]
        update_fields.append("bank_name")
    if "bank_account_number" in data:
        vendor.bank_account_number = data["bank_account_number"]
        update_fields.append("bank_account_number")
    if "bank_routing_code" in data:
        vendor.bank_routing_code = data["bank_routing_code"]
        update_fields.append("bank_routing_code")

    if "pan_number" in data or "gstin" in data:
        new_pan = normalize_tax_identifier(data.get("pan_number", vendor.pan_number))
        new_gstin = normalize_tax_identifier(data.get("gstin", vendor.gstin))
        if new_pan:
            validate_pan_number(new_pan)
        if new_gstin:
            validate_gstin(new_gstin)
        if new_pan and new_gstin:
            validate_pan_gstin_consistency(new_pan, new_gstin)

        if "pan_number" in data:
            vendor.pan_number = new_pan
            update_fields.append("pan_number")
        if "gstin" in data:
            vendor.gstin = new_gstin
            update_fields.append("gstin")

    vendor.save(update_fields=list(set(update_fields)))

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        previous_state=previous_state,
        new_state={
            "trade_name": vendor.trade_name,
            "address": vendor.address,
            "phone": vendor.phone,
            "bank_name": vendor.bank_name,
            "bank_account_number": vendor.bank_account_number,
            "bank_routing_code": vendor.bank_routing_code,
            "pan_number": vendor.pan_number,
            "gstin": vendor.gstin,
        },
    )
    return vendor


MAX_VENDOR_DOCUMENT_COUNT = 25
MAX_VENDOR_STORAGE_BYTES = 50 * 1024 * 1024  # 50MB


@transaction.atomic
def upload_vendor_document_service(
    *,
    vendor: Vendor,
    user: User,
    file,
    document_type: str,
    title: str = "",
    expiry_date=None,
    replaces_document: VendorDocument = None,
    replaces_document_id=None,
) -> VendorDocument:
    """
    Uploads a KYC or compliance document for a vendor with:
    1. Upload rate limiting per user/vendor.
    2. Document type choice validation.
    3. File validation (0-byte rejection, size limit, extension & magic-byte check).
    4. Concurrency-safe per-vendor document count quota (max 25).
    5. Concurrency-safe per-vendor total storage quota (max 50MB).
    6. Links replaces_document if specified (or automatically associates prior document of same type).
    7. Calculates document version. Never auto-verifies on upload.
    """
    # 1. Rate limiting
    rate_limit_key = f"user_{user.pk}" if (user and user.pk) else f"vendor_{vendor.pk}"
    check_upload_rate_limit(rate_limit_key, max_requests=10, window_seconds=60)

    # 2. Document type validation
    if document_type not in dict(VendorDocument.DOC_TYPE_CHOICES):
        raise ValidationError(f"Invalid document type '{document_type}'.")

    # 3. File content & format validation
    validate_file_upload(file)

    # 4. Concurrency-safe quota enforcement via select_for_update()
    locked_vendor = Vendor.objects.select_for_update().get(pk=vendor.pk)
    existing_docs = list(locked_vendor.documents.all())

    if len(existing_docs) >= MAX_VENDOR_DOCUMENT_COUNT:
        raise ValidationError(
            f"Maximum document quota reached ({MAX_VENDOR_DOCUMENT_COUNT} documents). "
            "Please remove obsolete documents or contact procurement."
        )

    current_storage = sum(
        d.file.size for d in existing_docs if d.file and hasattr(d.file, "size")
    )
    incoming_size = getattr(file, "size", 0) or 0
    if current_storage + incoming_size > MAX_VENDOR_STORAGE_BYTES:
        max_mb = MAX_VENDOR_STORAGE_BYTES // (1024 * 1024)
        raise ValidationError(
            f"Vendor storage quota exceeded (maximum {max_mb}MB across all KYC documents). "
            "Please contact procurement if you require additional quota."
        )

    if not title or not title.strip():
        title = getattr(file, "name", "Vendor Document")

    if not replaces_document and replaces_document_id:
        replaces_document = VendorDocument.objects.filter(pk=replaces_document_id, vendor=vendor).first()

    if not replaces_document:
        replaces_document = (
            VendorDocument.objects.filter(vendor=vendor, document_type=document_type)
            .order_by("-created_at")
            .first()
        )

    version = 1
    if replaces_document:
        version = (replaces_document.version or 1) + 1

    doc = VendorDocument.objects.create(
        vendor=locked_vendor,
        document_type=document_type,
        title=title.strip(),
        file=file,
        expiry_date=expiry_date,
        status=VendorDocument.STATUS_PENDING,
        is_verified=False,
        version=version,
        replaces_document=replaces_document,
    )

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_CREATE,
        target_model="VendorDocument",
        target_object_id=str(doc.id),
        new_state={
            "vendor": locked_vendor.legal_name,
            "document_type": doc.document_type,
            "title": doc.title,
            "version": version,
            "replaces_document_id": str(replaces_document.id) if replaces_document else None,
        },
    )
    return doc


CREDENTIAL_ISSUE_BLOCKED = {Vendor.STATUS_SUSPENDED, Vendor.STATUS_REJECTED}


def _generate_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
        ):
            return pwd


def _credentials_email_body(*, vendor: Vendor, email: str, password: str) -> str:
    links = "\n".join(f"  - {url}" for url in settings.VENDOR_PORTAL_LOGIN_URLS)
    lines = [
        f"Dear {vendor.legal_name},",
        "",
        "You have been registered as a supplier on ProcureSphere 360.",
        "",
        f"Vendor number: {vendor.vendor_number}",
        f"Login ID: {email}",
        f"Temporary password: {password}",
        "",
        "Sign in here:",
        links,
        "",
        "You will be asked to set your own password at first login. After that, complete",
        "your KYC details and upload the requested documents for review.",
        "",
        "If you did not expect this email, please ignore it and contact the procurement team.",
    ]
    return "\n".join(lines) + "\n"


def issue_vendor_credentials_service(
    *, vendor: Vendor, issued_by: User, auto_activate: bool = False
) -> VendorCredentialDispatch:
    """
    Creates (or resets) the vendor's portal login with a temporary password and emails the
    credentials plus portal login link(s) to the vendor's registered email.

    By default (auto_activate=False), the account is created in an inactive state (is_active=False)
    and the dispatch is placed in the Legal Manager's verification queue (legal_verification_status=PENDING).
    Upon Legal Manager verification & approval, the user account is activated and vendor status set to ACTIVE.

    The password exists only in memory for the email; it is never stored or audited.
    Raises ValidationError on rule violations or if the email could not be delivered
    (the dispatch is still recorded as FAILED so the executive can retry).
    """
    if not can_operate_governance(issued_by):
        raise PermissionDenied("Only Procurement Executives/Managers can issue vendor credentials.")
    if vendor.status in CREDENTIAL_ISSUE_BLOCKED:
        raise ValidationError(
            f"Credentials cannot be issued to a {vendor.get_status_display()} vendor."
        )

    email = User.objects.normalize_email((vendor.email or '').strip())
    if not email:
        raise ValidationError(
            f"Vendor {vendor.vendor_number} has no email address. Add one (Edit info) before issuing credentials."
        )
    password = _generate_temporary_password()

    with transaction.atomic():
        existing = User.objects.select_for_update().filter(email__iexact=email).first()
        if existing and existing.vendor_id != vendor.id:
            raise ValidationError(f"A different account already uses {email}.")
        role = Role.objects.get(code=Role.VENDOR_USER)
        if existing:
            existing.set_password(password)
            existing.must_change_password = True
            existing.is_active = auto_activate
            existing.save(update_fields=["password", "must_change_password", "is_active"])
            user, action = existing, "credentials_reset"
        else:
            user = User.objects.create_user(
                email,
                password,
                role=role,
                vendor=vendor,
                must_change_password=True,
                is_active=auto_activate,
            )
            action = "credentials_created"

        legal_status = (
            VendorCredentialDispatch.LEGAL_STATUS_APPROVED
            if auto_activate
            else VendorCredentialDispatch.LEGAL_STATUS_PENDING
        )
        dispatch = VendorCredentialDispatch.objects.create(
            vendor=vendor,
            user=user,
            sent_to=email,
            created_by=issued_by,
            legal_verification_status=legal_status,
            legal_verified_by=issued_by if auto_activate else None,
            legal_verified_at=timezone.now() if auto_activate else None,
        )
        create_audit_log_service(
            actor=issued_by,
            action=AuditLog.ACTION_UPDATE,
            target_model="Vendor",
            target_object_id=str(vendor.id),
            new_state={
                "event": action,
                "login_id": email,
                "vendor_number": vendor.vendor_number,
                "legal_verification_status": legal_status,
                "user_active": auto_activate,
            },
        )

    try:
        # Brevo REST API (HTTPS/443) when a real API key (xkeysib-...) is configured - needed on
        # hosts such as Render that block SMTP port 587. Otherwise use Django's mail backend
        # (Brevo SMTP relay with the xsmtpsib-... key).
        api_key = getattr(settings, "BREVO_API_KEY", "")
        subject = f"ProcureSphere 360 - Your Supplier Portal Access ({vendor.vendor_number})"
        text_body = _credentials_email_body(vendor=vendor, email=email, password=password)
        html_body = build_credentials_html(
            vendor_name=vendor.legal_name,
            vendor_number=vendor.vendor_number,
            email=email,
            password=password,
        )
        if api_key.startswith("xkeysib-"):
            payload = {
                "sender": {
                    "name": getattr(settings, "BREVO_SENDER_NAME", "ProcureSphere 360"),
                    "email": getattr(settings, "BREVO_SENDER_EMAIL", settings.DEFAULT_FROM_EMAIL),
                },
                "to": [{"email": email}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body,
            }
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers={
                    "api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            if not response.ok:
                raise RuntimeError(f"Brevo API error {response.status_code}: {response.text[:300]}")
        else:
            send_mail(
                subject=subject,
                message=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html_body,
                fail_silently=False,
            )
    except Exception as exc:  # HTTP / network failure: keep evidence, let the executive retry
        logger.exception("Vendor credential email failed for vendor %s", vendor.vendor_number)
        dispatch.status = VendorCredentialDispatch.STATUS_FAILED
        dispatch.error = str(exc)[:500]
        dispatch.save(update_fields=["status", "error", "updated_at"])
        raise ValidationError(
            f"Account created but the email to {email} could not be sent. "
            f"Use Resend to retry. Reason: {exc}"
        ) from exc

    dispatch.status = VendorCredentialDispatch.STATUS_SENT
    dispatch.sent_at = timezone.now()
    dispatch.save(update_fields=["status", "sent_at", "updated_at"])
    return dispatch


@transaction.atomic
def legal_validate_vendor_credentials_service(
    *, dispatch_id: str | uuid.UUID, legal_user: User, notes: str = ""
) -> VendorCredentialDispatch:
    """
    Validates and approves the vendor credentials dispatch as Legal Manager.
    Activates the vendor's user account, marks vendor as ACTIVE and EVENT ELIGIBLE.
    Verifies valid unverified KYC documents with audit records.
    Dispatches notifications to both Vendor and registering Procurement Executive.
    Records an immutable AuditLog entry.
    """
    if not can_legal_verify_vendor(legal_user):
        raise PermissionDenied(
            "Permission Denied: Only Legal Managers and Super Admins can verify and approve vendor portal credentials."
        )

    dispatch = (
        VendorCredentialDispatch.objects.select_for_update()
        .select_related("vendor", "user")
        .filter(pk=dispatch_id)
        .first()
    )
    if not dispatch:
        raise ValidationError("Vendor credential dispatch record not found.")

    if dispatch.legal_verification_status != VendorCredentialDispatch.LEGAL_STATUS_PENDING:
        raise ValidationError(
            f"Only pending credential dispatches can be approved. Current status: {dispatch.legal_verification_status}."
        )

    vendor = dispatch.vendor
    user = dispatch.user

    if vendor.status in CREDENTIAL_ISSUE_BLOCKED or vendor.status in [
        Vendor.STATUS_SUSPENDED,
        Vendor.STATUS_REJECTED,
        Vendor.STATUS_ON_HOLD,
    ]:
        raise ValidationError(
            f"Cannot approve credentials for a {vendor.get_status_display()} vendor."
        )

    eval_result = ProvisionalKYCPolicy.evaluate_vendor_kyc_compliance(vendor)
    if not eval_result["is_compliant"]:
        reasons = []
        if eval_result.get("missing_mandatory_types"):
            reasons.append(
                f"Missing mandatory documents: {', '.join(eval_result['missing_mandatory_types'])}"
            )
        if eval_result.get("unverified_mandatory_types"):
            reasons.append(
                f"Unverified mandatory documents: {', '.join(eval_result['unverified_mandatory_types'])}"
            )
        if eval_result.get("expired_documents_count"):
            reasons.append(f"{eval_result['expired_documents_count']} document(s) expired")
        if eval_result.get("rejected_count"):
            reasons.append(f"{eval_result['rejected_count']} document(s) rejected")
        if eval_result.get("action_required_count"):
            reasons.append(f"{eval_result['action_required_count']} document(s) require changes")
        detail = "; ".join(reasons) if reasons else "Mandatory compliance criteria not met."
        raise ValidationError(
            f"Cannot approve vendor credentials: KYC compliance requirements not met. {detail}"
        )

    previous_dispatch_status = dispatch.legal_verification_status

    # Update dispatch verification record
    dispatch.legal_verification_status = VendorCredentialDispatch.LEGAL_STATUS_APPROVED
    dispatch.legal_verified_by = legal_user
    dispatch.legal_verified_at = timezone.now()
    dispatch.legal_notes = (notes or "").strip()
    dispatch.save(
        update_fields=[
            "legal_verification_status",
            "legal_verified_by",
            "legal_verified_at",
            "legal_notes",
            "updated_at",
        ]
    )

    # Activate User
    if user:
        user.is_active = True
        user.save(update_fields=["is_active"])

    # Transition vendor to ACTIVE and ELIGIBLE if not suspended/blocked
    previous_vendor_status = vendor.status
    if vendor.status in [
        Vendor.STATUS_DRAFT,
        Vendor.STATUS_SUBMITTED,
        Vendor.STATUS_KYC_REVIEW,
        Vendor.STATUS_CHANGES_REQUIRED,
    ]:
        vendor.status = Vendor.STATUS_ACTIVE
        if notes:
            vendor.status_notes = notes.strip()

    vendor.is_event_eligible = True
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
    vendor.legal_approved_by = legal_user
    vendor.legal_approved_at = timezone.now()
    if notes:
        vendor.legal_approval_notes = notes.strip()

    vendor.save(
        update_fields=[
            "status",
            "status_notes",
            "is_event_eligible",
            "event_eligibility_status",
            "legal_approved_by",
            "legal_approved_at",
            "legal_approval_notes",
            "updated_at",
        ]
    )

    # Audit trail
    create_audit_log_service(
        actor=legal_user,
        action=AuditLog.ACTION_APPROVE,
        target_model="VendorCredentialDispatch",
        target_object_id=str(dispatch.id),
        previous_state={
            "legal_verification_status": previous_dispatch_status,
            "vendor_status": previous_vendor_status,
            "user_active": False,
            "is_event_eligible": False,
        },
        new_state={
            "legal_verification_status": VendorCredentialDispatch.LEGAL_STATUS_APPROVED,
            "vendor_status": vendor.status,
            "user_active": True,
            "is_event_eligible": True,
            "event_eligibility_status": Vendor.ELIGIBILITY_ELIGIBLE,
            "notes": dispatch.legal_notes,
        },
    )

    # 1. Notify Vendor: profile approved and eligible for events
    notify_vendor_users(
        vendor=vendor,
        notification_type=Notification.TYPE_KYC_REQUEST,
        title="Profile Legally Approved & Eligible for Events",
        message=(
            "Your vendor profile and KYC compliance documents have been reviewed and approved by the Legal Manager. "
            "Your account is activated and you are eligible to participate in applicable procurement events, "
            "subject to continuing compliance and document validity."
        ),
        target_url="/vendor/overview/",
    )

    # Email notification to vendor
    vendor_email = (user and user.email) or vendor.email
    if vendor_email:
        try:
            send_mail(
                subject="ProcureSphere 360 - Profile Legally Approved & Event Eligible",
                message=(
                    f"Dear {vendor.legal_name},\n\n"
                    "Your vendor profile and compliance documents have been reviewed and approved by Legal Manager.\n"
                    "Your portal account is active, and you are eligible to participate in applicable procurement events.\n\n"
                    "ProcureSphere 360 Procurement Team"
                ),
                from_email=None,
                recipient_list=[vendor_email],
                fail_silently=True,
            )
        except Exception:
            pass

    # 2. Notify Procurement Executive who registered vendor
    pe_user = vendor.created_by or dispatch.created_by
    if pe_user and pe_user.is_active:
        notify_users(
            users=[pe_user],
            notification_type=Notification.TYPE_KYC_REQUEST,
            title=f"Vendor Approved: {vendor.legal_name} is Event-Eligible",
            message=(
                f"Vendor '{vendor.legal_name}' ({vendor.vendor_number}) has been legally verified and approved by {legal_user.get_full_name() or legal_user.email}. "
                "The vendor is now ACTIVE and eligible to participate in sourcing events."
            ),
            target_url=f"/vendors/{vendor.id}/",
        )
        if pe_user.email:
            try:
                send_mail(
                    subject=f"ProcureSphere 360 - Vendor Approved: {vendor.legal_name}",
                    message=(
                        f"Dear {pe_user.get_full_name() or 'Procurement Executive'},\n\n"
                        f"Vendor '{vendor.legal_name}' ({vendor.vendor_number}) has been verified and approved by Legal Manager.\n"
                        "The vendor is now ACTIVE and eligible to participate in sourcing events.\n\n"
                        "ProcureSphere 360 System"
                    ),
                    from_email=None,
                    recipient_list=[pe_user.email],
                    fail_silently=True,
                )
            except Exception:
                pass

    return dispatch


@transaction.atomic
def legal_reject_vendor_credentials_service(
    *, dispatch_id: str | uuid.UUID, legal_user: User, notes: str
) -> VendorCredentialDispatch:
    """
    Rejects the vendor credentials dispatch as Legal Manager.
    Keeps user account deactivated (is_active=False), revokes event eligibility,
    and transitions vendor to CHANGES_REQUIRED.
    Requires reviewer notes. Dispatches notifications to Vendor and Procurement Executive.
    Records an immutable AuditLog entry.
    """
    if not can_legal_verify_vendor(legal_user):
        raise PermissionDenied(
            "Permission Denied: Only Legal Managers and Super Admins can verify vendor credentials."
        )

    if not (notes or "").strip():
        raise ValidationError("Reviewer notes are mandatory when rejecting vendor credentials.")

    dispatch = (
        VendorCredentialDispatch.objects.select_for_update()
        .select_related("vendor", "user")
        .filter(pk=dispatch_id)
        .first()
    )
    if not dispatch:
        raise ValidationError("Vendor credential dispatch record not found.")

    if dispatch.legal_verification_status != VendorCredentialDispatch.LEGAL_STATUS_PENDING:
        raise ValidationError(
            f"Only pending credential dispatches can be rejected. Current status: {dispatch.legal_verification_status}."
        )

    vendor = dispatch.vendor
    user = dispatch.user

    previous_dispatch_status = dispatch.legal_verification_status

    dispatch.legal_verification_status = VendorCredentialDispatch.LEGAL_STATUS_REJECTED
    dispatch.legal_verified_by = legal_user
    dispatch.legal_verified_at = timezone.now()
    dispatch.legal_notes = notes.strip()
    dispatch.save(
        update_fields=[
            "legal_verification_status",
            "legal_verified_by",
            "legal_verified_at",
            "legal_notes",
            "updated_at",
        ]
    )

    # Ensure user is inactive
    if user:
        user.is_active = False
        user.save(update_fields=["is_active"])

    previous_vendor_status = vendor.status
    vendor.status = Vendor.STATUS_CHANGES_REQUIRED
    vendor.is_event_eligible = False
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_INELIGIBLE
    vendor.status_notes = notes.strip()
    vendor.save(
        update_fields=[
            "status",
            "status_notes",
            "is_event_eligible",
            "event_eligibility_status",
            "updated_at",
        ]
    )

    # Audit trail
    create_audit_log_service(
        actor=legal_user,
        action=AuditLog.ACTION_REJECT,
        target_model="VendorCredentialDispatch",
        target_object_id=str(dispatch.id),
        previous_state={
            "legal_verification_status": previous_dispatch_status,
            "vendor_status": previous_vendor_status,
            "is_event_eligible": False,
        },
        new_state={
            "legal_verification_status": VendorCredentialDispatch.LEGAL_STATUS_REJECTED,
            "vendor_status": vendor.status,
            "is_event_eligible": False,
            "event_eligibility_status": Vendor.ELIGIBILITY_INELIGIBLE,
            "notes": notes.strip(),
        },
    )

    # 1. Notify Vendor: changes required with specific feedback
    if user:
        Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_KYC_REQUEST,
            title="Vendor Profile Review: Action Required",
            message=(
                f"Your vendor portal registration was reviewed by Legal Manager. Status: Changes Required. "
                f"Reviewer notes: {notes.strip()}. Please address the requested changes to proceed with event eligibility."
            ),
            target_url="/vendor/documents/",
        )

    other_users = [
        u for u in User.objects.filter(vendor=vendor, is_active=True) if not user or u.id != user.id
    ]
    if other_users:
        notify_users(
            users=other_users,
            notification_type=Notification.TYPE_KYC_REQUEST,
            title="Vendor Profile Review: Action Required",
            message=(
                f"Your vendor portal registration was reviewed by Legal Manager. Status: Changes Required. "
                f"Reviewer notes: {notes.strip()}. Please address the requested changes to proceed with event eligibility."
            ),
            target_url="/vendor/documents/",
        )

    # 2. Notify Procurement Executive
    pe_user = vendor.created_by or dispatch.created_by
    if pe_user and pe_user.is_active:
        notify_users(
            users=[pe_user],
            notification_type=Notification.TYPE_KYC_REQUEST,
            title=f"Vendor Verification Rejected: {vendor.legal_name}",
            message=(
                f"Vendor '{vendor.legal_name}' was rejected by Legal Manager {legal_user.get_full_name() or legal_user.email}. "
                f"Feedback: {notes.strip()}."
            ),
            target_url=f"/vendors/{vendor.id}/",
        )

    return dispatch


def scan_vendor_document_expirations_service(now=None) -> dict:
    """
    Idempotent scanner for vendor KYC document expirations and renewal reminders.
    - Documents with expiry_date < today: marked EXPIRED, is_verified=False.
      If mandatory, vendor event eligibility is suspended (is_event_eligible=False).
      Notifications dispatched to Vendor, Procurement Executive, and Legal Manager.
    - Documents expiring in 30, 15, or 7 days: renewal reminders dispatched.
    """
    today = (now or timezone.now()).date()
    expired_count = 0
    reminders_sent = 0
    newly_suspended_count = 0

    # 1. Process passed expirations
    expired_docs = (
        VendorDocument.objects.filter(expiry_date__lt=today)
        .exclude(status=VendorDocument.STATUS_EXPIRED)
        .select_related("vendor", "vendor__created_by")
    )

    for doc in expired_docs:
        with transaction.atomic():
            previous_status = doc.status
            doc.status = VendorDocument.STATUS_EXPIRED
            doc.is_verified = False
            doc.save(update_fields=["status", "is_verified", "updated_at"])

            VendorDocumentReviewHistory.objects.create(
                document=doc,
                reviewer=None,
                action="EXPIRED",
                notes=f"Document expired on {doc.expiry_date.isoformat()}.",
                previous_status=previous_status,
                new_status=VendorDocument.STATUS_EXPIRED,
            )

            create_audit_log_service(
                actor=None,
                action=AuditLog.ACTION_UPDATE,
                target_model="VendorDocument",
                target_object_id=str(doc.id),
                previous_state={"status": previous_status, "is_verified": True},
                new_state={
                    "status": VendorDocument.STATUS_EXPIRED,
                    "is_verified": False,
                    "reason": "Passed validity expiry date",
                },
            )

            vendor = doc.vendor
            mandatory_codes = ProvisionalKYCPolicy.get_mandatory_doc_types(vendor)
            if doc.document_type in mandatory_codes and vendor.is_event_eligible:
                vendor.is_event_eligible = False
                vendor.event_eligibility_status = Vendor.ELIGIBILITY_EXPIRED
                vendor.save(
                    update_fields=["is_event_eligible", "event_eligibility_status", "updated_at"]
                )
                newly_suspended_count += 1

                create_audit_log_service(
                    actor=None,
                    action=AuditLog.ACTION_UPDATE,
                    target_model="Vendor",
                    target_object_id=str(vendor.id),
                    previous_state={
                        "is_event_eligible": True,
                        "event_eligibility_status": Vendor.ELIGIBILITY_ELIGIBLE,
                    },
                    new_state={
                        "is_event_eligible": False,
                        "event_eligibility_status": Vendor.ELIGIBILITY_EXPIRED,
                        "reason": f"Mandatory document '{doc.title}' expired",
                    },
                )

                # Notifications
                notify_vendor_users(
                    vendor=vendor,
                    notification_type=Notification.TYPE_KYC_REQUEST,
                    title=f"KYC Document Expired: {doc.title}",
                    message=(
                        f"Mandatory document '{doc.title}' ({doc.get_document_type_display()}) expired on {doc.expiry_date}. "
                        "Your event participation eligibility has been temporarily suspended. Please upload a renewed document."
                    ),
                    target_url="/vendor/documents/",
                )

                if vendor.created_by and vendor.created_by.is_active:
                    notify_users(
                        users=[vendor.created_by],
                        notification_type=Notification.TYPE_KYC_REQUEST,
                        title=f"Compliance Alert: Document Expired for {vendor.legal_name}",
                        message=f"Mandatory KYC document '{doc.title}' for vendor '{vendor.legal_name}' has expired. Event eligibility suspended.",
                        target_url=f"/vendors/{vendor.id}/",
                    )

                legal_users = User.objects.filter(role__code=Role.LEGAL_MGR, is_active=True)
                if legal_users.exists():
                    notify_users(
                        users=legal_users,
                        notification_type=Notification.TYPE_KYC_REQUEST,
                        title=f"KYC Expiry: {vendor.legal_name}",
                        message=f"Vendor '{vendor.legal_name}' mandatory document '{doc.title}' expired. Eligibility revoked.",
                        target_url="/contracts/dashboard/",
                    )

            expired_count += 1

    # 2. Upcoming expiry reminders (30, 15, 7 days)
    for reminder_days in ProvisionalKYCPolicy.EXPIRY_REMINDER_DAYS:
        target_date = today + timezone.timedelta(days=reminder_days)
        upcoming_docs = VendorDocument.objects.filter(
            expiry_date=target_date,
            status=VendorDocument.STATUS_VERIFIED,
        ).select_related("vendor", "vendor__created_by")

        for doc in upcoming_docs:
            vendor = doc.vendor
            already_notified = Notification.objects.filter(
                recipient__in=User.objects.filter(vendor=vendor),
                notification_type=Notification.TYPE_KYC_REQUEST,
                title__contains=f"Renewal Warning ({reminder_days} Days): {doc.title}",
                created_at__date=today,
            ).exists()
            if not already_notified:
                notify_vendor_users(
                    vendor=vendor,
                    notification_type=Notification.TYPE_KYC_REQUEST,
                    title=f"Renewal Warning ({reminder_days} Days): {doc.title}",
                    message=f"Document '{doc.title}' expires in {reminder_days} days ({doc.expiry_date}). Please prepare and submit renewal documentation.",
                    target_url="/vendor/documents/",
                )
                reminders_sent += 1

    total_suspended_vendors = Vendor.objects.filter(
        event_eligibility_status__in=[Vendor.ELIGIBILITY_EXPIRED, Vendor.ELIGIBILITY_SUSPENDED],
        documents__status=VendorDocument.STATUS_EXPIRED,
    ).distinct().count()

    return {
        "expired_documents": expired_count,
        "expired_count": expired_count,
        "reminders_sent": reminders_sent,
        "suspended_vendors": newly_suspended_count,
        "total_suspended_vendors": total_suspended_vendors,
    }


@transaction.atomic
def request_vendor_changes_service(*, vendor: Vendor, reviewer: User, notes: str) -> Vendor:
    """
    Transitions Vendor to CHANGES_REQUIRED and notifies vendor with reviewer notes.
    """
    return set_vendor_status_governance_service(
        vendor=vendor,
        actor=reviewer,
        new_status=Vendor.STATUS_CHANGES_REQUIRED,
        notes=notes,
    )


@transaction.atomic
def create_vendor_contact_service(
    *,
    vendor: Vendor,
    user: User,
    data: dict,
) -> VendorContact:
    """
    Creates a new authorized vendor contact for the vendor.
    If is_primary is set, demotes other contacts of this vendor.
    """
    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is suspended. Contact creation is frozen."
        )

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    designation = (data.get("designation") or "").strip()
    is_primary = bool(data.get("is_primary", False))

    if not first_name or not last_name or not email:
        raise ValidationError("First name, last name, and email are required.")

    if is_primary:
        vendor.contacts.filter(is_primary=True).update(is_primary=False)

    contact = VendorContact.objects.create(
        vendor=vendor,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        designation=designation,
        is_primary=is_primary,
    )

    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_CREATE,
        target_model="VendorContact",
        target_object_id=str(contact.id),
        new_state={
            "vendor": vendor.legal_name,
            "name": f"{contact.first_name} {contact.last_name}",
            "email": contact.email,
            "designation": contact.designation,
            "is_primary": contact.is_primary,
        },
    )
    return contact


@transaction.atomic
def update_vendor_contact_service(
    *,
    contact: VendorContact,
    vendor: Vendor,
    user: User,
    data: dict,
) -> VendorContact:
    """
    Updates an authorized vendor contact.
    Enforces strict vendor-level isolation (contact must belong to vendor).
    """
    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is suspended. Contact updates are frozen."
        )

    if contact.vendor_id != vendor.id:
        raise PermissionDenied(
            "You do not have permission to modify a contact belonging to another vendor."
        )

    previous_state = {
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "email": contact.email,
        "phone": contact.phone,
        "designation": contact.designation,
        "is_primary": contact.is_primary,
    }

    if "first_name" in data:
        contact.first_name = (data["first_name"] or "").strip()
    if "last_name" in data:
        contact.last_name = (data["last_name"] or "").strip()
    if "email" in data:
        contact.email = (data["email"] or "").strip()
    if "phone" in data:
        contact.phone = (data["phone"] or "").strip()
    if "designation" in data:
        contact.designation = (data["designation"] or "").strip()

    if "is_primary" in data:
        is_primary = bool(data["is_primary"])
        if is_primary:
            vendor.contacts.exclude(pk=contact.pk).filter(is_primary=True).update(is_primary=False)
        contact.is_primary = is_primary

    contact.save()

    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="VendorContact",
        target_object_id=str(contact.id),
        previous_state=previous_state,
        new_state={
            "vendor": vendor.legal_name,
            "name": f"{contact.first_name} {contact.last_name}",
            "email": contact.email,
            "designation": contact.designation,
            "is_primary": contact.is_primary,
        },
    )
    return contact


@transaction.atomic
def delete_vendor_contact_service(
    *,
    contact: VendorContact,
    vendor: Vendor,
    user: User,
) -> None:
    """
    Deletes an authorized vendor contact.
    Enforces strict vendor-level isolation.
    """
    if vendor.status == Vendor.STATUS_SUSPENDED:
        raise ValidationError(
            f"Vendor '{vendor.legal_name}' is suspended. Contact deletion is frozen."
        )

    if contact.vendor_id != vendor.id:
        raise PermissionDenied(
            "You do not have permission to delete a contact belonging to another vendor."
        )

    contact_info = {
        "vendor": vendor.legal_name,
        "name": f"{contact.first_name} {contact.last_name}",
        "email": contact.email,
    }
    contact_id = str(contact.id)
    contact.delete()

    create_audit_log_service(
        actor=user,
        action=AuditLog.ACTION_DELETE,
        target_model="VendorContact",
        target_object_id=contact_id,
        previous_state=contact_info,
        new_state=None,
    )

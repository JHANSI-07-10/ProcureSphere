import logging
import secrets
import string
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log_service
from apps.core.validators import validate_file_upload
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
) -> Vendor:
    """
    Registers a new vendor in DRAFT status with unique vendor number.
    """
    if Vendor.objects.filter(tax_identification_number=tax_identification_number).exists():
        raise ValidationError(f"Vendor with tax ID '{tax_identification_number}' already exists.")

    vendor_count = Vendor.objects.count() + 1
    vendor_number = f"VND-{timezone.now().strftime('%Y')}-{vendor_count:05d}"

    vendor = Vendor.objects.create(
        legal_name=legal_name,
        trade_name=trade_name,
        vendor_number=vendor_number,
        tax_identification_number=tax_identification_number,
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
        },
    )

    return vendor


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


@transaction.atomic
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
def verify_vendor_document_service(*, document: VendorDocument, verifier: User) -> VendorDocument:
    """
    Marks a KYC document as verified (delegates to verify_vendor_document_decision_service).
    """
    if document.is_verified:
        raise ValidationError("Document is already verified.")
    return verify_vendor_document_decision_service(
        document=document, reviewer=verifier, decision="VERIFY", notes="Verified by governance reviewer."
    )


@transaction.atomic
def approve_vendor_service(*, vendor: Vendor, manager: User, notes: str = "") -> Vendor:
    """
    Approves Vendor and transitions state from KYC_REVIEW to APPROVED and ACTIVE.
    """
    if vendor.status not in [Vendor.STATUS_SUBMITTED, Vendor.STATUS_KYC_REVIEW]:
        raise ValidationError(f"Cannot approve vendor in status '{vendor.status}'.")

    # Ensure documents are uploaded
    if not vendor.documents.exists():
        raise ValidationError("Cannot approve vendor with no uploaded KYC documents.")

    previous_status = vendor.status
    vendor.status = Vendor.STATUS_ACTIVE
    vendor.status_notes = notes
    vendor.save(update_fields=["status", "status_notes", "updated_at"])

    AuditLog.objects.create(
        actor=manager,
        action=AuditLog.ACTION_APPROVE,
        target_model="Vendor",
        target_object_id=str(vendor.id),
        previous_state={"status": previous_status},
        new_state={"status": vendor.status, "notes": notes},
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
    Vendor self-service profile update: updates trade name, address, phone, and banking info.
    Legal name, TIN, vendor number, and category are locked and require governance approval.
    """
    previous_state = {
        "trade_name": vendor.trade_name,
        "address": vendor.address,
        "phone": vendor.phone,
        "bank_name": vendor.bank_name,
        "bank_account_number": vendor.bank_account_number,
        "bank_routing_code": vendor.bank_routing_code,
    }

    if "trade_name" in data:
        vendor.trade_name = data["trade_name"]
    if "address" in data:
        vendor.address = data["address"]
    if "phone" in data:
        vendor.phone = data["phone"]
    if "bank_name" in data:
        vendor.bank_name = data["bank_name"]
    if "bank_account_number" in data:
        vendor.bank_account_number = data["bank_account_number"]
    if "bank_routing_code" in data:
        vendor.bank_routing_code = data["bank_routing_code"]

    vendor.save(
        update_fields=[
            "trade_name",
            "address",
            "phone",
            "bank_name",
            "bank_account_number",
            "bank_routing_code",
            "updated_at",
        ]
    )

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
        },
    )
    return vendor


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
    Uploads a KYC or compliance document for a vendor.
    Links replaces_document if specified (or automatically associates prior document of same type).
    Calculates document version. Never auto-verifies on upload.
    """
    if document_type not in dict(VendorDocument.DOC_TYPE_CHOICES):
        raise ValidationError(f"Invalid document type '{document_type}'.")
    validate_file_upload(file)
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
        vendor=vendor,
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
            "vendor": vendor.legal_name,
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

    email = User.objects.normalize_email(vendor.email)
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
        send_mail(
            subject=f"ProcureSphere 360 - Your Supplier Portal Access ({vendor.vendor_number})",
            message=_credentials_email_body(vendor=vendor, email=email, password=password),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=build_credentials_html(
                vendor_name=vendor.legal_name,
                vendor_number=vendor.vendor_number,
                email=email,
                password=password,
            ),
            fail_silently=False,
        )
    except Exception as exc:  # SMTP / network failure: keep evidence, let the executive retry
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

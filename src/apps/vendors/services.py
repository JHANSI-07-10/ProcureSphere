import logging
import secrets
import string

import requests

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log_service
from apps.core.validators import validate_file_upload
from apps.notifications.models import Notification
from apps.notifications.services import notify_vendor_users

from .brevo_email import build_credentials_html
from .models import (
    Vendor,
    VendorCategory,
    VendorContact,
    VendorCredentialDispatch,
    VendorDocument,
    VendorRiskRecord,
)
from .permissions import can_operate_governance

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
    vendor.save(update_fields=[
        "legal_name", "trade_name", "email", "phone", "address",
        "category", "bank_name", "bank_account_number", "bank_routing_code", "updated_at",
    ])

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
        new_state={"deleted": True},
    )
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


@transaction.atomic
def verify_vendor_document_service(*, document: VendorDocument, verifier: User) -> VendorDocument:
    """
    Marks a KYC document as verified.
    """
    if document.is_verified:
        raise ValidationError("Document is already verified.")
    document.is_verified = True
    document.verified_by = verifier
    document.save(update_fields=["is_verified", "verified_by", "updated_at"])
    create_audit_log_service(
        actor=verifier,
        action=AuditLog.ACTION_VERIFY,
        target_model="VendorDocument",
        target_object_id=document.id,
        previous_state={"is_verified": False},
        new_state={"is_verified": True, "vendor": document.vendor.legal_name},
    )
    return document


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
) -> VendorDocument:
    """
    Uploads a KYC or compliance document for a vendor.
    """
    if document_type not in dict(VendorDocument.DOC_TYPE_CHOICES):
        raise ValidationError(f"Invalid document type '{document_type}'.")
    validate_file_upload(file)
    if not title or not title.strip():
        title = getattr(file, "name", "Vendor Document")

    doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=document_type,
        title=title.strip(),
        file=file,
        expiry_date=expiry_date,
        is_verified=False,
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
    *, vendor: Vendor, issued_by: User
) -> VendorCredentialDispatch:
    """
    Creates (or resets) the vendor's portal login with a temporary password and emails the
    credentials plus portal login link(s) to the vendor's registered email.

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
            existing.is_active = True
            existing.save(update_fields=["password", "must_change_password", "is_active"])
            user, action = existing, "credentials_reset"
        else:
            user = User.objects.create_user(
                email, password, role=role, vendor=vendor, must_change_password=True
            )
            action = "credentials_created"
        dispatch = VendorCredentialDispatch.objects.create(
            vendor=vendor, user=user, sent_to=email, created_by=issued_by
        )
        create_audit_log_service(
            actor=issued_by,
            action=AuditLog.ACTION_UPDATE,
            target_model="Vendor",
            target_object_id=str(vendor.id),
            new_state={"event": action, "login_id": email, "vendor_number": vendor.vendor_number},
        )

    try:
        # --- Brevo Transactional HTTP API (HTTPS/443 — works on Render) ---
        # Django's SMTP backend on port 587 is blocked by Render's network.
        api_key = getattr(settings, "BREVO_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "BREVO_API_KEY is not configured. Add it to your environment variables."
            )
        payload = {
            "sender": {
                "name": getattr(settings, "BREVO_SENDER_NAME", "ProcureSphere 360"),
                "email": getattr(settings, "BREVO_SENDER_EMAIL", settings.DEFAULT_FROM_EMAIL),
            },
            "to": [{"email": email}],
            "subject": f"ProcureSphere 360 - Your Supplier Portal Access ({vendor.vendor_number})",
            "htmlContent": build_credentials_html(
                vendor_name=vendor.legal_name,
                vendor_number=vendor.vendor_number,
                email=email,
                password=password,
            ),
            "textContent": _credentials_email_body(vendor=vendor, email=email, password=password),
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
            raise RuntimeError(
                f"Brevo API error {response.status_code}: {response.text[:300]}"
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
        raise PermissionDenied("You do not have permission to modify a contact belonging to another vendor.")

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
        raise PermissionDenied("You do not have permission to delete a contact belonging to another vendor.")

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

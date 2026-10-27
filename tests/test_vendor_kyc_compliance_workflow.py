"""
Comprehensive End-to-End Tests for Vendor KYC Compliance, Legal Approval & Event Eligibility.

Covers:
1. Document verification decisions (VERIFY, REJECT, REQUEST_CORRECTION) with VendorDocumentReviewHistory and AuditLog.
2. Legal Manager approval & rejection workflows with RBAC enforcement.
3. Dual notification routing to Vendor and registering Procurement Executive.
4. Expiry monitoring, idempotency, and automated event eligibility suspension.
5. Integration with Sourcing Event invitations and bidding eligibility enforcement.
"""

import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.sourcing.models import BidInvite, SourcingEvent, VendorBid
from apps.sourcing.services import (
    create_sourcing_event_service,
    invite_vendors_to_event_service,
    publish_sourcing_event_service,
    save_draft_bid_service,
    submit_vendor_bid_service,
    validate_bid_service,
)
from apps.vendors.kyc_policy import (
    ProvisionalKYCPolicy,
    evaluate_vendor_kyc_compliance,
)
from apps.vendors.models import (
    Vendor,
    VendorCategory,
    VendorCredentialDispatch,
    VendorDocument,
    VendorDocumentReviewHistory,
)
from apps.vendors.services import (
    issue_vendor_credentials_service,
    legal_reject_vendor_credentials_service,
    legal_validate_vendor_credentials_service,
    register_vendor_service,
    scan_vendor_document_expirations_service,
    upload_vendor_document_service,
    verify_vendor_document_decision_service,
)
from apps.contracts.selectors import (
    get_pending_documents_for_review_qs,
    get_pending_vendor_verifications_qs,
)
from apps.sourcing.services import (
    _assert_vendor_awardable,
    _invite_error,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_environment(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "ProcureSphere <no-reply@procuresphere.test>"
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def test_setup(db, db_roles):
    """Sets up cross-role users, category, and registered vendor."""
    proc_exec = User.objects.create_user(
        email="pe.officer@test.local",
        password="Password123!",
        first_name="Proc",
        last_name="Exec",
        role=db_roles[Role.PROC_EXEC],
    )
    legal_mgr = User.objects.create_user(
        email="legal.counsel@test.local",
        password="Password123!",
        first_name="Legal",
        last_name="Manager",
        role=db_roles[Role.LEGAL_MGR],
    )
    stores_user = User.objects.create_user(
        email="stores.user@test.local",
        password="Password123!",
        first_name="Stores",
        last_name="Receiver",
        role=db_roles[Role.STORES_RECEIVER],
    )
    category = VendorCategory.objects.create(name="Cloud Solutions", code="IT")
    vendor = register_vendor_service(
        legal_name="OmniCloud Infrastructure Corp",
        trade_name="OmniCloud",
        tax_identification_number="TIN-OMNI-7788",
        category=category,
        email="compliance@omnicloud.test",
        address="100 Tech Park, Silicon Way",
        created_by_user=proc_exec,
    )
    return {
        "proc_exec": proc_exec,
        "legal_mgr": legal_mgr,
        "stores_user": stores_user,
        "vendor": vendor,
        "category": category,
    }


def test_document_verification_decision_service(test_setup):
    """Verify document-level decisions: VERIFY, REJECT, and REQUEST_CORRECTION."""
    setup = test_setup
    vendor = setup["vendor"]
    legal_mgr = setup["legal_mgr"]

    doc_pdf = SimpleUploadedFile("tax_cert.pdf", b"%PDF-1.4 tax clearance", content_type="application/pdf")
    doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance Certificate",
        file=doc_pdf,
        status=VendorDocument.STATUS_PENDING,
        is_verified=False,
    )

    # 1. VERIFY
    verified_doc = verify_vendor_document_decision_service(
        document_id=doc.id,
        reviewer=legal_mgr,
        decision="VERIFY",
        reviewer_notes="Tax clearance valid until year-end",
    )
    assert verified_doc.status == VendorDocument.STATUS_VERIFIED
    assert verified_doc.is_verified is True
    assert verified_doc.verified_by == legal_mgr
    assert verified_doc.reviewer_notes == "Tax clearance valid until year-end"

    # Review history created
    history = VendorDocumentReviewHistory.objects.filter(document=doc).first()
    assert history is not None
    assert history.action == "VERIFY"
    assert history.reviewer == legal_mgr

    # 2. REQUEST_CORRECTION
    correction_doc = verify_vendor_document_decision_service(
        document_id=doc.id,
        reviewer=legal_mgr,
        decision="REQUEST_CORRECTION",
        reviewer_notes="Page 2 stamp is smudged; please re-upload clear copy.",
    )
    assert correction_doc.status == VendorDocument.STATUS_CHANGES_REQUIRED
    assert correction_doc.is_verified is False
    assert VendorDocumentReviewHistory.objects.filter(document=doc).count() == 2

    # 3. REJECT
    rejected_doc = verify_vendor_document_decision_service(
        document_id=doc.id,
        reviewer=legal_mgr,
        decision="REJECT",
        reviewer_notes="Document expired and unverifiable with revenue authority.",
    )
    assert rejected_doc.status == VendorDocument.STATUS_REJECTED
    assert rejected_doc.is_verified is False
    assert VendorDocumentReviewHistory.objects.filter(document=doc).count() == 3


def test_legal_document_decision_rbac_and_view(client, test_setup):
    """Ensure non-legal roles cannot record document decisions via HTTP endpoint."""
    setup = test_setup
    vendor = setup["vendor"]
    legal_mgr = setup["legal_mgr"]
    stores_user = setup["stores_user"]

    doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Articles of Incorporation",
        file=SimpleUploadedFile("inc.pdf", b"content", content_type="application/pdf"),
        status=VendorDocument.STATUS_PENDING,
    )

    url = reverse("legal_document_decision", kwargs={"document_id": doc.id})

    # Unauthorized role attempt (Stores Receiver)
    client.force_login(stores_user)
    resp = client.post(url, {"decision": "VERIFY", "reviewer_notes": "Looks fine"})
    assert resp.status_code == 302
    doc.refresh_from_db()
    assert doc.status == VendorDocument.STATUS_PENDING

    # Authorized Legal Manager
    client.force_login(legal_mgr)
    resp = client.post(url, {"decision": "VERIFY", "reviewer_notes": "Corporate registry confirmed"})
    assert resp.status_code == 302
    doc.refresh_from_db()
    assert doc.status == VendorDocument.STATUS_VERIFIED


def test_legal_approval_sets_event_eligibility_and_dual_notifications(test_setup):
    """
    Legal approval sets vendor active, is_event_eligible=True, and sends notifications
    to BOTH the vendor user and the registering Procurement Executive.
    """
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    # Issue credentials -> PENDING
    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_PENDING
    assert vendor.is_event_eligible is False
    assert vendor.event_eligibility_status == Vendor.ELIGIBILITY_PENDING

    # Create all mandatory KYC documents (CERT, TAX, BANK) verified
    for doc_type, title, filename in [
        (VendorDocument.DOC_TYPE_CERT, "Incorporation Certificate", "cert.pdf"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate", "tax.pdf"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document", "bank.pdf"),
    ]:
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=doc_type,
            title=title,
            file=SimpleUploadedFile(filename, b"data", content_type="application/pdf"),
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

    mail.outbox.clear()

    # Legal Manager validates and approves
    approved_dispatch = legal_validate_vendor_credentials_service(
        dispatch_id=dispatch.id,
        legal_user=legal_mgr,
        notes="Verified against state database. Approved for event participation.",
    )
    assert approved_dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_APPROVED

    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    assert vendor.is_event_eligible is True
    assert vendor.event_eligibility_status == Vendor.ELIGIBILITY_ELIGIBLE
    assert vendor.legal_approved_by == legal_mgr
    assert vendor.legal_approved_at is not None

    # Check Dual Notifications
    # 1. Vendor notification
    vendor_notif = Notification.objects.filter(
        recipient=approved_dispatch.user,
        notification_type=Notification.TYPE_KYC_REQUEST,
    ).first()
    assert vendor_notif is not None
    assert "eligible to participate in applicable procurement events" in vendor_notif.message

    # 2. Registering Procurement Executive notification
    pe_notif = Notification.objects.filter(
        recipient=proc_exec,
        notification_type=Notification.TYPE_KYC_REQUEST,
    ).first()
    assert pe_notif is not None
    assert vendor.legal_name in pe_notif.message
    assert "eligible to participate in sourcing events" in pe_notif.message

    # Check email notifications sent
    assert len(mail.outbox) >= 2


def test_legal_rejection_revokes_eligibility_and_notifies(test_setup):
    """Rejection sets changes required, revokes eligibility, and notifies both parties."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    rejected_dispatch = legal_reject_vendor_credentials_service(
        dispatch_id=dispatch.id,
        legal_user=legal_mgr,
        notes="Sanctions check flagged incomplete beneficial ownership disclosure.",
    )
    assert rejected_dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_REJECTED

    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_CHANGES_REQUIRED
    assert vendor.is_event_eligible is False
    assert vendor.event_eligibility_status == Vendor.ELIGIBILITY_INELIGIBLE

    # Vendor login blocked
    assert dispatch.user.is_active is False

    # Notifications dispatched to both
    assert Notification.objects.filter(recipient=dispatch.user).exists()
    assert Notification.objects.filter(recipient=proc_exec).exists()


def test_document_expiry_monitoring_and_eligibility_suspension(test_setup):
    """
    When mandatory documents expire, scan_vendor_document_expirations_service
    flags the document, suspends vendor event eligibility, and emits notifications idempotently.
    """
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    # First approve the vendor with all mandatory documents verified
    for doc_type, title, filename in [
        (VendorDocument.DOC_TYPE_CERT, "Incorporation Certificate", "cert.pdf"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate", "tax.pdf"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document", "bank.pdf"),
    ]:
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=doc_type,
            title=title,
            file=SimpleUploadedFile(filename, b"data", content_type="application/pdf"),
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)
    legal_validate_vendor_credentials_service(
        dispatch_id=dispatch.id, legal_user=legal_mgr, notes="Approved initially"
    )
    vendor.refresh_from_db()
    assert vendor.is_event_eligible is True

    # Upload mandatory document with past expiry date
    yesterday = timezone.now().date() - timedelta(days=1)
    expired_doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance Certificate",
        file=SimpleUploadedFile("tax.pdf", b"data", content_type="application/pdf"),
        issue_date=yesterday - timedelta(days=365),
        expiry_date=yesterday,
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )

    # Run expiration scan
    summary1 = scan_vendor_document_expirations_service()
    assert summary1["expired_documents"] >= 1
    assert summary1["suspended_vendors"] >= 1

    expired_doc.refresh_from_db()
    assert expired_doc.status == VendorDocument.STATUS_EXPIRED
    assert expired_doc.is_verified is False

    vendor.refresh_from_db()
    assert vendor.is_event_eligible is False
    assert vendor.event_eligibility_status == Vendor.ELIGIBILITY_EXPIRED

    # AuditLog recorded
    assert AuditLog.objects.filter(
        target_model="Vendor",
        target_object_id=str(vendor.id),
        new_state__event_eligibility_status="EXPIRED",
    ).exists()

    # Idempotency check: running scan again does not double-suspend or re-flag
    summary2 = scan_vendor_document_expirations_service()
    assert summary2["suspended_vendors"] == 0


def test_sourcing_event_eligibility_enforcement(test_setup):
    """
    Verify that ineligible vendors cannot be invited, cannot prepare bids,
    and cannot submit bids in the sourcing workflow.
    """
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]

    # Vendor is ACTIVE but NOT event eligible
    vendor.status = Vendor.STATUS_ACTIVE
    vendor.is_event_eligible = False
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_INELIGIBLE
    vendor.save()

    # Create sourcing event
    now = timezone.now()
    event = create_sourcing_event_service(
        title="Cloud Storage Framework 2026",
        description="Comprehensive Cloud Storage Sourcing Event",
        event_type=SourcingEvent.TYPE_RFQ,
        created_by_user=proc_exec,
        bid_start_date=now - timedelta(hours=1),
        bid_end_date=now + timedelta(days=10),
    )

    # 1. Invitation blocked with ValidationError
    with pytest.raises(ValidationError, match="not legally eligible"):
        invite_vendors_to_event_service(
            event=event,
            vendor_ids=[vendor.id],
            invited_by=proc_exec,
        )
    assert not BidInvite.objects.filter(event=event, vendor=vendor).exists()

    # Grant event eligibility
    vendor.is_event_eligible = True
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
    vendor.save()

    # Invitation succeeds
    results2 = invite_vendors_to_event_service(
        event=event,
        vendor_ids=[vendor.id],
        invited_by=proc_exec,
    )
    assert len(results2) == 1
    assert results2[0].vendor == vendor
    assert BidInvite.objects.filter(event=event, vendor=vendor).exists()

    # Publish event
    event = publish_sourcing_event_service(event=event, user=proc_exec)
    event.status = SourcingEvent.STATUS_BID_WINDOW
    event.save()

    # Revoke eligibility mid-way (e.g. document expired)
    vendor.is_event_eligible = False
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_SUSPENDED
    vendor.save()

    # 2. Saving draft bid blocked
    vendor_user = User.objects.create_user(
        email="omni.bidding@test.local",
        password="Password123!",
        role=setup["proc_exec"].role,
    )
    with pytest.raises(ValidationError, match="not legally eligible to participate in events"):
        save_draft_bid_service(
            event=event,
            vendor=vendor,
            user=vendor_user,
            proposal_summary="Technical bid summary",
        )

    # 3. Submitting bid blocked
    with pytest.raises(ValidationError, match="not legally eligible to participate in events"):
        submit_vendor_bid_service(
            event=event,
            vendor=vendor,
            line_items=[{"quantity": 1, "quoted_unit_price": 500, "item_description": "Storage"}],
            submitted_by_user=vendor_user,
        )


# ==============================================================================
# FOCUSED TARGETED REGRESSION TEST SUITE (F-01 THROUGH F-09)
# ==============================================================================


def test_approval_blocked_when_mandatory_documents_missing(test_setup):
    """F-01: Approval must be rejected with ValidationError when mandatory documents are missing."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    # 0 documents uploaded -> should fail with clear ValidationError
    with pytest.raises(ValidationError) as exc_info:
        legal_validate_vendor_credentials_service(
            dispatch_id=dispatch.id,
            legal_user=legal_mgr,
            notes="Attempting approval without docs",
        )
    assert "KYC compliance requirements not met" in str(exc_info.value)
    assert "Missing mandatory documents" in str(exc_info.value)

    # State must remain untouched (atomic guarantee)
    dispatch.refresh_from_db()
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_PENDING
    vendor.refresh_from_db()
    assert vendor.is_event_eligible is False
    assert dispatch.user.is_active is False


def test_approval_blocked_when_mandatory_documents_unverified(test_setup):
    """F-01: Approval must be rejected when mandatory documents exist but are unverified."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    # Upload all mandatory documents, but in STATUS_PENDING with is_verified=False
    for doc_type, title, filename in [
        (VendorDocument.DOC_TYPE_CERT, "Incorporation Certificate", "cert.pdf"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate", "tax.pdf"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document", "bank.pdf"),
    ]:
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=doc_type,
            title=title,
            file=SimpleUploadedFile(filename, b"content", content_type="application/pdf"),
            status=VendorDocument.STATUS_PENDING,
            is_verified=False,
        )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    with pytest.raises(ValidationError) as exc_info:
        legal_validate_vendor_credentials_service(
            dispatch_id=dispatch.id,
            legal_user=legal_mgr,
        )
    assert "Unverified mandatory documents" in str(exc_info.value)

    # F-02: Auto-verify loop is removed — documents MUST remain unverified
    for doc in vendor.documents.all():
        assert doc.is_verified is False
        assert doc.status == VendorDocument.STATUS_PENDING


def test_rejected_documents_remain_rejected_after_attempted_approval(test_setup):
    """F-02: Never convert a rejected document into a verified document on attempted approval."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    doc_cert = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Certificate",
        file=SimpleUploadedFile("cert.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )
    doc_tax = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance Certificate",
        file=SimpleUploadedFile("tax.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_REJECTED,
        is_verified=False,
        reviewer_notes="Fraudulent registration stamp detected.",
    )
    doc_bank = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_BANK,
        title="Bank Verification Document",
        file=SimpleUploadedFile("bank.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    with pytest.raises(ValidationError) as exc_info:
        legal_validate_vendor_credentials_service(dispatch_id=dispatch.id, legal_user=legal_mgr)
    assert "KYC compliance requirements not met" in str(exc_info.value)

    # Document MUST remain REJECTED and unverified
    doc_tax.refresh_from_db()
    assert doc_tax.status == VendorDocument.STATUS_REJECTED
    assert doc_tax.is_verified is False
    assert doc_tax.reviewer_notes == "Fraudulent registration stamp detected."


def test_expired_documents_prevent_approval_where_required_by_policy(test_setup):
    """F-01: Expired documents prevent credential approval."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    yesterday = timezone.now().date() - timedelta(days=2)
    VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Certificate",
        file=SimpleUploadedFile("cert.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )
    VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance Certificate",
        file=SimpleUploadedFile("tax.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
        expiry_date=yesterday,
    )
    VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_BANK,
        title="Bank Verification Document",
        file=SimpleUploadedFile("bank.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    with pytest.raises(ValidationError) as exc_info:
        legal_validate_vendor_credentials_service(dispatch_id=dispatch.id, legal_user=legal_mgr)
    assert "document(s) expired" in str(exc_info.value).lower()


def test_authorized_document_decisions_and_review_history_intact(test_setup):
    """F-02: Document-level decision service records review history and enforces authorization."""
    setup = test_setup
    vendor = setup["vendor"]
    legal_mgr = setup["legal_mgr"]
    stores_user = setup["stores_user"]

    doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Cert",
        file=SimpleUploadedFile("cert.pdf", b"content", content_type="application/pdf"),
        status=VendorDocument.STATUS_PENDING,
    )

    # Unauthorized caller
    with pytest.raises(PermissionDenied):
        verify_vendor_document_decision_service(
            document=doc, reviewer=stores_user, decision="VERIFY"
        )

    # Authorized caller
    verify_vendor_document_decision_service(
        document=doc, reviewer=legal_mgr, decision="VERIFY", notes="Verified by Legal Counsel"
    )
    doc.refresh_from_db()
    assert doc.status == VendorDocument.STATUS_VERIFIED
    assert doc.is_verified is True
    assert doc.verified_by == legal_mgr

    history = VendorDocumentReviewHistory.objects.filter(document=doc).first()
    assert history is not None
    assert history.action == "VERIFY"
    assert history.reviewer == legal_mgr
    assert history.notes == "Verified by Legal Counsel"


def test_both_document_download_routes_reverse_and_enforce_auth(client, test_setup, db_roles):
    """F-03: Both document download routes reverse cleanly and enforce proper authorization."""
    setup = test_setup
    vendor = setup["vendor"]
    legal_mgr = setup["legal_mgr"]

    doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Certificate",
        file=SimpleUploadedFile("cert.pdf", b"%PDF-1.4 sample content", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )

    # 1. Internal / Governance download route reverses
    internal_url = reverse("vendor_document_download", kwargs={"vendor_id": vendor.id, "doc_id": doc.id})
    assert internal_url == f"/vendors/{vendor.id}/documents/{doc.id}/download/"

    # 2. Vendor Portal download route reverses
    portal_url = reverse("vendor_portal_document_download", kwargs={"doc_type": "kyc", "doc_id": doc.id})
    assert portal_url == f"/vendor/documents/download/kyc/{doc.id}/"

    # Internal route requires governance / legal role
    client.force_login(legal_mgr)
    resp = client.get(internal_url)
    assert resp.status_code == 200

    # Vendor portal route enforces vendor isolation
    vendor_user = User.objects.create_user(
        email="vendor.portal.user@test.local",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor,
    )
    client.force_login(vendor_user)
    resp = client.get(portal_url)
    assert resp.status_code == 200

    # Cross-vendor unauthorized access blocked
    other_vendor = Vendor.objects.create(
        legal_name="Other Vendor Corp",
        vendor_number="VND-OTHER-99",
        category=setup["category"],
        tax_identification_number="TIN-OTHER-99",
        address="Other City",
    )
    other_user = User.objects.create_user(
        email="other.vendor.user@test.local",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=other_vendor,
    )
    client.force_login(other_user)
    resp = client.get(portal_url)
    assert resp.status_code == 403


def test_replacement_documents_appear_in_legal_review_queue(test_setup):
    """F-04: Replacement documents enter the Legal review queue even when no dispatch is pending."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    # Initially approve vendor
    for doc_type, title, filename in [
        (VendorDocument.DOC_TYPE_CERT, "Incorporation Certificate", "cert.pdf"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate", "tax.pdf"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document", "bank.pdf"),
    ]:
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=doc_type,
            title=title,
            file=SimpleUploadedFile(filename, b"data", content_type="application/pdf"),
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)
    legal_validate_vendor_credentials_service(
        dispatch_id=dispatch.id, legal_user=legal_mgr, notes="Initial approval"
    )
    dispatch.refresh_from_db()
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_APPROVED

    original_tax_doc = vendor.documents.get(document_type=VendorDocument.DOC_TYPE_TAX)

    # Vendor uploads replacement document
    vendor_user = dispatch.user
    replacement_file = SimpleUploadedFile("tax_renewed.pdf", b"new data", content_type="application/pdf")
    replacement_doc = upload_vendor_document_service(
        vendor=vendor,
        user=vendor_user,
        file=replacement_file,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance Certificate 2026-2027",
        replaces_document=original_tax_doc,
    )
    assert replacement_doc.status == VendorDocument.STATUS_PENDING
    assert replacement_doc.is_verified is False
    assert replacement_doc.version == 2
    assert replacement_doc.replaces_document == original_tax_doc

    # Check Legal Manager review queue
    pending_items = get_pending_vendor_verifications_qs()
    vendor_ids_in_queue = [item.vendor.id for item in pending_items]
    assert vendor.id in vendor_ids_in_queue

    # Check pending documents selector
    pending_docs = get_pending_documents_for_review_qs()
    assert replacement_doc in list(pending_docs)


def test_eligibility_restoration_requires_complete_compliance_and_authorization(test_setup):
    """F-04: Event eligibility is restored ONLY when all mandatory documents are compliant."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    # Setup active vendor with 3 documents, but tax doc is rejected
    doc_cert = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Certificate",
        file=SimpleUploadedFile("cert.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )
    doc_bank = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_BANK,
        title="Bank Verification Document",
        file=SimpleUploadedFile("bank.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_VERIFIED,
        is_verified=True,
    )
    doc_tax_rejected = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Certificate Old",
        file=SimpleUploadedFile("tax_old.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_REJECTED,
        is_verified=False,
    )

    vendor.status = Vendor.STATUS_ACTIVE
    vendor.is_event_eligible = False
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_INELIGIBLE
    vendor.save()

    User.objects.create_user(
        email="vendor.portal.user@omnicloud.test",
        password="Password123!",
        vendor=vendor,
        role=Role.objects.get(code=Role.VENDOR_USER),
    )

    # Upload non-mandatory document and verify it -> eligibility NOT restored
    doc_other = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_OTHER,
        title="ISO Certification",
        file=SimpleUploadedFile("iso.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_PENDING,
        is_verified=False,
    )
    verify_vendor_document_decision_service(
        document=doc_other, reviewer=legal_mgr, decision="VERIFY", notes="Verified ISO"
    )
    vendor.refresh_from_db()
    assert vendor.is_event_eligible is False

    # Upload replacement mandatory tax document
    doc_tax_new = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Certificate New",
        file=SimpleUploadedFile("tax_new.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_PENDING,
        is_verified=False,
        replaces_document=doc_tax_rejected,
    )

    # Delete or replace the rejected document so full compliance is achieved upon verification
    doc_tax_rejected.status = VendorDocument.STATUS_EXPIRED
    doc_tax_rejected.save()

    mail.outbox.clear()

    # Verify the new tax document
    verify_vendor_document_decision_service(
        document=doc_tax_new, reviewer=legal_mgr, decision="VERIFY", notes="Tax verified"
    )

    vendor.refresh_from_db()
    assert vendor.is_event_eligible is True
    assert vendor.event_eligibility_status == Vendor.ELIGIBILITY_ELIGIBLE

    # AuditLog recorded
    assert AuditLog.objects.filter(
        target_model="Vendor",
        target_object_id=str(vendor.id),
        new_state__event_eligibility_status="ELIGIBLE",
    ).exists()

    # Dual notifications dispatched
    assert Notification.objects.filter(
        notification_type=Notification.TYPE_KYC_REQUEST,
        title__contains="Eligibility Restored",
    ).exists()


def test_missing_eligibility_attributes_fail_closed(test_setup):
    """F-05: Ensure missing or false eligibility fails closed across all sourcing checks."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]

    vendor.status = Vendor.STATUS_ACTIVE
    vendor.is_event_eligible = False
    vendor.save()

    now = timezone.now()
    event = create_sourcing_event_service(
        title="Fail-Closed Test Event",
        description="Testing fail closed gating",
        event_type=SourcingEvent.TYPE_RFQ,
        created_by_user=proc_exec,
        bid_start_date=now - timedelta(hours=1),
        bid_end_date=now + timedelta(days=5),
    )

    # 1. _invite_error fails closed
    invite_err = _invite_error(event, vendor.id, vendor)
    assert invite_err is not None
    assert "not legally eligible to participate in events" in invite_err

    # 2. _assert_vendor_awardable fails closed
    with pytest.raises(ValidationError, match="not legally eligible to participate in events"):
        _assert_vendor_awardable(vendor)

    # 3. Dummy object with missing is_event_eligible attribute fails closed
    class DummyVendor:
        legal_name = "Mock Corp"
        status = Vendor.STATUS_ACTIVE
        id = uuid.uuid4()
        def get_status_display(self):
            return "Active"

    mock_v = DummyVendor()
    assert _invite_error(event, mock_v.id, mock_v) is not None
    with pytest.raises(ValidationError, match="not legally eligible"):
        _assert_vendor_awardable(mock_v)


def test_reviewer_notes_preserved_in_every_relevant_audit_record(test_setup):
    """F-06: Both notes and reviewer_notes are normalized and recorded in Document, ReviewHistory, and AuditLog."""
    setup = test_setup
    vendor = setup["vendor"]
    legal_mgr = setup["legal_mgr"]

    doc1 = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Cert",
        file=SimpleUploadedFile("cert1.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_PENDING,
    )
    doc2 = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance",
        file=SimpleUploadedFile("tax1.pdf", b"data", content_type="application/pdf"),
        status=VendorDocument.STATUS_PENDING,
    )

    # Caller using 'notes' parameter
    verify_vendor_document_decision_service(
        document=doc1,
        reviewer=legal_mgr,
        decision="VERIFY",
        notes="Note provided via notes parameter",
    )
    doc1.refresh_from_db()
    assert doc1.reviewer_notes == "Note provided via notes parameter"
    hist1 = VendorDocumentReviewHistory.objects.filter(document=doc1).first()
    assert hist1.notes == "Note provided via notes parameter"
    log1 = AuditLog.objects.filter(target_model="VendorDocument", target_object_id=str(doc1.id)).first()
    assert log1.new_state["notes"] == "Note provided via notes parameter"

    # Caller using 'reviewer_notes' parameter
    verify_vendor_document_decision_service(
        document=doc2,
        reviewer=legal_mgr,
        decision="REJECT",
        reviewer_notes="Note provided via reviewer_notes parameter",
    )
    doc2.refresh_from_db()
    assert doc2.reviewer_notes == "Note provided via reviewer_notes parameter"
    hist2 = VendorDocumentReviewHistory.objects.filter(document=doc2).first()
    assert hist2.notes == "Note provided via reviewer_notes parameter"
    log2 = AuditLog.objects.filter(target_model="VendorDocument", target_object_id=str(doc2.id)).first()
    assert log2.new_state["notes"] == "Note provided via reviewer_notes parameter"


def test_vendor_and_registering_pe_receive_correct_notifications(test_setup):
    """F-04 / F-07: Dual notifications route to vendor user and registering procurement executive."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    for doc_type, title, filename in [
        (VendorDocument.DOC_TYPE_CERT, "Incorporation Certificate", "cert.pdf"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate", "tax.pdf"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document", "bank.pdf"),
    ]:
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=doc_type,
            title=title,
            file=SimpleUploadedFile(filename, b"data", content_type="application/pdf"),
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    Notification.objects.all().delete()
    legal_validate_vendor_credentials_service(
        dispatch_id=dispatch.id,
        legal_user=legal_mgr,
        notes="Full compliance approved",
    )

    # 1. Vendor notification
    vendor_notifs = Notification.objects.filter(recipient=dispatch.user)
    assert vendor_notifs.exists()
    assert "Profile Legally Approved" in vendor_notifs.first().title

    # 2. Registering Procurement Executive notification
    pe_notifs = Notification.objects.filter(recipient=proc_exec)
    assert pe_notifs.exists()
    assert "Vendor Approved" in pe_notifs.first().title
    assert "Event-Eligible" in pe_notifs.first().title


def test_notification_failures_and_repeated_processing_idempotence(test_setup, monkeypatch):
    """F-07: SMTP email failures do not break approval; repeated scans do not create duplicate suspensions."""
    setup = test_setup
    vendor = setup["vendor"]
    proc_exec = setup["proc_exec"]
    legal_mgr = setup["legal_mgr"]

    for doc_type, title, filename in [
        (VendorDocument.DOC_TYPE_CERT, "Incorporation Certificate", "cert.pdf"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate", "tax.pdf"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document", "bank.pdf"),
    ]:
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=doc_type,
            title=title,
            file=SimpleUploadedFile(filename, b"data", content_type="application/pdf"),
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=proc_exec)

    # Mock send_mail to simulate network / SMTP exception
    def mock_failing_send_mail(*args, **kwargs):
        raise ConnectionError("SMTP server down")

    monkeypatch.setattr("apps.vendors.services.send_mail", mock_failing_send_mail)

    # Approval should succeed without crashing due to fail_silently=True handling
    approved_dispatch = legal_validate_vendor_credentials_service(
        dispatch_id=dispatch.id,
        legal_user=legal_mgr,
        notes="Approval with SMTP down",
    )
    assert approved_dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_APPROVED
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    assert vendor.is_event_eligible is True

    # Repeated expiration scanning is idempotent
    scan1 = scan_vendor_document_expirations_service()
    scan2 = scan_vendor_document_expirations_service()
    assert scan1["suspended_vendors"] == 0
    assert scan2["suspended_vendors"] == 0


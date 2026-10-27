"""
Integration and Unit Tests for Legal Manager Vendor Credential & KYC Verification Workflow.

Workflow:
1. Procurement Executive issues vendor credentials -> account created with is_active=False.
2. Vendor attempt to log in before legal verification is blocked with a clear pending legal approval message.
3. Legal Manager sees the vendor in the "Vendor Credential & KYC Verification Queue" on the Legal Dashboard.
4. Non-legal roles cannot approve or reject vendor credentials (backend RBAC).
5. Legal Manager approves & validates -> vendor user activated, vendor status becomes ACTIVE, documents verified, AuditLog recorded.
6. Vendor can now log in with credentials and access the Vendor Portal dashboard and account details.
7. Legal Manager can also reject credentials with mandatory notes -> status transitions to CHANGES_REQUIRED.
"""

import re

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.vendors.models import Vendor, VendorCategory, VendorCredentialDispatch, VendorDocument
from apps.vendors.services import (
    issue_vendor_credentials_service,
    legal_reject_vendor_credentials_service,
    legal_validate_vendor_credentials_service,
    register_vendor_service,
)

pytestmark = pytest.mark.django_db

LOGIN_URLS = ["http://localhost:8000/login/", "https://procure.onrender.com/login/"]


@pytest.fixture(autouse=True)
def email_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.VENDOR_PORTAL_LOGIN_URLS = LOGIN_URLS
    settings.DEFAULT_FROM_EMAIL = "ProcureSphere <no-reply@test.local>"
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _temp_password(body):
    return re.search(r"Temporary password: (\S+)", body).group(1)


@pytest.fixture
def workflow_setup(db, db_roles):
    """Sets up users across roles and a registered vendor with KYC document."""
    exec_user = User.objects.create_user(
        email="proc.exec@test.local",
        password="Password123!",
        first_name="Proc",
        last_name="Exec",
        role=db_roles[Role.PROC_EXEC],
    )
    legal_user = User.objects.create_user(
        email="legal.mgr@test.local",
        password="Password123!",
        first_name="Legal",
        last_name="Manager",
        role=db_roles[Role.LEGAL_MGR],
    )
    stores_user = User.objects.create_user(
        email="stores@test.local",
        password="Password123!",
        first_name="Stores",
        last_name="Receiver",
        role=db_roles[Role.STORES_RECEIVER],
    )
    category = VendorCategory.objects.create(name="IT Hardware", code="CAT-ITHW")
    vendor = register_vendor_service(
        legal_name="Apex Global Technologies",
        trade_name="Apex Tech",
        tax_identification_number="TIN-APEX-9901",
        category=category,
        email="portal@apextech.test",
        address="42 Cyber Towers, Tech City",
        created_by_user=exec_user,
    )
    from django.core.files.uploadedfile import SimpleUploadedFile

    dummy_pdf = SimpleUploadedFile(
        "apex_inc.pdf", b"%PDF-1.4 dummy content", content_type="application/pdf"
    )
    doc_cert = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Certificate of Incorporation",
        file=dummy_pdf,
        is_verified=False,
    )
    doc_tax = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Tax Clearance Certificate",
        file=dummy_pdf,
        is_verified=False,
    )
    doc_bank = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_BANK,
        title="Bank Verification Letter",
        file=dummy_pdf,
        is_verified=False,
    )
    return {
        "exec_user": exec_user,
        "legal_user": legal_user,
        "stores_user": stores_user,
        "vendor": vendor,
        "doc": doc_cert,
        "doc_cert": doc_cert,
        "doc_tax": doc_tax,
        "doc_bank": doc_bank,
    }


def test_procurement_executive_issues_credentials_sets_pending_legal_verification(
    client, workflow_setup
):
    w = workflow_setup
    vendor = w["vendor"]
    exec_user = w["exec_user"]

    # Procurement Executive issues credentials
    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=exec_user)

    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_PENDING
    assert dispatch.legal_verified_by is None
    assert dispatch.legal_verified_at is None

    vendor_user = User.objects.get(vendor=vendor)
    assert vendor_user.is_active is False
    assert vendor_user.must_change_password is True
    assert vendor_user.role_code == Role.VENDOR_USER

    # Extract dispatched password
    assert len(mail.outbox) == 1
    temp_pass = _temp_password(mail.outbox[0].body)
    assert vendor_user.check_password(temp_pass)

    # Vendor attempts to log in with correct credentials
    login_resp = client.post("/login/", {"email": vendor_user.email, "password": temp_pass})
    # Should not redirect to dashboard; should render login page with pending legal review notice
    assert login_resp.status_code == 200
    content = login_resp.content.decode()
    assert "pending Legal Manager verification and approval" in content
    assert "Access will be activated once legal review is complete" in content

    # Vendor cannot access protected vendor portal
    dashboard_resp = client.get("/vendor/")
    assert dashboard_resp.status_code == 302
    assert "/login/" in dashboard_resp.url


def test_legal_manager_dashboard_displays_pending_verification_queue(client, workflow_setup):
    w = workflow_setup
    vendor = w["vendor"]
    exec_user = w["exec_user"]
    legal_user = w["legal_user"]

    # Issue credentials
    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=exec_user)

    # Legal Manager views dashboard
    client.force_login(legal_user)
    resp = client.get(reverse("contracts_dashboard"))
    assert resp.status_code == 200
    content = resp.content.decode()

    # Must display the queue header and vendor details
    assert (
        "Vendor Credential &amp; KYC Verification Queue" in content
        or "Vendor Credential & KYC Verification Queue" in content
    )
    assert vendor.legal_name in content
    assert vendor.vendor_number in content
    assert dispatch.sent_to in content
    assert "PENDING LEGAL" in content
    assert "APPROVE / VALIDATE" in content
    assert "Reject" in content


def test_non_legal_role_cannot_validate_or_reject_credentials(client, workflow_setup):
    w = workflow_setup
    vendor = w["vendor"]
    exec_user = w["exec_user"]
    stores_user = w["stores_user"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=exec_user)

    # Stores user attempts POST approval
    client.force_login(stores_user)
    resp = client.post(
        reverse("legal_validate_credentials", args=[dispatch.id]),
        {"notes": "Attempted unauthorized validation"},
    )
    assert resp.status_code == 302
    dispatch.refresh_from_db()
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_PENDING
    assert dispatch.user.is_active is False

    # Direct service call raises PermissionDenied
    with pytest.raises(PermissionDenied):
        legal_validate_vendor_credentials_service(
            dispatch_id=dispatch.id, legal_user=stores_user, notes="Malicious attempt"
        )

    with pytest.raises(PermissionDenied):
        legal_reject_vendor_credentials_service(
            dispatch_id=dispatch.id, legal_user=stores_user, notes="Malicious attempt"
        )


def test_legal_manager_validates_and_approves_vendor_credentials_end_to_end(client, workflow_setup):
    from apps.vendors.services import verify_vendor_document_decision_service

    w = workflow_setup
    vendor = w["vendor"]
    exec_user = w["exec_user"]
    legal_user = w["legal_user"]
    doc_cert = w["doc_cert"]
    doc_tax = w["doc_tax"]
    doc_bank = w["doc_bank"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=exec_user)
    temp_pass = _temp_password(mail.outbox[0].body)
    vendor_user = dispatch.user

    # 1. Attempt approval before documents are verified -> blocked with ValidationError
    with pytest.raises(ValidationError, match="KYC compliance requirements not met"):
        legal_validate_vendor_credentials_service(
            dispatch_id=dispatch.id,
            legal_user=legal_user,
            notes="Premature attempt",
        )

    # 2. Legal Manager verifies documents via authorized service
    verify_vendor_document_decision_service(
        document=doc_cert, reviewer=legal_user, decision="VERIFY", reviewer_notes="Valid incorporation"
    )
    verify_vendor_document_decision_service(
        document=doc_tax, reviewer=legal_user, decision="VERIFY", reviewer_notes="Valid tax clearance"
    )
    verify_vendor_document_decision_service(
        document=doc_bank, reviewer=legal_user, decision="VERIFY", reviewer_notes="Valid bank details"
    )

    # 3. Now Legal Manager validates and approves credentials via dashboard POST
    client.force_login(legal_user)
    resp = client.post(
        reverse("legal_validate_credentials", args=[dispatch.id]),
        {"notes": "Verified KYC incorporation certificate, TIN, and business registration."},
    )
    assert resp.status_code == 302
    assert resp.url == reverse("contracts_dashboard")

    # Verify dispatch state
    dispatch.refresh_from_db()
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_APPROVED
    assert dispatch.legal_verified_by == legal_user
    assert dispatch.legal_verified_at is not None
    assert "incorporation certificate" in dispatch.legal_notes

    # Verify user state
    vendor_user.refresh_from_db()
    assert vendor_user.is_active is True

    # Verify vendor state
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    assert vendor.is_event_eligible is True
    assert vendor.event_eligibility_status == Vendor.ELIGIBILITY_ELIGIBLE

    # Verify immutable AuditLog
    audit_entry = AuditLog.objects.filter(
        target_model="VendorCredentialDispatch",
        target_object_id=str(dispatch.id),
        action=AuditLog.ACTION_APPROVE,
    ).first()
    assert audit_entry is not None
    assert audit_entry.actor == legal_user
    assert (
        audit_entry.new_state["legal_verification_status"]
        == VendorCredentialDispatch.LEGAL_STATUS_APPROVED
    )

    # Now the vendor can log in!
    client.logout()
    login_resp = client.post("/login/", {"email": vendor_user.email, "password": temp_pass})
    assert login_resp.status_code == 302
    assert login_resp.url in ("/vendor/", "/accounts/change-password/", reverse("accounts:change_password"))

    # First request to protected page is redirected to forced password change by middleware
    portal_first_resp = client.get("/vendor/")
    assert portal_first_resp.status_code == 302
    assert portal_first_resp.url in (reverse("password_change"), "/accounts/change-password/")

    # Complete password change
    change_resp = client.post(
        reverse("password_change"),
        {
            "current_password": temp_pass,
            "new_password1": "SecureNewVendorPass2026!",
            "new_password2": "SecureNewVendorPass2026!",
        },
    )
    assert change_resp.status_code == 302

    # Vendor can now access Vendor Portal Dashboard and see account details
    portal_resp = client.get("/vendor/")
    assert portal_resp.status_code == 200
    portal_content = portal_resp.content.decode()
    assert vendor.legal_name in portal_content
    assert vendor.vendor_number in portal_content


def test_legal_manager_rejects_vendor_credentials_workflow(client, workflow_setup):
    w = workflow_setup
    vendor = w["vendor"]
    exec_user = w["exec_user"]
    legal_user = w["legal_user"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=exec_user)
    temp_pass = _temp_password(mail.outbox[0].body)
    vendor_user = dispatch.user

    # Attempt rejection without mandatory notes
    client.force_login(legal_user)
    empty_notes_resp = client.post(
        reverse("legal_reject_credentials", args=[dispatch.id]),
        {"notes": "   "},
    )
    assert empty_notes_resp.status_code == 302
    dispatch.refresh_from_db()
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_PENDING

    # Reject with valid notes
    reject_resp = client.post(
        reverse("legal_reject_credentials", args=[dispatch.id]),
        {"notes": "GST certificate missing official seal; bank IFSC code unverified."},
    )
    assert reject_resp.status_code == 302
    assert reject_resp.url == reverse("contracts_dashboard")

    dispatch.refresh_from_db()
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_REJECTED
    assert dispatch.legal_verified_by == legal_user
    assert "GST certificate" in dispatch.legal_notes

    vendor_user.refresh_from_db()
    assert vendor_user.is_active is False

    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_CHANGES_REQUIRED
    assert "GST certificate" in vendor.status_notes

    # Vendor login remains blocked
    client.logout()
    login_resp = client.post("/login/", {"email": vendor_user.email, "password": temp_pass})
    assert login_resp.status_code == 200
    assert "pending Legal Manager verification and approval" in login_resp.content.decode()

"""Procurement Executive: register vendor -> issue credentials -> emailed login -> forced password change."""

import re
from unittest import mock

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.vendors.models import Vendor, VendorCredentialDispatch, VendorDocument
from apps.vendors.services import (
    issue_vendor_credentials_service,
    legal_validate_vendor_credentials_service,
)

pytestmark = pytest.mark.django_db

LOGIN_URLS = ["http://localhost:8000/login/", "https://procure.onrender.com/login/"]


@pytest.fixture(autouse=True)
def email_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.VENDOR_PORTAL_LOGIN_URLS = LOGIN_URLS
    settings.BREVO_API_KEY = ""  # SMTP path (locmem); REST path is tested separately
    settings.DEFAULT_FROM_EMAIL = "ProcureSphere <no-reply@test.local>"


def _login(client, user, password=None):
    if password is None:
        client.force_login(user)
        return
    resp = client.post("/login/", {"email": user.email, "password": password})
    assert resp.status_code == 302, "portal login with temporary password failed"


def _temp_password(body):
    return re.search(r"Temporary password: (\S+)", body).group(1)


def test_register_via_dashboard_modal_then_issue_credentials(client, procurement_world):
    w = procurement_world
    _login(client, w["exec"])
    resp = client.post(
        reverse("vendors_register_modal"),
        {
            "legal_name": "Delta Components Pvt Ltd",
            "tax_identification_number": "TIN-DELTA",
            "category": str(w["hardware"].id),
            "email": "Contact@Delta.test",
            "address": "5 Industrial Area",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    vendor = Vendor.objects.get(tax_identification_number="TIN-DELTA")
    assert vendor.status == Vendor.STATUS_DRAFT
    assert not User.objects.filter(vendor=vendor).exists()

    resp = client.post(
        reverse("vendor_issue_credentials", args=[vendor.id]), HTTP_HX_REQUEST="true"
    )
    assert resp.status_code == 200
    user = User.objects.get(vendor=vendor)
    assert user.role_code == Role.VENDOR_USER
    assert user.must_change_password is True

    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["Contact@delta.test"]
    for url in LOGIN_URLS:
        assert url in msg.body
    assert vendor.vendor_number in msg.body
    assert user.check_password(_temp_password(msg.body))

    dispatch = VendorCredentialDispatch.objects.get(vendor=vendor)
    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT
    assert dispatch.created_by == w["exec"]
    assert dispatch.legal_verification_status == VendorCredentialDispatch.LEGAL_STATUS_PENDING
    # the temporary password must never be persisted in audit evidence
    pwd = _temp_password(msg.body)
    assert not any(pwd in str(a.new_state) for a in AuditLog.objects.filter(target_model="Vendor"))


def test_vendor_logs_in_and_is_forced_to_change_password(client, procurement_world):
    w = procurement_world
    dispatch = issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    temp = _temp_password(mail.outbox[0].body)
    vendor_user = User.objects.get(vendor=w["vendor_draft"])

    # Attempting login before legal approval is blocked with warning message
    blocked_resp = client.post("/login/", {"email": vendor_user.email, "password": temp})
    assert blocked_resp.status_code == 200
    assert "pending Legal Manager verification and approval" in blocked_resp.content.decode()

    # Attach mandatory verified KYC documents so legal validation passes compliance checks
    from django.core.files.uploadedfile import SimpleUploadedFile
    dummy_pdf = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 dummy", content_type="application/pdf")
    for doc_type, title in [
        (VendorDocument.DOC_TYPE_CERT, "Certificate of Incorporation"),
        (VendorDocument.DOC_TYPE_TAX, "Tax Clearance Certificate"),
        (VendorDocument.DOC_TYPE_BANK, "Bank Verification Document"),
    ]:
        VendorDocument.objects.create(
            vendor=w["vendor_draft"],
            document_type=doc_type,
            title=title,
            file=dummy_pdf,
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

    # Legal Manager validates and approves credentials
    legal_mgr = User.objects.create_user(
        email="legal.mgr@test.local",
        password="Password123!",
        role=Role.objects.get(code=Role.LEGAL_MGR),
    )
    legal_validate_vendor_credentials_service(dispatch_id=dispatch.id, legal_user=legal_mgr)

    _login(client, vendor_user, temp)
    resp = client.get("/")
    assert resp.status_code == 302 and resp.url in (reverse("password_change"), "/accounts/change-password/")

    # wrong current password is rejected
    resp = client.post(
        reverse("password_change"),
        {
            "current_password": "nope",
            "new_password1": "BrandNew#Pass2026",
            "new_password2": "BrandNew#Pass2026",
        },
    )
    assert resp.status_code == 200
    vendor_user.refresh_from_db()
    assert vendor_user.must_change_password is True

    resp = client.post(
        reverse("password_change"),
        {
            "current_password": temp,
            "new_password1": "BrandNew#Pass2026",
            "new_password2": "BrandNew#Pass2026",
        },
    )
    assert resp.status_code == 302
    vendor_user.refresh_from_db()
    assert vendor_user.must_change_password is False
    assert vendor_user.check_password("BrandNew#Pass2026")
    assert client.get("/").status_code != 302 or client.get("/").url != reverse("password_change")


def test_resend_resets_password_and_keeps_single_user(procurement_world):
    w = procurement_world
    issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    first = _temp_password(mail.outbox[0].body)
    issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["mgr"])
    second = _temp_password(mail.outbox[1].body)
    user = User.objects.get(vendor=w["vendor_draft"])
    assert User.objects.filter(vendor=w["vendor_draft"]).count() == 1
    assert first != second
    assert not user.check_password(first)
    assert user.check_password(second)


def test_email_failure_is_recorded_and_retryable(procurement_world):
    w = procurement_world
    with mock.patch("apps.vendors.services.send_mail", side_effect=OSError("smtp down")):
        with pytest.raises(ValidationError):
            issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    dispatch = VendorCredentialDispatch.objects.get(vendor=w["vendor_draft"])
    assert dispatch.status == VendorCredentialDispatch.STATUS_FAILED
    issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    assert VendorCredentialDispatch.objects.filter(status="SENT").count() == 1
    assert len(mail.outbox) == 1


def test_blocked_for_suspended_vendor_and_email_collision(procurement_world):
    w = procurement_world
    with pytest.raises(ValidationError):
        issue_vendor_credentials_service(vendor=w["vendor_susp"], issued_by=w["exec"])
    # vendor_draft email is already used by another account
    User.objects.create_user(
        w["vendor_draft"].email,
        "Password123!",
        role=Role.objects.get(code=Role.VENDOR_USER),
        vendor=w["vendor_a"],
    )
    with pytest.raises(ValidationError):
        issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    assert mail.outbox == []


@pytest.mark.parametrize("key", ["finance", "auditor", "requester", "stores", "user_a"])
def test_other_roles_cannot_issue_credentials_or_open_register_modal(
    client, procurement_world, key
):
    w = procurement_world
    with pytest.raises(PermissionDenied):
        issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w[key])
    _login(client, w[key])
    r1 = client.post(reverse("vendor_issue_credentials", args=[w["vendor_draft"].id]))
    r2 = client.get(reverse("vendors_register_modal"))
    assert r1.status_code == 403 and r2.status_code == 403
    assert mail.outbox == []
    assert not User.objects.filter(vendor=w["vendor_draft"]).exists()


def test_duplicate_tax_id_rejected_in_modal(client, procurement_world):
    w = procurement_world
    _login(client, w["exec"])
    resp = client.post(
        reverse("vendors_register_modal"),
        {
            "legal_name": "Dup Co",
            "tax_identification_number": "TIN-A",
            "category": str(w["hardware"].id),
            "email": "dup@x.test",
            "address": "x",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert Vendor.objects.filter(legal_name="Dup Co").count() == 0


def test_brevo_rest_api_used_when_api_key_configured(settings, procurement_world):
    w = procurement_world
    settings.BREVO_API_KEY = "xkeysib-test"
    settings.BREVO_SENDER_EMAIL = "sender@test.local"
    with mock.patch("apps.vendors.services.requests.post") as post:
        post.return_value.ok = True
        dispatch = issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT
    assert mail.outbox == []
    kwargs = post.call_args.kwargs
    assert kwargs["headers"]["api-key"] == "xkeysib-test"
    assert kwargs["json"]["to"] == [{"email": w["vendor_draft"].email.lower()}]
    assert "Temporary password:" in kwargs["json"]["textContent"]

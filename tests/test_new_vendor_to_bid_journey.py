"""New vendor journey: register -> credentials -> login -> KYC -> approval -> invitation -> bid."""

from datetime import timedelta

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.sourcing import services as svc
from apps.sourcing.models import BidInvite, VendorBid
from apps.vendors.models import Vendor
from apps.vendors.services import issue_vendor_credentials_service

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture(autouse=True)
def email_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.VENDOR_PORTAL_LOGIN_URLS = ["http://localhost:8000/login/"]
    settings.BREVO_API_KEY = ""  # SMTP path (locmem); REST path is tested separately


def _event(w):
    now = timezone.now()
    return svc.create_sourcing_event_service(
        title="Network refresh",
        event_type="RFQ",
        bid_start_date=now - timedelta(hours=1),
        bid_end_date=now + timedelta(days=5),
        description="d",
        technical_requirements="t",
        commercial_requirements="c",
        created_by_user=w["exec"],
    )


def test_new_vendor_is_listed_as_pending_then_invitable_after_kyc_approval(
    client, procurement_world
):
    w = procurement_world
    vendor = w["vendor_draft"]
    event = _event(w)
    client.force_login(w["exec"])

    # Draft vendor: visible in the invitation panel as pending, but not selectable.
    page = client.get(reverse("sourcing_detail", args=[event.id])).content.decode()
    assert vendor.legal_name in page
    assert f'value="{vendor.id}"' not in page
    htmx = client.get(reverse("sourcing_invites_htmx", args=[event.id])).content.decode()
    assert vendor.legal_name in htmx and "awaiting KYC approval" in htmx
    resp = client.post(
        reverse("sourcing_invites_htmx", args=[event.id]),
        {"op": "invite", "vendor_ids": [str(vendor.id)]},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 400
    assert not BidInvite.objects.filter(event=event, vendor=vendor).exists()

    # Credentials emailed; vendor logs in, sets own password, uploads KYC and submits.
    issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])
    temp = mail.outbox[0].body.split("Temporary password: ")[1].split()[0]
    vendor_user = User.objects.get(vendor=vendor)
    vclient = client.__class__()
    assert (
        vclient.post("/login/", {"email": vendor_user.email, "password": temp}).status_code == 302
    )
    assert (
        vclient.post(
            reverse("password_change"),
            {
                "current_password": temp,
                "new_password1": "BrandNew#Pass2026",
                "new_password2": "BrandNew#Pass2026",
            },
        ).status_code
        == 302
    )
    assert vclient.get("/").status_code == 200  # vendor dashboard
    vclient.post(
        reverse("vendor_profile"),
        {
            "action": "upload_kyc",
            "document_type": "OTHER",
            "title": "GST certificate",
            "kyc_file": SimpleUploadedFile(
                "gst.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
        },
    )
    vclient.post(reverse("vendor_profile"), {"action": "submit_kyc"})
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED

    # Documents must be verified before activation is allowed.
    assert client.post(reverse("vendor_kyc_action", args=[vendor.id, "approve"])).status_code == 302
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    doc = vendor.documents.get()
    client.post(
        reverse("vendor_kyc_action", args=[vendor.id, "verify-document"]),
        {"document_id": str(doc.id)},
    )

    # Executive approves -> vendor becomes selectable and can be invited via the UI.
    assert client.post(reverse("vendor_kyc_action", args=[vendor.id, "approve"])).status_code == 302
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    page = client.get(reverse("sourcing_detail", args=[event.id])).content.decode()
    assert f'value="{vendor.id}"' in page
    resp = client.post(
        reverse("sourcing_invites_htmx", args=[event.id]),
        {"op": "invite", "vendor_ids": [str(vendor.id)]},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    svc.invite_vendors_to_event_service(
        event=event, vendor_ids=[w["vendor_a"].id], invited_by=w["exec"]
    )
    svc.publish_sourcing_event_service(event=event, user=w["exec"])

    # The new vendor sees only its event and can submit a bid; other vendors cannot see it.
    assert event.event_number in vclient.get("/vendor/sourcing/").content.decode()
    resp = vclient.post(
        reverse("vendor_bid_create", args=[event.id]),
        {
            "action": "submit_bid",
            "proposal_summary": "s",
            "technical_proposal": "t",
            "commercial_proposal": "c",
            "line_description[]": ["Switch"],
            "line_quantity[]": ["2"],
            "line_price[]": ["500"],
        },
    )
    assert resp.status_code == 302
    assert VendorBid.objects.filter(event=event, vendor=vendor).exists()

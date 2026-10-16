"""
Vendor KYC verification and activation workflow (TC-KYC-001 .. TC-KYC-015).

Workflow under test: Procurement registers vendor (DRAFT) -> vendor uploads KYC documents and
submits -> Legal Manager verifies / rejects documents or requests corrections -> Procurement
(Executive/Manager) activates only when every KYC gate passes -> only ACTIVE vendors are invitable.
Every test asserts the HTTP response AND the resulting database state.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.sourcing import services as sourcing_svc
from apps.sourcing.models import BidInvite
from apps.vendors.models import Vendor, VendorDocument
from apps.vendors.services import (
    approve_vendor_service,
    reject_vendor_document_service,
    upload_vendor_document_service,
    verify_vendor_document_service,
)

pytestmark = [pytest.mark.django_db, pytest.mark.regression]

PDF = b"%PDF-1.4 kyc"


def _pdf(name="doc.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def _doc(vendor, user, doc_type="TAX_CLEARANCE", title="Tax certificate"):
    return upload_vendor_document_service(
        vendor=vendor, user=user, file=_pdf(), document_type=doc_type, title=title
    )


def _set_status(vendor, status):
    Vendor.objects.filter(pk=vendor.pk).update(status=status)
    vendor.refresh_from_db()
    return vendor


def _kyc_url(vendor, action):
    return reverse("vendor_kyc_action", args=[vendor.id, action])


def _htmx_url(vendor, action):
    return reverse("vendors_kyc_htmx", args=[vendor.id, action])


@pytest.fixture
def world(procurement_world, db_roles):
    """procurement_world + a Legal Manager and a submitted vendor with one pending document."""
    w = dict(procurement_world)
    w["legal"] = User.objects.create_user(
        email="legal@test.local", password="Password123!", role=db_roles[Role.LEGAL_MGR]
    )
    w["vendor_user_draft"] = User.objects.create_user(
        email="rep@draft.test",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=w["vendor_draft"],
    )
    return w


@pytest.fixture
def submitted(world):
    """A SUBMITTED vendor (vendor_kyc fixture vendor moved to SUBMITTED) with one pending doc."""
    vendor = _set_status(world["vendor_kyc"], Vendor.STATUS_SUBMITTED)
    doc = _doc(vendor, world["exec"])
    return vendor, doc


# ------------------------------------------------------------ registration (001, 002) -------


def test_tc_kyc_001_exec_registers_vendor_as_draft(client, world):
    client.force_login(world["exec"])
    resp = client.post(
        reverse("vendors_register_modal"),
        {
            "legal_name": "Omega Components Pvt Ltd",
            "tax_identification_number": "TIN-OMEGA",
            "category": str(world["hardware"].id),
            "email": "omega@vendor.test",
            "address": "9 Industrial Estate",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code == 200
    vendor = Vendor.objects.get(tax_identification_number="TIN-OMEGA")
    assert vendor.status == Vendor.STATUS_DRAFT
    assert vendor.vendor_number.startswith("VND-")
    assert not vendor.documents.exists()
    assert AuditLog.objects.filter(
        target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_CREATE
    ).exists()


def test_tc_kyc_002_mandatory_registration_fields_validated(client, world):
    client.force_login(world["exec"])
    before = Vendor.objects.count()
    resp = client.post(
        reverse("vendors_register_modal"),
        {"legal_name": "", "tax_identification_number": "", "email": "not-an-email"},
        HTTP_HX_REQUEST="true",
    )
    assert resp.status_code in (200, 400)
    assert Vendor.objects.count() == before
    # duplicate tax id is rejected too
    resp = client.post(
        reverse("vendors_register_modal"),
        {
            "legal_name": "Dup",
            "tax_identification_number": world["vendor_a"].tax_identification_number,
            "category": str(world["hardware"].id),
            "email": "dup@vendor.test",
            "address": "x",
        },
        HTTP_HX_REQUEST="true",
    )
    assert Vendor.objects.count() == before


# ------------------------------------------------------------ vendor submission (003) -------


def test_tc_kyc_003_vendor_uploads_documents_and_submits(client, world):
    vendor = world["vendor_draft"]
    client.force_login(world["vendor_user_draft"])
    # cannot submit without a document
    client.post(reverse("vendor_profile"), {"action": "submit_kyc"})
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_DRAFT

    client.post(
        reverse("vendor_profile"),
        {
            "action": "upload_kyc",
            "document_type": "TAX_CLEARANCE",
            "title": "GST certificate",
            "kyc_file": _pdf("gst.pdf"),
        },
    )
    doc = vendor.documents.get()
    assert doc.review_status == VendorDocument.REVIEW_PENDING and doc.is_verified is False

    client.post(reverse("vendor_profile"), {"action": "submit_kyc"})
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED


# ------------------------------------------------------------ monitoring UI (004, 005) ------


def test_tc_kyc_004_exec_sees_status_without_unauthorised_controls(client, world, submitted):
    client.force_login(world["exec"])
    # Draft vendor: accurate status, no approve / request-changes controls at all.
    draft = world["vendor_draft"]
    page = client.get(reverse("vendor_detail", args=[draft.id])).content.decode()
    assert "KYC &amp; activation status" in page
    assert "Awaiting vendor documents and KYC submission." in page
    assert "Approve &amp; activate" not in page
    assert "Request Changes" not in page
    assert "No governance transitions are available" in page  # consistent with the panel

    # Submitted but unverified: still no activation control, blockers are listed.
    vendor, doc = submitted
    page = client.get(reverse("vendor_detail", args=[vendor.id])).content.decode()
    assert "awaiting verification" in page
    assert "Approve &amp; activate" not in page
    assert ">Verify<" in page  # exec is an authorised KYC reviewer under the RBAC matrix

    # Once documents are verified the activation control appears.
    verify_vendor_document_service(document=doc, verifier=world["legal"])
    page = client.get(reverse("vendor_detail", args=[vendor.id])).content.decode()
    assert "Approve &amp; activate" in page
    assert "Ready for activation by Procurement." in page


def test_tc_kyc_005_legal_manager_reviews_documents_but_cannot_activate(client, world, submitted):
    vendor, doc = submitted
    client.force_login(world["legal"])
    resp = client.get(reverse("vendor_detail", args=[vendor.id]))
    assert resp.status_code == 200
    page = resp.content.decode()
    assert ">Verify<" in page and ">Reject<" in page
    queue = client.get(reverse("vendor_onboarding"))
    assert queue.status_code == 200
    assert vendor.legal_name in queue.content.decode()
    assert "/vendors/onboarding/" in client.get("/").content.decode()  # Legal sidebar link
    assert "Request Changes" in page
    assert "Approve &amp; activate" not in page
    assert (
        client.get(reverse("vendor_document_download", args=[vendor.id, doc.id])).status_code == 200
    )


# ------------------------------------------------------------ legal decisions (006-008) -----


def test_tc_kyc_006_legal_approves_document_and_decision_persists(client, world, submitted):
    vendor, doc = submitted
    client.force_login(world["legal"])
    resp = client.post(_kyc_url(vendor, "verify-document"), {"document_id": str(doc.id)})
    assert resp.status_code == 302
    doc.refresh_from_db()
    assert doc.review_status == VendorDocument.REVIEW_VERIFIED
    assert doc.is_verified is True
    assert doc.verified_by == world["legal"]
    assert doc.reviewed_at is not None
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED  # verifying never activates
    # a second verification is not allowed (idempotent / no duplicate audit)
    client.post(_kyc_url(vendor, "verify-document"), {"document_id": str(doc.id)})
    assert (
        AuditLog.objects.filter(
            target_model="VendorDocument", target_object_id=str(doc.id), action="VERIFY"
        ).count()
        == 1
    )


def test_tc_kyc_007_legal_rejects_document_with_reason_and_vendor_can_correct(
    client, world, submitted
):
    vendor, doc = submitted
    vendor_user = User.objects.create_user(
        email="rep@kyc.test",
        password="Password123!",
        role=Role.objects.get(code=Role.VENDOR_USER),
        vendor=vendor,
    )
    client.force_login(world["legal"])
    # reason is mandatory
    client.post(_kyc_url(vendor, "reject-document"), {"document_id": str(doc.id), "notes": " "})
    doc.refresh_from_db()
    assert doc.review_status == VendorDocument.REVIEW_PENDING

    resp = client.post(
        _kyc_url(vendor, "reject-document"),
        {"document_id": str(doc.id), "notes": "Certificate expired"},
    )
    assert resp.status_code == 302
    doc.refresh_from_db()
    assert doc.review_status == VendorDocument.REVIEW_REJECTED
    assert doc.review_notes == "Certificate expired"
    assert doc.is_verified is False
    assert Notification.objects.filter(recipient=vendor_user, title__icontains="rejected").exists()

    # a rejected document blocks activation ...
    with pytest.raises(ValidationError):
        approve_vendor_service(vendor=vendor, manager=world["exec"])
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    # ... until the vendor uploads a replacement and it is verified (history is kept).
    replacement = _doc(vendor, vendor_user, title="Valid tax certificate")
    assert vendor.documents.count() == 2
    with pytest.raises(ValidationError):
        approve_vendor_service(vendor=vendor, manager=world["exec"])  # replacement still pending
    verify_vendor_document_service(document=replacement, verifier=world["legal"])
    approve_vendor_service(vendor=vendor, manager=world["exec"], notes="ok")
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE


def test_tc_kyc_008_legal_requests_corrections_vendor_stays_non_active(client, world, submitted):
    vendor, doc = submitted
    client.force_login(world["legal"])
    client.post(_kyc_url(vendor, "request-changes"), {"notes": ""})
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED  # explanation is mandatory

    resp = client.post(
        _kyc_url(vendor, "request-changes"), {"notes": "Upload a legible incorporation certificate"}
    )
    assert resp.status_code == 302
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_CHANGES_REQUIRED
    assert "legible incorporation" in vendor.status_notes
    with pytest.raises(ValidationError):
        approve_vendor_service(vendor=vendor, manager=world["exec"])
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_CHANGES_REQUIRED
    assert AuditLog.objects.filter(
        target_model="Vendor",
        target_object_id=str(vendor.id),
        new_state__status=Vendor.STATUS_CHANGES_REQUIRED,
        actor=world["legal"],
    ).exists()


# ------------------------------------------------------------ activation gates (009, 010) ---


def test_tc_kyc_009_activation_blocked_while_required_documents_missing(client, settings, world):
    client.force_login(world["exec"])
    # no documents at all
    vendor = _set_status(world["vendor_kyc"], Vendor.STATUS_SUBMITTED)
    resp = client.post(_htmx_url(vendor, "approve"), {"notes": "x"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 400
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED

    # mandatory document type configured but only another type is verified
    settings.VENDOR_MANDATORY_KYC_DOC_TYPES = ["INC_CERT"]
    doc = _doc(vendor, world["exec"], "TAX_CLEARANCE")
    verify_vendor_document_service(document=doc, verifier=world["legal"])
    resp = client.post(_htmx_url(vendor, "approve"), {"notes": "x"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 400
    assert "Mandatory documents missing" in resp.content.decode()
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED

    inc = _doc(vendor, world["exec"], "INC_CERT", "Incorporation")
    verify_vendor_document_service(document=inc, verifier=world["legal"])
    resp = client.post(_htmx_url(vendor, "approve"), {"notes": "ok"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE


def test_tc_kyc_010_activation_blocked_while_verification_pending_or_rejected(
    client, world, submitted
):
    vendor, doc = submitted
    client.force_login(world["exec"])
    for _ in range(2):  # pending
        resp = client.post(_kyc_url(vendor, "approve"), {"notes": "x"})
        assert resp.status_code == 302
        vendor.refresh_from_db()
        assert vendor.status == Vendor.STATUS_SUBMITTED
    reject_vendor_document_service(document=doc, reviewer=world["legal"], reason="blurry scan")
    client.post(_kyc_url(vendor, "approve"), {"notes": "x"})
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    # the generic status endpoint cannot be used to bypass the gate either
    client.post(
        reverse("vendor_status_change", args=[vendor.id]), {"status": "ACTIVE", "notes": "x"}
    )
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    # DRAFT vendors cannot be activated through any path
    draft = world["vendor_draft"]
    client.post(_kyc_url(draft, "approve"), {"notes": "x"})
    client.post(
        reverse("vendor_status_change", args=[draft.id]), {"status": "ACTIVE", "notes": "x"}
    )
    draft.refresh_from_db()
    assert draft.status == Vendor.STATUS_DRAFT


# ------------------------------------------------------------ authorisation (011) -----------


@pytest.mark.parametrize("user_key", ["requester", "stores", "finance", "auditor", "user_a"])
def test_tc_kyc_011_unauthorised_roles_cannot_approve_verify_or_reject(
    client, world, submitted, user_key
):
    vendor, doc = submitted
    client.force_login(world[user_key])
    for action, data in [
        ("approve", {"notes": "x"}),
        ("verify-document", {"document_id": str(doc.id)}),
        ("reject-document", {"document_id": str(doc.id), "notes": "x"}),
        ("request-changes", {"notes": "x"}),
        ("start-review", {}),
    ]:
        assert client.post(_kyc_url(vendor, action), data).status_code == 403
        assert client.post(_htmx_url(vendor, action), data).status_code == 403
    vendor.refresh_from_db()
    doc.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    assert doc.review_status == VendorDocument.REVIEW_PENDING


def test_tc_kyc_011_legal_manager_cannot_activate_or_start_review(client, world, submitted):
    vendor, doc = submitted
    verify_vendor_document_service(document=doc, verifier=world["legal"])
    client.force_login(world["legal"])
    assert client.post(_kyc_url(vendor, "approve"), {"notes": "x"}).status_code == 403
    assert client.post(_htmx_url(vendor, "approve"), {"notes": "x"}).status_code == 403
    assert client.post(_kyc_url(vendor, "start-review")).status_code == 403
    assert (
        client.post(
            reverse("vendor_status_change", args=[vendor.id]), {"status": "ACTIVE", "notes": "x"}
        ).status_code
        == 403
    )
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    with pytest.raises(PermissionDenied):
        approve_vendor_service(vendor=vendor, manager=world["legal"])


def test_tc_kyc_011_api_cannot_activate_or_edit_status(api_client, world, submitted):
    vendor, doc = submitted
    verify_vendor_document_service(document=doc, verifier=world["legal"])
    # unauthorised role
    api_client.force_authenticate(world["requester"])
    res = api_client.post(
        f"/api/v1/vendors/{vendor.id}/set-status/", {"status": "ACTIVE", "notes": "x"}
    )
    assert res.status_code in (403, 404)
    # authorised role: set-status has no SUBMITTED -> ACTIVE transition (only the KYC service)
    api_client.force_authenticate(world["exec"])
    res = api_client.post(
        f"/api/v1/vendors/{vendor.id}/set-status/", {"status": "ACTIVE", "notes": "x"}
    )
    assert res.status_code == 400
    # status is read-only on the serializer
    api_client.patch(f"/api/v1/vendors/{vendor.id}/", {"status": "ACTIVE"}, format="json")
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED


def test_tc_kyc_011_status_not_editable_via_edit_form_and_idor_on_documents(
    client, world, submitted
):
    vendor, doc = submitted
    other = world["vendor_a"]
    client.force_login(world["exec"])
    client.post(
        reverse("vendor_edit", args=[vendor.id]),
        {
            "legal_name": vendor.legal_name,
            "email": vendor.email,
            "address": vendor.address,
            "category": str(vendor.category_id),
            "status": "ACTIVE",
        },
    )
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_SUBMITTED
    # a document id belonging to vendor X cannot be actioned through vendor Y's URL
    client.force_login(world["legal"])
    assert (
        client.post(_kyc_url(other, "verify-document"), {"document_id": str(doc.id)}).status_code
        == 404
    )
    assert (
        client.get(reverse("vendor_document_download", args=[other.id, doc.id])).status_code == 404
    )
    doc.refresh_from_db()
    assert doc.review_status == VendorDocument.REVIEW_PENDING


# ------------------------------------------------------------ success + audit (012, 013) ----


def test_tc_kyc_012_and_013_full_authorised_workflow_with_audit_and_replay(
    client, world, submitted
):
    vendor, doc = submitted
    client.force_login(world["legal"])
    assert (
        client.post(_kyc_url(vendor, "verify-document"), {"document_id": str(doc.id)}).status_code
        == 302
    )

    client.force_login(world["exec"])
    assert client.post(_kyc_url(vendor, "start-review")).status_code == 302
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_KYC_REVIEW

    before = timezone.now()
    assert client.post(_kyc_url(vendor, "approve"), {"notes": "KYC complete"}).status_code == 302
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    assert vendor.status_notes == "KYC complete"

    # replay: second approval is rejected and does not duplicate the transition
    client.post(_kyc_url(vendor, "approve"), {"notes": "again"})
    client.post(_htmx_url(vendor, "approve"), {"notes": "again"}, HTTP_HX_REQUEST="true")
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    assert vendor.status_notes == "KYC complete"
    approvals = AuditLog.objects.filter(
        target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_APPROVE
    )
    assert approvals.count() == 1

    # TC-KYC-013: reviewer, action, timestamp, decision are all recorded.
    entry = approvals.get()
    assert entry.actor == world["exec"]
    assert entry.previous_state["status"] == Vendor.STATUS_KYC_REVIEW
    assert entry.new_state["status"] == Vendor.STATUS_ACTIVE
    assert entry.timestamp >= before - timedelta(seconds=5)
    verify_entry = AuditLog.objects.get(
        target_model="VendorDocument", target_object_id=str(doc.id), action="VERIFY"
    )
    assert verify_entry.actor == world["legal"]
    assert verify_entry.new_state["review_status"] == VendorDocument.REVIEW_VERIFIED
    # audit rows are append-only
    with pytest.raises(Exception):
        entry.action = "X"
        entry.save()


# ------------------------------------------------------------ eligibility (014) ------------


def _event(w):
    now = timezone.now()
    return sourcing_svc.create_sourcing_event_service(
        title="Eligibility check",
        event_type="RFQ",
        bid_start_date=now - timedelta(hours=1),
        bid_end_date=now + timedelta(days=3),
        description="d",
        technical_requirements="t",
        commercial_requirements="c",
        created_by_user=w["exec"],
    )


@pytest.mark.parametrize(
    "status",
    [
        Vendor.STATUS_DRAFT,
        Vendor.STATUS_SUBMITTED,
        Vendor.STATUS_KYC_REVIEW,
        Vendor.STATUS_CHANGES_REQUIRED,
        Vendor.STATUS_REJECTED,
    ],
)
def test_tc_kyc_014_non_active_vendors_cannot_be_invited(world, status):
    vendor = _set_status(world["vendor_draft"], status)
    event = _event(world)
    with pytest.raises(ValidationError):
        sourcing_svc.invite_vendors_to_event_service(
            event=event, vendor_ids=[vendor.id], invited_by=world["exec"]
        )
    assert not BidInvite.objects.filter(event=event, vendor=vendor).exists()


def test_tc_kyc_014_vendor_becomes_invitable_only_after_kyc_activation(world, submitted):
    vendor, doc = submitted
    event = _event(world)
    with pytest.raises(ValidationError):
        sourcing_svc.invite_vendors_to_event_service(
            event=event, vendor_ids=[vendor.id], invited_by=world["exec"]
        )
    verify_vendor_document_service(document=doc, verifier=world["legal"])
    approve_vendor_service(vendor=vendor, manager=world["mgr"], notes="ok")
    invites = sourcing_svc.invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor.id], invited_by=world["exec"]
    )
    assert len(invites) == 1 and invites[0].vendor_id == vendor.id


# ------------------------------------------------------------ regression (015) -------------


def test_tc_kyc_015_existing_active_vendor_workflows_unaffected(client, world):
    client.force_login(world["exec"])
    active = world["vendor_a"]
    page = client.get(reverse("vendor_detail", args=[active.id]))
    assert page.status_code == 200
    body = page.content.decode()
    assert "Approve &amp; activate" not in body  # already active: nothing to activate
    # governance hold / release still works through the transition table
    client.post(
        reverse("vendor_status_change", args=[active.id]), {"status": "ON_HOLD", "notes": "audit"}
    )
    active.refresh_from_db()
    assert active.status == Vendor.STATUS_ON_HOLD
    client.post(
        reverse("vendor_status_change", args=[active.id]), {"status": "ACTIVE", "notes": "cleared"}
    )
    active.refresh_from_db()
    assert active.status == Vendor.STATUS_ACTIVE
    # the HTMX documents modal renders for governance readers
    assert client.get(reverse("vendors_documents_modal", args=[active.id])).status_code == 200

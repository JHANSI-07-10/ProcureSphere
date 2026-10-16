"""Procurement Executive Dashboard HTMX workspace (4 pillars).

Covers the new HTMX fragment/action endpoints in vendors, sourcing and orders:
positive RBAC (PROC_EXEC), negative RBAC (Requester/Vendor), service wiring
(approve, risk, create event, amend PO) and validation failures. No mock data.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role
from apps.orders.models import POAmendment
from apps.requisitions.models import PurchaseRequisition
from apps.sourcing.models import SourcingEvent
from apps.vendors.models import Vendor, VendorRiskRecord
from apps.vendors.services import upload_vendor_document_service

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture(autouse=True)
def _fast_password_hashes(settings):
    """Speed up fixture user creation (auth behavior is not under test)."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _login(client, user):
    client.force_login(user)


def _submitted_vendor_with_doc(world):
    vendor = world["vendor_draft"]
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.STATUS_SUBMITTED)
    vendor.refresh_from_db()
    upload_vendor_document_service(
        vendor=vendor,
        user=world["exec"],
        file=SimpleUploadedFile("kyc.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
        document_type="TAX_CLEARANCE",
        title="Tax clearance",
    )
    return vendor


def _approved_pr(world):
    return PurchaseRequisition.objects.create(
        pr_number="PR-TEST-00001",
        title="Test laptops",
        justification="Fleet refresh",
        requester=world["requester"],
        department=world["cost_center"].department,
        cost_center=world["cost_center"],
        status=PurchaseRequisition.STATUS_APPROVED,
        total_amount=Decimal("5000.00"),
        requested_delivery_date=timezone.now().date() + timedelta(days=30),
    )


def _draft_event(world, pr=None):
    from apps.sourcing.services import create_sourcing_event_service

    now = timezone.now()
    return create_sourcing_event_service(
        title="Test RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(hours=1),
        bid_end_date=now + timedelta(days=7),
        description="Supply test laptops",
        requisition=pr,
        technical_weight=Decimal("60.00"),
        commercial_weight=Decimal("40.00"),
        created_by_user=world["exec"],
    )


def _issued_po(world):
    from apps.orders.services import generate_purchase_order_service

    return generate_purchase_order_service(
        vendor=world["vendor_a"],
        cost_center=world["cost_center"],
        line_items=[
            {"item_description": "Laptop", "quantity": 10, "unit_price": "1200.00"},
        ],
        created_by_user=world["exec"],
    )


class TestPillar1VendorQualification:
    def test_qualification_tab_lists_submitted_and_kyc(self, client, procurement_world):
        w = procurement_world
        _submitted_vendor_with_doc(w)
        _login(client, w["exec"])
        body = client.get("/vendors/htmx/qualification/").content.decode()
        assert "Draft Vendor Co" in body
        assert "KYC Vendor Co" in body

    @pytest.mark.parametrize("user_key", ["requester", "stores", "user_a"])
    def test_qualification_tab_forbidden(self, client, procurement_world, user_key):
        _login(client, procurement_world[user_key])
        assert client.get("/vendors/htmx/qualification/").status_code == 403

    def test_documents_modal_and_verify(self, client, procurement_world):
        w = procurement_world
        vendor = _submitted_vendor_with_doc(w)
        _login(client, w["exec"])
        body = client.get(f"/vendors/{vendor.id}/htmx/documents/").content.decode()
        assert "Tax clearance" in body
        doc = vendor.documents.first()
        resp = client.post(
            f"/vendors/{vendor.id}/htmx/kyc/verify-document/",
            {"document_id": str(doc.id)},
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        doc.refresh_from_db()
        assert doc.is_verified is True

    def test_risk_modal_creates_record(self, client, procurement_world):
        w = procurement_world
        vendor = _submitted_vendor_with_doc(w)
        _login(client, w["exec"])
        assert client.get(f"/vendors/{vendor.id}/htmx/risk/").status_code == 200
        resp = client.post(
            f"/vendors/{vendor.id}/htmx/risk/",
            {"risk_level": "MEDIUM", "risk_flags": ["FINANCIAL"], "notes": "Thin margins"},
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        assert resp["HX-Trigger"] == "vendor-updated"
        assert VendorRiskRecord.objects.filter(vendor=vendor, risk_level="MEDIUM").exists()

    def test_kyc_approve_activates_vendor(self, client, procurement_world):
        w = procurement_world
        vendor = _submitted_vendor_with_doc(w)
        _login(client, w["exec"])
        # Activation is gated on verified documents.
        blocked = client.post(
            f"/vendors/{vendor.id}/htmx/kyc/approve/", {"notes": "x"}, HTTP_HX_REQUEST="true"
        )
        assert blocked.status_code == 400
        vendor.refresh_from_db()
        assert vendor.status == Vendor.STATUS_SUBMITTED
        client.post(
            f"/vendors/{vendor.id}/htmx/kyc/verify-document/",
            {"document_id": str(vendor.documents.first().id)},
            HTTP_HX_REQUEST="true",
        )
        resp = client.post(
            f"/vendors/{vendor.id}/htmx/kyc/approve/",
            {"notes": "Docs verified"},
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        vendor.refresh_from_db()
        assert vendor.status == Vendor.STATUS_ACTIVE

    def test_kyc_actions_forbidden_for_requester(self, client, procurement_world):
        w = procurement_world
        vendor = _submitted_vendor_with_doc(w)
        _login(client, w["requester"])
        assert (
            client.post(f"/vendors/{vendor.id}/htmx/kyc/approve/", {"notes": "x"}).status_code
            == 403
        )
        assert (
            client.post(
                f"/vendors/{vendor.id}/htmx/risk/",
                {"risk_level": "LOW", "notes": "x"},
            ).status_code
            == 403
        )


class TestPillar2Sourcing:
    def test_ready_prs_tab(self, client, procurement_world):
        w = procurement_world
        pr = _approved_pr(w)
        _login(client, w["exec"])
        body = client.get("/sourcing-events/htmx/ready-prs/").content.decode()
        assert pr.pr_number in body
        assert "Test laptops" in body

    def test_ready_prs_forbidden(self, client, procurement_world):
        _login(client, procurement_world["requester"])
        assert client.get("/sourcing-events/htmx/ready-prs/").status_code == 403

    def test_events_tab(self, client, procurement_world):
        w = procurement_world
        event = _draft_event(w)
        _login(client, w["exec"])
        body = client.get("/sourcing-events/htmx/events-tab/").content.decode()
        assert event.event_number in body

    def test_create_event_modal_and_post(self, client, procurement_world):
        w = procurement_world
        pr = _approved_pr(w)
        _login(client, w["exec"])
        assert client.get("/sourcing-events/htmx/create/").status_code == 200
        now = timezone.now()
        start = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        end = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        before = SourcingEvent.objects.count()
        resp = client.post(
            "/sourcing-events/htmx/create/",
            {
                "title": "HTMX RFQ",
                "event_type": "RFQ",
                "requisition": str(pr.id),
                "bid_start_date": start,
                "bid_end_date": end,
                "description": "Scope",
                "technical_weight": "60",
                "commercial_weight": "40",
            },
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        assert SourcingEvent.objects.count() == before + 1
        assert resp["HX-Trigger"] == "sourcing-updated"

    def test_create_event_rejects_bad_weights(self, client, procurement_world):
        w = procurement_world
        _login(client, w["exec"])
        now = timezone.now()
        resp = client.post(
            "/sourcing-events/htmx/create/",
            {
                "title": "Bad weights",
                "event_type": "RFQ",
                "bid_start_date": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "bid_end_date": (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"),
                "description": "Scope",
                "technical_weight": "70",
                "commercial_weight": "40",
            },
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 400

    def test_invites_fragment_and_invite_flow(self, client, procurement_world):
        w = procurement_world
        event = _draft_event(w)
        _login(client, w["exec"])
        body = client.get(f"/sourcing-events/{event.id}/htmx/invites/").content.decode()
        assert "No vendors invited yet" in body  # invitation table empty
        assert "Alpha Hardware Ltd" in body  # eligible ACTIVE vendor offered for invite
        resp = client.post(
            f"/sourcing-events/{event.id}/htmx/invites/",
            {"op": "invite", "vendor_ids": [str(w["vendor_a"].id)]},
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        assert event.invitations.filter(vendor=w["vendor_a"]).exists()

    def test_invite_ineligible_vendor_rejected(self, client, procurement_world):
        w = procurement_world
        event = _draft_event(w)
        _login(client, w["exec"])
        resp = client.post(
            f"/sourcing-events/{event.id}/htmx/invites/",
            {"op": "invite", "vendor_ids": [str(w["vendor_susp"].id)]},
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 400
        assert not event.invitations.filter(vendor=w["vendor_susp"]).exists()


class TestPillar3Evaluation:
    def test_sealed_fragment_before_close(self, client, procurement_world):
        w = procurement_world
        event = _draft_event(w)
        _login(client, w["exec"])
        body = client.get(f"/sourcing-events/{event.id}/htmx/evaluation/").content.decode()
        assert "sealed" in body.lower()

    def test_evaluation_forbidden_for_requester(self, client, procurement_world):
        w = procurement_world
        event = _draft_event(w)
        _login(client, w["requester"])
        assert client.get(f"/sourcing-events/{event.id}/htmx/evaluation/").status_code == 403

    def test_score_rejected_outside_technical_review(self, client, procurement_world):
        w = procurement_world
        from apps.sourcing import services as svc
        from apps.sourcing.models import VendorBid

        event = _draft_event(w)
        svc.invite_vendors_to_event_service(
            event=event, vendor_ids=[str(w["vendor_a"].id)], invited_by=w["exec"]
        )
        svc.publish_sourcing_event_service(event=event, user=w["exec"])
        bid = svc.submit_vendor_bid_service(
            event=event,
            vendor=w["vendor_a"],
            line_items=[
                {"item_description": "Laptop", "quantity": 10, "quoted_unit_price": "1100.00"}
            ],
            proposal_summary="Offer",
            technical_proposal="Tech offer",
        )
        assert bid.status in (VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED)
        _login(client, w["exec"])
        resp = client.post(
            f"/sourcing-events/{event.id}/htmx/score/",
            {"stage": "technical", "bid_id": str(bid.id), "score": "80"},
            HTTP_HX_REQUEST="true",
        )
        # BID_WINDOW is not TECHNICAL_REVIEW -> service raises ValidationError -> 400
        assert resp.status_code == 400


class TestPillar4POExecution:
    def test_awards_pending_tab(self, client, procurement_world):
        _login(client, procurement_world["exec"])
        assert client.get("/purchase-orders/htmx/awards-pending/").status_code == 200

    def test_awards_pending_forbidden(self, client, procurement_world):
        _login(client, procurement_world["requester"])
        assert client.get("/purchase-orders/htmx/awards-pending/").status_code == 403

    def test_amend_modal_get(self, client, procurement_world):
        w = procurement_world
        po = _issued_po(w)
        _login(client, w["exec"])
        body = client.get(f"/purchase-orders/{po.id}/htmx/amend/").content.decode()
        assert po.po_number in body
        assert "Amendment reason" in body

    def test_amend_post_increments_version_and_preserves_snapshot(self, client, procurement_world):
        w = procurement_world
        po = _issued_po(w)
        line = po.lines.first()
        old_total = str(po.total_amount)
        _login(client, w["exec"])
        resp = client.post(
            f"/purchase-orders/{po.id}/htmx/amend/",
            {
                "reason": "Customer increased quantity",
                f"quantity_{line.id}": "20",
                f"unit_price_{line.id}": "1200.00",
            },
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        assert resp["HX-Trigger"] == "po-amended"
        po.refresh_from_db()
        assert po.version == 2
        amendment = POAmendment.objects.get(po=po, amendment_number=1)
        assert Decimal(amendment.previous_version_snapshot["total_amount"]) == Decimal(old_total)
        assert amendment.reason == "Customer increased quantity"

    def test_amend_requires_reason(self, client, procurement_world):
        w = procurement_world
        po = _issued_po(w)
        line = po.lines.first()
        _login(client, w["exec"])
        resp = client.post(
            f"/purchase-orders/{po.id}/htmx/amend/",
            {
                "reason": "",
                f"quantity_{line.id}": "10",
                f"unit_price_{line.id}": "1200.00",
            },
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 400
        po.refresh_from_db()
        assert po.version == 1
        assert not POAmendment.objects.filter(po=po).exists()

    @pytest.mark.parametrize("user_key", ["requester", "stores", "user_a"])
    def test_amend_forbidden(self, client, procurement_world, user_key):
        w = procurement_world
        po = _issued_po(w)
        _login(client, w[user_key])
        assert client.get(f"/purchase-orders/{po.id}/htmx/amend/").status_code == 403

    def test_detail_shows_amend_ui(self, client, procurement_world):
        w = procurement_world
        po = _issued_po(w)
        _login(client, w["exec"])
        body = client.get(f"/purchase-orders/{po.id}/").content.decode()
        assert "Amend PO" in body
        assert "Amendment History" in body


class TestDashboardWorkspace:
    def test_proc_exec_dashboard_renders_workspace(self, client, procurement_world):
        _login(client, procurement_world["exec"])
        body = client.get("/").content.decode()
        assert "Executive workspace" in body
        assert "Vendor Qualification" in body
        assert "PO Execution" in body

    def test_requester_dashboard_has_no_exec_workspace(self, client, procurement_world):
        _login(client, procurement_world["requester"])
        body = client.get("/").content.decode()
        assert "Executive workspace" not in body
        assert procurement_world["requester"].role.code == Role.REQUESTER

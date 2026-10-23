from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.sourcing.models import (
    BidVersion,
    Clarification,
    SourcingEvent,
    VendorBid,
)
from apps.sourcing.services import (
    create_sourcing_event_service,
    invite_vendors_to_event_service,
    publish_sourcing_event_service,
    submit_vendor_bid_service,
    validate_bid_service,
)
from apps.vendors.models import Vendor, VendorCategory, VendorDocument
from apps.vendors.services import (
    register_vendor_service,
    upload_vendor_document_service,
)


@pytest.fixture
def vendor_setup(db_roles):
    """
    Sets up two separate vendors with users, organizations, and categories
    to rigorously test multi-tenant isolation, RBAC, and IDOR prevention.
    """
    org = Organization.objects.create(name="HPE Global", code="HPE-GLB")
    dept = Department.objects.create(name="Procurement", code="PROC", organization=org)
    cost_center = CostCenter.objects.create(name="IT Hardware", code="CC-IT-01", department=dept)
    category = VendorCategory.objects.create(name="Enterprise Hardware", code="CAT-HARDWARE")

    # Procurement Staff
    proc_exec = User.objects.create_user(
        email="proc_exec@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )

    # Vendor A
    vendor_a = register_vendor_service(
        legal_name="Acme Technology Solutions Inc",
        tax_identification_number="TIN-ACME-001",
        category=category,
        email="contact@acme.com",
        address="100 Technology Way, San Jose, CA",
    )
    vendor_a.status = Vendor.STATUS_ACTIVE
    vendor_a.save()

    user_vendor_a = User.objects.create_user(
        email="agent@acme.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor_a,
    )

    # Vendor B
    vendor_b = register_vendor_service(
        legal_name="Global Systems Partner Ltd",
        tax_identification_number="TIN-GLOBAL-002",
        category=category,
        email="contact@globalsys.com",
        address="500 Enterprise Blvd, Austin, TX",
    )
    vendor_b.status = Vendor.STATUS_ACTIVE
    vendor_b.save()

    user_vendor_b = User.objects.create_user(
        email="agent@globalsys.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor_b,
    )

    return {
        "org": org,
        "cost_center": cost_center,
        "proc_exec": proc_exec,
        "vendor_a": vendor_a,
        "user_vendor_a": user_vendor_a,
        "vendor_b": vendor_b,
        "user_vendor_b": user_vendor_b,
    }


# ==============================================================================
# 1. AUTHENTICATION & ACCESS CONTROL TESTS
# ==============================================================================


@pytest.mark.django_db
def test_unauthenticated_user_redirected_from_vendor_dashboard(client):
    """Unauthenticated requests to /vendor/ are redirected to login."""
    response = client.get("/vendor/")
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_non_vendor_role_rejected_from_vendor_dashboard(client, vendor_setup):
    """Internal enterprise users (e.g. PROC_EXEC) are denied access (HTTP 403) to /vendor/."""
    client.force_login(vendor_setup["proc_exec"])
    response = client.get("/vendor/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_authenticated_vendor_can_access_dashboard(client, vendor_setup):
    """Authenticated VENDOR_USER accesses dashboard home with real KPI data."""
    client.force_login(vendor_setup["user_vendor_a"])
    response = client.get("/vendor/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Vendor Portal" in content
    assert "Acme Technology Solutions Inc" in content


# ==============================================================================
# 2. COMPANY PROFILE & KYC ISOLATION TESTS
# ==============================================================================


@pytest.mark.django_db
def test_vendor_can_view_and_update_own_profile(client, vendor_setup):
    """Vendor can view their own profile and update registered company info."""
    client.force_login(vendor_setup["user_vendor_a"])
    response = client.get("/vendor/profile/")
    assert response.status_code == 200
    assert "TIN-ACME-001" in response.content.decode()

    # Post update
    update_data = {
        "action": "update_profile",
        "trade_name": "Acme Tech Solutions",
        "bank_name": "Silicon Valley Commercial Bank",
        "bank_account_number": "9876543210",
        "bank_routing_code": "121000358",
        "address": "200 Innovation Parkway, San Jose, CA",
        "phone": "+919876543210",
    }
    post_res = client.post("/vendor/profile/", update_data, follow=True)
    assert post_res.status_code == 200

    vendor_setup["vendor_a"].refresh_from_db()
    assert vendor_setup["vendor_a"].trade_name == "Acme Tech Solutions"
    assert vendor_setup["vendor_a"].bank_name == "Silicon Valley Commercial Bank"
    assert vendor_setup["vendor_a"].address == "200 Innovation Parkway, San Jose, CA"
    assert vendor_setup["vendor_a"].phone == "+919876543210"


@pytest.mark.django_db
def test_vendor_phone_validation_on_dashboard_requires_plus_91_and_10_digits(client, vendor_setup):
    """
    On the Vendor Dashboard, phone number validation strictly enforces
    country code (+91) followed by exactly 10 digits.
    """
    client.force_login(vendor_setup["user_vendor_a"])

    base_data = {
        "action": "update_profile",
        "trade_name": "Acme Tech",
        "address": "123 Tech Park",
        "bank_name": "Test Bank",
        "bank_account_number": "1234567890",
        "bank_routing_code": "ROUTING01",
    }

    # 1. Invalid: Missing +91 country code (e.g. 10 digits only)
    res_no_code = client.post("/vendor/profile/", {**base_data, "phone": "9876543210"}, follow=True)
    assert res_no_code.status_code == 200
    assert "country code (+91)" in res_no_code.content.decode()

    # 2. Invalid: Wrong country code (+1 instead of +91)
    res_wrong_code = client.post("/vendor/profile/", {**base_data, "phone": "+19876543210"}, follow=True)
    assert res_wrong_code.status_code == 200
    assert "country code (+91)" in res_wrong_code.content.decode()

    # 3. Invalid: +91 with only 9 digits
    res_short = client.post("/vendor/profile/", {**base_data, "phone": "+91987654321"}, follow=True)
    assert res_short.status_code == 200
    assert "exactly 10 digits" in res_short.content.decode()

    # 4. Invalid: +91 with 11 digits
    res_long = client.post("/vendor/profile/", {**base_data, "phone": "+9198765432109"}, follow=True)
    assert res_long.status_code == 200
    assert "exactly 10 digits" in res_long.content.decode()

    # 5. Invalid: 12-digit number NOT starting with 91
    res_invalid_12 = client.post("/vendor/profile/", {**base_data, "phone": "123456789012"}, follow=True)
    assert res_invalid_12.status_code == 200
    assert "country code (+91)" in res_invalid_12.content.decode()

    # 6. Invalid: Empty phone
    res_empty = client.post("/vendor/profile/", {**base_data, "phone": ""}, follow=True)
    assert res_empty.status_code == 200
    assert "required" in res_empty.content.decode().lower()

    # 7. Valid: 12 numbers starting with 91 (e.g. 919876543210 auto-normalizes to +919876543210)
    res_12_valid = client.post("/vendor/profile/", {**base_data, "phone": "919876543210"}, follow=True)
    assert res_12_valid.status_code == 200
    assert "updated successfully" in res_12_valid.content.decode()
    vendor_setup["vendor_a"].refresh_from_db()
    assert vendor_setup["vendor_a"].phone == "+919876543210"

    # 8. Valid: +91 followed by 10 digits with space/hyphen formatting (normalizes)
    res_valid_spaced = client.post("/vendor/profile/", {**base_data, "phone": "+91 9876543210"}, follow=True)
    assert res_valid_spaced.status_code == 200
    assert "updated successfully" in res_valid_spaced.content.decode()

    vendor_setup["vendor_a"].refresh_from_db()
    assert vendor_setup["vendor_a"].phone == "+919876543210"



@pytest.mark.django_db
def test_vendor_kyc_document_upload(client, vendor_setup):
    """Vendor uploads a KYC document securely."""
    client.force_login(vendor_setup["user_vendor_a"])
    file_content = b"%PDF-1.4 sample kyc tax certificate content"
    test_pdf = SimpleUploadedFile("tax_cert.pdf", file_content, content_type="application/pdf")

    post_data = {
        "action": "upload_kyc",
        "document_type": VendorDocument.DOC_TYPE_TAX,
        "title": "Corporate Tax Certificate 2026",
        "kyc_file": test_pdf,
    }
    res = client.post("/vendor/profile/", post_data, follow=True)
    assert res.status_code == 200

    docs = VendorDocument.objects.filter(vendor=vendor_setup["vendor_a"])
    assert docs.count() == 1
    assert docs.first().title == "Corporate Tax Certificate 2026"


# ==============================================================================
# 3. SOURCING & INVITATION LEVEL AUTHORIZATION (NO IDOR)
# ==============================================================================


@pytest.mark.django_db
def test_vendor_only_sees_invited_sourcing_events(client, vendor_setup):
    """Vendor A sees only events where Vendor A is invited; uninvited events are hidden."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    # Event 1: Invited Vendor A
    event_a = create_sourcing_event_service(
        title="Server Cluster RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=5),
        description="Compute nodes",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event_a, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event_a, user=exec_user)

    # Event 2: Invited Vendor B ONLY
    event_b = create_sourcing_event_service(
        title="Network Switches RFP",
        event_type=SourcingEvent.TYPE_RFP,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=5),
        description="Core switches",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event_b, vendor_ids=[vendor_setup["vendor_b"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event_b, user=exec_user)

    # Vendor A accesses sourcing list
    client.force_login(vendor_setup["user_vendor_a"])
    res = client.get("/vendor/sourcing/")
    assert res.status_code == 200
    content = res.content.decode()
    assert event_a.event_number in content
    assert event_b.event_number not in content


@pytest.mark.django_db
def test_vendor_uninvited_event_detail_rejected_with_403(client, vendor_setup):
    """IDOR TEST: Accessing uninvited event by changing ID in URL returns HTTP 403 Forbidden."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    # Event invited only to Vendor B
    event_b = create_sourcing_event_service(
        title="Confidential Telecom RFP",
        event_type=SourcingEvent.TYPE_RFP,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=5),
        description="Private telecom requirements",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event_b, vendor_ids=[vendor_setup["vendor_b"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event_b, user=exec_user)

    # Vendor A tries to access event_b detail directly
    client.force_login(vendor_setup["user_vendor_a"])
    res = client.get(f"/vendor/sourcing/{event_b.id}/")
    assert res.status_code == 403


# ==============================================================================
# 4. BID LIFECYCLE, SEALED ENVELOPES & VALIDATION
# ==============================================================================


@pytest.mark.django_db
def test_bid_draft_creation_and_save(client, vendor_setup):
    """Vendor can prepare a bid, save draft line items, and inspect envelopes."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="High Density Storage RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=5),
        description="Storage shelves and controllers",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    client.force_login(vendor_setup["user_vendor_a"])
    res_get = client.get(f"/vendor/bids/create/{event.id}/")
    assert res_get.status_code == 200

    # Save Draft
    draft_data = {
        "action": "save_draft",
        "proposal_summary": "Initial draft proposal for storage array",
        "technical_proposal": "Full NVMe array with redundant power supplies",
        "commercial_proposal": "Net 30 terms, 3 year warranty included",
        "line_description[]": ["NVMe Storage Node 100TB", "SAS Expansion Shelf"],
        "line_quantity[]": ["2", "4"],
        "line_price[]": ["12500.00", "4500.00"],
    }
    res_post = client.post(f"/vendor/bids/create/{event.id}/", draft_data, follow=True)
    assert res_post.status_code == 200

    bid = VendorBid.objects.get(event=event, vendor=vendor_setup["vendor_a"])
    assert bid.status == VendorBid.STATUS_DRAFT
    assert bid.version == 1
    assert bid.lines.count() == 2
    # Total: (2 * 12500) + (4 * 4500) = 25000 + 18000 = 43000
    assert bid.total_bid_amount == Decimal("43000.00")


@pytest.mark.django_db
def test_bid_validation_detects_empty_and_zero_items(vendor_setup):
    """Server-side validation service rejects incomplete bids."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Test RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=5),
        description="Validation test event",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    bid = VendorBid.objects.create(
        event=event,
        vendor=vendor_setup["vendor_a"],
        bid_number="BID-VAL-001",
        status=VendorBid.STATUS_DRAFT,
        version=1,
    )

    # Empty bid
    result = validate_bid_service(bid=bid)
    assert result["is_valid"] is False
    assert len(result["errors"]) > 0


@pytest.mark.django_db
def test_bid_submission_creates_v1_snapshot_and_audit(client, vendor_setup):
    """
    Submitting a validated bid:
    - Changes status to STATUS_SUBMITTED
    - Sets submitted_at timestamp
    - Generates immutable BidVersion snapshot
    - Logs an AuditLog record
    """
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="GPU Server RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=5),
        description="H100 accelerator nodes",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    client.force_login(vendor_setup["user_vendor_a"])

    submit_data = {
        "action": "submit_bid",
        "proposal_summary": "Final sealed commercial and technical proposal",
        "technical_proposal": "Certified dual socket nodes with 8x H100 SXM5",
        "commercial_proposal": "Standard enterprise commercial terms",
        "line_description[]": ["GPU Node H100"],
        "line_quantity[]": ["4"],
        "line_price[]": ["285000.00"],
    }
    res = client.post(f"/vendor/bids/create/{event.id}/", submit_data, follow=True)
    assert res.status_code == 200

    bid = VendorBid.objects.get(event=event, vendor=vendor_setup["vendor_a"])
    assert bid.status == VendorBid.STATUS_SUBMITTED
    assert bid.submitted_at is not None
    assert bid.version == 1

    # Check immutable version snapshot
    versions = BidVersion.objects.filter(bid=bid)
    assert versions.count() == 1
    v1 = versions.first()
    assert v1.version_number == 1
    assert Decimal(str(v1.total_bid_amount)) == Decimal("1140000.00")
    assert len(v1.snapshot_data["lines"]) == 1

    # Check Audit log
    audit = AuditLog.objects.filter(target_model="VendorBid", target_object_id=str(bid.id))
    assert audit.exists()


# ==============================================================================
# 5. SERVER-SIDE DEADLINE SECURITY TESTS
# ==============================================================================


@pytest.mark.django_db
def test_bid_submission_rejected_after_deadline(vendor_setup):
    """Submissions attempted after the deadline are strictly rejected server-side."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    # Event published while open; its deadline then passed 1 hour ago
    event = create_sourcing_event_service(
        title="Expired RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=7),
        bid_end_date=now + timedelta(days=1),
        description="Past deadline event",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)
    event.bid_end_date = now - timedelta(hours=1)
    event.save(update_fields=["bid_end_date"])

    lines = [
        {
            "item_description": "Expired Node",
            "quantity": Decimal("1"),
            "quoted_unit_price": Decimal("100.00"),
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        submit_vendor_bid_service(
            event=event,
            vendor=vendor_setup["vendor_a"],
            submitted_by_user=vendor_setup["user_vendor_a"],
            line_items=lines,
            proposal_summary="Late bid",
            technical_proposal="Late specs",
            commercial_proposal="Late terms",
        )
    assert "deadline" in str(exc_info.value).lower()


# ==============================================================================
# 6. IMMUTABLE BID AMENDMENTS & VERSIONING TESTS
# ==============================================================================


@pytest.mark.django_db
def test_bid_amendment_before_deadline_creates_v2_without_overwriting_v1(client, vendor_setup):
    """
    Vendor amends submitted bid before deadline:
    - Version increments to 2
    - Previous V1 immutable snapshot remains intact
    - Status set to STATUS_AMENDED
    """
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Network Core Switch RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=4),
        description="Switching infrastructure",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    # Initial submission (V1)
    v1_lines = [
        {
            "item_description": "Core Switch 100G",
            "quantity": Decimal("2"),
            "quoted_unit_price": Decimal("20000.00"),
        }
    ]
    bid = submit_vendor_bid_service(
        event=event,
        vendor=vendor_setup["vendor_a"],
        submitted_by_user=vendor_setup["user_vendor_a"],
        line_items=v1_lines,
        proposal_summary="V1 Proposal",
        technical_proposal="V1 Specs",
        commercial_proposal="V1 Terms",
    )
    assert bid.version == 1
    assert bid.total_bid_amount == Decimal("40000.00")

    # Amend bid (V2)
    client.force_login(vendor_setup["user_vendor_a"])
    amend_data = {
        "amendment_reason": "Price discount negotiated with component supplier",
        "proposal_summary": "V2 Discounted Proposal",
        "technical_proposal": "V2 Specs with updated firmware guarantee",
        "commercial_proposal": "5% discount applied",
        "line_description[]": ["Core Switch 100G"],
        "line_quantity[]": ["2"],
        "line_price[]": ["19000.00"],  # Discounted from 20000 to 19000
    }
    res = client.post(f"/vendor/bids/{bid.id}/amend/", amend_data, follow=True)
    assert res.status_code == 200

    bid.refresh_from_db()
    assert bid.version == 2
    assert bid.status == VendorBid.STATUS_AMENDED
    assert bid.total_bid_amount == Decimal("38000.00")

    # Verify both V1 and V2 snapshots exist in BidVersion
    versions = list(BidVersion.objects.filter(bid=bid).order_by("version_number"))
    assert len(versions) == 2
    assert versions[0].version_number == 1
    assert Decimal(str(versions[0].total_bid_amount)) == Decimal("40000.00")
    assert versions[1].version_number == 2
    assert Decimal(str(versions[1].total_bid_amount)) == Decimal("38000.00")
    assert versions[1].amendment_reason == "Price discount negotiated with component supplier"


@pytest.mark.django_db
def test_bid_amendment_rejected_after_deadline(client, vendor_setup):
    """Amendment attempted after deadline is rejected with a business error."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Router RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=5),
        bid_end_date=now + timedelta(days=2),
        description="Edge Routers",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    v1_lines = [
        {
            "item_description": "Router",
            "quantity": Decimal("1"),
            "quoted_unit_price": Decimal("5000.00"),
        }
    ]
    bid = submit_vendor_bid_service(
        event=event,
        vendor=vendor_setup["vendor_a"],
        submitted_by_user=vendor_setup["user_vendor_a"],
        line_items=v1_lines,
        proposal_summary="Initial",
        technical_proposal="Specs",
        commercial_proposal="Terms",
    )

    # Fast forward event deadline into the past
    event.bid_end_date = now - timedelta(hours=2)
    event.save()

    client.force_login(vendor_setup["user_vendor_a"])
    amend_data = {
        "amendment_reason": "Late change",
        "line_description[]": ["Router"],
        "line_quantity[]": ["1"],
        "line_price[]": ["4800.00"],
    }
    res = client.post(f"/vendor/bids/{bid.id}/amend/", amend_data, follow=True)
    assert res.status_code == 200
    assert "deadline passed" in res.content.decode().lower()

    bid.refresh_from_db()
    assert bid.version == 1  # Unchanged


@pytest.mark.django_db
def test_bid_withdrawal_before_deadline_preserves_version_and_updates_status(client, vendor_setup):
    """
    Vendor withdraws submitted bid before deadline:
    - Status transitions to STATUS_WITHDRAWN
    - Immutable snapshot in BidVersion is preserved
    - Withdrawn bid appears under 'withdrawn' tab
    """
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Fiber Cable RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=3),
        description="Fiber optic links",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    lines = [
        {
            "item_description": "Single-mode fiber spool",
            "quantity": Decimal("10"),
            "quoted_unit_price": Decimal("500.00"),
        }
    ]
    bid = submit_vendor_bid_service(
        event=event,
        vendor=vendor_setup["vendor_a"],
        submitted_by_user=vendor_setup["user_vendor_a"],
        line_items=lines,
        proposal_summary="Fiber proposal",
        technical_proposal="Technical specs",
        commercial_proposal="Commercial terms",
    )
    assert bid.status == VendorBid.STATUS_SUBMITTED

    client.force_login(vendor_setup["user_vendor_a"])

    # Withdraw bid with business reason
    withdraw_data = {
        "withdrawal_reason": "Supply chain interruption prevented fulfilling delivery commitments",
    }
    res = client.post(f"/vendor/bids/{bid.id}/withdraw/", withdraw_data, follow=True)
    assert res.status_code == 200

    bid.refresh_from_db()
    assert bid.status == VendorBid.STATUS_WITHDRAWN

    # Verify immutable snapshot exists
    assert bid.versions.filter(version_number=1).exists()

    # Detail view reflects withdrawn banner and disabled actions
    detail_res = client.get(f"/vendor/bids/{bid.id}/")
    assert detail_res.status_code == 200
    assert "Bid Formally Withdrawn" in detail_res.content.decode()
    assert detail_res.context["can_withdraw"] is False
    assert detail_res.context["can_amend"] is False

    # Listed under withdrawn tab
    list_res = client.get("/vendor/bids/?tab=withdrawn")
    assert list_res.status_code == 200
    assert bid.bid_number in list_res.content.decode()


@pytest.mark.django_db
def test_bid_withdrawal_rejected_after_deadline(client, vendor_setup):
    """Bid withdrawal attempted after deadline is rejected."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Firewall RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=4),
        bid_end_date=now + timedelta(days=1),
        description="Next-gen firewalls",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    lines = [
        {
            "item_description": "Hardware Appliance",
            "quantity": Decimal("1"),
            "quoted_unit_price": Decimal("12000.00"),
        }
    ]
    bid = submit_vendor_bid_service(
        event=event,
        vendor=vendor_setup["vendor_a"],
        submitted_by_user=vendor_setup["user_vendor_a"],
        line_items=lines,
        proposal_summary="Security appliance",
        technical_proposal="Specs",
        commercial_proposal="Terms",
    )

    # Fast forward event past deadline
    event.bid_end_date = now - timedelta(hours=1)
    event.save()

    client.force_login(vendor_setup["user_vendor_a"])
    withdraw_data = {"withdrawal_reason": "Attempting late withdrawal"}
    res = client.post(f"/vendor/bids/{bid.id}/withdraw/", withdraw_data, follow=True)
    assert res.status_code == 200
    assert "deadline" in res.content.decode().lower()

    bid.refresh_from_db()
    assert bid.status == VendorBid.STATUS_SUBMITTED  # Remains submitted


@pytest.mark.django_db
def test_withdrawn_bid_cannot_be_amended_or_resubmitted(client, vendor_setup):
    """Withdrawn bids cannot be amended or edited/re-submitted."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Storage Array RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=3),
        description="SAN Storage",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    lines = [
        {
            "item_description": "SAN Storage Controller",
            "quantity": Decimal("1"),
            "quoted_unit_price": Decimal("25000.00"),
        }
    ]
    bid = submit_vendor_bid_service(
        event=event,
        vendor=vendor_setup["vendor_a"],
        submitted_by_user=vendor_setup["user_vendor_a"],
        line_items=lines,
        proposal_summary="Storage",
        technical_proposal="Specs",
        commercial_proposal="Terms",
    )

    client.force_login(vendor_setup["user_vendor_a"])
    client.post(
        f"/vendor/bids/{bid.id}/withdraw/",
        {"withdrawal_reason": "Business restructuring"},
        follow=True,
    )

    bid.refresh_from_db()
    assert bid.status == VendorBid.STATUS_WITHDRAWN

    # Attempting to amend fails
    amend_data = {
        "amendment_reason": "Trying to amend withdrawn bid",
        "line_description[]": ["SAN Storage Controller"],
        "line_quantity[]": ["1"],
        "line_price[]": ["22000.00"],
    }
    res_amend = client.post(f"/vendor/bids/{bid.id}/amend/", amend_data, follow=True)
    assert res_amend.status_code == 200
    assert "Only submitted bids can be amended" in res_amend.content.decode()

    # Attempting to access create view redirects with warning
    res_create = client.get(f"/vendor/bids/create/{event.id}/", follow=True)
    assert res_create.status_code == 200
    assert "withdrawn and cannot be modified" in res_create.content.decode()


# ==============================================================================
# 7. CROSS-VENDOR IDOR / BOLA PROTECTION TESTS
# ==============================================================================


@pytest.mark.django_db
def test_vendor_cannot_view_or_amend_another_vendors_bid(client, vendor_setup):
    """
    IDOR TEST: Vendor B attempting to access or amend Vendor A's bid returns HTTP 403 Forbidden.
    """
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Blade Servers RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=3),
        description="Blade chassis and nodes",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event,
        vendor_ids=[vendor_setup["vendor_a"].id, vendor_setup["vendor_b"].id],
        invited_by=exec_user,
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    # Vendor A submits bid
    v1_lines = [
        {
            "item_description": "Blade Node",
            "quantity": Decimal("10"),
            "quoted_unit_price": Decimal("8000.00"),
        }
    ]
    bid_a = submit_vendor_bid_service(
        event=event,
        vendor=vendor_setup["vendor_a"],
        submitted_by_user=vendor_setup["user_vendor_a"],
        line_items=v1_lines,
        proposal_summary="Acme proposal",
        technical_proposal="Acme specs",
        commercial_proposal="Acme terms",
    )

    # Vendor B attempts direct GET of bid_a
    client.force_login(vendor_setup["user_vendor_b"])
    res_get = client.get(f"/vendor/bids/{bid_a.id}/")
    assert res_get.status_code == 403

    # Vendor B attempts direct POST amendment to bid_a
    res_amend = client.post(
        f"/vendor/bids/{bid_a.id}/amend/",
        {
            "amendment_reason": "Malicious tampering",
            "line_description[]": ["Hack"],
            "line_quantity[]": ["1"],
            "line_price[]": ["1.00"],
        },
    )
    assert res_amend.status_code == 403


# ==============================================================================
# 8. PURCHASE ORDER REVIEW & ACKNOWLEDGEMENT TESTS
# ==============================================================================


@pytest.mark.django_db
def test_vendor_can_view_and_acknowledge_own_po(client, vendor_setup):
    """Vendor can review issued PO and formally acknowledge it with audit timestamps."""
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-0099",
        vendor=vendor_setup["vendor_a"],
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("50000.00"),
        tax_amount=Decimal("4000.00"),
        total_amount=Decimal("54000.00"),
        terms_and_conditions="Standard Payment Terms Net 30",
    )
    POLine.objects.create(
        po=po,
        item_description="Enterprise Compute Server",
        quantity=Decimal("5"),
        unit_price=Decimal("10000.00"),
        line_total=Decimal("50000.00"),
    )

    client.force_login(vendor_setup["user_vendor_a"])
    # View PO list & detail
    res_list = client.get("/vendor/purchase-orders/?tab=pending_ack")
    assert res_list.status_code == 200
    assert po.po_number in res_list.content.decode()

    res_detail = client.get(f"/vendor/purchase-orders/{po.id}/")
    assert res_detail.status_code == 200
    assert "Enterprise Compute Server" in res_detail.content.decode()

    # Acknowledge PO
    ack_res = client.post(
        f"/vendor/purchase-orders/{po.id}/acknowledge/",
        {"acknowledgement_notes": "Accepted. Production dispatched under ticket #9901."},
        follow=True,
    )
    assert ack_res.status_code == 200

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACKNOWLEDGED
    assert po.acknowledged_at is not None
    assert po.acknowledged_by == vendor_setup["user_vendor_a"]
    assert "9901" in po.acknowledgement_notes


@pytest.mark.django_db
def test_vendor_cannot_view_or_acknowledge_other_vendors_po(client, vendor_setup):
    """IDOR TEST: Vendor B attempting to access or acknowledge Vendor A's PO receives HTTP 403."""
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-CONFIDENTIAL",
        vendor=vendor_setup["vendor_a"],
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("100000.00"),
    )

    client.force_login(vendor_setup["user_vendor_b"])

    # Attempt GET
    res_get = client.get(f"/vendor/purchase-orders/{po.id}/")
    assert res_get.status_code == 403

    # Attempt POST Acknowledgement
    res_post = client.post(
        f"/vendor/purchase-orders/{po.id}/acknowledge/", {"acknowledgement_notes": "Fake ack"}
    )
    assert res_post.status_code == 403


# ==============================================================================
# 9. CLARIFICATIONS MODULE TESTS
# ==============================================================================


@pytest.mark.django_db
def test_clarifications_ask_and_view(client, vendor_setup):
    """Vendor can ask a clarification on an invited event and view history."""
    now = timezone.now()
    exec_user = vendor_setup["proc_exec"]

    event = create_sourcing_event_service(
        title="Power Redundancy RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=1),
        bid_end_date=now + timedelta(days=3),
        description="Facility power",
        created_by_user=exec_user,
    )
    invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor_setup["vendor_a"].id], invited_by=exec_user
    )
    publish_sourcing_event_service(event=event, user=exec_user)

    client.force_login(vendor_setup["user_vendor_a"])
    post_data = {
        "event_id": str(event.id),
        "question": "Is dual redundant N+1 power feed mandatory for all racks?",
    }
    res = client.post("/vendor/clarifications/", post_data, follow=True)
    assert res.status_code == 200

    clarifications = Clarification.objects.filter(event=event, vendor=vendor_setup["vendor_a"])
    assert clarifications.count() == 1
    assert "N+1" in clarifications.first().question


# ==============================================================================
# 10. SECURE DOCUMENT VAULT TESTS
# ==============================================================================


@pytest.mark.django_db
def test_secure_document_download_authorization(client, vendor_setup):
    """Authorized vendor can download own file; cross-vendor download returns HTTP 403."""
    client.force_login(vendor_setup["user_vendor_a"])
    file_content = b"%PDF-1.4 confidential vendor tax document bytes"
    test_pdf = SimpleUploadedFile("vendor_a_tax.pdf", file_content, content_type="application/pdf")

    doc_a = upload_vendor_document_service(
        vendor=vendor_setup["vendor_a"],
        user=vendor_setup["user_vendor_a"],
        file=test_pdf,
        document_type=VendorDocument.DOC_TYPE_TAX,
        title="Vendor A Secret Tax",
    )

    # Vendor A can download
    res_a = client.get(f"/vendor/documents/download/kyc/{doc_a.id}/")
    assert res_a.status_code == 200

    # Vendor B cannot download (403 Forbidden)
    client.force_login(vendor_setup["user_vendor_b"])
    res_b = client.get(f"/vendor/documents/download/kyc/{doc_a.id}/")
    assert res_b.status_code == 403


# ==============================================================================
# 11. NOTIFICATIONS & REPORTS
# ==============================================================================


@pytest.mark.django_db
def test_notifications_and_reports_render(client, vendor_setup):
    """Vendor notifications and reports render real database records without error."""
    # Create notification for Vendor A's user
    Notification.objects.create(
        recipient=vendor_setup["user_vendor_a"],
        notification_type=Notification.TYPE_RFQ_INVITATION,
        title="New RFQ Invitation Received",
        message="You have been invited to participate in Server Cluster RFQ.",
        target_url="/vendor/sourcing/",
    )

    client.force_login(vendor_setup["user_vendor_a"])

    # Notifications page
    res_notif = client.get("/vendor/notifications/")
    assert res_notif.status_code == 200
    assert "New RFQ Invitation Received" in res_notif.content.decode()

    # Reports page
    res_rep = client.get("/vendor/reports/?tab=bids")
    assert res_rep.status_code == 200

    # Account page
    res_acc = client.get("/vendor/account/")
    assert res_acc.status_code == 200
    assert "agent@acme.com" in res_acc.content.decode()


@pytest.mark.django_db
def test_vendor_submit_partial_invoice(client, vendor_setup):
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    v2 = vendor_setup["vendor_b"]
    u2 = vendor_setup["user_vendor_b"]
    proc_exec = vendor_setup["proc_exec"]
    cost_center = vendor_setup["cost_center"]

    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=cost_center,
        created_by=proc_exec,
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("300.00"),
    )
    po_line1 = POLine.objects.create(
        po=po, item_description="Server Rack", quantity=Decimal("2"), unit_price=Decimal("100.00"), line_total=Decimal("200.00")
    )
    po_line2 = POLine.objects.create(
        po=po, item_description="Network Cable", quantity=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )

    client.force_login(u2)
    response = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-V2-001",
            "invoice_date": timezone.now().date(),
            "due_date": timezone.now().date() + timedelta(days=30),
            f"qty_{po_line1.id}": "1",
            f"price_{po_line1.id}": "100.00",
        },
    )
    assert response.status_code == 403

    client.force_login(u1)
    response = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-V1-001",
            "invoice_date": timezone.now().date(),
            "due_date": timezone.now().date() + timedelta(days=30),
            "notes": "Partial shipment for rack 1",
            f"qty_{po_line1.id}": "1",
            f"price_{po_line1.id}": "105.00",
            f"qty_{po_line2.id}": "0",
            f"price_{po_line2.id}": "10.00",
        },
    )
    assert response.status_code == 302
    assert response.url == f"/vendor/purchase-orders/{po.id}/"

    from apps.invoices.models import SupplierInvoice
    invoice = SupplierInvoice.objects.get(invoice_number="INV-V1-001")
    assert invoice.vendor == v1
    assert invoice.po == po
    assert invoice.lines.count() == 1
    
    inv_line = invoice.lines.first()
    assert inv_line.po_line == po_line1
    assert inv_line.quantity == Decimal("1")
    assert inv_line.unit_price == Decimal("105.00")
    assert inv_line.line_total == Decimal("105.00")


# ==============================================================================
# 11b. VENDOR INVOICE INTEGRATION & ERROR HANDLING TESTS
# ==============================================================================


@pytest.mark.django_db
def test_get_invoice_form_returns_200_for_eligible_po(client, vendor_setup):
    """1. GET request on the invoice submission form returns HTTP 200 for eligible PO."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )
    POLine.objects.create(
        po=po,
        item_description="Test Item",
        quantity=Decimal("2"),
        unit_price=Decimal("500.00"),
        line_total=Decimal("1000.00"),
    )

    client.force_login(u1)
    res = client.get(f"/vendor/purchase-orders/{po.id}/submit-invoice/")
    assert res.status_code == 200
    assert "Submit Invoice" in res.content.decode()


@pytest.mark.django_db
def test_post_with_valid_data_creates_invoice_successfully(client, vendor_setup):
    """2. Valid POST creates an invoice and lines successfully."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )
    line = POLine.objects.create(
        po=po,
        item_description="Hardware Module",
        quantity=Decimal("2"),
        unit_price=Decimal("500.00"),
        line_total=Decimal("1000.00"),
    )

    client.force_login(u1)
    res = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-VALID-001",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            "notes": "Full order delivery",
            f"qty_{line.id}": "2",
            f"price_{line.id}": "500.00",
        },
    )
    assert res.status_code == 302
    from apps.invoices.models import SupplierInvoice
    inv = SupplierInvoice.objects.get(invoice_number="INV-VALID-001")
    assert inv.vendor == v1
    assert inv.po == po
    assert inv.subtotal == Decimal("1000.00")
    assert inv.total_amount == Decimal("1100.00")
    assert inv.lines.count() == 1


@pytest.mark.django_db
def test_invalid_form_data_returns_validation_errors(client, vendor_setup):
    """3. Invalid form data returns validation errors and redirects back or preserves."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )
    line = POLine.objects.create(
        po=po,
        item_description="Hardware Module",
        quantity=Decimal("2"),
        unit_price=Decimal("500.00"),
        line_total=Decimal("1000.00"),
    )

    client.force_login(u1)
    # Missing invoice number
    res = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            f"qty_{line.id}": "2",
            f"price_{line.id}": "500.00",
        },
        follow=True,
    )
    assert res.status_code == 200
    assert "Invoice number, date, and due date are required" in res.content.decode()

    # Zero or negative quantity
    res2 = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-ZERO-QTY",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            f"qty_{line.id}": "0",
            f"price_{line.id}": "500.00",
        },
        follow=True,
    )
    assert res2.status_code == 200
    assert "greater than 0" in res2.content.decode()


@pytest.mark.django_db
def test_unauthenticated_user_cannot_submit_invoice(client, vendor_setup):
    """4. An unauthenticated user cannot submit an invoice."""
    v1 = vendor_setup["vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )

    # Anonymous GET
    res_get = client.get(f"/vendor/purchase-orders/{po.id}/submit-invoice/")
    assert res_get.status_code == 302
    assert "/login" in res_get.url

    # Anonymous POST
    res_post = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={"invoice_number": "INV-ANON-001"},
    )
    assert res_post.status_code == 302
    assert "/login" in res_post.url


@pytest.mark.django_db
def test_cross_vendor_cannot_access_or_submit_invoice(client, vendor_setup):
    """5. A different vendor cannot access or submit an invoice against another vendor's PO."""
    v1 = vendor_setup["vendor_a"]
    u2 = vendor_setup["user_vendor_b"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )

    client.force_login(u2)
    # Vendor B cannot GET Vendor A's invoice submission page
    res_get = client.get(f"/vendor/purchase-orders/{po.id}/submit-invoice/")
    assert res_get.status_code == 403

    # Vendor B cannot POST invoice against Vendor A's PO
    res_post = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={"invoice_number": "INV-ATTACK-001"},
    )
    assert res_post.status_code == 403


@pytest.mark.django_db
def test_invalid_or_nonexistent_po_uuid_returns_404(client, vendor_setup):
    """6. An invalid or nonexistent PO UUID is handled correctly."""
    import uuid

    client.force_login(vendor_setup["user_vendor_a"])
    random_id = uuid.uuid4()

    res_get = client.get(f"/vendor/purchase-orders/{random_id}/submit-invoice/")
    assert res_get.status_code == 404

    res_post = client.post(f"/vendor/purchase-orders/{random_id}/submit-invoice/", data={})
    assert res_post.status_code == 404


@pytest.mark.django_db
def test_ineligible_po_status_prevents_submission(client, vendor_setup):
    """7. An ineligible PO status prevents submission."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po_draft = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_DRAFT,
        total_amount=Decimal("1000.00"),
    )

    client.force_login(u1)
    # GET on DRAFT PO redirects back with error
    res_get = client.get(f"/vendor/purchase-orders/{po_draft.id}/submit-invoice/", follow=True)
    assert res_get.status_code == 200
    assert "not eligible for invoicing" in res_get.content.decode()

    # POST on DRAFT PO redirects back with error and creates nothing
    res_post = client.post(
        f"/vendor/purchase-orders/{po_draft.id}/submit-invoice/",
        data={"invoice_number": "INV-DRAFT-001"},
        follow=True,
    )
    assert res_post.status_code == 200
    assert "not eligible for invoicing" in res_post.content.decode()


@pytest.mark.django_db
def test_duplicate_invoice_submission_handled(client, vendor_setup):
    """8. Duplicate invoice submissions are handled according to existing rules."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )
    line = POLine.objects.create(
        po=po,
        item_description="Server Fan",
        quantity=Decimal("2"),
        unit_price=Decimal("50.00"),
        line_total=Decimal("100.00"),
    )

    client.force_login(u1)
    # 1st submission
    res1 = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-DUP-TEST-001",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            f"qty_{line.id}": "1",
            f"price_{line.id}": "50.00",
        },
    )
    assert res1.status_code == 302

    # 2nd submission with duplicate number
    res2 = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-DUP-TEST-001",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            f"qty_{line.id}": "1",
            f"price_{line.id}": "50.00",
        },
        follow=True,
    )
    assert res2.status_code == 200
    assert "Duplicate invoice detected" in res2.content.decode()


@pytest.mark.django_db
def test_invoice_listing_page_renders_without_url_errors(client, vendor_setup):
    """9. The invoice listing page renders without URL reversal errors."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )

    client.force_login(u1)
    res = client.get("/vendor/invoices/")
    assert res.status_code == 200
    assert "Invoices" in res.content.decode()
    assert "Submit Invoice" in res.content.decode()


@pytest.mark.django_db
def test_purchase_order_detail_page_renders_without_url_errors(client, vendor_setup):
    """10. The Purchase Order detail page renders without URL reversal errors."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        po_number="PO-DETAIL-2026",
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("3500.00"),
    )

    client.force_login(u1)
    res = client.get(f"/vendor/purchase-orders/{po.id}/")
    assert res.status_code == 200
    content = res.content.decode()
    assert "PO-DETAIL-2026" in content
    assert f"/vendor/purchase-orders/{po.id}/submit-invoice/" in content


@pytest.mark.django_db
def test_invoice_appears_in_vendor_invoice_list_after_submission(client, vendor_setup):
    """11. The invoice appears in the correct vendor's invoice list after submission."""
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )
    line = POLine.objects.create(
        po=po,
        item_description="Optical Transceiver",
        quantity=Decimal("4"),
        unit_price=Decimal("250.00"),
        line_total=Decimal("1000.00"),
    )

    client.force_login(u1)
    client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-LISTING-99",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            f"qty_{line.id}": "4",
            f"price_{line.id}": "250.00",
        },
    )

    res_invoices = client.get("/vendor/invoices/")
    assert res_invoices.status_code == 200
    assert "INV-LISTING-99" in res_invoices.content.decode()

    # Vendor B cannot see Vendor A's invoice
    client.force_login(vendor_setup["user_vendor_b"])
    res_b = client.get("/vendor/invoices/")
    assert res_b.status_code == 200
    assert "INV-LISTING-99" not in res_b.content.decode()


# ==============================================================================
# 12. REGRESSION AUDIT: SESSION STABILITY & CACHE ISOLATION TESTS
# ==============================================================================


@pytest.mark.django_db
def test_vendor_session_stability_post_invoice_submission(client, vendor_setup):
    """
    Regression check: Submitting an invoice must NEVER invalidate or terminate
    the vendor's authenticated session.
    """
    v1 = vendor_setup["vendor_a"]
    u1 = vendor_setup["user_vendor_a"]
    po = PurchaseOrder.objects.create(
        vendor=v1,
        cost_center=vendor_setup["cost_center"],
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("1000.00"),
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Optical Transceiver",
        quantity=Decimal("5"),
        unit_price=Decimal("200.00"),
        line_total=Decimal("1000.00"),
    )

    client.force_login(u1)

    # Submit invoice
    post_res = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "invoice_number": "INV-SESS-STABLE-001",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timedelta(days=30)).isoformat(),
            f"qty_{po_line.id}": "2",
            f"price_{po_line.id}": "200.00",
        },
    )
    # Redirects back to PO detail or invoices list without login redirect
    assert post_res.status_code == 302
    assert "/login" not in post_res.url

    # Check vendor remains authenticated on subsequent requests
    dash_res = client.get("/vendor/")
    assert dash_res.status_code == 200
    assert dash_res.context["vendor"] == v1

    inv_res = client.get("/vendor/invoices/")
    assert inv_res.status_code == 200
    assert "INV-SESS-STABLE-001" in inv_res.content.decode()


@pytest.mark.django_db
def test_vendor_session_stability_post_bid_lifecycle(client, vendor_setup):
    """
    Regression check: Full bid lifecycle actions (draft, submit, amend, withdraw)
    must preserve the vendor's authenticated session throughout.
    """
    from apps.sourcing.models import BidInvite

    w = vendor_setup
    client.force_login(w["user_vendor_a"])

    event = SourcingEvent.objects.create(
        event_number="RFQ-SESS-2026",
        title="Session Test Event",
        event_type=SourcingEvent.TYPE_RFQ,
        status=SourcingEvent.STATUS_BID_WINDOW,
        bid_start_date=timezone.now() - timedelta(days=1),
        bid_end_date=timezone.now() + timedelta(days=5),
        created_by=w["proc_exec"],
    )
    BidInvite.objects.create(event=event, vendor=w["vendor_a"])

    # 1. Draft
    draft_res = client.post(
        f"/vendor/sourcing/{event.id}/bid/",
        data={"action": "save_draft", "total_price": "5000.00"},
    )
    assert draft_res.status_code == 302
    assert "/login" not in draft_res.url

    # 2. Submit
    sub_res = client.post(
        f"/vendor/sourcing/{event.id}/bid/",
        data={"action": "submit_bid", "total_price": "5000.00", "terms_agreed": "on"},
    )
    assert sub_res.status_code == 302
    assert "/login" not in sub_res.url

    # 3. Withdraw
    bid = VendorBid.objects.get(event=event, vendor=w["vendor_a"])
    with_res = client.post(
        f"/vendor/bids/{bid.id}/withdraw/",
        data={"reason": "Testing session persistence during withdrawal."},
    )
    assert with_res.status_code == 302
    assert "/login" not in with_res.url

    # Verify session remains intact
    dash_res = client.get("/vendor/")
    assert dash_res.status_code == 200
    assert dash_res.context["vendor"] == w["vendor_a"]


@pytest.mark.django_db
def test_vendor_dashboard_metrics_isolation_and_live_freshness(client, vendor_setup):
    """
    Regression check: Vendor dashboard metrics must be strictly isolated per vendor
    and immediately reflect state changes without stale or leaking counts.
    """
    from apps.vendors.selectors import get_vendor_dashboard_metrics

    w = vendor_setup
    v_a = w["vendor_a"]
    v_b = w["vendor_b"]

    # Initial metrics for Vendor A and B
    m_a_initial = get_vendor_dashboard_metrics(v_a, w["user_vendor_a"])
    m_b_initial = get_vendor_dashboard_metrics(v_b, w["user_vendor_b"])

    # Issue PO for Vendor A
    po_a = PurchaseOrder.objects.create(
        vendor=v_a,
        cost_center=w["cost_center"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("2500.00"),
    )

    # Vendor A's pos_requiring_ack must increment immediately
    m_a_after_po = get_vendor_dashboard_metrics(v_a, w["user_vendor_a"])
    assert m_a_after_po["pos_requiring_ack"] == m_a_initial["pos_requiring_ack"] + 1

    # Vendor B's pos_requiring_ack must remain unchanged (strict isolation)
    m_b_after_po = get_vendor_dashboard_metrics(v_b, w["user_vendor_b"])
    assert m_b_after_po["pos_requiring_ack"] == m_b_initial["pos_requiring_ack"]

    # Acknowledge PO via POST
    client.force_login(w["user_vendor_a"])
    ack_res = client.post(f"/vendor/purchase-orders/{po_a.id}/acknowledge/")
    assert ack_res.status_code == 302
    assert "/login" not in ack_res.url

    # Now pos_requiring_ack returns to initial, active_pos reflects the acknowledged PO
    m_a_after_ack = get_vendor_dashboard_metrics(v_a, w["user_vendor_a"])
    assert m_a_after_ack["pos_requiring_ack"] == m_a_initial["pos_requiring_ack"]
    assert m_a_after_ack["active_pos"] >= 1


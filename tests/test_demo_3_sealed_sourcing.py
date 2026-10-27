from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import Organization
from apps.sourcing.models import SourcingEvent
from apps.sourcing.selectors import get_sealed_vendor_bids
from apps.sourcing.services import (
    create_sourcing_event_service,
    evaluate_and_award_sourcing_event_service,
    invite_vendors_to_event_service,
    publish_sourcing_event_service,
    submit_vendor_bid_service,
)
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import register_vendor_service


@pytest.mark.django_db
def test_demo_3_sealed_sourcing_event_and_award_workflow(db_roles):
    """
    Day-90 Acceptance Demonstration 3:
    Run RFQ with ≥3 simulated vendor bids, enforce sealed bid privacy before close, perform technical/commercial evaluation, and obtain award approval.
    """
    today = timezone.now()

    # 1. Setup Organization, Procurement Exec, and 3 Active Vendors
    Organization.objects.create(name="HPE Tech Solutions", code="HPE-US")

    proc_exec = User.objects.create_user(
        email="proc_exec@hpe.com", password="Password123!", role=db_roles[Role.PROC_EXEC]
    )
    proc_mgr = User.objects.create_user(
        email="proc_mgr@hpe.com", password="Password123!", role=db_roles[Role.PROC_MGR]
    )

    category = VendorCategory.objects.create(name="Cloud Services", code="CAT-CLOUD")

    vendor1 = register_vendor_service(
        legal_name="Alpha Cloud Corp",
        tax_identification_number="TIN-ALPHA-01",
        category=category,
        email="contact@alpha.com",
        address="San Jose CA",
    )
    vendor1.status = Vendor.STATUS_ACTIVE
    vendor1.is_event_eligible = True
    vendor1.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
    vendor1.save()

    vendor2 = register_vendor_service(
        legal_name="Beta Systems Inc",
        tax_identification_number="TIN-BETA-02",
        category=category,
        email="contact@beta.com",
        address="Austin TX",
    )
    vendor2.status = Vendor.STATUS_ACTIVE
    vendor2.is_event_eligible = True
    vendor2.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
    vendor2.save()

    vendor3 = register_vendor_service(
        legal_name="Gamma Networks LLC",
        tax_identification_number="TIN-GAMMA-03",
        category=category,
        email="contact@gamma.com",
        address="Seattle WA",
    )
    vendor3.status = Vendor.STATUS_ACTIVE
    vendor3.is_event_eligible = True
    vendor3.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
    vendor3.save()

    vendor_user1 = User.objects.create_user(
        email="user@alpha.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor1,
    )

    # 2. Create RFQ Sourcing Event
    event = create_sourcing_event_service(
        title="Enterprise Hybrid Cloud Infrastructure RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=today - timedelta(days=1),
        bid_end_date=today + timedelta(days=7),
        description="Procurement of hybrid cloud server nodes and storage arrays.",
        created_by_user=proc_exec,
    )
    assert event.status == SourcingEvent.STATUS_DRAFT
    assert event.is_sealed is True

    # 3. Publish Event & Invite Vendors -> Status BID_WINDOW
    invites = invite_vendors_to_event_service(
        event=event, vendor_ids=[vendor1.id, vendor2.id, vendor3.id], invited_by=proc_exec
    )
    assert len(invites) == 3
    event = publish_sourcing_event_service(event=event, user=proc_exec)
    assert event.status == SourcingEvent.STATUS_BID_WINDOW

    # 4. Submit Sealed Bids from 3 Vendors
    bid1 = submit_vendor_bid_service(
        event=event,
        vendor=vendor1,
        line_items=[
            {"item_description": "Cloud Node Block", "quantity": 10, "quoted_unit_price": "4500.00"}
        ],
        proposal_summary="Alpha 10-node proposal",
        submitted_by_user=vendor_user1,
    )
    assert bid1.total_bid_amount == Decimal("45000.00")

    bid2 = submit_vendor_bid_service(
        event=event,
        vendor=vendor2,
        line_items=[
            {"item_description": "Cloud Node Block", "quantity": 10, "quoted_unit_price": "4200.00"}
        ],
        proposal_summary="Beta competitive 10-node offer",
    )
    assert bid2.total_bid_amount == Decimal("42000.00")

    bid3 = submit_vendor_bid_service(
        event=event,
        vendor=vendor3,
        line_items=[
            {"item_description": "Cloud Node Block", "quantity": 10, "quoted_unit_price": "4800.00"}
        ],
        proposal_summary="Gamma premium offer",
    )
    assert bid3.total_bid_amount == Decimal("48000.00")

    # 5. Verify Sealed Privacy: Evaluators see NO bids while in BID_WINDOW
    evaluator_view_bids = get_sealed_vendor_bids(event=event, requesting_user=proc_exec)
    assert evaluator_view_bids.count() == 0

    # Vendor 1 sees ONLY vendor 1's bid
    vendor1_view_bids = get_sealed_vendor_bids(event=event, requesting_user=vendor_user1)
    assert vendor1_view_bids.count() == 1
    assert vendor1_view_bids.first().vendor == vendor1

    # 6. Close Bid Window & Transition to TECHNICAL / COMMERCIAL REVIEW -> Evaluators can inspect bids
    event.status = SourcingEvent.STATUS_COMMERCIAL_REVIEW
    event.save(update_fields=["status"])

    evaluator_view_after_close = get_sealed_vendor_bids(event=event, requesting_user=proc_exec)
    assert evaluator_view_after_close.count() == 3

    # 7. Execute Award Decision to Lowest Evaluated Vendor (Beta Systems - $42,000)
    award = evaluate_and_award_sourcing_event_service(
        event=event,
        winning_bid=bid2,
        award_reason="Lowest compliant commercial quote meeting technical SLA requirements.",
        approved_by_user=proc_mgr,
    )

    assert award.winning_bid == bid2
    assert event.status == SourcingEvent.STATUS_AWARDED

    # 8. Verify Audit Trail Records
    audit = AuditLog.objects.filter(
        target_model="AwardDecision", target_object_id=str(award.id)
    ).first()
    assert audit is not None
    assert audit.actor == proc_mgr

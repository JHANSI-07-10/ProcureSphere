from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import (
    GoodsReceipt,
    InspectionRecord,
    ReceiptLine,
    RejectionRecord,
    StockHandoffRecord,
)
from apps.receipts.services import (
    correct_receipt_line_quantity_service,
    create_goods_receipt_service,
    handoff_goods_to_stock_service,
    process_vendor_return_service,
    record_inspection_service,
)
from apps.receipts.views import get_stores_dashboard_data
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def stores_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Global", code="HPE-BATCH2-ORG")
    dept = Department.objects.create(organization=org, name="Warehouse Ops", code="DEPT-WH-02")
    cost_center = CostCenter.objects.create(department=dept, code="CC-WH-02", name="Central Depot")
    category = VendorCategory.objects.create(name="Networking", code="CAT-NET-02")
    vendor = Vendor.objects.create(
        legal_name="Acme Tech Solutions",
        tax_identification_number="TIN-ACME-002",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_batch2@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    admin_user = User.objects.create_user(
        email="admin_batch2@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    requester = User.objects.create_user(
        email="requester_batch2@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    auditor = User.objects.create_user(
        email="auditor_batch2@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    staff_user = User.objects.create_user(
        email="staff_no_role@hpe.com",
        password="Password123!",
        is_staff=True,
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-BATCH2-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("50000.00"),
        tax_amount=Decimal("5000.00"),
        total_amount=Decimal("55000.00"),
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Enterprise Edge Router",
        quantity=Decimal("100.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("400.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="Optical Transceiver 100G",
        quantity=Decimal("50.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("100.00"),
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "vendor": vendor,
        "receiver": receiver,
        "admin_user": admin_user,
        "requester": requester,
        "auditor": auditor,
        "staff_user": staff_user,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


# ==============================================================================
# PHASE 3 / DAY 11: PARTIAL & MULTIPLE DELIVERY SCENARIOS
# ==============================================================================


@pytest.mark.django_db
def test_day11_po_line_received_across_multiple_grns(stores_setup):
    """
    Day 11: A single PO line is delivered and received across 3 separate GRNs.
    Verifies remaining quantity recalculation after each delivery and cumulative totals.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]

    # Delivery 1: 40 units received and accepted
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("40.00")},
        ],
        delivery_note_number="DN-DELIV-01",
    )
    line1.refresh_from_db()
    po.refresh_from_db()
    assert line1.quantity_received == Decimal("40.00")
    assert line1.remaining_quantity == Decimal("60.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert grn1.lines.count() == 1

    # Delivery 2: 35 units received and accepted
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("35.00")},
        ],
        delivery_note_number="DN-DELIV-02",
    )
    line1.refresh_from_db()
    po.refresh_from_db()
    assert line1.quantity_received == Decimal("75.00")
    assert line1.remaining_quantity == Decimal("25.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert grn2.lines.count() == 1

    # Delivery 3: Final 25 units for line1
    grn3 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("25.00")},
        ],
        delivery_note_number="DN-DELIV-03",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("100.00")
    assert line1.remaining_quantity == Decimal("0.00")
    # PO is still PARTIAL_RECEIPT because line2 has not been received yet
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # All 3 GRNs exist independently with complete audit trails
    assert GoodsReceipt.objects.filter(po=po).count() == 3
    assert set([grn1.grn_number, grn2.grn_number, grn3.grn_number]) == set(
        GoodsReceipt.objects.filter(po=po).values_list("grn_number", flat=True)
    )


@pytest.mark.django_db
def test_day11_partial_delivery_with_rejections_and_replacement_shipment(stores_setup):
    """
    Day 11: Rejection on delivery 1 leaves remaining quantity open for vendor replacement on delivery 2.
    """
    po = stores_setup["po"]
    line2 = stores_setup["line2"]  # 50 units
    receiver = stores_setup["receiver"]

    # Delivery 1: 30 received, 20 accepted, 10 rejected
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("20.00"),
                "rejection_reason": "Damaged laser packaging",
            }
        ],
        delivery_note_number="DN-REP-01",
    )
    line2.refresh_from_db()
    assert line2.quantity_received == Decimal("20.00")
    # Remaining quantity that vendor still owes: 50 - 20 = 30
    assert line2.remaining_quantity == Decimal("30.00")

    # Delivery 2: Vendor replaces rejected units plus remaining units (30 units)
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("30.00"),
            }
        ],
        delivery_note_number="DN-REP-02",
    )
    line2.refresh_from_db()
    assert line2.quantity_received == Decimal("50.00")
    assert line2.remaining_quantity == Decimal("0.00")

    # Both GRNs are intact and rejection record on GRN 1 is preserved
    r_line1 = grn1.lines.get(po_line=line2)
    assert r_line1.quantity_rejected == Decimal("10.00")
    assert r_line1.rejections.count() == 1
    assert r_line1.rejections.first().rejected_quantity == Decimal("10.00")


@pytest.mark.django_db
def test_day11_multiple_lines_different_delivery_rates(stores_setup):
    """
    Day 11: Multiple PO lines with different received and accepted quantities across deliveries.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]  # 100 units
    line2 = stores_setup["line2"]  # 50 units
    receiver = stores_setup["receiver"]

    # GRN 1: Delivers 50 of line1, 10 of line2
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
            {"po_line_id": str(line2.id), "quantity_received": Decimal("10.00")},
        ],
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert line1.quantity_received == Decimal("50.00")
    assert line2.quantity_received == Decimal("10.00")
    assert line1.remaining_quantity == Decimal("50.00")
    assert line2.remaining_quantity == Decimal("40.00")

    # GRN 2: Delivers remaining 50 of line1, 20 of line2 (accepted 15, rejected 5)
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("15.00"),
                "rejection_reason": "Failed spectral test",
            },
        ],
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert line1.quantity_received == Decimal("100.00")
    assert line1.remaining_quantity == Decimal("0.00")
    assert line2.quantity_received == Decimal("25.00")
    assert line2.remaining_quantity == Decimal("25.00")


@pytest.mark.django_db
def test_day11_prevention_of_receiving_more_than_outstanding_po_quantity(stores_setup):
    """
    Day 11: Prevents over-receiving across multiple delivery attempts.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]  # 100 units
    receiver = stores_setup["receiver"]

    # GRN 1 receives 80
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("80.00")},
        ],
    )
    line1.refresh_from_db()
    assert line1.remaining_quantity == Decimal("20.00")

    # Attempt to receive 25 (exceeds remaining 20)
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {"po_line_id": str(line1.id), "quantity_received": Decimal("25.00")},
            ],
        )
    assert "cannot exceed remaining quantity (20.00)" in str(exc.value)

    # Database remains unchanged
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("80.00")
    assert GoodsReceipt.objects.filter(po=po).count() == 1


# ==============================================================================
# PHASE 4 / DAY 12: FINAL RECEIPT & PO COMPLETION WORKFLOW
# ==============================================================================


@pytest.mark.django_db
def test_day12_po_completion_reached_only_when_all_lines_satisfied(stores_setup):
    """
    Day 12: PO reaches COMPLETED only when every line satisfies quantity_received >= quantity.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]  # 100
    line2 = stores_setup["line2"]  # 50
    receiver = stores_setup["receiver"]

    # GRN 1 completes line1, but line2 remains 0
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("100.00")},
        ],
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # GRN 2 receives 49 of line2 (1 still outstanding)
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line2.id), "quantity_received": Decimal("49.00")},
        ],
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # GRN 3 receives the final 1 unit for line2
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line2.id), "quantity_received": Decimal("1.00")},
        ],
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED


@pytest.mark.django_db
def test_day12_rejected_quantities_do_not_incorrectly_complete_po(stores_setup):
    """
    Day 12: If shipment is received but part is rejected, PO must NOT be marked COMPLETED.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]  # 100
    line2 = stores_setup["line2"]  # 50
    receiver = stores_setup["receiver"]

    # Receive 100 on line1, and 50 on line2 but reject 10 on line2
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("100.00")},
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("40.00"),
                "rejection_reason": "Failed QC spec",
            },
        ],
    )
    line2.refresh_from_db()
    po.refresh_from_db()
    assert line2.quantity_received == Decimal("40.00")
    assert line2.remaining_quantity == Decimal("10.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT


@pytest.mark.django_db
def test_day12_cannot_receive_against_completed_po(stores_setup):
    """
    Day 12: Once a PO is COMPLETED, any further GRN attempts are blocked.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    line2 = stores_setup["line2"]
    receiver = stores_setup["receiver"]

    # Fully receive PO
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("100.00")},
            {"po_line_id": str(line2.id), "quantity_received": Decimal("50.00")},
        ],
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    # Attempt another GRN
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {"po_line_id": str(line1.id), "quantity_received": Decimal("1.00")},
            ],
        )
    assert "Cannot record goods receipt against PO in status 'COMPLETED'" in str(exc.value)


@pytest.mark.django_db
def test_day12_atomic_rollback_on_multi_line_failure(stores_setup):
    """
    Day 12: Atomic rollback ensures no partial records exist if any line fails during GRN creation.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    line2 = stores_setup["line2"]
    receiver = stores_setup["receiver"]

    initial_grn_count = GoodsReceipt.objects.count()

    # Line 1 is valid (50), Line 2 has invalid negative received quantity (-5)
    with pytest.raises(ValidationError):
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
                {"po_line_id": str(line2.id), "quantity_received": Decimal("-5.00")},
            ],
        )

    # Rollback verified: No new GRN, no change to line1
    assert GoodsReceipt.objects.count() == initial_grn_count
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("0.00")


# ==============================================================================
# PHASE 5 / DAY 13: REJECTIONS, RETURNS & QUANTITY CORRECTIONS
# ==============================================================================


@pytest.mark.django_db
def test_day13_rejection_requires_reason_and_creates_rejection_record(stores_setup):
    """
    Day 13: Rejections require a mandatory rejection reason and store immutable records.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("20.00")},
        ],
    )
    r_line = grn.lines.first()

    # Rejection via inspection without reason fails
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(r_line.id),
                    "passed": False,
                    "rejected_quantity": Decimal("5.00"),
                    "rejection_reason": "",
                    "inspection_notes": "",
                }
            ],
        )
    assert "Rejection reason is required" in str(exc.value)

    # Rejection with reason succeeds
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line.id),
                "passed": False,
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "Broken RJ45 connectors",
            }
        ],
    )
    r_line.refresh_from_db()
    assert r_line.quantity_rejected == Decimal("5.00")
    assert r_line.quantity_accepted == Decimal("15.00")
    rejection = RejectionRecord.objects.get(receipt_line=r_line)
    assert rejection.rejected_quantity == Decimal("5.00")
    assert "Broken RJ45 connectors" in rejection.rejection_reason


@pytest.mark.django_db
def test_day13_vendor_return_bounded_by_rejected_quantity(stores_setup):
    """
    Day 13: Return quantity is bounded by rejected quantity and cannot be exceeded or negative.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("15.00"),
                "rejection_reason": "Defective power supply",
            }
        ],
    )
    r_line = grn.lines.first()
    rejection = r_line.rejections.first()

    # Negative return quantity blocked
    with pytest.raises(ValidationError) as exc:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("-1.00"),
        )
    assert "must be greater than 0" in str(exc.value)

    # Over-return quantity blocked (trying to return 6 when 5 rejected)
    with pytest.raises(ValidationError) as exc:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("6.00"),
        )
    assert "cannot exceed rejected quantity" in str(exc.value)

    # Valid return succeeds
    ret = process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rejection,
        return_quantity=Decimal("5.00"),
        tracking_reference="TRACK-FEDEX-12345",
        return_notes="Dispatched to vendor RMA depot",
    )
    assert ret.returned_to_vendor is True
    assert "TRACK-FEDEX-12345" in ret.rejection_reason


@pytest.mark.django_db
def test_day13_duplicate_vendor_return_blocked(stores_setup):
    """
    Day 13: Repeated return submission against an already-returned rejection record is blocked.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("8.00"),
                "rejection_reason": "Chassis scratched",
            }
        ],
    )
    r_line = grn.lines.first()
    rejection = r_line.rejections.first()

    # First dispatch succeeds
    process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rejection,
        return_quantity=Decimal("2.00"),
    )
    rejection.refresh_from_db()
    assert rejection.returned_to_vendor is True

    # Duplicate dispatch fails
    with pytest.raises(ValidationError) as exc:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("2.00"),
        )
    assert "already been returned to vendor" in str(exc.value)


@pytest.mark.django_db
def test_day13_quantity_correction_preserves_history_and_recalculates(stores_setup):
    """
    Day 13: Non-destructive quantity correction logs audit trail and recalculates PO status.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]  # 100 units
    line2 = stores_setup["line2"]  # 50 units
    receiver = stores_setup["receiver"]

    # GRN fully satisfies PO
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("100.00")},
            {"po_line_id": str(line2.id), "quantity_received": Decimal("50.00")},
        ],
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    r_line1 = grn.lines.get(po_line=line1)

    # Correct line1 received quantity from 100 to 90 due to counting discrepancy
    correct_receipt_line_quantity_service(
        receipt_line=r_line1,
        corrected_by=receiver,
        new_quantity_received=Decimal("90.00"),
        new_quantity_accepted=Decimal("90.00"),
        reason="Physical count adjustment after container unpack",
        correction_notes="Found 10 units missing from outer carton",
    )

    r_line1.refresh_from_db()
    line1.refresh_from_db()
    po.refresh_from_db()

    assert r_line1.quantity_received == Decimal("90.00")
    assert line1.quantity_received == Decimal("90.00")
    assert line1.remaining_quantity == Decimal("10.00")
    # PO completion status correctly reverts to PARTIAL_RECEIPT!
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # AuditLog preserves exact previous and updated states
    log = AuditLog.objects.filter(
        target_model="ReceiptLine",
        target_object_id=str(r_line1.id),
        new_state__action="QUANTITY_CORRECTION",
    ).first()
    assert log is not None
    assert log.previous_state["quantity_received"] == "100.00"
    assert log.new_state["quantity_received"] == "90.00"
    assert log.new_state["reason"] == "Physical count adjustment after container unpack"


@pytest.mark.django_db
def test_day13_quantity_correction_cannot_reduce_below_handed_off_stock(stores_setup):
    """
    Day 13: Cannot correct accepted quantity below warehouse stock that has already been handed off.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
        ],
    )
    r_line1 = grn.lines.first()

    # Hand off all 50 units into stock
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-4B",
    )
    assert r_line1.is_handed_off is True
    assert r_line1.stock_handoff.quantity_handed_off == Decimal("50.00")

    # Attempt to correct accepted quantity to 40
    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=r_line1,
            corrected_by=receiver,
            new_quantity_received=Decimal("40.00"),
            new_quantity_accepted=Decimal("40.00"),
            reason="Mistake in intake",
        )
    assert "cannot be less than already handed-off warehouse stock (50.00)" in str(exc.value)


@pytest.mark.django_db
def test_day13_security_no_staff_bypass_on_returns_and_corrections(stores_setup):
    """
    Day 13: Security requirement - Staff user without STORES_RECEIVER role cannot perform returns or corrections.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]
    staff_user = stores_setup["staff_user"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("15.00"),
                "rejection_reason": "Damaged pins",
            }
        ],
    )
    r_line1 = grn.lines.first()
    rejection = r_line1.rejections.first()

    client = Client()
    client.force_login(staff_user)

    # 1. Staff cannot process vendor return
    resp1 = client.post(
        reverse("record_vendor_return", kwargs={"rejection_id": rejection.id}),
        {"return_quantity": "5.00"},
        follow=True,
    )
    rejection.refresh_from_db()
    assert rejection.returned_to_vendor is False
    assert any("Permission Denied" in m.message for m in resp1.context["messages"])

    # 2. Staff cannot correct receipt quantity
    resp2 = client.post(
        reverse("receipt_line_correct", kwargs={"line_id": r_line1.id}),
        {"quantity_received": "18.00", "reason": "Staff tampering"},
        follow=True,
    )
    r_line1.refresh_from_db()
    assert r_line1.quantity_received == Decimal("20.00")
    assert any("Permission Denied" in m.message for m in resp2.context["messages"])


# ==============================================================================
# PHASE 6 / DAY 14: RECEIPT / INSPECTION DASHBOARD & STATUS TRACKING
# ==============================================================================


@pytest.mark.django_db
def test_day14_dashboard_metrics_data_correctness_and_consistency(stores_setup):
    """
    Day 14: Verifies get_stores_dashboard_data computes mutually exclusive, database-backed metrics.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    line2 = stores_setup["line2"]
    receiver = stores_setup["receiver"]

    # Initially: 0 GRNs, 1 pending delivery PO
    initial_data = get_stores_dashboard_data()
    assert initial_data["metrics"]["total_grns"] == 0
    assert initial_data["metrics"]["pending_deliveries"] >= 1
    assert initial_data["metrics"]["completed_pos"] == 0

    # GRN 1: Received, passed inspection
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
        ],
    )
    record_inspection_service(
        receipt=grn1,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(grn1.lines.first().id),
                "passed": True,
                "inspection_notes": "All units passed",
            }
        ],
    )

    # GRN 2: Received, failed inspection (rejection)
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("10.00"),
                "rejection_reason": "Failed insulation test",
                "inspection_notes": "Inspection FAILED - Defective insulation",
            }
        ],
    )

    # GRN 3: Received, pending inspection (lines uninspected)
    grn3 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("20.00")},
        ],
    )

    # Re-fetch dashboard data
    data = get_stores_dashboard_data()
    metrics = data["metrics"]

    # Verification of metrics
    assert metrics["total_grns"] == 3
    assert metrics["passed_receipts"] == 1
    assert metrics["failed_receipts"] == 1
    assert metrics["pending_inspections"] == 1

    # Mutually exclusive status consistency: sum of inspection categories equals total GRNs
    assert (
        metrics["passed_receipts"]
        + metrics["failed_receipts"]
        + metrics["pending_inspections"]
        + metrics["partially_inspected_receipts"]
        == metrics["total_grns"]
    )

    assert metrics["total_rejections"] == 1
    assert metrics["pending_vendor_returns_count"] == 1
    assert metrics["returned_to_vendor_count"] == 0

    # Perform vendor return on GRN 2 rejection
    rej = RejectionRecord.objects.first()
    process_vendor_return_service(returned_by=receiver, rejection_record=rej)

    data_after_return = get_stores_dashboard_data()
    assert data_after_return["metrics"]["returned_to_vendor_count"] == 1
    assert data_after_return["metrics"]["pending_vendor_returns_count"] == 0


@pytest.mark.django_db
def test_day14_dashboard_http_endpoint_and_rbac(stores_setup):
    """
    Day 14: Verifies the dedicated /receipts/dashboard/ view enforces RBAC and delivers data.
    """
    receiver = stores_setup["receiver"]
    admin_user = stores_setup["admin_user"]
    auditor = stores_setup["auditor"]
    requester = stores_setup["requester"]

    client = Client()

    # 1. Anonymous user redirected to login
    resp_anon = client.get(reverse("receipt_dashboard"))
    assert resp_anon.status_code == 302
    assert "/login/" in resp_anon.url

    # 2. Requester denied access (redirected to receipts list with error message)
    client.force_login(requester)
    resp_req = client.get(reverse("receipt_dashboard"), follow=True)
    assert any("Permission Denied" in m.message for m in resp_req.context["messages"])

    # 3. Stores Receiver granted 200 OK
    client.force_login(receiver)
    resp_recv = client.get(reverse("receipt_dashboard"))
    assert resp_recv.status_code == 200
    assert "metrics" in resp_recv.context
    assert "recent_grns" in resp_recv.context

    # 4. Super Admin granted 200 OK
    client.force_login(admin_user)
    resp_admin = client.get(reverse("receipt_dashboard"))
    assert resp_admin.status_code == 200

    # 5. Compliance / Auditor granted 200 OK (read-only audit visibility)
    client.force_login(auditor)
    resp_audit = client.get(reverse("receipt_dashboard"))
    assert resp_audit.status_code == 200


@pytest.mark.django_db
def test_day14_receipts_list_filtering_and_status_tracking(stores_setup):
    """
    Day 14: Verifies status filters (PENDING, PASSED, FAILED) and search work accurately on receipts list.
    """
    po = stores_setup["po"]
    line1 = stores_setup["line1"]
    receiver = stores_setup["receiver"]

    grn_pass = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("10.00")},
        ],
        delivery_note_number="DN-PASS-01",
    )
    record_inspection_service(
        receipt=grn_pass,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(grn_pass.lines.first().id),
                "passed": True,
                "inspection_notes": "Passed QC",
            }
        ],
    )

    grn_fail = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("5.00"),
                "rejection_reason": "Broken latch",
            }
        ],
        delivery_note_number="DN-FAIL-01",
    )

    client = Client()
    client.force_login(receiver)

    # Filter status=PASSED
    resp_pass = client.get(reverse("receipts_list") + "?status=PASSED")
    grn_ids_pass = [g.id for g in resp_pass.context["receipts"]]
    assert grn_pass.id in grn_ids_pass
    assert grn_fail.id not in grn_ids_pass

    # Filter status=FAILED
    resp_fail = client.get(reverse("receipts_list") + "?status=FAILED")
    grn_ids_fail = [g.id for g in resp_fail.context["receipts"]]
    assert grn_fail.id in grn_ids_fail
    assert grn_pass.id not in grn_ids_fail

    # Filter search by delivery note
    resp_dn = client.get(reverse("receipts_list") + "?q=DN-PASS-01")
    assert len(resp_dn.context["receipts"]) == 1
    assert resp_dn.context["receipts"][0].id == grn_pass.id

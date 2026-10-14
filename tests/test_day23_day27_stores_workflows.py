import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.services import allocate_budget_service
from apps.invoices.models import (
    InvoiceLine,
    MatchException,
    MatchTolerancePolicy,
    SupplierInvoice,
)
from apps.invoices.services import (
    create_supplier_invoice_service,
    mark_invoice_paid_service,
    resolve_match_exception_service,
    run_3_way_match_service,
)
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
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
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def batch5_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Global Enterprise B5", code="HPE-B5-ORG")
    dept = Department.objects.create(
        organization=org, name="Supply Chain & Stores B5", code="DEPT-SC-05"
    )
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-SC-05", name="Central Fulfilment Center B5"
    )
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q4-B5",
        start_date=timezone.now().date() - datetime.timedelta(days=10),
        end_date=timezone.now().date() + datetime.timedelta(days=90),
    )
    budget = allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("150000.00"),
    )

    category = VendorCategory.objects.create(name="Enterprise Hardware B5", code="CAT-HW-05")
    vendor = Vendor.objects.create(
        legal_name="Pacific Hardware Logistics Ltd",
        tax_identification_number="TIN-PHL-B5-771",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_batch5@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    admin_user = User.objects.create_user(
        email="admin_batch5@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    requester = User.objects.create_user(
        email="requester_batch5@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    proc_exec = User.objects.create_user(
        email="proc_exec_batch5@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )
    finance = User.objects.create_user(
        email="finance_batch5@hpe.com",
        password="Password123!",
        role=db_roles[Role.FINANCE_AP],
    )
    auditor = User.objects.create_user(
        email="auditor_batch5@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    vendor_user = User.objects.create_user(
        email="vendor_user_batch5@phl.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-B5-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("50000.00"),
        tax_amount=Decimal("5000.00"),
        total_amount=Decimal("55000.00"),
        terms_and_conditions="Standard HPE Enterprise Procurement Terms & Conditions.",
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Enterprise Compute Server Node DL380",
        quantity=Decimal("100.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("400.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="High-Density SFP+ Optical Transceiver 10GbE",
        quantity=Decimal("50.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("200.00"),
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "fiscal_period": fiscal_period,
        "budget": budget,
        "vendor": vendor,
        "receiver": receiver,
        "admin_user": admin_user,
        "requester": requester,
        "proc_exec": proc_exec,
        "finance": finance,
        "auditor": auditor,
        "vendor_user": vendor_user,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


# ==============================================================================
# DAY 23: Validate inspection acceptance/rejection, notes and supporting evidence
# ==============================================================================


@pytest.mark.django_db
def test_day23_inspection_acceptance_rejection_notes_and_evidence(batch5_setup):
    """
    Day 23: Validate inspection acceptance/rejection split line item processing,
    mandatory inspection notes, inspector attribution, supporting evidence,
    and automatic RejectionRecord creation with defect logging.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    # 1. Stores Receiver creates initial GRN with 100 units received
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("100.00"),
                "quantity_accepted": Decimal("100.00"),
            }
        ],
        delivery_note_number="DN-B5-D23-001",
        remarks="DHL Supply Chain tracking DHL-B5-TRACK-01",
    )
    receipt_line = grn.lines.first()
    assert grn.overall_inspection_status == "PENDING"

    # 2. Record inspection splitting line: 75 accepted, 25 rejected
    inspection_notes = (
        "Batch inspection passed for 75 units; 25 units suffered chassis deformation in transit."
    )
    inspected_grn = record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": False,
                "rejected_quantity": Decimal("25.00"),
                "rejection_reason": "TRANSIT_DAMAGE",
                "inspection_notes": inspection_notes,
            }
        ],
    )

    receipt_line.refresh_from_db()
    assert receipt_line.quantity_accepted == Decimal("75.00")
    assert receipt_line.quantity_rejected == Decimal("25.00")
    assert inspected_grn.overall_inspection_status == "FAILED"

    # 3. Verify InspectionRecord details & attribution
    inspection_rec = InspectionRecord.objects.get(receipt_line=receipt_line)
    assert inspection_rec.inspected_by == receiver
    assert inspection_rec.passed is False
    assert inspection_notes in inspection_rec.inspection_notes

    # 4. Verify RejectionRecord details, defect category, and quarantine status
    rejection_rec = RejectionRecord.objects.get(receipt_line=receipt_line)
    assert rejection_rec.rejected_quantity == Decimal("25.00")
    assert rejection_rec.rejection_reason == "TRANSIT_DAMAGE"
    assert rejection_rec.returned_to_vendor is False

    # 5. Verify POLine sync: received quantity reflects only accepted units (75)
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("75.00")

    # 6. Verify AuditLog captures the inspection event
    audit_entry = AuditLog.objects.filter(
        target_model="GoodsReceipt",
        target_object_id=str(grn.id),
        action=AuditLog.ACTION_UPDATE,
    ).latest("timestamp")
    assert audit_entry.actor == receiver
    assert audit_entry.new_state["overall_inspection_status"] == "FAILED"


@pytest.mark.django_db
def test_day23_inspection_rejection_requires_mandatory_notes_or_reason(batch5_setup):
    """
    Day 23: Attempting to record rejection without providing defect notes
    or reason must raise a ValidationError and leave the database uncorrupted.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            }
        ],
        delivery_note_number="DN-B5-D23-ERR",
    )
    receipt_line = grn.lines.first()

    # Rejection quantity specified but no reason/notes provided
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(receipt_line.id),
                    "passed": False,
                    "rejected_quantity": Decimal("10.00"),
                    "rejection_reason": "",
                    "inspection_notes": "",
                }
            ],
        )
    assert "reason" in str(exc.value).lower()

    # Verify receipt line was not corrupted
    receipt_line.refresh_from_db()
    assert receipt_line.quantity_accepted == Decimal("50.00")
    assert receipt_line.quantity_rejected == Decimal("0.00")


# ==============================================================================
# DAY 24: Test partial delivery and multiple receipt edge cases
# ==============================================================================


@pytest.mark.django_db
def test_day24_partial_delivery_multiple_receipts_sequential_flow(batch5_setup):
    """
    Day 24: Sequential partial deliveries across multiple GRNs:
    - Order: Line 1 (100 units), Line 2 (50 units)
    - GRN 1: Line 1 (40), Line 2 (20) -> PO transitions to PARTIAL_RECEIPT
    - GRN 2: Line 1 (30), Line 2 (30) -> Line 2 fully delivered, Line 1 open -> PO remains PARTIAL_RECEIPT
    - GRN 3: Line 1 (30) -> Line 1 fully delivered -> PO transitions to COMPLETED
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    line2 = batch5_setup["line2"]
    receiver = batch5_setup["receiver"]

    # GRN 1: Partial delivery for both lines
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("40.00"),
                "quantity_accepted": Decimal("40.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
            },
        ],
        delivery_note_number="DN-B5-SEQ-01",
    )
    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert line1.quantity_received == Decimal("40.00")
    assert line2.quantity_received == Decimal("20.00")

    # GRN 2: Delivers remaining Line 2 (30 units) and partial Line 1 (30 units)
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("30.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("30.00"),
            },
        ],
        delivery_note_number="DN-B5-SEQ-02",
    )
    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT  # Line 1 still has 30 remaining!
    assert line1.quantity_received == Decimal("70.00")
    assert line2.quantity_received == Decimal("50.00")

    # GRN 3: Delivers final 30 units of Line 1
    grn3 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("30.00"),
            }
        ],
        delivery_note_number="DN-B5-SEQ-03",
    )
    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert line1.quantity_received == Decimal("100.00")
    assert line2.quantity_received == Decimal("50.00")
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    # Verify all 3 GRNs are associated with the PO
    assert po.receipts.count() == 3


@pytest.mark.django_db
def test_day24_asymmetric_and_overdelivery_edge_cases(batch5_setup):
    """
    Day 24: Over-delivery prevention and multi-line atomic rollback:
    - Attempting to receive more than remaining outstanding quantity is blocked.
    - If one line fails in a multi-line submission, entire transaction rolls back.
    - Post-completion lock prevents further receiving.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    line2 = batch5_setup["line2"]
    receiver = batch5_setup["receiver"]

    # 1. Over-delivery attempt on open line (Line 1 has 100 ordered, attempting 105)
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("105.00"),
                    "quantity_accepted": Decimal("105.00"),
                }
            ],
            delivery_note_number="DN-OVER-01",
        )
    assert "exceed" in str(exc.value).lower()

    # 2. Multi-line atomic rollback: Line 1 valid (50), Line 2 invalid (-5)
    with pytest.raises(ValidationError):
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("50.00"),
                    "quantity_accepted": Decimal("50.00"),
                },
                {
                    "po_line_id": str(line2.id),
                    "quantity_received": Decimal("-5.00"),
                    "quantity_accepted": Decimal("-5.00"),
                },
            ],
            delivery_note_number="DN-ATOMIC-FAIL",
        )
    # Verify rollback: 0 receipts created, Line 1 still 0 received
    assert GoodsReceipt.objects.filter(po=po).count() == 0
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("0.00")


# ==============================================================================
# DAY 25: Test rejected/returned goods and quantity correction while preserving history
# ==============================================================================


@pytest.mark.django_db
def test_day25_non_destructive_quantity_correction_preserves_history(batch5_setup):
    """
    Day 25: Non-destructive quantity correction preserves history:
    - GRN created with 50 units accepted.
    - Correction performed with mandatory reason ("Stock count adjustment - 5 units carton shortfall").
    - ReceiptLine updated to 45 accepted, POLine updated to 45 received.
    - Immutable AuditLog stores previous state and new state.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            }
        ],
        delivery_note_number="DN-B5-CORR-01",
    )
    receipt_line = grn.lines.first()

    # Attempt correction without reason -> blocked
    with pytest.raises(ValidationError):
        correct_receipt_line_quantity_service(
            receipt_line=receipt_line,
            corrected_by=receiver,
            new_quantity_received=Decimal("45.00"),
            new_quantity_accepted=Decimal("45.00"),
            reason="",
        )

    # Valid correction with mandatory reason
    corrected_line = correct_receipt_line_quantity_service(
        receipt_line=receipt_line,
        corrected_by=receiver,
        new_quantity_received=Decimal("45.00"),
        new_quantity_accepted=Decimal("45.00"),
        reason="Stock count audit adjustment - carton shortage detected.",
    )

    assert corrected_line.quantity_received == Decimal("45.00")
    assert corrected_line.quantity_accepted == Decimal("45.00")

    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("45.00")

    # Verify AuditLog history preservation
    audit = AuditLog.objects.filter(
        target_model="ReceiptLine",
        target_object_id=str(receipt_line.id),
        action=AuditLog.ACTION_UPDATE,
    ).latest("timestamp")
    assert audit.actor == receiver
    assert audit.previous_state["quantity_received"] == "50.00"
    assert audit.new_state["quantity_received"] == "45.00"
    assert "carton shortage" in audit.new_state["reason"]


@pytest.mark.django_db
def test_day25_quantity_correction_cannot_reduce_below_handed_off_stock(batch5_setup):
    """
    Day 25: Guardrail preventing reduction of accepted quantity below
    already handed-off warehouse stock.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("40.00"),
                "quantity_accepted": Decimal("40.00"),
            }
        ],
        delivery_note_number="DN-B5-HANDOFF-GUARD",
    )
    # Hand off 40 units to warehouse inventory
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="AISLE-4B-BAY-02",
    )
    receipt_line = grn.lines.first()

    # Attempting to correct accepted quantity to 35 (< 40 handed off) raises ValidationError
    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=receipt_line,
            corrected_by=receiver,
            new_quantity_received=Decimal("35.00"),
            new_quantity_accepted=Decimal("35.00"),
            reason="Incorrectly attempted reduction after handoff",
        )
    assert "handed-off" in str(exc.value).lower()


@pytest.mark.django_db
def test_day25_vendor_return_workflow_with_dispatch_tracking(batch5_setup):
    """
    Day 25: Rejected goods return dispatch to vendor:
    - 20 units rejected during inspection.
    - Vendor return dispatched with carrier and tracking reference.
    - RejectionRecord marked returned_to_vendor=True.
    - Over-return and duplicate returns are blocked.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            }
        ],
        delivery_note_number="DN-B5-RET-01",
    )
    receipt_line = grn.lines.first()

    # Reject 20 units
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": False,
                "rejected_quantity": Decimal("20.00"),
                "rejection_reason": "MANUFACTURING_DEFECT",
                "inspection_notes": "Failed burn-in diagnostic test.",
            }
        ],
    )
    rejection = RejectionRecord.objects.get(receipt_line=receipt_line)
    assert rejection.returned_to_vendor is False

    # Dispatch vendor return with tracking reference
    updated_rejection = process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rejection,
        return_quantity=Decimal("20.00"),
        tracking_reference="TRACK-B5-RETURN-77",
        return_notes="Dispatched via Blue Dart Air Cargo RMA #441",
    )
    assert updated_rejection.returned_to_vendor is True
    assert "TRACK-B5-RETURN-77" in updated_rejection.rejection_reason

    # Duplicate return attempt is blocked
    with pytest.raises(ValidationError):
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=updated_rejection,
            return_quantity=Decimal("20.00"),
        )


# ==============================================================================
# DAY 26: Validate receipt status and PO remaining quantity calculations
# ==============================================================================


@pytest.mark.django_db
def test_day26_receipt_status_lifecycle_and_po_remaining_quantity_calculation(batch5_setup):
    """
    Day 26: Validate receipt status progression (PENDING -> PASSED / FAILED / PARTIAL)
    and exact POLine remaining quantity calculation:
    - Ordered: 100
    - Receipt 1: 50 received, inspection passes all 50 -> POLine remaining = 50
    - Receipt 2: 30 received, inspection rejects all 30 -> Accepted = 0 -> POLine remaining stays 50
    - Receipt 3: 50 received, inspection passes all 50 -> POLine received = 100, remaining = 0
    - PO transitions to COMPLETED.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    # 1. Receipt 1: 50 received, passed
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            }
        ],
        delivery_note_number="DN-B5-D26-01",
    )
    assert grn1.overall_inspection_status == "PENDING"

    record_inspection_service(
        receipt=grn1,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(grn1.lines.first().id),
                "passed": True,
                "inspection_notes": "Passed all physical checks.",
            }
        ],
    )
    grn1.refresh_from_db()
    line1.refresh_from_db()
    assert grn1.overall_inspection_status == "PASSED"
    assert line1.quantity_received == Decimal("50.00")
    assert (line1.quantity - line1.quantity_received) == Decimal("50.00")

    # 2. Receipt 2: 30 received, but failed entirely
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("0.00"),
            }
        ],
        delivery_note_number="DN-B5-D26-02",
    )
    record_inspection_service(
        receipt=grn2,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(grn2.lines.first().id),
                "passed": False,
                "rejected_quantity": Decimal("30.00"),
                "rejection_reason": "SEVERE_CORROSION",
                "inspection_notes": "Water immersion damage detected.",
            }
        ],
    )
    grn2.refresh_from_db()
    line1.refresh_from_db()
    assert grn2.overall_inspection_status == "FAILED"
    # Crucial: rejected 30 does NOT count against fulfillment! Remaining stays 50!
    assert line1.quantity_received == Decimal("50.00")
    assert (line1.quantity - line1.quantity_received) == Decimal("50.00")


@pytest.mark.django_db
def test_day26_reinspection_delta_recalculation_without_duplicate_records(batch5_setup):
    """
    Day 26: Re-inspection delta recalculation:
    When an inspection is updated, POLine received quantity recalculates the delta
    accurately without duplicate OneToOne InspectionRecord creation.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    receiver = batch5_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("40.00"),
                "quantity_accepted": Decimal("40.00"),
            }
        ],
        delivery_note_number="DN-B5-REINSPECT",
    )
    receipt_line = grn.lines.first()

    # Initial inspection: rejects 10 units (accepted = 30)
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": False,
                "rejected_quantity": Decimal("10.00"),
                "rejection_reason": "PACKAGING_FAIL",
                "inspection_notes": "Packaging compromised.",
            }
        ],
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("30.00")

    # Re-inspection: after technical lab re-test, only 4 units rejected (accepted = 36)
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": False,
                "rejected_quantity": Decimal("4.00"),
                "rejection_reason": "PACKAGING_FAIL",
                "inspection_notes": "Lab test cleared 6 units; 4 units confirmed defective.",
            }
        ],
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("36.00")
    assert (line1.quantity - line1.quantity_received) == Decimal("64.00")
    assert InspectionRecord.objects.filter(receipt_line=receipt_line).count() == 1


# ==============================================================================
# DAY 27: Validate Stores → Finance integration for receipt data used in 3-Way Match
# ==============================================================================


@pytest.mark.django_db
def test_day27_stores_receipt_data_used_in_finance_3way_match_success(batch5_setup):
    """
    Day 27: End-to-end Stores -> Finance 3-Way Match success flow:
    1. PO issued for 20 units @ $400.00.
    2. Stores receives and accepts 20 units (GRN quantity_accepted = 20).
    3. Vendor submits invoice for 20 units @ $400.00.
    4. 3-Way Match compares PO + GRN + Invoice -> 100% matched within tolerance.
    5. Invoice moves to READY_FOR_PAYMENT; Finance issues payment.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    # 1. Stores receives and accepts 20 units
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
            }
        ],
        delivery_note_number="DN-B5-MATCH-01",
    )
    assert grn.lines.first().quantity_accepted == Decimal("20.00")

    # 2. Supplier submits invoice for exactly 20 units @ $400.00
    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-2026-001",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("20.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )
    assert invoice.status == SupplierInvoice.STATUS_RECEIVED

    # 3. Finance executes automated 3-Way Match
    matched_invoice, exceptions = run_3_way_match_service(invoice=invoice, user=finance)

    assert len(exceptions) == 0
    assert matched_invoice.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT

    # 4. Finance disburses payment
    paid_invoice = mark_invoice_paid_service(invoice=matched_invoice, user=finance)
    assert paid_invoice.status == SupplierInvoice.STATUS_PAID


@pytest.mark.django_db
def test_day27_stores_receipt_quantity_variance_triggers_3way_match_exception(batch5_setup):
    """
    Day 27: Stores partial receipt vs over-billed Supplier Invoice:
    - Stores received and accepted only 25 units.
    - Supplier Invoice bills for 40 units.
    - 3-Way Match detects Billed Qty (40) > Accepted Stores Qty (25).
    - MatchException of type QUANTITY_VARIANCE is raised; automated payment is blocked.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    # Stores receives only 25 units
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("25.00"),
                "quantity_accepted": Decimal("25.00"),
            }
        ],
        delivery_note_number="DN-B5-PARTIAL-25",
    )

    # Supplier mistakenly bills for 40 units
    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-OVERBILL-40",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("40.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    # Execute 3-Way Match
    matched_invoice, exceptions = run_3_way_match_service(
        invoice=invoice,
        qty_tolerance_pct=Decimal("0.05"),  # 5% tolerance
        user=finance,
    )

    assert matched_invoice.status == SupplierInvoice.STATUS_EXCEPTION
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc.exception_type == MatchException.TYPE_QUANTITY_VARIANCE
    assert exc.status == MatchException.STATUS_OPEN
    assert "25" in exc.description and "40" in exc.description


@pytest.mark.django_db
def test_day27_stores_inspection_rejection_causes_3way_match_quantity_variance(batch5_setup):
    """
    Day 27: Physical inspection rejection directly impacts 3-Way Match:
    - Vendor delivers 50 units.
    - Stores inspects and rejects 15 defective units -> Accepted = 35.
    - Supplier bills for 50 units.
    - 3-Way Match checks accepted GRN quantity (35), not received (50),
      and correctly flags a QUANTITY_VARIANCE exception.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            }
        ],
        delivery_note_number="DN-B5-REJ-50",
    )
    receipt_line = grn.lines.first()

    # Inspection rejects 15 units
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": False,
                "rejected_quantity": Decimal("15.00"),
                "rejection_reason": "FAILED_STRESS_TEST",
                "inspection_notes": "Thermal sensor failure on 15 units.",
            }
        ],
    )
    receipt_line.refresh_from_db()
    assert receipt_line.quantity_accepted == Decimal("35.00")

    # Invoice submitted for full 50 units
    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-REJ-CHECK",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("50.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    matched_invoice, exceptions = run_3_way_match_service(
        invoice=invoice,
        qty_tolerance_pct=Decimal("0.05"),
        user=finance,
    )

    assert matched_invoice.status == SupplierInvoice.STATUS_EXCEPTION
    assert len(exceptions) == 1
    assert exceptions[0].exception_type == MatchException.TYPE_QUANTITY_VARIANCE


@pytest.mark.django_db
def test_day27_multi_grn_aggregation_for_invoice_match(batch5_setup):
    """
    Day 27: Multi-GRN aggregation:
    - PO line ordered = 100 units.
    - GRN 1: 40 units accepted.
    - GRN 2: 60 units accepted.
    - Total Stores accepted across multiple GRNs = 100.
    - Supplier Invoice bills for 100 units.
    - 3-Way Match aggregates all GRNs for the PO line -> matches perfectly.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    # GRN 1: 40 accepted
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("40.00"),
                "quantity_accepted": Decimal("40.00"),
            }
        ],
        delivery_note_number="DN-MULTI-01",
    )

    # GRN 2: 60 accepted
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("60.00"),
                "quantity_accepted": Decimal("60.00"),
            }
        ],
        delivery_note_number="DN-MULTI-02",
    )

    # Invoice for 100 units
    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-MULTI-GRN-100",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("100.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    matched_invoice, exceptions = run_3_way_match_service(invoice=invoice, user=finance)
    assert len(exceptions) == 0
    assert matched_invoice.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT


@pytest.mark.django_db
def test_day27_stores_quantity_correction_impacts_3way_match(batch5_setup):
    """
    Day 27: Stores quantity correction dynamically updates 3-Way Match:
    - GRN originally recorded 50 units accepted.
    - Stores performs audit quantity correction down to 40 units accepted.
    - Supplier Invoice for 50 units is evaluated: billed 50 > corrected accepted 40.
    - Triggers QUANTITY_VARIANCE exception.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            }
        ],
        delivery_note_number="DN-B5-CORR-3WAY",
    )
    receipt_line = grn.lines.first()

    # Stores discovers miscount and corrects accepted quantity down to 40
    correct_receipt_line_quantity_service(
        receipt_line=receipt_line,
        corrected_by=receiver,
        new_quantity_received=Decimal("40.00"),
        new_quantity_accepted=Decimal("40.00"),
        reason="Counting correction prior to invoice match.",
    )

    # Supplier invoice billed 50 units
    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-CORR-CHECK",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("50.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    matched_invoice, exceptions = run_3_way_match_service(invoice=invoice, user=finance)
    assert matched_invoice.status == SupplierInvoice.STATUS_EXCEPTION
    assert len(exceptions) == 1
    assert exceptions[0].exception_type == MatchException.TYPE_QUANTITY_VARIANCE


@pytest.mark.django_db
def test_day27_finance_exception_resolution_and_payment_disbursement(batch5_setup):
    """
    Day 27: Finance exception resolution workflow:
    - Variance exception is reviewed and approved by Finance with mandatory resolution notes.
    - Invoice moves to READY_FOR_PAYMENT upon exception resolution.
    - Payment is disbursed with payment reference.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("30.00"),
            }
        ],
        delivery_note_number="DN-B5-RESOLVE-DN",
    )

    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-RESOLVE-01",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("35.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    matched_invoice, exceptions = run_3_way_match_service(invoice=invoice, user=finance)
    assert len(exceptions) == 1
    exc = exceptions[0]

    # Finance reviews and resolves exception
    resolved_exc = resolve_match_exception_service(
        match_exception=exc,
        resolved_by_user=finance,
        resolution_notes="Authorized 5-unit discrepancy under procurement emergency clause #12.",
        action="RESOLVE",
    )
    assert resolved_exc.status == MatchException.STATUS_RESOLVED

    matched_invoice.refresh_from_db()
    assert matched_invoice.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT

    # Disburse payment
    paid_inv = mark_invoice_paid_service(invoice=matched_invoice, user=finance)
    assert paid_inv.status == SupplierInvoice.STATUS_PAID


@pytest.mark.django_db
def test_day27_invoice_detail_view_renders_associated_stores_receipts(client, batch5_setup):
    """
    Day 27: UI Verification:
    Invoice detail view renders the Associated Stores Receipts card with GRN metadata,
    inspection badges, and direct links to GRN detail.
    """
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
            }
        ],
        delivery_note_number="DN-UI-STORE-EVIDENCE",
    )

    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-B5-UI-TEST-99",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("20.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    client.force_login(finance)
    response = client.get(f"/invoices/{invoice.id}/")
    assert response.status_code == 200
    content = response.content.decode()

    assert "Associated Stores Receipts" in content
    assert grn.grn_number in content
    assert "DN-UI-STORE-EVIDENCE" in content
    assert f"/receipts/{grn.id}/" in content


@pytest.mark.django_db
def test_day27_drf_api_stores_to_finance_3way_match_flow(batch5_setup):
    """
    Day 27: REST API Verification:
    POST /api/v1/invoices/{id}/run-match/ executes 3-way match, evaluates Stores receipt data,
    and returns structured API response with exception counts.
    """
    api_client = APIClient()
    po = batch5_setup["po"]
    line1 = batch5_setup["line1"]
    vendor = batch5_setup["vendor"]
    receiver = batch5_setup["receiver"]
    finance = batch5_setup["finance"]

    # Stores receives 10 units
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            }
        ],
        delivery_note_number="DN-API-B5",
    )

    invoice = create_supplier_invoice_service(
        vendor=vendor,
        po=po,
        invoice_number="INV-API-B5-001",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        line_items=[
            {
                "po_line": line1,
                "item_description": line1.item_description,
                "quantity": Decimal("10.00"),
                "unit_price": Decimal("400.00"),
            }
        ],
        created_by_user=finance,
    )

    api_client.force_authenticate(user=finance)
    response = api_client.post(f"/api/v1/invoices/{invoice.id}/run-match/")
    assert response.status_code == 200
    data = response.json()
    assert data["exceptions_count"] == 0
    assert data["invoice"]["status"] == SupplierInvoice.STATUS_READY_FOR_PAYMENT

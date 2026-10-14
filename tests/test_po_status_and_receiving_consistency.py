from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import Role, User
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import (
    GoodsReceipt,
    InspectionRecord,
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
def consistency_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Tech", code="HPE-CONS-ORG")
    dept = Department.objects.create(organization=org, name="Logistics", code="DEPT-LOG-CONS")
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-LOG-CONS", name="Central Receiving"
    )
    category = VendorCategory.objects.create(name="Hardware", code="CAT-HW-CONS")
    vendor = Vendor.objects.create(
        legal_name="Enterprise Hardware Ltd",
        tax_identification_number="TIN-EHW-CONS",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_cons@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )

    buyer = User.objects.create_user(
        email="buyer_cons@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-CONS-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("15000.00"),
        tax_amount=Decimal("1500.00"),
        total_amount=Decimal("16500.00"),
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Server Blade Gen 11",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("1000.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="64GB ECC Memory Kit",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("500.00"),
    )

    return {
        "org": org,
        "vendor": vendor,
        "cost_center": cost_center,
        "receiver": receiver,
        "buyer": buyer,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


@pytest.mark.django_db
def test_full_receipt_transitions_po_to_completed(consistency_setup):
    """Rule 1: Full receipt of all lines transitions PO to COMPLETED."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]
    line2 = consistency_setup["line2"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
        ],
        delivery_note_number="DN-FULL-001",
    )

    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert grn.lines.count() == 2
    assert line1.quantity_received == Decimal("10.00")
    assert line1.remaining_quantity == Decimal("0.00")
    assert line2.quantity_received == Decimal("10.00")
    assert line2.remaining_quantity == Decimal("0.00")
    assert po.status == PurchaseOrder.STATUS_COMPLETED


@pytest.mark.django_db
def test_partial_receipt_transitions_po_to_partial_receipt(consistency_setup):
    """Rule 2: Partial receipt of any line transitions PO to PARTIAL_RECEIPT."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]
    line2 = consistency_setup["line2"]

    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("4.00"),
                "quantity_accepted": Decimal("4.00"),
            }
        ],
        delivery_note_number="DN-PART-001",
    )

    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert line1.quantity_received == Decimal("4.00")
    assert line1.remaining_quantity == Decimal("6.00")
    assert line2.quantity_received == Decimal("0.00")
    assert line2.remaining_quantity == Decimal("10.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT


@pytest.mark.django_db
def test_second_receipt_completing_partial_po(consistency_setup):
    """Rule 3: Multiple partial receipts accumulate received quantities and complete PO."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]
    line2 = consistency_setup["line2"]

    # First GRN: partial line1 & line2
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("6.00"),
                "quantity_accepted": Decimal("6.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            },
        ],
        delivery_note_number="DN-BATCH-1",
    )

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # Second GRN: remaining quantities
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("4.00"),
                "quantity_accepted": Decimal("4.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            },
        ],
        delivery_note_number="DN-BATCH-2",
    )

    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert line1.quantity_received == Decimal("10.00")
    assert line1.remaining_quantity == Decimal("0.00")
    assert line2.quantity_received == Decimal("10.00")
    assert line2.remaining_quantity == Decimal("0.00")
    assert po.status == PurchaseOrder.STATUS_COMPLETED
    assert GoodsReceipt.objects.filter(po=po).count() == 2


@pytest.mark.django_db
def test_attempt_to_receive_more_than_remaining_quantity(consistency_setup):
    """Rule 4: Attempt to receive more than remaining quantity raises ValidationError."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("11.00"),  # Ordered only 10.00
                }
            ],
            delivery_note_number="DN-OVER-001",
        )
    assert "cannot exceed remaining quantity" in str(exc.value)
    assert GoodsReceipt.objects.filter(po=po).count() == 0


@pytest.mark.django_db
def test_attempt_to_receive_completed_po(consistency_setup):
    """Rule 5: Attempt to receive against a COMPLETED PO is rejected."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    po.status = PurchaseOrder.STATUS_COMPLETED
    po.save(update_fields=["status"])

    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("1.00"),
                }
            ],
        )
    assert "Cannot record goods receipt against PO in status 'COMPLETED'" in str(exc.value)


@pytest.mark.django_db
def test_rejected_quantity_does_not_increase_po_received_quantity(consistency_setup):
    """Rule 6: Rejected quantities do not increase PO's received/accepted count."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("7.00"),  # 3 rejected
                "rejection_reason": "Damaged pins on 3 units",
            }
        ],
        delivery_note_number="DN-REJ-001",
    )

    line1.refresh_from_db()
    po.refresh_from_db()

    assert line1.quantity_received == Decimal("7.00")
    assert line1.remaining_quantity == Decimal("3.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    grn = GoodsReceipt.objects.get(po=po)
    r_line = grn.lines.get(po_line=line1)
    assert r_line.quantity_received == Decimal("10.00")
    assert r_line.quantity_accepted == Decimal("7.00")
    assert r_line.quantity_rejected == Decimal("3.00")
    assert RejectionRecord.objects.filter(receipt_line=r_line).count() == 1


@pytest.mark.django_db
def test_initial_inspection_and_reinspection_delta(consistency_setup):
    """Rules 7 & 8: Initial inspection and subsequent reinspection apply exact deltas."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]
    line2 = consistency_setup["line2"]

    # Initial GRN (all 10 received and initially accepted)
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
        ],
        delivery_note_number="DN-INSP-001",
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    r_line1 = grn.lines.get(po_line=line1)
    r_line2 = grn.lines.get(po_line=line2)

    # Initial Inspection: 2 units of line1 fail QA
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line1.id),
                "passed": False,
                "rejected_quantity": Decimal("2.00"),
                "rejection_reason": "Thermal test failure",
                "inspection_notes": "2 units overheated during burn-in test",
            },
            {
                "receipt_line_id": str(r_line2.id),
                "passed": True,
                "inspection_notes": "All 10 passed memory check",
            },
        ],
    )

    line1.refresh_from_db()
    po.refresh_from_db()
    r_line1.refresh_from_db()

    assert r_line1.quantity_accepted == Decimal("8.00")
    assert r_line1.quantity_rejected == Decimal("2.00")
    assert line1.quantity_received == Decimal("8.00")
    assert line1.remaining_quantity == Decimal("2.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # Reinspection: vendor repaired 1 unit, now only 1 rejected (accepted becomes 9.00)
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line1.id),
                "passed": False,
                "rejected_quantity": Decimal("1.00"),
                "rejection_reason": "1 unit remaining defective",
                "inspection_notes": "1 unit retested and passed, 1 unit remaining bad",
            }
        ],
    )

    line1.refresh_from_db()
    po.refresh_from_db()
    r_line1.refresh_from_db()

    assert r_line1.quantity_accepted == Decimal("9.00")
    assert r_line1.quantity_rejected == Decimal("1.00")
    assert line1.quantity_received == Decimal("9.00")
    assert line1.remaining_quantity == Decimal("1.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT


@pytest.mark.django_db
def test_reinspection_does_not_double_count(consistency_setup):
    """Rule 9: Repeated reinspection with identical values causes 0 delta and does not corrupt PO."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            }
        ],
        delivery_note_number="DN-IDEMPOTENT",
    )

    r_line1 = grn.lines.get(po_line=line1)

    # First inspection
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line1.id),
                "passed": True,
                "inspection_notes": "QA Pass",
            }
        ],
    )

    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("10.00")

    # Second inspection with same parameters
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line1.id),
                "passed": True,
                "inspection_notes": "QA Pass Confirmation",
            }
        ],
    )

    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("10.00")
    assert line1.remaining_quantity == Decimal("0.00")
    assert InspectionRecord.objects.filter(receipt_line=r_line1).count() == 1


@pytest.mark.django_db
def test_remaining_quantity_never_becomes_negative(consistency_setup):
    """Rule 10: POLine.remaining_quantity property is never negative."""
    line1 = consistency_setup["line1"]
    line1.quantity = Decimal("10.00")
    line1.quantity_received = Decimal("10.00")
    assert line1.remaining_quantity == Decimal("0.00")

    # Extreme edge test: if quantity_received > quantity
    line1.quantity_received = Decimal("12.00")
    assert line1.remaining_quantity == Decimal("0.00")


@pytest.mark.django_db
def test_cross_po_tampering_blocked(consistency_setup):
    """Rule 11: Cross-PO line tampering is rejected in both receipt creation and inspection."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]

    # Other PO and line
    other_po = PurchaseOrder.objects.create(
        po_number="PO-OTHER-2026",
        vendor=consistency_setup["vendor"],
        cost_center=consistency_setup["cost_center"],
        status=PurchaseOrder.STATUS_ISSUED,
    )
    other_line = POLine.objects.create(
        po=other_po,
        item_description="Unrelated Item",
        quantity=Decimal("5.00"),
        unit_price=Decimal("200.00"),
    )

    # Submitting other_line against po raises ValidationError
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(other_line.id),
                    "quantity_received": Decimal("2.00"),
                }
            ],
        )
    assert "does not belong to Purchase Order" in str(exc.value)


@pytest.mark.django_db
def test_vendor_return_workflow_marks_returned_and_preserves_stock(consistency_setup, db_roles):
    """Rule 12: Vendor return marks rejected items returned, preserves evidence, and never increases accepted stock."""
    from apps.audit.models import AuditLog

    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("7.00"),  # 3 rejected
                "rejection_reason": "Damaged casing",
            }
        ],
        delivery_note_number="DN-RETURN-TEST",
    )
    r_line1 = grn.lines.get(po_line=line1)
    rejection = RejectionRecord.objects.get(receipt_line=r_line1)
    assert rejection.returned_to_vendor is False
    assert rejection.rejected_quantity == Decimal("3.00")

    # Process return
    updated_rej = process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rejection,
        return_quantity=Decimal("3.00"),
        tracking_reference="TRACK-DHL-9988",
        return_notes="Handed to DHL courier for return to vendor",
    )
    assert updated_rej.returned_to_vendor is True
    assert "TRACK-DHL-9988" in updated_rej.rejection_reason

    # Verify accepted stock remains strictly 7 (never increased by return)
    r_line1.refresh_from_db()
    line1.refresh_from_db()
    assert r_line1.quantity_accepted == Decimal("7.00")
    assert line1.quantity_received == Decimal("7.00")

    # Verify audit log recorded
    log = AuditLog.objects.filter(target_model="RejectionRecord", action="RETURN_TO_VENDOR").first()
    assert log is not None
    assert log.new_state["returned_to_vendor"] is True
    assert log.new_state["tracking_reference"] == "TRACK-DHL-9988"

    # Negative test: Excessive return quantity is blocked
    with pytest.raises(ValidationError) as exc:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("5.00"),  # > 3.00
        )
    assert "cannot exceed rejected quantity" in str(exc.value)

    # Negative test: Unauthorized role blocked
    auditor = User.objects.create_user(
        email="auditor_return_block@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    with pytest.raises(ValidationError) as exc_auth:
        process_vendor_return_service(
            returned_by=auditor,
            rejection_record=rejection,
        )
    assert "Unauthorized" in str(exc_auth.value)


@pytest.mark.django_db
def test_quantity_correction_preserves_history_and_recalculates_po_status(
    consistency_setup, db_roles
):
    """Rule 13: Quantity correction preserves history, updates PO line, and recalculates PO status."""
    from apps.audit.models import AuditLog

    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]
    line2 = consistency_setup["line2"]

    # Initial full receipt -> PO completed
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
        ],
        delivery_note_number="DN-CORR-FULL",
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    r_line1 = grn.lines.get(po_line=line1)

    # Apply quantity correction: clerk recounts and finds only 8 servers delivered
    corrected_line = correct_receipt_line_quantity_service(
        receipt_line=r_line1,
        corrected_by=receiver,
        new_quantity_received=Decimal("8.00"),
        new_quantity_accepted=Decimal("8.00"),
        reason="Physical recount discrepancy: 2 fewer servers found in shipment container",
        correction_notes="Verified by Stores Supervisor",
    )

    # Verify line quantities updated
    assert corrected_line.quantity_received == Decimal("8.00")
    assert corrected_line.quantity_accepted == Decimal("8.00")
    assert "[Correction: Physical recount discrepancy" in corrected_line.notes

    # Verify PO line updated
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("8.00")
    assert line1.remaining_quantity == Decimal("2.00")

    # Verify PO status reverted from COMPLETED to PARTIAL_RECEIPT!
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # Verify AuditLog captured full previous state and new state
    log = AuditLog.objects.filter(
        target_model="ReceiptLine",
        target_object_id=str(r_line1.id),
        action=AuditLog.ACTION_UPDATE,
    ).latest("timestamp")
    assert log is not None
    assert log.previous_state["quantity_received"] == "10.00"
    assert log.previous_state["po_status"] == PurchaseOrder.STATUS_COMPLETED
    assert log.new_state["quantity_received"] == "8.00"
    assert log.new_state["po_status"] == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert "Physical recount discrepancy" in log.new_state["reason"]


@pytest.mark.django_db
def test_quantity_correction_cannot_reduce_below_handed_off_stock(consistency_setup):
    """Rule 14: Quantity correction cannot reduce accepted quantity below already handed-off stock."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            }
        ],
        delivery_note_number="DN-HANDOFF-CORR",
    )
    r_line1 = grn.lines.get(po_line=line1)

    # Hand off all 10 to warehouse stock
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="RACK-A1",
    )
    assert r_line1.is_handed_off is True
    assert r_line1.stock_handoff_record.quantity_handed_off == Decimal("10.00")

    # Attempting to correct accepted quantity to 5 (< 10 handed off) raises ValidationError
    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=r_line1,
            corrected_by=receiver,
            new_quantity_received=Decimal("5.00"),
            new_quantity_accepted=Decimal("5.00"),
            reason="Attempting to reduce below stock",
        )
    assert "cannot be less than already handed-off warehouse stock" in str(exc.value)


@pytest.mark.django_db
def test_quantity_correction_validations(consistency_setup, db_roles):
    """Rule 15: Validates required reason, positive quantity, PO line limits, and role authorization."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
        delivery_note_number="DN-VAL-TEST",
    )
    r_line1 = grn.lines.get(po_line=line1)

    # 1. Missing reason is rejected
    with pytest.raises(ValidationError) as exc_reason:
        correct_receipt_line_quantity_service(
            receipt_line=r_line1,
            corrected_by=receiver,
            new_quantity_received=Decimal("6.00"),
            reason="",
        )
    assert "Correction reason is mandatory" in str(exc_reason.value)

    # 2. Negative/zero quantity is rejected
    with pytest.raises(ValidationError) as exc_zero:
        correct_receipt_line_quantity_service(
            receipt_line=r_line1,
            corrected_by=receiver,
            new_quantity_received=Decimal("0.00"),
            reason="Zero not allowed",
        )
    assert "must be greater than 0" in str(exc_zero.value)

    # 3. Exceeding ordered quantity is rejected
    with pytest.raises(ValidationError) as exc_limit:
        correct_receipt_line_quantity_service(
            receipt_line=r_line1,
            corrected_by=receiver,
            new_quantity_received=Decimal("15.00"),  # Ordered only 10.00
            new_quantity_accepted=Decimal("15.00"),
            reason="Over limit",
        )
    assert "exceed ordered quantity" in str(exc_limit.value)

    # 4. Unauthorized role is rejected
    requester = User.objects.create_user(
        email="req_corr_block@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    with pytest.raises(ValidationError) as exc_auth:
        correct_receipt_line_quantity_service(
            receipt_line=r_line1,
            corrected_by=requester,
            new_quantity_received=Decimal("4.00"),
            reason="Requester cannot correct",
        )
    assert "Unauthorized" in str(exc_auth.value)


@pytest.mark.django_db
def test_po_status_reverts_to_issued_when_all_quantities_corrected_to_zero_accepted(
    consistency_setup,
):
    """Rule 16: PO status reverts to ISSUED if all accepted quantities on PO lines become zero."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("4.00"),
                "quantity_accepted": Decimal("4.00"),
            }
        ],
        delivery_note_number="DN-REVERT-ISSUED",
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    r_line1 = grn.lines.get(po_line=line1)

    # Correct line such that 0 is accepted (all 4 rejected due to latent damage)
    correct_receipt_line_quantity_service(
        receipt_line=r_line1,
        corrected_by=receiver,
        new_quantity_received=Decimal("4.00"),
        new_quantity_accepted=Decimal("0.00"),
        reason="Latent manufacturing defect discovered before handoff: entire shipment rejected",
    )

    line1.refresh_from_db()
    po.refresh_from_db()
    assert line1.quantity_received == Decimal("0.00")
    assert line1.remaining_quantity == Decimal("10.00")
    assert po.status == PurchaseOrder.STATUS_ISSUED


@pytest.mark.django_db
def test_client_return_and_correction_views(client, consistency_setup, db_roles):
    """Rule 17: HTTP view endpoints for vendor return and quantity correction enforce RBAC and succeed."""
    po = consistency_setup["po"]
    receiver = consistency_setup["receiver"]
    line1 = consistency_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("7.00"),
                "rejection_reason": "Damaged pins",
            }
        ],
        delivery_note_number="DN-CLIENT-TEST",
    )
    r_line1 = grn.lines.get(po_line=line1)
    rejection = RejectionRecord.objects.get(receipt_line=r_line1)

    # Test unauthorized user blocked
    auditor = User.objects.create_user(
        email="auditor_view_guard@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    client.force_login(auditor)

    resp_ret_unauth = client.post(
        f"/receipts/rejections/{rejection.id}/return/",
        {"tracking_reference": "UNAUTH-TRACK"},
    )
    assert resp_ret_unauth.status_code == 302
    rejection.refresh_from_db()
    assert rejection.returned_to_vendor is False

    resp_corr_unauth = client.post(
        f"/receipts/lines/{r_line1.id}/correct/",
        {"quantity_received": "8.00", "reason": "Unauthorized attempt"},
    )
    assert resp_corr_unauth.status_code == 302
    r_line1.refresh_from_db()
    assert r_line1.quantity_received == Decimal("10.00")

    # Test authenticated stores receiver succeeds
    client.force_login(receiver)

    # 1. Vendor return via view
    resp_ret = client.post(
        f"/receipts/rejections/{rejection.id}/return/",
        {
            "tracking_reference": "DHL-WEB-7788",
            "return_notes": "Returned via web action",
        },
    )
    assert resp_ret.status_code == 302
    rejection.refresh_from_db()
    assert rejection.returned_to_vendor is True
    assert "DHL-WEB-7788" in rejection.rejection_reason

    # 2. Quantity correction via view
    resp_corr = client.post(
        f"/receipts/lines/{r_line1.id}/correct/",
        {
            "quantity_received": "9.00",
            "quantity_accepted": "6.00",
            "reason": "Corrected via web modal",
            "notes": "Verified count",
        },
    )
    assert resp_corr.status_code == 302
    r_line1.refresh_from_db()
    assert r_line1.quantity_received == Decimal("9.00")
    assert r_line1.quantity_accepted == Decimal("6.00")
    assert r_line1.quantity_rejected == Decimal("3.00")

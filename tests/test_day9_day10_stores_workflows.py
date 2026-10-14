from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

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
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def day9_day10_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Tech India", code="HPE-IN-01")
    dept = Department.objects.create(organization=org, name="Operations WH", code="DEPT-OPS-WH")
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-STORES-910", name="Stores Dock"
    )
    category = VendorCategory.objects.create(name="Hardware & Servers", code="CAT-SRV-910")
    vendor = Vendor.objects.create(
        legal_name="HPE Enterprise Solutions",
        tax_identification_number="TIN-HPE-ES-910",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="stores_susmitha@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-REC-0910",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("30000.00"),
        tax_amount=Decimal("3000.00"),
        total_amount=Decimal("33000.00"),
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="HPE ProLiant DL380 Gen10 Server",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("2000.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="HPE 32GB Dual Rank DDR4 Memory",
        quantity=Decimal("50.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("200.00"),
    )

    return {
        "org": org,
        "vendor": vendor,
        "receiver": receiver,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


# ==============================================================================
# DAY 9: PS - REC — Complete rejection/return workflow and quantity correction
# ==============================================================================


@pytest.mark.django_db
def test_day9_rejection_record_stores_defect_and_return_status(day9_day10_setup):
    """Day 9: Quality rejection logs defect reason and tracks initial return status."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("7.00"),
                "rejection_reason": "3 servers failed diagnostic memory POST test",
                "inspection_notes": "Defect confirmed during initial burn-in inspection",
            }
        ],
        delivery_note_number="DN-DAY9-001",
    )

    r_line = grn.lines.get(po_line=line1)
    assert r_line.quantity_received == Decimal("10.00")
    assert r_line.quantity_accepted == Decimal("7.00")
    assert r_line.quantity_rejected == Decimal("3.00")

    rejection = RejectionRecord.objects.get(receipt_line=r_line)
    assert rejection.rejected_quantity == Decimal("3.00")
    assert "memory POST test" in rejection.rejection_reason
    assert rejection.returned_to_vendor is False


@pytest.mark.django_db
def test_day9_vendor_return_partial_and_full_dispatch_with_tracking_ref(day9_day10_setup):
    """Day 9: Dispatches return to vendor with tracking reference, preserving accepted stock."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("6.00"),
                "rejection_reason": "4 units damaged in freight transit",
            }
        ],
        delivery_note_number="DN-DAY9-002",
    )

    r_line = grn.lines.get(po_line=line1)
    rejection = RejectionRecord.objects.get(receipt_line=r_line)

    # Process vendor return
    updated_rej = process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rejection,
        return_quantity=Decimal("4.00"),
        tracking_reference="FEDEX-RMA-2026-9911",
        return_notes="Dispatched to vendor warranty repair center in Bengaluru",
    )

    assert updated_rej.returned_to_vendor is True
    assert "FEDEX-RMA-2026-9911" in updated_rej.rejection_reason
    assert "Bengaluru" in updated_rej.rejection_reason

    # Verify audit trail
    audit = AuditLog.objects.filter(
        target_model="RejectionRecord",
        target_object_id=str(rejection.id),
        action="RETURN_TO_VENDOR",
    ).first()
    assert audit is not None
    assert audit.actor == receiver
    assert audit.previous_state["returned_to_vendor"] is False
    assert audit.new_state["returned_to_vendor"] is True
    assert audit.new_state["tracking_reference"] == "FEDEX-RMA-2026-9911"
    assert audit.new_state["return_quantity"] == "4.00"

    # Crucial ERP rule: accepted warehouse quantity must remain exactly 6.00 (not inflated)
    r_line.refresh_from_db()
    assert r_line.quantity_accepted == Decimal("6.00")
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("6.00")


@pytest.mark.django_db
def test_day9_vendor_return_cannot_exceed_rejected_quantity(day9_day10_setup):
    """Day 9: Guard prevents returning more than the rejected quantity or negative returns."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("3.00"),
                "rejection_reason": "2 defective fans",
            }
        ],
        delivery_note_number="DN-DAY9-003",
    )

    rejection = RejectionRecord.objects.get(receipt_line__receipt=grn)

    # 1. Over return attempt
    with pytest.raises(ValidationError) as exc_over:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("5.00"),  # Only 2 were rejected
            tracking_reference="TRACK-ERR",
        )
    assert "cannot exceed rejected quantity" in str(exc_over.value)

    # 2. Zero or negative return attempt
    with pytest.raises(ValidationError) as exc_neg:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("0.00"),
            tracking_reference="TRACK-ERR",
        )
    assert "must be greater than 0" in str(exc_neg.value)


@pytest.mark.django_db
def test_day9_quantity_correction_workflow_non_destructive(day9_day10_setup):
    """Day 9: Non-destructive quantity correction preserves prior state in AuditLog and recalculates PO."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("8.00"),
                "quantity_accepted": Decimal("8.00"),
            }
        ],
        delivery_note_number="DN-DAY9-004",
    )
    r_line = grn.lines.get(po_line=line1)

    # Recount adjustment: 8 -> 9 received, 7 accepted, 2 rejected
    corrected_line = correct_receipt_line_quantity_service(
        receipt_line=r_line,
        corrected_by=receiver,
        new_quantity_received=Decimal("9.00"),
        new_quantity_accepted=Decimal("7.00"),
        reason="Physical dock recount verified 9 units delivered, 2 have cosmetic chassis damage",
        correction_notes="Approved by warehouse supervisor",
    )

    assert corrected_line.quantity_received == Decimal("9.00")
    assert corrected_line.quantity_accepted == Decimal("7.00")
    assert corrected_line.quantity_rejected == Decimal("2.00")

    # PO Line received quantity synchronized to accepted quantity
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("7.00")
    assert line1.remaining_quantity == Decimal("3.00")

    # AuditLog verifies prior and new states
    audit = AuditLog.objects.filter(
        target_model="ReceiptLine",
        target_object_id=str(r_line.id),
        action=AuditLog.ACTION_UPDATE,
    ).latest("timestamp")
    assert audit.actor == receiver
    assert audit.previous_state["quantity_received"] == "8.00"
    assert audit.previous_state["quantity_accepted"] == "8.00"
    assert audit.new_state["quantity_received"] == "9.00"
    assert audit.new_state["quantity_accepted"] == "7.00"
    assert audit.new_state["quantity_rejected"] == "2.00"
    assert "recount verified" in audit.new_state["reason"]


@pytest.mark.django_db
def test_day9_quantity_correction_blocked_if_less_than_handed_off_stock(day9_day10_setup):
    """Day 9: Quantity correction cannot reduce accepted quantity below already handed-off stock."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

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
        delivery_note_number="DN-DAY9-005",
    )
    r_line = grn.lines.get(po_line=line1)

    # Hand off to stock
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="RACK-SEC-01",
    )
    assert r_line.is_handed_off is True

    # Attempt to correct accepted to 3 (< 5 in stock)
    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=r_line,
            corrected_by=receiver,
            new_quantity_received=Decimal("4.00"),
            new_quantity_accepted=Decimal("3.00"),
            reason="Attempting to reduce below warehouse stock",
        )
    assert "cannot be less than already handed-off warehouse stock" in str(exc.value)


@pytest.mark.django_db
def test_day9_quantity_correction_requires_mandatory_reason(day9_day10_setup):
    """Day 9: Blank or missing reason triggers ValidationError to preserve audit integrity."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

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
        delivery_note_number="DN-DAY9-006",
    )
    r_line = grn.lines.get(po_line=line1)

    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=r_line,
            corrected_by=receiver,
            new_quantity_received=Decimal("6.00"),
            reason="   ",  # Whitespace only
        )
    assert "Correction reason is mandatory" in str(exc.value)


@pytest.mark.django_db
def test_day9_unauthorized_user_blocked_from_returns_and_corrections(
    client, day9_day10_setup, db_roles
):
    """
    Day 9 RBAC:
    - Service and view layers block Requester, Auditor, and ordinary staff without Stores role.
    - Unauthenticated requests redirect to login.
    - Super Admin and Stores Receiver are explicitly permitted.
    """
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("3.00"),
                "rejection_reason": "Damaged pins",
            }
        ],
        delivery_note_number="DN-DAY9-007",
    )
    r_line = grn.lines.get(po_line=line1)
    rejection = RejectionRecord.objects.get(receipt_line=r_line)

    requester = User.objects.create_user(
        email="req_guard_9@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    auditor = User.objects.create_user(
        email="auditor_guard_9@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    staff_ordinary = User.objects.create_user(
        email="staff_guard_9@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        is_staff=True,
    )

    # 1. Service layer rejection for all unauthorized users
    for unauth_user in [requester, auditor, staff_ordinary]:
        with pytest.raises(ValidationError) as exc_ret:
            process_vendor_return_service(
                returned_by=unauth_user,
                rejection_record=rejection,
                tracking_reference="TRACK-UNAUTH",
            )
        assert "Unauthorized" in str(exc_ret.value)

        with pytest.raises(ValidationError) as exc_corr:
            correct_receipt_line_quantity_service(
                receipt_line=r_line,
                corrected_by=unauth_user,
                new_quantity_received=Decimal("4.00"),
                reason="Unauthorized recount",
            )
        assert "Unauthorized" in str(exc_corr.value)

    # 2. View layer: Unauthenticated redirected to login
    ret_url = f"/receipts/rejections/{rejection.id}/return/"
    corr_url = f"/receipts/lines/{r_line.id}/correct/"

    resp_unauth_ret = client.post(ret_url, {"tracking_reference": "UNAUTH"})
    assert resp_unauth_ret.status_code == 302
    assert "/login/" in resp_unauth_ret.url

    resp_unauth_corr = client.post(corr_url, {"new_quantity_received": "4.00", "reason": "Unauth"})
    assert resp_unauth_corr.status_code == 302
    assert "/login/" in resp_unauth_corr.url

    # 3. View layer: Requester, Auditor, Ordinary Staff blocked with permission denied message
    for unauth_user in [requester, auditor, staff_ordinary]:
        client.force_login(unauth_user)

        resp_ret = client.post(ret_url, {"tracking_reference": "ATTEMPT"}, follow=True)
        assert resp_ret.status_code == 200
        assert any(
            "Permission Denied: Only Stores Receiver or Super Admin can process vendor returns"
            in m.message
            for m in resp_ret.context["messages"]
        )

        resp_corr = client.post(
            corr_url,
            {"new_quantity_received": "4.00", "reason": "Attempt"},
            follow=True,
        )
        assert resp_corr.status_code == 200
        assert any(
            "Permission Denied: Only Stores Receiver or Super Admin can correct receipt quantities"
            in m.message
            for m in resp_corr.context["messages"]
        )

    # 4. View layer & service layer: Super Admin is authorized
    super_admin = User.objects.create_user(
        email="superadmin_guard_9@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    client.force_login(super_admin)
    resp_admin_ret = client.post(
        ret_url,
        {
            "tracking_reference": "ADMIN-RMA-OK",
            "return_quantity": "1.00",
            "return_notes": "Admin processed return",
        },
        follow=True,
    )
    assert resp_admin_ret.status_code == 200
    assert any(
        "Vendor return recorded successfully" in m.message
        for m in resp_admin_ret.context["messages"]
    )


# ==============================================================================
# DAY 10: PS - REC — Complete inspection evidence and accept/reject functionality
# ==============================================================================


@pytest.mark.django_db
def test_day10_inspection_evidence_captures_notes_and_inspector_attribution(day9_day10_setup):
    """Day 10: Inspection captures inspector identity, pass/fail status, and technical QA notes."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

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
        delivery_note_number="DN-DAY10-001",
    )
    r_line = grn.lines.get(po_line=line1)

    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line.id),
                "passed": True,
                "inspection_notes": "Passed HPE hardware diagnostic test suite (CPUs, ECC RAM, redundant PSUs)",
            }
        ],
    )

    insp = InspectionRecord.objects.get(receipt_line=r_line)
    assert insp.passed is True
    assert insp.inspected_by == receiver
    assert "HPE hardware diagnostic test suite" in insp.inspection_notes
    assert r_line.inspection_status == "PASSED"


@pytest.mark.django_db
def test_day10_accept_reject_split_line_updates_po_line_and_creates_rejection_record(
    day9_day10_setup,
):
    """Day 10: Split accept/reject line correctly updates PO line received quantity and creates rejection record."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

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
        delivery_note_number="DN-DAY10-002",
    )
    r_line = grn.lines.get(po_line=line1)

    # Perform inspection splitting 10 units: 7 passed, 3 failed
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(r_line.id),
                "passed": False,
                "rejected_quantity": Decimal("3.00"),
                "rejection_reason": "3 chassis cracked during cargo handling",
                "inspection_notes": "7 chassis intact and tested OK; 3 rejected",
                "returned_to_vendor": False,
            }
        ],
    )

    r_line.refresh_from_db()
    assert r_line.quantity_received == Decimal("10.00")
    assert r_line.quantity_accepted == Decimal("7.00")
    assert r_line.quantity_rejected == Decimal("3.00")
    assert r_line.inspection_status == "REJECTED"

    # PO Line remaining quantity is 10 - 7 = 3
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("7.00")
    assert line1.remaining_quantity == Decimal("3.00")

    # Rejection record created
    rej = RejectionRecord.objects.get(receipt_line=r_line)
    assert rej.rejected_quantity == Decimal("3.00")
    assert "chassis cracked" in rej.rejection_reason


@pytest.mark.django_db
def test_day10_grn_detail_view_exposes_complete_audit_trail_and_evidence(client, day9_day10_setup):
    """Day 10: GRN detail view renders inspection evidence, notes, and immutable audit logs."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("4.00"),
                "rejection_reason": "1 unit power supply short circuit",
                "inspection_notes": "Electrical safety inspection completed",
            }
        ],
        delivery_note_number="DN-DAY10-003",
    )

    client.force_login(receiver)
    response = client.get(f"/receipts/{grn.id}/")

    assert response.status_code == 200
    content = response.content.decode()

    # Evidence details present in HTML
    assert grn.grn_number in content
    assert "Electrical safety inspection completed" in content
    assert "1 unit power supply short circuit" in content
    assert "Immutable Receipt History" in content
    assert "Audit Details &amp; State Delta" in content or "Audit Details" in content


@pytest.mark.django_db
def test_day10_stock_handoff_idempotency_prevents_duplicate_posting(day9_day10_setup):
    """Day 10 / Stock Handoff: Duplicate handoff submissions update in place and do not double-post stock."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

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
        delivery_note_number="DN-DAY10-004",
    )

    # First handoff submission
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BIN-A1",
        handoff_notes="Initial storage",
    )
    assert StockHandoffRecord.objects.filter(receipt_line__receipt=grn).count() == 1

    # Second handoff submission (retry / repeated button click)
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BIN-A1-UPDATED",
        handoff_notes="Updated notes",
    )
    # Still exactly 1 record per line item
    assert StockHandoffRecord.objects.filter(receipt_line__receipt=grn).count() == 1
    stock_rec = StockHandoffRecord.objects.get(receipt_line__receipt=grn)
    assert stock_rec.storage_location == "BIN-A1-UPDATED"
    assert stock_rec.quantity_handed_off == Decimal("5.00")


@pytest.mark.django_db
def test_day10_transaction_rollback_cleanly_reverts_on_failure(day9_day10_setup):
    """Day 10 / State Integrity: Failure during multi-step handoff rolls back all database changes."""
    po = day9_day10_setup["po"]
    receiver = day9_day10_setup["receiver"]
    line1 = day9_day10_setup["line1"]

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
        delivery_note_number="DN-DAY10-005",
    )

    # Attempt handoff with invalid/unauthorized user raises error and rolls back
    unauth = User.objects.create_user(
        email="unauth_handoff@hpe.com",
        password="Password123!",
    )
    with pytest.raises(ValidationError):
        handoff_goods_to_stock_service(
            receipt=grn,
            handed_off_by=unauth,
            storage_location="BIN-ERR",
        )

    # Verify no stock records created
    assert not StockHandoffRecord.objects.filter(receipt_line__receipt=grn).exists()
    grn.refresh_from_db()
    assert grn.stock_handoff_status == "PENDING_HANDOFF"

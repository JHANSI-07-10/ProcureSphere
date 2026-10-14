from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import GoodsReceipt, InspectionRecord, ReceiptLine, RejectionRecord
from apps.receipts.services import create_goods_receipt_service, record_inspection_service
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def inspection_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Tech", code="HPE-INSP-ORG")
    dept = Department.objects.create(organization=org, name="Warehouse", code="DEPT-WH-01")
    cost_center = CostCenter.objects.create(department=dept, code="CC-WH-01", name="Receiving Dock")
    category = VendorCategory.objects.create(name="Networking", code="CAT-NET-01")
    vendor = Vendor.objects.create(
        legal_name="HPE Aruba Networks",
        tax_identification_number="TIN-ARUBA-INSP",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_insp@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-INSP-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("10000.00"),
        tax_amount=Decimal("1000.00"),
        total_amount=Decimal("11000.00"),
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Aruba CX 6200F Switch",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("1000.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="SFP+ 10G Transceiver",
        quantity=Decimal("20.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("100.00"),
    )

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
                "notes": "First partial batch",
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
                "notes": "Full transceiver delivery",
            },
        ],
        delivery_note_number="DN-ARUBA-2026",
        remarks="Delivered via DHL Express",
    )

    return {
        "org": org,
        "vendor": vendor,
        "receiver": receiver,
        "po": po,
        "line1": line1,
        "line2": line2,
        "grn": grn,
    }


@pytest.mark.django_db
def test_authenticated_stores_receiver_can_open_grn_inspection_page(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]

    client.force_login(receiver)
    response = client.get(f"/receipts/{grn.id}/inspect/")

    assert response.status_code == 200
    assert grn.grn_number.encode() in response.content
    assert inspection_setup["po"].po_number.encode() in response.content
    assert (
        b"Line Item Quality Inspection &amp; Rejection" in response.content
        or b"Line Item Quality Inspection" in response.content
        or b"Quality Inspection" in response.content
    )


@pytest.mark.django_db
def test_unauthenticated_user_cannot_access_inspection(client, inspection_setup):
    grn = inspection_setup["grn"]

    response = client.get(f"/receipts/{grn.id}/inspect/")
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_valid_inspection_creates_inspection_record(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]
    line2 = lines[1]

    client.force_login(receiver)
    post_data = {
        f"passed_{line1.id}": "true",
        f"inspection_notes_{line1.id}": "All 5 switches pass functional & visual QA tests.",
        f"passed_{line2.id}": "true",
        f"inspection_notes_{line2.id}": "All 20 transceivers verified.",
    }

    response = client.post(f"/receipts/{grn.id}/inspect/", post_data)
    assert response.status_code == 302
    assert response.url == f"/receipts/{grn.id}/"

    insp1 = InspectionRecord.objects.get(receipt_line=line1)
    assert insp1.passed is True
    assert insp1.inspected_by == receiver
    assert "visual QA tests" in insp1.inspection_notes

    insp2 = InspectionRecord.objects.get(receipt_line=line2)
    assert insp2.passed is True
    assert insp2.inspected_by == receiver


@pytest.mark.django_db
def test_failed_inspection_and_rejection_creation(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]  # received 5.00
    line2 = lines[1]  # received 20.00

    client.force_login(receiver)
    post_data = {
        f"passed_{line1.id}": "false",
        f"inspection_notes_{line1.id}": "2 switches have broken chassis ports.",
        f"rejected_quantity_{line1.id}": "2.00",
        f"rejection_reason_{line1.id}": "Physical port damage during transit",
        f"returned_to_vendor_{line1.id}": "on",
        f"passed_{line2.id}": "true",
        f"inspection_notes_{line2.id}": "Transceivers 100% verified.",
    }

    response = client.post(f"/receipts/{grn.id}/inspect/", post_data)
    assert response.status_code == 302

    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("5.00")
    assert line1.quantity_accepted == Decimal("3.00")
    assert line1.quantity_rejected == Decimal("2.00")
    assert line1.inspection_status == "REJECTED"

    # Verify InspectionRecord
    insp1 = InspectionRecord.objects.get(receipt_line=line1)
    assert insp1.passed is False
    assert "broken chassis ports" in insp1.inspection_notes

    # Verify RejectionRecord
    rej = RejectionRecord.objects.get(receipt_line=line1)
    assert rej.rejected_quantity == Decimal("2.00")
    assert "Physical port damage" in rej.rejection_reason
    assert rej.returned_to_vendor is True


@pytest.mark.django_db
def test_rejected_quantity_cannot_exceed_received_quantity(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]  # received 5.00

    client.force_login(receiver)
    post_data = {
        f"passed_{line1.id}": "false",
        f"inspection_notes_{line1.id}": "Attempting over-rejection",
        f"rejected_quantity_{line1.id}": "15.00",  # exceeds 5.00
        f"rejection_reason_{line1.id}": "Invalid high rejection",
    }

    response = client.post(f"/receipts/{grn.id}/inspect/", post_data)
    assert response.status_code == 200
    assert b"cannot exceed received quantity" in response.content

    # Ensure no corrupt rejection record created
    assert not RejectionRecord.objects.filter(receipt_line=line1).exists()


@pytest.mark.django_db
def test_negative_rejected_quantity_is_rejected(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]

    client.force_login(receiver)
    post_data = {
        f"passed_{line1.id}": "false",
        f"inspection_notes_{line1.id}": "Negative test",
        f"rejected_quantity_{line1.id}": "-5.00",
        f"rejection_reason_{line1.id}": "Invalid negative",
    }

    response = client.post(f"/receipts/{grn.id}/inspect/", post_data)
    assert response.status_code == 200
    assert b"cannot be negative" in response.content


@pytest.mark.django_db
def test_reinspection_updates_without_duplicate_onetoone_records(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]
    line2 = lines[1]

    # First inspection: Pass
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(line1.id),
                "passed": True,
                "inspection_notes": "First pass inspection",
            },
            {
                "receipt_line_id": str(line2.id),
                "passed": True,
                "inspection_notes": "First pass transceivers",
            },
        ],
    )

    assert InspectionRecord.objects.filter(receipt_line=line1).count() == 1
    insp1 = InspectionRecord.objects.get(receipt_line=line1)
    assert insp1.passed is True
    assert insp1.inspection_notes == "First pass inspection"

    # Second inspection (Re-inspection): Fail with 1 rejection
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(line1.id),
                "passed": False,
                "rejected_quantity": Decimal("1.00"),
                "rejection_reason": "Found defective capacitor upon stress testing",
                "inspection_notes": "Secondary stress testing failed on 1 unit",
                "returned_to_vendor": True,
            },
            {
                "receipt_line_id": str(line2.id),
                "passed": True,
                "inspection_notes": "Transceivers still good",
            },
        ],
    )

    # Verify no duplicate OneToOne record created
    assert InspectionRecord.objects.filter(receipt_line=line1).count() == 1
    insp1.refresh_from_db()
    assert insp1.passed is False
    assert "Secondary stress testing" in insp1.inspection_notes

    line1.refresh_from_db()
    assert line1.quantity_accepted == Decimal("4.00")
    assert line1.quantity_rejected == Decimal("1.00")


@pytest.mark.django_db
def test_cross_grn_receipt_line_tampering_is_rejected(client, inspection_setup, db_roles):
    receiver = inspection_setup["receiver"]
    grn1 = inspection_setup["grn"]
    po = inspection_setup["po"]

    # Create a second distinct GRN
    grn2 = GoodsReceipt.objects.create(
        grn_number="GRN-2026-UNRELATED",
        po=po,
        received_by=receiver,
        received_date=timezone.now(),
    )
    foreign_line = ReceiptLine.objects.create(
        receipt=grn2,
        po_line=inspection_setup["line1"],
        quantity_received=Decimal("2.00"),
        quantity_accepted=Decimal("2.00"),
    )

    # Attempt to submit foreign_line against grn1 in the service
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn1,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(foreign_line.id),
                    "passed": True,
                    "inspection_notes": "Tampering attempt",
                }
            ],
        )
    assert "does not match Goods Receipt or PO reference" in str(exc.value)


@pytest.mark.django_db
def test_grn_detail_view_displays_complete_information(client, inspection_setup):
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]

    # Add an inspection and rejection
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(line1.id),
                "passed": False,
                "rejected_quantity": Decimal("1.00"),
                "rejection_reason": "Damaged power socket",
                "inspection_notes": "Physical defects noted on socket",
                "returned_to_vendor": True,
            }
        ],
    )

    client.force_login(receiver)
    response = client.get(f"/receipts/{grn.id}/")

    assert response.status_code == 200
    assert grn.grn_number.encode() in response.content
    assert inspection_setup["po"].po_number.encode() in response.content
    assert b"Damaged power socket" in response.content
    assert b"Quality Inspection" in response.content


@pytest.mark.django_db
def test_unauthorized_roles_cannot_perform_inspection(inspection_setup, db_roles):
    """RBAC: Requester, Auditor, and Vendor users cannot execute record_inspection_service."""
    grn = inspection_setup["grn"]
    line1 = grn.lines.first()

    requester = User.objects.create_user(
        email="requester_unauth@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    auditor = User.objects.create_user(
        email="auditor_unauth@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    vendor_user = User.objects.create_user(
        email="vendor_unauth@hpe.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
    )

    staff_ordinary = User.objects.create_user(
        email="staff_unauth_insp@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        is_staff=True,
    )

    for unauth_user in [requester, auditor, vendor_user, staff_ordinary]:
        with pytest.raises(ValidationError) as exc:
            record_inspection_service(
                receipt=grn,
                inspected_by=unauth_user,
                inspection_items=[
                    {
                        "receipt_line_id": str(line1.id),
                        "passed": True,
                        "inspection_notes": "Unauthorized attempt",
                    }
                ],
            )
        assert "Unauthorized: Only Stores Receiver or Super Admin" in str(exc.value)

    # Super Admin can perform quality inspections
    admin = User.objects.create_user(
        email="admin_insp_ok@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    rec = record_inspection_service(
        receipt=grn,
        inspected_by=admin,
        inspection_items=[
            {
                "receipt_line_id": str(line1.id),
                "passed": True,
                "inspection_notes": "Super admin inspection passed",
            }
        ],
    )
    assert rec.overall_inspection_status in ["PASSED", "INSPECTED", "PARTIALLY_INSPECTED"]


@pytest.mark.django_db
def test_auditor_and_unauthorized_roles_blocked_from_inspection_view(
    client, inspection_setup, db_roles
):
    """RBAC View: Unauthorized roles and ordinary staff accessing /receipts/<id>/inspect/ are blocked."""
    grn = inspection_setup["grn"]
    line1 = grn.lines.first()

    auditor = User.objects.create_user(
        email="auditor_view@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    staff_ordinary = User.objects.create_user(
        email="staff_view_insp@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        is_staff=True,
    )

    for unauth_user in [auditor, staff_ordinary]:
        client.force_login(unauth_user)

        # GET request
        response = client.get(f"/receipts/{grn.id}/inspect/", follow=True)
        assert response.status_code == 200
        assert any(
            "Only Stores Receiver or Super Admin can perform quality inspections" in m.message
            for m in response.context["messages"]
        )

        # POST request attempt
        post_data = {
            f"passed_{line1.id}": "true",
            f"inspection_notes_{line1.id}": "Tampering attempt",
        }
        response_post = client.post(f"/receipts/{grn.id}/inspect/", post_data, follow=True)
        assert response_post.status_code == 200
        assert any(
            "Only Stores Receiver or Super Admin can perform quality inspections" in m.message
            for m in response_post.context["messages"]
        )


@pytest.mark.django_db
def test_rejection_without_reason_is_rejected_in_view(client, inspection_setup):
    """Validation: Rejecting an item without a reason in UI triggers a validation error."""
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    line1 = grn.lines.first()

    client.force_login(receiver)
    post_data = {
        f"passed_{line1.id}": "false",
        f"inspection_notes_{line1.id}": "Failed test",
        f"rejected_quantity_{line1.id}": "2.00",
        f"rejection_reason_{line1.id}": "",  # Empty rejection reason
    }

    response = client.post(f"/receipts/{grn.id}/inspect/", post_data)
    assert response.status_code == 200
    assert b"Rejection reason is required" in response.content
    assert not RejectionRecord.objects.filter(receipt_line=line1).exists()


@pytest.mark.django_db
def test_rejection_without_reason_or_notes_rejected_in_service(inspection_setup):
    """Validation: Calling service with rejected quantity but no reason or notes raises ValidationError."""
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    line1 = grn.lines.first()

    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(line1.id),
                    "passed": False,
                    "rejected_quantity": Decimal("2.00"),
                    "rejection_reason": "",
                    "inspection_notes": "",
                }
            ],
        )
    assert "Rejection reason is required" in str(exc.value)


@pytest.mark.django_db
def test_inspection_audit_log_captures_previous_and_new_state(inspection_setup):
    """Audit: Inspection updates create an AuditLog capturing both previous_state and new_state."""
    from apps.audit.models import AuditLog

    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    line1 = grn.lines.first()

    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(line1.id),
                "passed": False,
                "rejected_quantity": Decimal("1.50"),
                "rejection_reason": "Broken heat sink",
                "inspection_notes": "Hardware defect identified",
                "returned_to_vendor": True,
            }
        ],
    )

    audit = AuditLog.objects.filter(
        target_model="GoodsReceipt", target_object_id=str(grn.id)
    ).first()
    assert audit is not None
    assert audit.actor == receiver
    assert audit.action == AuditLog.ACTION_UPDATE
    assert audit.previous_state is not None
    assert "overall_inspection_status" in audit.previous_state
    assert audit.new_state is not None
    assert audit.new_state["overall_inspection_status"] == "FAILED"

    # Line details in new_state
    line_snapshot = [
        line_item for line_item in audit.new_state["lines"] if line_item["line_id"] == str(line1.id)
    ][0]
    assert line_snapshot["quantity_rejected"] == "1.50"
    assert line_snapshot["rejection_reason"] == "Broken heat sink"
    assert line_snapshot["inspection_notes"] == "Hardware defect identified"


@pytest.mark.django_db
def test_atomic_rollback_on_multi_line_inspection_error(inspection_setup):
    """Transactional Integrity: If one line in batch inspection fails, all line changes roll back."""
    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]
    lines = list(grn.lines.all())
    line1 = lines[0]
    line2 = lines[1]

    # Batch with line1 valid, but line2 invalid (negative quantity)
    with pytest.raises(ValidationError):
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(line1.id),
                    "passed": True,
                    "inspection_notes": "Line 1 should rollback",
                },
                {
                    "receipt_line_id": str(line2.id),
                    "passed": False,
                    "rejected_quantity": Decimal("-5.00"),  # Invalid negative
                },
            ],
        )

    # Verify line1 was rolled back and has no inspection record
    line1.refresh_from_db()
    assert not InspectionRecord.objects.filter(receipt_line=line1).exists()
    assert line1.inspection_status == "PENDING"


@pytest.mark.django_db
def test_cannot_inspect_nonexistent_receipt_line_raises_validation_error(inspection_setup):
    """Validation: Passing a non-existent receipt_line_id raises a clear ValidationError."""
    import uuid

    receiver = inspection_setup["receiver"]
    grn = inspection_setup["grn"]

    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(uuid.uuid4()),
                    "passed": True,
                    "inspection_notes": "Ghost line",
                }
            ],
        )
    assert "does not exist" in str(exc.value)

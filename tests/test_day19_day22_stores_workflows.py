import datetime
from decimal import Decimal
from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.orders.models import DeliverySchedule, POLine, PurchaseOrder
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
def batch4_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Global Enterprise B4", code="HPE-B4-ORG")
    dept = Department.objects.create(
        organization=org, name="Supply Chain & Stores B4", code="DEPT-SC-04"
    )
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-SC-04", name="Central Fulfilment Center B4"
    )
    category = VendorCategory.objects.create(name="Computing & Storage B4", code="CAT-COMP-04")
    vendor = Vendor.objects.create(
        legal_name="Enterprise Systems Logistics",
        tax_identification_number="TIN-ESL-B4-998",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_batch4@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    admin_user = User.objects.create_user(
        email="admin_batch4@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    requester = User.objects.create_user(
        email="requester_batch4@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    proc_exec = User.objects.create_user(
        email="proc_exec_batch4@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )
    finance = User.objects.create_user(
        email="finance_batch4@hpe.com",
        password="Password123!",
        role=db_roles[Role.FINANCE_AP],
    )
    auditor = User.objects.create_user(
        email="auditor_batch4@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    vendor_user = User.objects.create_user(
        email="vendor_user_batch4@esl.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-B4-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("80000.00"),
        tax_amount=Decimal("8000.00"),
        total_amount=Decimal("88000.00"),
        terms_and_conditions="Standard HPE Enterprise Procurement Terms & Conditions.",
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Enterprise Blade Server ProLiant Gen11",
        quantity=Decimal("100.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("600.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="High-Speed Fibre Channel SAN Switch",
        quantity=Decimal("40.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("500.00"),
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
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
# DAY 19: End-to-end Procurement → PO → Stores receipt integration
# ==============================================================================


@pytest.mark.django_db
def test_day19_e2e_procurement_po_to_stores_receipt_flow(client, batch4_setup):
    """
    Day 19: End-to-end flow from Procurement PO issuance to Stores partial receipt
    and final completion:
    1. PO is in ISSUED status with open line items.
    2. Stores Receiver initiates partial receipt for both lines.
    3. PO transitions to PARTIAL_RECEIPT, cumulative quantities update.
    4. Subsequent receipt satisfies remaining quantities.
    5. PO transitions to COMPLETED, receiving is locked.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    line2 = batch4_setup["line2"]
    receiver = batch4_setup["receiver"]
    proc_exec = batch4_setup["proc_exec"]

    client.force_login(receiver)

    # 1. PO detail view before receipts
    resp_initial = client.get(f"/purchase-orders/{po.id}/")
    assert resp_initial.status_code == 200
    initial_content = resp_initial.content.decode()
    assert "PO-2026-B4-001" in initial_content
    assert "Start Goods Receipt" in initial_content

    # 2. First receipt: Partial delivery (50/100 servers, 20/40 switches)
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
                "notes": "First partial shipment",
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
                "notes": "First partial shipment switches",
            },
        ],
        delivery_note_number="DN-B4-SHIPMENT-01",
        remarks="Partial delivery received in good condition",
    )

    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert line1.quantity_received == Decimal("50.00")
    assert line1.remaining_quantity == Decimal("50.00")
    assert line2.quantity_received == Decimal("20.00")
    assert line2.remaining_quantity == Decimal("20.00")

    # 3. PO detail view now displays PARTIAL_RECEIPT badge and the Goods Receipts card
    resp_partial = client.get(f"/purchase-orders/{po.id}/")
    assert resp_partial.status_code == 200
    partial_content = resp_partial.content.decode()
    assert "Partial Receipt" in partial_content
    assert grn1.grn_number in partial_content
    assert "DN-B4-SHIPMENT-01" in partial_content
    assert "Goods Receipts & Deliveries" in partial_content

    # 4. Second receipt: Fulfill remaining quantities (50 servers, 20 switches)
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
                "notes": "Final balance delivery",
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
                "notes": "Final balance switches",
            },
        ],
        delivery_note_number="DN-B4-SHIPMENT-02",
        remarks="Balance delivery fulfilling entire purchase order",
    )

    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert po.status == PurchaseOrder.STATUS_COMPLETED
    assert line1.quantity_received == Decimal("100.00")
    assert line1.remaining_quantity == Decimal("0.00")
    assert line2.quantity_received == Decimal("40.00")
    assert line2.remaining_quantity == Decimal("0.00")

    # 5. PO detail view displays COMPLETED badge and both GRNs for Procurement Executive
    client.force_login(proc_exec)
    resp_complete = client.get(f"/purchase-orders/{po.id}/")
    assert resp_complete.status_code == 200
    complete_content = resp_complete.content.decode()
    assert "Completed" in complete_content
    assert grn1.grn_number in complete_content
    assert grn2.grn_number in complete_content

    # Stores Receiver viewing completed PO detail is redirected (scoped to receivable POs)
    client.force_login(receiver)
    resp_recv_complete = client.get(f"/purchase-orders/{po.id}/")
    assert resp_recv_complete.status_code == 302

    # 6. Post-completion lock: Attempting further receipts on completed PO raises ValidationError
    with pytest.raises(ValidationError) as exc_info:
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
            delivery_note_number="DN-ILLEGAL-POST-COMPLETION",
        )
    assert "Cannot record goods receipt against PO in status 'COMPLETED'" in str(exc_info.value)


@pytest.mark.django_db
def test_day19_po_detail_view_goods_receipts_history_table(client, batch4_setup):
    """
    Day 19: PO detail view renders associated Goods Receipts table with GRN metadata,
    inspection status badges, and direct links to GRN details.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]
    client.force_login(receiver)

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("25.00"),
                "quantity_accepted": Decimal("25.00"),
            }
        ],
        delivery_note_number="DN-HIST-001",
    )

    response = client.get(f"/purchase-orders/{po.id}/")
    assert response.status_code == 200
    content = response.content.decode()

    assert "Goods Receipts & Deliveries" in content
    assert grn.grn_number in content
    assert "DN-HIST-001" in content
    assert f"/receipts/{grn.id}/" in content
    assert "View GRN" in content


@pytest.mark.django_db
def test_day19_po_lifecycle_state_guards_for_receipts(batch4_setup):
    """
    Day 19: Validates PO state machine guards:
    - DRAFT: Blocked
    - APPROVAL: Blocked
    - CANCELLED: Blocked
    """
    vendor = batch4_setup["vendor"]
    cost_center = batch4_setup["cost_center"]
    receiver = batch4_setup["receiver"]

    for blocked_status in [
        PurchaseOrder.STATUS_DRAFT,
        PurchaseOrder.STATUS_APPROVAL,
        PurchaseOrder.STATUS_CANCELLED,
    ]:
        test_po = PurchaseOrder.objects.create(
            po_number=f"PO-BLOCKED-{blocked_status}",
            vendor=vendor,
            cost_center=cost_center,
            status=blocked_status,
            total_amount=Decimal("5000.00"),
        )
        test_line = POLine.objects.create(
            po=test_po,
            item_description="Test Item",
            quantity=Decimal("10.00"),
            unit_price=Decimal("500.00"),
        )

        with pytest.raises(ValidationError) as exc:
            create_goods_receipt_service(
                po=test_po,
                received_by=receiver,
                receipt_items=[
                    {
                        "po_line_id": str(test_line.id),
                        "quantity_received": Decimal("5.00"),
                        "quantity_accepted": Decimal("5.00"),
                    }
                ],
            )
        assert f"Cannot record goods receipt against PO in status '{blocked_status}'" in str(
            exc.value
        )


@pytest.mark.django_db
def test_day19_procurement_executive_vs_stores_receiver_ui_actions(client, batch4_setup):
    """
    Day 19: Stores Receiver vs Procurement Executive role-specific action buttons:
    - Stores Receiver sees 'Start Goods Receipt' and does NOT see 'Amend PO'.
    - Procurement Executive sees 'Amend PO' and does NOT see 'Start Goods Receipt'.
    """
    po = batch4_setup["po"]
    receiver = batch4_setup["receiver"]
    proc_exec = batch4_setup["proc_exec"]

    # Stores Receiver
    client.force_login(receiver)
    resp_receiver = client.get(f"/purchase-orders/{po.id}/")
    assert resp_receiver.status_code == 200
    content_receiver = resp_receiver.content.decode()
    assert "Start Goods Receipt" in content_receiver
    assert "Amend PO (Change Order)" not in content_receiver

    # Procurement Executive
    client.force_login(proc_exec)
    resp_pe = client.get(f"/purchase-orders/{po.id}/")
    assert resp_pe.status_code == 200
    content_pe = resp_pe.content.decode()
    assert "Amend PO (Change Order)" in content_pe
    assert "Start Goods Receipt" not in content_pe


# ==============================================================================
# DAY 20: Validate Stores RBAC, receiver permissions and restricted actions
# ==============================================================================


@pytest.mark.django_db
def test_day20_stores_receiver_positive_permissions(client, batch4_setup):
    """
    Day 20: Stores Receiver has positive authorization to access all Stores views,
    queues, and operational endpoints.
    """
    receiver = batch4_setup["receiver"]
    client.force_login(receiver)

    assert client.get("/receipts/").status_code == 200
    assert client.get("/receipts/dashboard/").status_code == 200
    assert client.get("/receipts/inspections/").status_code == 200
    assert client.get("/receipts/handoffs/").status_code == 200
    assert client.get("/receipts/rejections/").status_code == 200
    assert client.get("/purchase-orders/").status_code == 200


@pytest.mark.django_db
def test_day20_unauthorized_roles_blocked_from_stores_services(batch4_setup):
    """
    Day 20: Non-stores roles (Requester, Vendor User, Procurement Exec, Finance, Auditor)
    are strictly rejected with ValidationError when invoking Stores services.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    unauthorized_users = [
        batch4_setup["requester"],
        batch4_setup["vendor_user"],
        batch4_setup["proc_exec"],
        batch4_setup["finance"],
        batch4_setup["auditor"],
    ]

    for unauth_user in unauthorized_users:
        with pytest.raises(ValidationError) as exc:
            create_goods_receipt_service(
                po=po,
                received_by=unauth_user,
                receipt_items=[
                    {
                        "po_line_id": str(line1.id),
                        "quantity_received": Decimal("5.00"),
                        "quantity_accepted": Decimal("5.00"),
                    }
                ],
            )
        assert "Unauthorized: Only Stores Receiver or Super Admin" in str(exc.value)


@pytest.mark.django_db
def test_day20_vendor_user_blocked_from_all_stores_queues_and_views(client, batch4_setup):
    """
    Day 20: Vendor Portal User attempting to access internal Stores receipts,
    queues, or dashboards is blocked and redirected to vendor dashboard.
    """
    vendor_user = batch4_setup["vendor_user"]
    po = batch4_setup["po"]
    client.force_login(vendor_user)

    for path in [
        "/receipts/",
        "/receipts/dashboard/",
        "/receipts/inspections/",
        "/receipts/handoffs/",
        "/receipts/rejections/",
        f"/receipts/create/{po.id}/",
    ]:
        resp = client.get(path, follow=False)
        assert resp.status_code == 302
        assert "/vendor" in resp.url or "/login/" in resp.url or "/orders/" in resp.url


@pytest.mark.django_db
def test_day20_stores_receiver_blocked_from_procurement_po_execution(client, batch4_setup):
    """
    Day 20: Stores Receiver attempting to perform procurement executive actions
    (e.g., amend PO, generate PO) is blocked with PermissionDenied.
    """
    receiver = batch4_setup["receiver"]
    po = batch4_setup["po"]
    client.force_login(receiver)

    resp_amend = client.get(reverse("orders_amend_modal", kwargs={"pk": po.id}))
    assert resp_amend.status_code == 403


@pytest.mark.django_db
def test_day20_drf_api_rbac_stores_vs_unauthorized_roles(batch4_setup):
    """
    Day 20: REST API (/api/v1/receipts/) verifies backend authorization:
    - Stores Receiver can read (200).
    - Vendor user is denied (403).
    - Unauthenticated is rejected (401 or 403).
    - Auditor can read (200).
    """
    api_client = APIClient()
    receiver = batch4_setup["receiver"]
    vendor_user = batch4_setup["vendor_user"]
    auditor = batch4_setup["auditor"]

    # 1. Unauthenticated -> 401 or 403 (unauthorized/forbidden)
    resp_unauth = api_client.get("/api/v1/receipts/")
    assert resp_unauth.status_code in [401, 403]

    # 2. Stores Receiver -> 200
    api_client.force_authenticate(user=receiver)
    resp_receiver = api_client.get("/api/v1/receipts/")
    assert resp_receiver.status_code == 200

    # 3. Vendor User -> 403
    api_client.force_authenticate(user=vendor_user)
    resp_vendor = api_client.get("/api/v1/receipts/")
    assert resp_vendor.status_code == 403

    # 4. Auditor -> 200
    api_client.force_authenticate(user=auditor)
    resp_auditor = api_client.get("/api/v1/receipts/")
    assert resp_auditor.status_code == 200


# ==============================================================================
# DAY 21: Receipt/Inspection integration and regression testing
# ==============================================================================


@pytest.mark.django_db
def test_day21_receipt_to_inspection_lifecycle_progression(client, batch4_setup):
    """
    Day 21: Receipt to Inspection lifecycle progression:
    1. GRN created initially PENDING inspection.
    2. Inspection queue lists GRN.
    3. Inspection recorded with PASSED status.
    4. Stock handoff queue displays GRN ready for warehouse handoff.
    5. Hand off to stock moves goods into warehouse.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]

    # 1. Create GRN
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
        delivery_note_number="DN-INSP-FLOW-01",
    )
    assert grn.overall_inspection_status == "PENDING"
    assert grn.can_hand_off_to_stock is True

    # 2. Record Inspection (PASSED)
    receipt_line = grn.lines.first()
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": True,
                "inspection_notes": "Passed technical inspection; 100% verified.",
            }
        ],
    )
    grn.refresh_from_db()
    assert grn.overall_inspection_status == "PASSED"

    # 3. Hand off to Stock
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-4-ELECTRONICS",
        handoff_notes="Shelved in Server Rack Bay 4",
    )
    grn.refresh_from_db()
    assert grn.stock_handoff_status == "HANDED_OFF"
    assert grn.lines.first().is_handed_off is True


@pytest.mark.django_db
def test_day21_inspection_rejection_and_vendor_return_flow(batch4_setup):
    """
    Day 21: Quality non-conformance rejection and vendor return workflow:
    - 20 units received.
    - Inspection rejects 5 units due to packaging damage.
    - RejectionRecord created for 5 units.
    - Process vendor return with tracking reference.
    - RejectionRecord marked returned_to_vendor=True.
    - Only 15 units can be handed off to stock.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]

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
        delivery_note_number="DN-REJ-RETURN-01",
    )
    rline = grn.lines.first()

    # Record inspection with partial rejection
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rline.id),
                "passed": False,
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "Damaged exterior chassis during transit",
                "inspection_notes": "5 units quarantined",
            }
        ],
    )

    rline.refresh_from_db()
    grn.refresh_from_db()

    assert rline.quantity_accepted == Decimal("15.00")
    assert rline.quantity_rejected == Decimal("5.00")
    assert grn.overall_inspection_status == "FAILED"

    rej_rec = rline.rejections.first()
    assert rej_rec is not None
    assert rej_rec.rejected_quantity == Decimal("5.00")
    assert rej_rec.returned_to_vendor is False

    # Process return to vendor
    process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rej_rec,
        return_quantity=Decimal("5.00"),
        tracking_reference="FEDEX-RET-884920",
        return_notes="Dispatched back to vendor RMA warehouse",
    )

    rej_rec.refresh_from_db()
    assert rej_rec.returned_to_vendor is True
    assert "FEDEX-RET-884920" in rej_rec.rejection_reason

    # Hand off remaining 15 accepted units
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-MAIN",
    )
    rline.refresh_from_db()
    assert rline.stock_handoff.quantity_handed_off == Decimal("15.00")


@pytest.mark.django_db
def test_day21_inspection_status_recalculation_and_po_quantity_sync(batch4_setup):
    """
    Day 21: Inspection status change dynamically syncs POLine received quantity
    and updates PO completion status with immutable audit logging.
    """
    po = batch4_setup["po"]
    line2 = batch4_setup["line2"]  # quantity = 40.00
    receiver = batch4_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("40.00"),
                "quantity_accepted": Decimal("40.00"),
            }
        ],
        delivery_note_number="DN-SYNC-001",
    )
    line2.refresh_from_db()
    assert line2.quantity_received == Decimal("40.00")

    rline = grn.lines.first()

    # Re-inspect to reject 10 units after stress testing
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rline.id),
                "passed": False,
                "rejected_quantity": Decimal("10.00"),
                "rejection_reason": "Failed 48-hour thermal load test",
            }
        ],
    )

    rline.refresh_from_db()
    line2.refresh_from_db()

    assert rline.quantity_accepted == Decimal("30.00")
    assert rline.quantity_rejected == Decimal("10.00")
    assert line2.quantity_received == Decimal("30.00")
    assert line2.remaining_quantity == Decimal("10.00")

    # Verify audit log was recorded
    audit = AuditLog.objects.filter(
        target_model="GoodsReceipt", target_object_id=str(grn.id)
    ).first()
    assert audit is not None


@pytest.mark.django_db
def test_day21_inspection_regression_invalid_transitions(batch4_setup):
    """
    Day 21: Edge cases and negative validations in inspection:
    - Negative rejected quantity rejected.
    - Rejected quantity exceeding received quantity rejected.
    - Rejection without reason rejected.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]

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
        delivery_note_number="DN-INVAL-01",
    )
    rline = grn.lines.first()

    # 1. Negative rejected quantity
    with pytest.raises(ValidationError) as exc1:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(rline.id),
                    "rejected_quantity": Decimal("-5.00"),
                }
            ],
        )
    assert "cannot be negative" in str(exc1.value)

    # 2. Rejected quantity > received quantity
    with pytest.raises(ValidationError) as exc2:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(rline.id),
                    "rejected_quantity": Decimal("15.00"),
                }
            ],
        )
    assert "cannot exceed received quantity" in str(exc2.value)

    # 3. Rejected without reason or notes
    with pytest.raises(ValidationError) as exc3:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(rline.id),
                    "rejected_quantity": Decimal("5.00"),
                    "rejection_reason": "",
                    "inspection_notes": "",
                }
            ],
        )
    assert "Rejection reason is required" in str(exc3.value)


# ==============================================================================
# DAY 22: Validate GRN/service-entry mandatory fields, quantities and receipt validations
# ==============================================================================


@pytest.mark.django_db
def test_day22_mandatory_field_validations(batch4_setup):
    """
    Day 22: Mandatory field validations on Goods Receipt creation:
    - po is mandatory
    - received_by is mandatory
    - receipt_items is non-empty
    - po_line is valid and belongs to the specified PO
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]
    vendor = batch4_setup["vendor"]
    cost_center = batch4_setup["cost_center"]

    # 1. Missing PO
    with pytest.raises(ValidationError) as exc1:
        create_goods_receipt_service(
            po=None,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("5.00")}],
        )
    assert "Purchase Order reference is mandatory" in str(exc1.value)

    # 2. Missing received_by
    with pytest.raises(ValidationError) as exc2:
        create_goods_receipt_service(
            po=po,
            received_by=None,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("5.00")}],
        )
    assert "Received by user reference is mandatory" in str(exc2.value)

    # 3. Empty receipt_items
    with pytest.raises(ValidationError) as exc3:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[],
        )
    assert "Receipt must contain at least one line item" in str(exc3.value)

    # 4. Line item with mismatched PO line (belongs to a different PO)
    other_po = PurchaseOrder.objects.create(
        po_number="PO-OTHER-2026",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("1000.00"),
    )
    other_line = POLine.objects.create(
        po=other_po,
        item_description="Other PO Line",
        quantity=Decimal("10.00"),
        unit_price=Decimal("100.00"),
    )

    with pytest.raises(ValidationError) as exc4:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {"po_line_id": str(other_line.id), "quantity_received": Decimal("5.00")}
            ],
        )
    assert "does not belong to Purchase Order" in str(exc4.value)


@pytest.mark.django_db
def test_day22_quantity_validation_guards(batch4_setup):
    """
    Day 22: Strict quantity validations:
    - Zero or negative quantity received rejected.
    - Non-numeric quantity received rejected.
    - Over-delivery exceeding remaining quantity rejected.
    - Negative quantity accepted rejected.
    - Quantity accepted exceeding quantity received rejected.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]

    # 1. Zero quantity received
    with pytest.raises(ValidationError) as exc1:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("0.00")}],
        )
    assert "must be greater than 0" in str(exc1.value)

    # 2. Negative quantity received
    with pytest.raises(ValidationError) as exc2:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("-10.00")}],
        )
    assert "must be greater than 0" in str(exc2.value)

    # 3. Over-delivery (quantity_received > 100.00)
    with pytest.raises(ValidationError) as exc3:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("105.00")}],
        )
    assert "cannot exceed remaining quantity" in str(exc3.value)

    # 4. Negative quantity accepted
    with pytest.raises(ValidationError) as exc4:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("10.00"),
                    "quantity_accepted": Decimal("-2.00"),
                }
            ],
        )
    assert "cannot be negative" in str(exc4.value)

    # 5. Quantity accepted > quantity received
    with pytest.raises(ValidationError) as exc5:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("10.00"),
                    "quantity_accepted": Decimal("12.00"),
                }
            ],
        )
    assert "cannot exceed quantity received" in str(exc5.value)


@pytest.mark.django_db
def test_day22_cumulative_quantities_across_sequential_receipts(batch4_setup):
    """
    Day 22: Cumulative received quantity enforcement across sequential receipts:
    - Line ordered = 100
    - GRN 1: 40 received (remaining = 60)
    - GRN 2: 35 received (remaining = 25)
    - GRN 3: Attempting 30 received is blocked (exceeds 25)
    - GRN 3: 25 received succeeds, remaining = 0, line fully received.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]

    # GRN 1
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("40.00")}],
        delivery_note_number="DN-SEQ-01",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("40.00")
    assert line1.remaining_quantity == Decimal("60.00")

    # GRN 2
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("35.00")}],
        delivery_note_number="DN-SEQ-02",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("75.00")
    assert line1.remaining_quantity == Decimal("25.00")

    # Attempt over-delivery on GRN 3
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("30.00")}],
            delivery_note_number="DN-SEQ-03-OVER",
        )
    assert "cannot exceed remaining quantity (25.00)" in str(exc.value)

    # Exact remaining balance on GRN 3
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("25.00")}],
        delivery_note_number="DN-SEQ-03",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("100.00")
    assert line1.remaining_quantity == Decimal("0.00")


@pytest.mark.django_db
def test_day22_service_entry_and_delivery_documentation(batch4_setup):
    """
    Day 22: Verifies delivery note, waybill/carrier reference, and remarks
    are persisted on GoodsReceipt and recorded in AuditLog.
    """
    po = batch4_setup["po"]
    line1 = batch4_setup["line1"]
    receiver = batch4_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
                "notes": "Verified against air waybill",
            }
        ],
        delivery_note_number="AWB-DHL-99281726",
        remarks="Delivered via DHL Express Priority. Seals intact.",
    )

    assert grn.delivery_note_number == "AWB-DHL-99281726"
    assert grn.remarks == "Delivered via DHL Express Priority. Seals intact."
    assert grn.received_date is not None

    audit = AuditLog.objects.filter(
        target_model="GoodsReceipt", target_object_id=str(grn.id)
    ).first()
    assert audit is not None
    assert audit.new_state["grn_number"] == grn.grn_number
    assert audit.new_state["po_number"] == po.po_number

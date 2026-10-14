import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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
    create_goods_receipt_service,
    handoff_goods_to_stock_service,
    record_inspection_service,
)
from apps.reports.services import (
    get_receipt_delivery_performance_summary,
    get_receipt_rejection_report,
)
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def batch3_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Global B3", code="HPE-BATCH3-ORG")
    dept = Department.objects.create(organization=org, name="Warehouse Ops B3", code="DEPT-WH-03")
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-WH-03", name="Regional Depot B3"
    )
    category = VendorCategory.objects.create(name="Electronics B3", code="CAT-ELEC-03")
    vendor = Vendor.objects.create(
        legal_name="Apex Hardware Systems",
        tax_identification_number="TIN-APEX-003",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_batch3@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    admin_user = User.objects.create_user(
        email="admin_batch3@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    requester = User.objects.create_user(
        email="requester_batch3@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    auditor = User.objects.create_user(
        email="auditor_batch3@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    finance = User.objects.create_user(
        email="finance_batch3@hpe.com",
        password="Password123!",
        role=db_roles[Role.FINANCE_AP],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-BATCH3-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("60000.00"),
        tax_amount=Decimal("6000.00"),
        total_amount=Decimal("66000.00"),
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Precision Server Blade Gen11",
        quantity=Decimal("100.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("500.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="Hot-Plug Redundant Power Supply",
        quantity=Decimal("50.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("200.00"),
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
        "finance": finance,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


# ==============================================================================
# DAY 15: TEST PO → PARTIAL RECEIPT → INSPECTION WORKFLOW
# ==============================================================================


@pytest.mark.django_db
def test_day15_e2e_po_partial_receipt_inspection_and_handoff_workflow(batch3_setup):
    """
    Day 15.1: End-to-end verification of PO -> Partial Receipt -> Inspection -> Stock Handoff.
    Verifies state transitions, balances, and audit records at every phase.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    receiver = batch3_setup["receiver"]

    # Step 1: Initial PO State
    assert po.status == PurchaseOrder.STATUS_ISSUED
    assert line1.quantity_received == Decimal("0.00")
    assert line1.remaining_quantity == Decimal("100.00")

    # Step 2: Partial Receipt of 40 units
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("40.00")},
        ],
        delivery_note_number="DN-DAY15-001",
        remarks="Partial delivery for server rack assembly",
    )
    po.refresh_from_db()
    line1.refresh_from_db()
    assert grn.grn_number.startswith("GRN-")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert line1.quantity_received == Decimal("40.00")
    assert line1.remaining_quantity == Decimal("60.00")

    receipt_line = grn.lines.first()
    assert receipt_line.quantity_received == Decimal("40.00")
    assert receipt_line.quantity_accepted == Decimal(
        "40.00"
    )  # initially accepted before inspection
    assert grn.overall_inspection_status == "PENDING"

    # Step 3: Inspection - 35 accepted, 5 rejected
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line.id),
                "passed": False,
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "Physical hairline crack on heatsink connector",
                "inspection_notes": "Passed functional check on 35 units; 5 units failed mechanical check",
            }
        ],
    )
    grn.refresh_from_db()
    line1.refresh_from_db()
    receipt_line.refresh_from_db()

    assert grn.overall_inspection_status == "FAILED"
    assert receipt_line.quantity_accepted == Decimal("35.00")
    assert receipt_line.quantity_rejected == Decimal("5.00")
    # PO Line received quantity reflects net accepted items
    assert line1.quantity_received == Decimal("35.00")
    assert line1.remaining_quantity == Decimal("65.00")

    inspection = receipt_line.inspection
    assert inspection is not None
    assert inspection.passed is False
    assert "Passed functional check" in inspection.inspection_notes

    rejection = receipt_line.rejections.first()
    assert rejection is not None
    assert rejection.rejected_quantity == Decimal("5.00")
    assert "hairline crack" in rejection.rejection_reason

    # Step 4: Stock Handoff of the 35 accepted units to Central Inventory
    handoff_receipt = handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="MAIN-DEPOT-ROW-4",
        handoff_notes="Released to rack assembly team",
    )
    handoff_records = StockHandoffRecord.objects.filter(receipt_line__receipt=grn)
    assert handoff_records.count() == 1
    assert handoff_records.first().storage_location == "MAIN-DEPOT-ROW-4"
    assert handoff_records.first().quantity_handed_off == Decimal("35.00")

    grn.refresh_from_db()
    assert grn.stock_handoff_status == "HANDED_OFF"

    # Step 5: Verify Audit trail created
    audit_entries = AuditLog.objects.filter(target_object_id=str(grn.id))
    assert audit_entries.count() >= 2  # GRN create + record inspection


@pytest.mark.django_db
def test_day15_inspection_rejection_quarantine_and_evidence(batch3_setup):
    """
    Day 15.2: Verification of quality rejection records, evidence documentation,
    and quarantine disposition without destroying transaction history.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    receiver = batch3_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
        ],
        delivery_note_number="DN-REJECT-TEST",
    )
    rl = grn.lines.first()

    # Reject entire quantity of 50 units
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl.id),
                "passed": False,
                "rejected_quantity": Decimal("50.00"),
                "rejection_reason": "Packaging compromised, water ingress detected across all crates",
                "inspection_notes": "Quarantined in Sector Q-8 pending vendor RMA pickup",
            }
        ],
    )
    grn.refresh_from_db()
    rl.refresh_from_db()
    line1.refresh_from_db()

    assert grn.overall_inspection_status == "FAILED"
    assert rl.quantity_accepted == Decimal("0.00")
    assert rl.quantity_rejected == Decimal("50.00")
    # PO Line remaining quantity must remain 100 because 0 were accepted
    assert line1.quantity_received == Decimal("0.00")
    assert line1.remaining_quantity == Decimal("100.00")

    rejection = rl.rejections.first()
    assert rejection.rejected_quantity == Decimal("50.00")
    assert "water ingress" in rejection.rejection_reason
    assert rejection.returned_to_vendor is False


@pytest.mark.django_db
def test_day15_unauthorized_roles_blocked_from_receipt_inspection_handoff(batch3_setup):
    """
    Day 15.3: Verify strict RBAC authorization guards: Requesters, Finance, and Auditor
    are forbidden from executing receipt, inspection, and handoff state changes.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    requester = batch3_setup["requester"]
    auditor = batch3_setup["auditor"]
    receiver = batch3_setup["receiver"]

    # Requester cannot create GRN
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=requester,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("10.00")}],
        )
    assert "Unauthorized" in str(exc.value)

    # Valid GRN created by Stores Receiver
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("10.00")}],
    )
    rl = grn.lines.first()

    # Auditor cannot record inspection
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=auditor,
            inspection_items=[{"receipt_line_id": str(rl.id), "passed": True}],
        )
    assert "Unauthorized" in str(exc.value)

    # Requester cannot hand off to stock
    with pytest.raises(ValidationError) as exc:
        handoff_goods_to_stock_service(
            receipt=grn,
            handed_off_by=requester,
            storage_location="WH-MAIN",
        )
    assert "Unauthorized" in str(exc.value)


@pytest.mark.django_db
def test_day15_inspection_quantity_validation_guards(batch3_setup):
    """
    Day 15.4: Verify domain guards: rejected quantity cannot exceed received quantity,
    cannot be negative, and rejection reason is mandatory when rejecting goods.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    receiver = batch3_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("20.00")}],
    )
    rl = grn.lines.first()

    # Guard 1: Rejected quantity exceeds received quantity
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(rl.id),
                    "passed": False,
                    "rejected_quantity": Decimal("25.00"),
                    "rejection_reason": "Defective components",
                }
            ],
        )
    assert "cannot exceed received quantity" in str(exc.value)

    # Guard 2: Negative rejected quantity
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(rl.id),
                    "passed": False,
                    "rejected_quantity": Decimal("-5.00"),
                    "rejection_reason": "Defective components",
                }
            ],
        )
    assert "cannot be negative" in str(exc.value)

    # Guard 3: Rejection without reason or notes
    with pytest.raises(ValidationError) as exc:
        record_inspection_service(
            receipt=grn,
            inspected_by=receiver,
            inspection_items=[
                {
                    "receipt_line_id": str(rl.id),
                    "passed": False,
                    "rejected_quantity": Decimal("5.00"),
                    "rejection_reason": "",
                    "inspection_notes": "",
                }
            ],
        )
    assert "Rejection reason is required" in str(exc.value)


# ==============================================================================
# DAY 16: COMPLETE RECEIPT / REJECTION / DELIVERY REPORTING
# ==============================================================================


@pytest.mark.django_db
def test_day16_receipt_rejection_report_delivery_performance_calculation(batch3_setup):
    """
    Day 16.1: Verify delivery performance reporting against DeliverySchedule
    (ON_TIME vs LATE, variance in days, rejection rates).
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    line2 = batch3_setup["line2"]
    receiver = batch3_setup["receiver"]

    today = timezone.now().date()
    yesterday = today - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)

    # Schedule line1 expected yesterday (so delivery today is LATE by +1 day)
    DeliverySchedule.objects.create(
        po=po,
        po_line=line1,
        expected_delivery_date=yesterday,
        quantity_expected=Decimal("100.00"),
    )

    # Schedule line2 expected tomorrow (so delivery today is ON_TIME by -1 day)
    DeliverySchedule.objects.create(
        po=po,
        po_line=line2,
        expected_delivery_date=tomorrow,
        quantity_expected=Decimal("50.00"),
    )

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
            {"po_line_id": str(line2.id), "quantity_received": Decimal("25.00")},
        ],
    )

    # Record inspection: line1 has 10 rejected, line2 has 0 rejected
    rl1 = grn.lines.get(po_line=line1)
    rl2 = grn.lines.get(po_line=line2)
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl1.id),
                "passed": False,
                "rejected_quantity": Decimal("10.00"),
                "rejection_reason": "Broken latch",
            },
            {
                "receipt_line_id": str(rl2.id),
                "passed": True,
                "rejected_quantity": Decimal("0.00"),
            },
        ],
    )

    report = get_receipt_rejection_report(po_number=po.po_number)
    assert len(report) == 2

    row_l1 = next(r for r in report if r["item_description"] == line1.item_description)
    assert row_l1["delivery_performance"] == "LATE"
    assert row_l1["days_variance"] == 1
    assert row_l1["qty_received"] == 50.0
    assert row_l1["qty_accepted"] == 40.0
    assert row_l1["qty_rejected"] == 10.0
    assert row_l1["rejection_rate_percent"] == 20.0
    assert "Broken latch" in row_l1["rejection_reason"]

    row_l2 = next(r for r in report if r["item_description"] == line2.item_description)
    assert row_l2["delivery_performance"] == "ON_TIME"
    assert row_l2["days_variance"] == -1
    assert row_l2["qty_received"] == 25.0
    assert row_l2["qty_rejected"] == 0.0
    assert row_l2["rejection_rate_percent"] == 0.0


@pytest.mark.django_db
def test_day16_delivery_performance_summary_kpi_metrics(batch3_setup):
    """
    Day 16.2: Verify aggregated KPI summary math across multiple receipts,
    including overall rejection percentage, on-time percentage, and return statuses.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    receiver = batch3_setup["receiver"]

    today = timezone.now().date()
    DeliverySchedule.objects.create(
        po=po,
        po_line=line1,
        expected_delivery_date=today,
        quantity_expected=Decimal("100.00"),
    )

    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("40.00")}],
    )
    rl1 = grn1.lines.first()
    record_inspection_service(
        receipt=grn1,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl1.id),
                "passed": False,
                "rejected_quantity": Decimal("8.00"),
                "rejection_reason": "Slight oxidation",
            }
        ],
    )

    summary = get_receipt_delivery_performance_summary(vendor=batch3_setup["vendor"].legal_name)
    assert summary["total_grns"] == 1
    assert summary["total_lines"] == 1
    assert summary["total_qty_received"] == 40.0
    assert summary["total_qty_accepted"] == 32.0
    assert summary["total_qty_rejected"] == 8.0
    assert summary["overall_rejection_rate_percent"] == 20.0
    assert summary["on_time_delivery_rate_percent"] == 100.0
    assert summary["returns_pending"] == 1


@pytest.mark.django_db
def test_day16_api_endpoints_receipt_rejection_and_delivery_summary(api_client, batch3_setup):
    """
    Day 16.3: Verify REST API endpoints for receipt rejection report and delivery performance summary.
    """
    admin_user = batch3_setup["admin_user"]
    api_client.force_authenticate(user=admin_user)

    url_report = reverse("report-receipt-rejection")
    resp_report = api_client.get(url_report, {"po_number": batch3_setup["po"].po_number})
    assert resp_report.status_code == 200
    assert isinstance(resp_report.data, list)

    url_summary = reverse("report-receipt-delivery-summary")
    resp_summary = api_client.get(url_summary)
    assert resp_summary.status_code == 200
    assert "total_grns" in resp_summary.data
    assert "overall_rejection_rate_percent" in resp_summary.data


@pytest.mark.django_db
def test_day16_delivery_performance_report_view_and_rbac(batch3_setup):
    """
    Day 16.4: Verify server-rendered Delivery Performance Report view, RBAC scoping,
    and CSV/XLSX export triggers with audit logging.
    """
    client = Client()
    report_url = reverse("receipt_delivery_performance_report")

    # 1. Unauthenticated -> Redirect to login
    resp_anon = client.get(report_url)
    assert resp_anon.status_code in [302, 403]

    # 2. Requester -> Permission denied redirect
    client.force_login(batch3_setup["requester"])
    resp_req = client.get(report_url, follow=True)
    assert "Permission Denied" in str(resp_req.content)

    # 3. Stores Receiver -> Authorized (HTTP 200)
    client.force_login(batch3_setup["receiver"])
    resp_stores = client.get(report_url)
    assert resp_stores.status_code == 200
    assert "Receipt, Rejection & Delivery Performance" in resp_stores.content.decode("utf-8")

    # 4. Auditor -> Authorized read-only (HTTP 200)
    client.force_login(batch3_setup["auditor"])
    resp_auditor = client.get(report_url)
    assert resp_auditor.status_code == 200

    # 5. Export CSV -> Returns CSV content and logs audit entry
    resp_csv = client.get(report_url + "?export=CSV")
    assert resp_csv.status_code == 200
    assert resp_csv["Content-Type"] == "text/csv"
    assert "grn_number" in resp_csv.content.decode("utf-8")
    assert AuditLog.objects.filter(target_object_id="DeliveryPerformanceReport_CSV").exists()

    # 6. Export XLSX -> Returns valid Excel workbook
    resp_xlsx = client.get(report_url + "?export=XLSX")
    assert resp_xlsx.status_code == 200
    assert "spreadsheetml.sheet" in resp_xlsx["Content-Type"]
    wb = openpyxl.load_workbook(BytesIO(resp_xlsx.content))
    assert "Delivery Performance" in wb.sheetnames
    assert AuditLog.objects.filter(target_object_id="DeliveryPerformanceReport_XLSX").exists()


# ==============================================================================
# DAY 17: VALIDATE MULTIPLE RECEIPTS AND CUMULATIVE QUANTITIES
# ==============================================================================


@pytest.mark.django_db
def test_day17_sequential_three_grns_cumulative_quantities(batch3_setup):
    """
    Day 17.1: Verify sequential receipt across 3 distinct GRNs against a single PO line.
    Confirms exact cumulative balance tracking and status transitions.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    receiver = batch3_setup["receiver"]

    # GRN 1: Receive 30 units
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("30.00")}],
        delivery_note_number="DN-SEQ-01",
    )
    line1.refresh_from_db()
    po.refresh_from_db()
    assert line1.quantity_received == Decimal("30.00")
    assert line1.remaining_quantity == Decimal("70.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # GRN 2: Receive 45 units
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("45.00")}],
        delivery_note_number="DN-SEQ-02",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("75.00")
    assert line1.remaining_quantity == Decimal("25.00")

    # GRN 3: Receive 25 units (completing line 1)
    grn3 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("25.00")}],
        delivery_note_number="DN-SEQ-03",
    )
    line1.refresh_from_db()
    po.refresh_from_db()
    assert line1.quantity_received == Decimal("100.00")
    assert line1.remaining_quantity == Decimal("0.00")
    # PO remains PARTIAL_RECEIPT because line2 has not been received
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # Verify all 3 GRNs exist independently
    assert GoodsReceipt.objects.filter(po=po).count() == 3
    assert {grn1.grn_number, grn2.grn_number, grn3.grn_number} == {
        g.grn_number for g in GoodsReceipt.objects.filter(po=po)
    }


@pytest.mark.django_db
def test_day17_asymmetric_multi_line_delivery_tracking(batch3_setup):
    """
    Day 17.2: Verify asymmetric delivery schedules where multiple PO lines
    are fulfilled independently at different rates.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]  # 100 ordered
    line2 = batch3_setup["line2"]  # 50 ordered
    receiver = batch3_setup["receiver"]

    # Delivery A: 100% of line 2, but 0% of line 1
    grn_a = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line2.id), "quantity_received": Decimal("50.00")}],
        delivery_note_number="DN-ASYM-A",
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    po.refresh_from_db()

    assert line2.quantity_received == Decimal("50.00")
    assert line2.remaining_quantity == Decimal("0.00")
    assert line1.quantity_received == Decimal("0.00")
    assert line1.remaining_quantity == Decimal("100.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # Delivery B: 40 units of line 1
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("40.00")}],
        delivery_note_number="DN-ASYM-B",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("40.00")
    assert line1.remaining_quantity == Decimal("60.00")


@pytest.mark.django_db
def test_day17_rejection_and_subsequent_replacement_receipt(batch3_setup):
    """
    Day 17.3: Verify rejection and subsequent replacement shipment.
    When units are rejected, the remaining quantity recalculates to allow replacement shipment.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]  # 100 ordered
    receiver = batch3_setup["receiver"]

    # Shipment 1: 50 units received
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")}],
        delivery_note_number="DN-REP-01",
    )
    rl1 = grn1.lines.first()

    # Inspection rejects 15 units -> net accepted is 35 units
    record_inspection_service(
        receipt=grn1,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl1.id),
                "passed": False,
                "rejected_quantity": Decimal("15.00"),
                "rejection_reason": "Damaged pins on connector",
            }
        ],
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("35.00")
    assert line1.remaining_quantity == Decimal("65.00")

    # Supplier ships replacement of 15 units + 30 units of remaining balance = 45 units
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("45.00")}],
        delivery_note_number="DN-REP-02",
    )
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("80.00")
    assert line1.remaining_quantity == Decimal("20.00")


@pytest.mark.django_db
def test_day17_over_delivery_prevention_across_multiple_receipts(batch3_setup):
    """
    Day 17.4: Verify over-delivery guard blocks intake when requested quantity
    exceeds line remaining balance, preserving database state completely.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]  # 100 ordered
    receiver = batch3_setup["receiver"]

    # Receive 80 units
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("80.00")}],
    )
    line1.refresh_from_db()
    assert line1.remaining_quantity == Decimal("20.00")

    # Attempt to receive 25 units (exceeds remaining 20 units)
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("25.00")}],
        )
    assert "cannot exceed remaining quantity (20.00)" in str(exc.value)

    # State untouched
    line1.refresh_from_db()
    assert line1.quantity_received == Decimal("80.00")
    assert GoodsReceipt.objects.filter(po=po).count() == 1


# ==============================================================================
# DAY 18: VALIDATE FINAL RECEIPT, REJECTED QUANTITY AND PO COMPLETION SCENARIOS
# ==============================================================================


@pytest.mark.django_db
def test_day18_final_receipt_transitions_po_to_completed(batch3_setup):
    """
    Day 18.1: Verify final receipt of all remaining items automatically transitions
    PO status from PARTIAL_RECEIPT to COMPLETED.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]  # 100 ordered
    line2 = batch3_setup["line2"]  # 50 ordered
    receiver = batch3_setup["receiver"]

    # Delivery 1: Partial delivery
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("60.00")},
            {"po_line_id": str(line2.id), "quantity_received": Decimal("50.00")},
        ],
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

    # Delivery 2: Final delivery for remaining 40 units of line 1
    grn_final = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {"po_line_id": str(line1.id), "quantity_received": Decimal("40.00")},
        ],
        delivery_note_number="DN-FINAL-COMPLETION",
    )
    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert line1.quantity_received == Decimal("100.00")
    assert line1.remaining_quantity == Decimal("0.00")
    assert line2.quantity_received == Decimal("50.00")
    assert line2.remaining_quantity == Decimal("0.00")
    assert po.status == PurchaseOrder.STATUS_COMPLETED


@pytest.mark.django_db
def test_day18_rejections_prevent_premature_po_completion(batch3_setup):
    """
    Day 18.2: Verify inspection rejections correctly decrement accepted balances
    and prevent premature PO completion when physical quantity was rejected.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]  # 100 ordered
    line2 = batch3_setup["line2"]  # 50 ordered
    receiver = batch3_setup["receiver"]

    # Pre-fulfill line 2
    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line2.id), "quantity_received": Decimal("50.00")}],
    )

    # Receive remaining 100 units of line 1 in a single GRN
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("100.00")}],
    )
    rl = grn.lines.first()
    po.refresh_from_db()
    # Initially accepted before inspection -> temporarily marked COMPLETED
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    # Quality Inspection rejects 20 units
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl.id),
                "passed": False,
                "rejected_quantity": Decimal("20.00"),
                "rejection_reason": "Failed high-voltage endurance test",
            }
        ],
    )
    po.refresh_from_db()
    line1.refresh_from_db()

    # Rejection moves PO back to PARTIAL_RECEIPT because line1 only has 80 accepted units
    assert line1.quantity_received == Decimal("80.00")
    assert line1.remaining_quantity == Decimal("20.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT


@pytest.mark.django_db
def test_day18_post_completion_receipt_lock(batch3_setup):
    """
    Day 18.3: Verify post-completion lock: once a PO reaches COMPLETED status,
    further goods receipt creation is blocked with a validation error.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    line2 = batch3_setup["line2"]
    receiver = batch3_setup["receiver"]

    # Fully receive both lines
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

    # Attempt to create another GRN against completed PO
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("5.00")}],
        )
    assert "Cannot record goods receipt against PO in status 'COMPLETED'" in str(exc.value)


@pytest.mark.django_db
def test_day18_atomic_transaction_and_history_preservation_on_completion(batch3_setup):
    """
    Day 18.4: Verify transaction atomicity and history preservation:
    if a final receipt fails validation midway, entire transaction rolls back cleanly
    without corrupting existing GRNs, PO lines, or PO status.
    """
    po = batch3_setup["po"]
    line1 = batch3_setup["line1"]
    line2 = batch3_setup["line2"]
    receiver = batch3_setup["receiver"]

    # Initial partial receipt of line 1 (50 units)
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[{"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")}],
    )
    po.refresh_from_db()
    line1.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert line1.quantity_received == Decimal("50.00")

    # Attempt second receipt with one valid line and one invalid line (negative quantity)
    with pytest.raises(ValidationError):
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {"po_line_id": str(line1.id), "quantity_received": Decimal("50.00")},
                {"po_line_id": str(line2.id), "quantity_received": Decimal("-10.00")},
            ],
        )

    # Verify rollback: PO line 1 still has 50 received, line 2 has 0 received, PO is still PARTIAL_RECEIPT
    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT
    assert line1.quantity_received == Decimal("50.00")
    assert line2.quantity_received == Decimal("0.00")
    assert GoodsReceipt.objects.filter(po=po).count() == 1

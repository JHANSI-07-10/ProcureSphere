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
from apps.orders.models import DeliverySchedule, POLine, PurchaseOrder
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
from apps.receipts.views import get_stores_dashboard_data
from apps.reports.services import (
    get_receipt_delivery_performance_summary,
    get_receipt_rejection_report,
)
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def stores_final_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Global Enterprise Final", code="HPE-FIN-ORG")
    dept = Department.objects.create(
        organization=org, name="Logistics & Warehousing", code="DEPT-LW-FIN"
    )
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-LW-FIN", name="Central Warehousing Hub"
    )
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q4-FIN",
        start_date=timezone.now().date() - datetime.timedelta(days=10),
        end_date=timezone.now().date() + datetime.timedelta(days=90),
    )
    budget = allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("200000.00"),
    )

    category = VendorCategory.objects.create(name="Networking Systems", code="CAT-NET-FIN")
    vendor = Vendor.objects.create(
        legal_name="Apex Telemetry & Networking Inc",
        tax_identification_number="TIN-ATN-FIN-990",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_final@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    admin_user = User.objects.create_user(
        email="admin_final@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    requester = User.objects.create_user(
        email="requester_final@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    auditor = User.objects.create_user(
        email="auditor_final@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    proc_exec = User.objects.create_user(
        email="proc_exec_final@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-FIN-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status="ISSUED",
        total_amount=Decimal("35000.00"),
    )
    line1 = POLine.objects.create(
        po=po,
        item_description="Optical Transceiver 100G QSFP28",
        quantity=Decimal("50.00"),
        unit_of_measure="EA",
        unit_price=Decimal("500.00"),
        quantity_received=Decimal("0.00"),
    )
    line2 = POLine.objects.create(
        po=po,
        item_description="Cat6A Shielded Patch Cable 10m",
        quantity=Decimal("100.00"),
        unit_of_measure="EA",
        unit_price=Decimal("100.00"),
        quantity_received=Decimal("0.00"),
    )

    expected_delivery = timezone.now().date() + datetime.timedelta(days=5)
    DeliverySchedule.objects.create(
        po=po,
        po_line=line1,
        expected_delivery_date=expected_delivery,
        quantity_expected=Decimal("50.00"),
    )
    DeliverySchedule.objects.create(
        po=po,
        po_line=line2,
        expected_delivery_date=expected_delivery,
        quantity_expected=Decimal("100.00"),
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "budget": budget,
        "vendor": vendor,
        "receiver": receiver,
        "admin_user": admin_user,
        "requester": requester,
        "auditor": auditor,
        "proc_exec": proc_exec,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


# ==============================================================================
# Full Stores Regression: PO -> Receipt -> Inspection -> Rejection/Acceptance -> Completion
# ==============================================================================


@pytest.mark.django_db
def test_full_stores_regression_lifecycle_po_to_completion(stores_final_setup):
    """
    End-to-End Stores Regression:
    1. PO issued for 2 lines: Line 1 (50 units), Line 2 (100 units).
    2. First partial receipt: Line 1 (30 units), Line 2 (50 units). PO status becomes PARTIAL_RECEIPT.
    3. Inspection of GRN 1:
       - Line 1: 25 passed, 5 rejected (with mandatory rejection reason).
       - Line 2: 50 passed, 0 rejected.
    4. Vendor Return: 5 rejected units dispatched with carrier tracking ref.
    5. Stock handoff: 25 units (L1) and 50 units (L2) transferred to inventory stock.
    6. Quantity correction: 1 unit on L1 corrected down to 24 with mandatory audit log.
    7. Second receipt (final fulfillment):
       - Line 1: 26 units received and passed.
       - Line 2: 50 units received and passed.
    8. Stock handoff of GRN 2 completed.
    9. PO transitions to COMPLETED.
    10. Post-completion lock: subsequent receipt attempt is rejected.
    """
    po = stores_final_setup["po"]
    line1 = stores_final_setup["line1"]
    line2 = stores_final_setup["line2"]
    receiver = stores_final_setup["receiver"]

    # 1. First Partial Receipt
    grn1 = create_goods_receipt_service(
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
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            },
        ],
        delivery_note_number="DN-REG-PARTIAL-01",
    )
    po.refresh_from_db()
    assert po.status == "PARTIAL_RECEIPT"
    assert grn1.overall_inspection_status == "PENDING"
    assert grn1.stock_handoff_status == "PENDING_HANDOFF"

    # 2. Quality Inspection
    rl1_grn1 = grn1.lines.get(po_line=line1)
    rl2_grn1 = grn1.lines.get(po_line=line2)

    record_inspection_service(
        receipt=grn1,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl1_grn1.id),
                "passed": False,
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "DEFECTIVE_OPTICS",
                "inspection_notes": "Laser attenuation out of spec on 5 units.",
            },
            {
                "receipt_line_id": str(rl2_grn1.id),
                "passed": True,
                "inspection_notes": "Passed continuity and impedance tests.",
            },
        ],
    )
    grn1.refresh_from_db()
    rl1_grn1.refresh_from_db()
    rl2_grn1.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()

    assert grn1.overall_inspection_status == "FAILED"  # has at least one rejection
    assert rl1_grn1.quantity_accepted == Decimal("25.00")
    assert rl1_grn1.quantity_rejected == Decimal("5.00")
    assert rl2_grn1.quantity_accepted == Decimal("50.00")
    assert rl2_grn1.quantity_rejected == Decimal("0.00")
    assert line1.quantity_received == Decimal("25.00")
    assert line2.quantity_received == Decimal("50.00")

    # 3. Vendor Return of 5 Defective Units
    rejection = rl1_grn1.rejections.first()
    assert rejection is not None
    updated_rejection = process_vendor_return_service(
        returned_by=receiver,
        rejection_record=rejection,
        return_quantity=Decimal("5.00"),
        tracking_reference="FEDEX-RMA-88490",
        return_notes="Dispatched to vendor RMA depot under warranty ticket #8849.",
    )
    assert updated_rejection.returned_to_vendor is True
    assert "FEDEX-RMA-88490" in updated_rejection.rejection_reason

    # 4. Non-Destructive Quantity Correction
    # Stores audits L1 and corrects accepted quantity from 25 to 24 before stock handoff
    corr_line = correct_receipt_line_quantity_service(
        receipt_line=rl1_grn1,
        corrected_by=receiver,
        new_quantity_received=Decimal("29.00"),
        new_quantity_accepted=Decimal("24.00"),
        reason="1 transceiver found cracked during binning; corrected pre-disbursement.",
    )
    line1.refresh_from_db()
    assert corr_line.quantity_accepted == Decimal("24.00")
    assert line1.quantity_received == Decimal("24.00")

    # Audit log entry created
    audit = AuditLog.objects.filter(
        target_model="ReceiptLine",
        target_object_id=str(rl1_grn1.id),
        action=AuditLog.ACTION_UPDATE,
    ).latest("timestamp")
    assert "cracked" in audit.new_state.get("reason", "")
    assert audit.new_state["quantity_accepted"] == "24.00"

    # 5. Stock Handoff of GRN 1 (Hand off finalized accepted items: 24 L1, 50 L2)
    grn1_handed = handoff_goods_to_stock_service(
        receipt=grn1,
        handed_off_by=receiver,
        storage_location="WAREHOUSE-BAY-A4",
        handoff_notes="Transferred to primary inventory warehouse.",
    )
    assert grn1_handed.stock_handoff_status == "HANDED_OFF"
    assert StockHandoffRecord.objects.filter(receipt_line__receipt=grn1).count() == 2
    for handoff_rec in StockHandoffRecord.objects.filter(receipt_line__receipt=grn1):
        assert handoff_rec.handed_off_by == receiver
        assert handoff_rec.storage_location == "WAREHOUSE-BAY-A4"

    # 6. Second Receipt (Final Fulfillment to complete the PO)
    # Line 1 ordered 50, currently received 24 -> remaining = 26
    # Line 2 ordered 100, currently received 50 -> remaining = 50
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("26.00"),
                "quantity_accepted": Decimal("26.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
            },
        ],
        delivery_note_number="DN-REG-FINAL-02",
    )
    rl1_grn2 = grn2.lines.get(po_line=line1)
    rl2_grn2 = grn2.lines.get(po_line=line2)

    # Inspect second receipt: all passed
    record_inspection_service(
        receipt=grn2,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl1_grn2.id),
                "passed": True,
                "inspection_notes": "Replacement batch passed 100% inspection.",
            },
            {
                "receipt_line_id": str(rl2_grn2.id),
                "passed": True,
                "inspection_notes": "All patch cables passed test.",
            },
        ],
    )
    grn2.refresh_from_db()
    assert grn2.overall_inspection_status == "PASSED"

    # Stock handoff for second receipt
    handoff_goods_to_stock_service(
        receipt=grn2,
        handed_off_by=receiver,
        storage_location="WAREHOUSE-BAY-B2",
    )
    grn2.refresh_from_db()
    assert grn2.stock_handoff_status == "HANDED_OFF"

    # 7. Verify PO Completion
    po.refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()
    assert line1.quantity_received == Decimal("50.00")
    assert line2.quantity_received == Decimal("100.00")
    assert po.status == "COMPLETED"

    # 8. Post-Completion Lock: No further receipt allowed against COMPLETED PO
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("1.00"),
                    "quantity_accepted": Decimal("1.00"),
                }
            ],
            delivery_note_number="DN-EXTRA-BLOCKED",
        )
    assert "Cannot record goods receipt against PO in status 'COMPLETED'" in str(exc.value)


# ==============================================================================
# Validate Receipt/Rejection/Delivery Performance Reports and Dashboard Data
# ==============================================================================


@pytest.mark.django_db
def test_receipt_rejection_and_delivery_performance_reporting_and_kpis(stores_final_setup):
    """
    Validate Receipt, Rejection and Delivery Performance Report:
    - Accurate calculation of rejection rates and on-time delivery rates
    - Date, vendor, PO, and status filtering
    - KPI summary dictionary correctness
    """
    po = stores_final_setup["po"]
    line1 = stores_final_setup["line1"]
    receiver = stores_final_setup["receiver"]

    # Create GRN 1: On-time delivery
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("30.00"),
                "quantity_accepted": Decimal("25.00"),
            }
        ],
        delivery_note_number="DN-KPI-01",
    )
    rl1 = grn1.lines.first()
    record_inspection_service(
        receipt=grn1,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl1.id),
                "passed": False,
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "BURNT_CHIP",
            }
        ],
    )

    # 1. Test Report Query Engine
    report_data = get_receipt_rejection_report(
        vendor="Apex Telemetry",
        po_number=po.po_number,
    )
    assert len(report_data) >= 1
    row = report_data[0]
    assert row["grn_number"] == grn1.grn_number
    assert row["vendor"] == "Apex Telemetry & Networking Inc"
    assert row["qty_received"] == 30.0
    assert row["qty_accepted"] == 25.0
    assert row["qty_rejected"] == 5.0
    assert row["rejection_rate_percent"] == 16.67
    assert row["inspection_status"] == "REJECTED"
    assert row["delivery_performance"] in ["ON_TIME", "ON_SCHEDULE"]

    # 2. Test Summary KPIs
    summary = get_receipt_delivery_performance_summary(vendor="Apex Telemetry")
    assert summary["total_grns"] >= 1
    assert summary["total_qty_received"] >= 30.0
    assert summary["total_qty_accepted"] >= 25.0
    assert summary["total_qty_rejected"] >= 5.0
    assert summary["overall_rejection_rate_percent"] > 0.0
    assert "on_time_delivery_rate_percent" in summary
    assert "returns_pending" in summary


@pytest.mark.django_db
def test_receipt_delivery_performance_report_http_and_export_views(client, stores_final_setup):
    """
    Validate Report HTTP View and CSV/XLSX Export:
    - Stores Receiver and Auditor have authorized view access.
    - Export format CSV returns text/csv with AuditLog entry.
    - Export format XLSX returns spreadsheet MIME type with AuditLog entry.
    - Unauthorized roles (Requester) are redirected with error.
    """
    po = stores_final_setup["po"]
    line1 = stores_final_setup["line1"]
    receiver = stores_final_setup["receiver"]
    auditor = stores_final_setup["auditor"]
    requester = stores_final_setup["requester"]

    create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("20.00"),
                "quantity_accepted": Decimal("20.00"),
            }
        ],
        delivery_note_number="DN-EXP-01",
    )

    # 1. Receiver accesses report page
    client.force_login(receiver)
    resp = client.get("/receipts/reports/delivery-performance/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Receipt, Rejection & Delivery Performance" in content
    assert "Total GRNs" in content
    assert "Export CSV" in content

    # 2. Auditor has read-only access
    client.force_login(auditor)
    resp_auditor = client.get("/receipts/reports/delivery-performance/")
    assert resp_auditor.status_code == 200

    # 3. CSV Export generates download and AuditLog
    client.force_login(receiver)
    resp_csv = client.get("/receipts/reports/delivery-performance/?export=CSV")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv["Content-Type"]
    assert "attachment; filename=" in resp_csv["Content-Disposition"]
    assert "DN-EXP-01" in resp_csv.content.decode()

    csv_audit = AuditLog.objects.filter(
        actor=receiver,
        action=AuditLog.ACTION_EXPORT,
        target_model="GoodsReceipt",
        target_object_id="DeliveryPerformanceReport_CSV",
    ).first()
    assert csv_audit is not None
    assert csv_audit.new_state["format"] == "CSV"

    # 4. XLSX Export generates spreadsheet and AuditLog
    resp_xlsx = client.get("/receipts/reports/delivery-performance/?export=XLSX")
    assert resp_xlsx.status_code == 200
    assert "application/vnd.openxmlformats-officedocument" in resp_xlsx["Content-Type"]

    xlsx_audit = AuditLog.objects.filter(
        actor=receiver,
        action=AuditLog.ACTION_EXPORT,
        target_model="GoodsReceipt",
        target_object_id="DeliveryPerformanceReport_XLSX",
    ).first()
    assert xlsx_audit is not None
    assert xlsx_audit.new_state["format"] == "XLSX"

    # 5. Unauthorized role blocked
    client.force_login(requester)
    resp_unauth = client.get("/receipts/reports/delivery-performance/")
    assert resp_unauth.status_code == 302
    assert "/receipts/" in resp_unauth.url


@pytest.mark.django_db
def test_stores_dashboard_data_and_view_rendering(client, stores_final_setup):
    """
    Validate Stores Dashboard:
    - get_stores_dashboard_data computes all KPIs (GRNs, pending inspections, handoffs, returns).
    - /receipts/dashboard/ renders responsive metrics, quick lifecycle hub, and recent GRN table.
    - Home view '/' dispatches Stores Receiver to stores_dashboard.html.
    """
    po = stores_final_setup["po"]
    line1 = stores_final_setup["line1"]
    receiver = stores_final_setup["receiver"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("15.00"),
                "quantity_accepted": Decimal("15.00"),
            }
        ],
        delivery_note_number="DN-DASH-CHECK",
    )

    # 1. Test Selector Data Computation
    data = get_stores_dashboard_data(receiver)
    assert "metrics" in data
    assert "recent_grns" in data
    metrics = data["metrics"]
    assert metrics["total_grns"] >= 1
    assert "pending_inspections" in metrics
    assert "pending_stock_handoff_grns" in metrics
    assert "completed_stock_handoff_grns" in metrics
    assert "returned_to_vendor_count" in metrics
    assert "quantity_corrections_count" in metrics

    # 2. Test Dashboard View
    client.force_login(receiver)
    resp = client.get("/receipts/dashboard/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Stores & Receiving" in content
    assert "Pending Inspection" in content
    assert "Pending Stock Handoff" in content
    assert grn.grn_number in content

    # 3. Test Home View dispatch for Stores Receiver
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    home_content = resp_home.content.decode()
    assert "Stores & Receiving" in home_content


# ==============================================================================
# Final Stores Module Validation, Dashboard Review, and UAT Readiness
# ==============================================================================


@pytest.mark.django_db
def test_stores_dedicated_queues_and_rbac_integrity(client, stores_final_setup, db_roles):
    """
    Validate Dedicated Queues & Strict RBAC:
    - /receipts/inspections/ (Inspection Queue)
    - /receipts/handoffs/ (Stock Handoff Queue)
    - /receipts/rejections/ (Rejections & Returns Queue)
    - Stores Receiver has full access; vendor users and unauthenticated users are blocked.
    """
    receiver = stores_final_setup["receiver"]
    vendor_user = User.objects.create_user(
        email="vendor_user_final_rb@hpe.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
    )

    client.force_login(receiver)

    # All queues accessible to Stores Receiver
    assert client.get("/receipts/inspections/").status_code == 200
    assert client.get("/receipts/handoffs/").status_code == 200
    assert client.get("/receipts/rejections/").status_code == 200

    # Vendor user is blocked from all Stores queues and redirected
    client.force_login(vendor_user)
    for q_url in ["/receipts/inspections/", "/receipts/handoffs/", "/receipts/rejections/"]:
        resp = client.get(q_url)
        assert resp.status_code == 302
        assert "/vendor" in resp.url

    # Unauthenticated user is redirected to login
    client.logout()
    for q_url in ["/receipts/inspections/", "/receipts/handoffs/", "/receipts/rejections/"]:
        resp = client.get(q_url)
        assert resp.status_code == 302
        assert "/login/" in resp.url


@pytest.mark.django_db
def test_stores_defect_closure_and_edge_case_validations(stores_final_setup):
    """
    Defect Closure & Edge Case Integrity:
    - Attempting to receive more than remaining quantity is blocked.
    - Negative receiving quantities are blocked.
    - Vendor return cannot exceed rejected quantity.
    - Quantity correction cannot reduce below handed-off stock.
    - Quantity correction requires mandatory reason.
    """
    po = stores_final_setup["po"]
    line1 = stores_final_setup["line1"]
    receiver = stores_final_setup["receiver"]

    # 1. Over-receiving blocked
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("55.00"),  # Ordered only 50
                    "quantity_accepted": Decimal("55.00"),
                }
            ],
            delivery_note_number="DN-OVER-55",
        )
    assert "cannot exceed remaining quantity" in str(exc.value)

    # 2. Negative quantity blocked
    with pytest.raises(ValidationError) as exc:
        create_goods_receipt_service(
            po=po,
            received_by=receiver,
            receipt_items=[
                {
                    "po_line_id": str(line1.id),
                    "quantity_received": Decimal("-10.00"),
                    "quantity_accepted": Decimal("-10.00"),
                }
            ],
            delivery_note_number="DN-NEG-10",
        )
    assert "must be greater than 0" in str(exc.value)

    # Create valid receipt and inspection
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
        delivery_note_number="DN-EDGE-01",
    )
    rl = grn.lines.first()
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl.id),
                "passed": False,
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "DEFECTIVE_PINS",
            }
        ],
    )
    rejection = rl.rejections.first()

    # 3. Cannot return more than rejected quantity
    with pytest.raises(ValidationError) as exc:
        process_vendor_return_service(
            returned_by=receiver,
            rejection_record=rejection,
            return_quantity=Decimal("10.00"),  # Rejected only 5
        )
    assert "exceed rejected quantity" in str(exc.value)

    # Hand off accepted stock (15 units)
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-EDGE-01",
    )

    # 4. Quantity correction cannot reduce below handed-off stock (15)
    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=rl,
            corrected_by=receiver,
            new_quantity_received=Decimal("14.00"),
            new_quantity_accepted=Decimal("10.00"),  # 10 < 15 handed off
            reason="Illegal down-correction below inventory handoff.",
        )
    assert "cannot be less than already handed-off warehouse stock" in str(exc.value)

    # 5. Quantity correction requires mandatory reason
    with pytest.raises(ValidationError) as exc:
        correct_receipt_line_quantity_service(
            receipt_line=rl,
            corrected_by=receiver,
            new_quantity_received=Decimal("18.00"),
            new_quantity_accepted=Decimal("15.00"),
            reason="   ",  # whitespace only
        )
    assert "Correction reason is mandatory to preserve audit trail" in str(exc.value)

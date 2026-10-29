import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.services import allocate_budget_service
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.services import (
    create_goods_receipt_service,
    handoff_goods_to_stock_service,
    record_inspection_service,
)
from apps.receipts.views import get_stores_dashboard_data
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def stores_dashboard_test_world(db, db_roles):
    """
    Sets up a fully configured world with:
    - Stores receiver, Super admin, Vendor, Requester users
    - Purchase orders in ISSUED and PARTIAL_RECEIPT states
    - GRN awaiting inspection
    - Inspected GRN with accepted lines awaiting stock handoff
    - Rejected lines with RejectionRecords
    """
    org = Organization.objects.create(name="HPE Global Stores Org", code="HPE-STO-ORG")
    dept = Department.objects.create(
        organization=org, name="Logistics & Warehousing", code="DEPT-LW-STO"
    )
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-LW-STO", name="Central Stores Hub"
    )
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q4-STO",
        start_date=timezone.now().date() - datetime.timedelta(days=10),
        end_date=timezone.now().date() + datetime.timedelta(days=90),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("250000.00"),
    )

    category = VendorCategory.objects.create(name="Precision Hardware", code="CAT-PHW")
    vendor = Vendor.objects.create(
        legal_name="Apex Global Hardware Ltd",
        tax_identification_number="TIN-AGH-2026-001",
        category=category,
        email="contact@apexhardware.test",
        status=Vendor.STATUS_ACTIVE,
    )

    receiver_user = User.objects.create_user(
        email="stores_receiver@test.local",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
        department=dept,
    )
    admin_user = User.objects.create_user(
        email="stores_admin@test.local",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    auditor_user = User.objects.create_user(
        email="stores_auditor@test.local",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    requester_user = User.objects.create_user(
        email="stores_requester@test.local",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
    )
    vendor_user = User.objects.create_user(
        email="stores_vendor@test.local",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor,
    )

    # POs for filtering tests
    po_draft = PurchaseOrder.objects.create(
        po_number="PO-STO-DRAFT-003",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_DRAFT,
        total_amount=Decimal("5000.00"),
    )
    po_completed = PurchaseOrder.objects.create(
        po_number="PO-STO-COMP-004",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_COMPLETED,
        total_amount=Decimal("15000.00"),
    )

    # 1. PO awaiting delivery (ISSUED)
    po_delivery = PurchaseOrder.objects.create(
        po_number="PO-STO-DELIV-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("10000.00"),
        tax_amount=Decimal("1000.00"),
        total_amount=Decimal("11000.00"),
    )
    POLine.objects.create(
        po=po_delivery,
        item_description="Enterprise SSD 3.84TB NVMe",
        quantity=Decimal("10.00"),
        unit_of_measure="EA",
        unit_price=Decimal("1000.00"),
    )

    # 2. PO for Receipts & Inspections
    po_receipt = PurchaseOrder.objects.create(
        po_number="PO-STO-REC-002",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        subtotal=Decimal("20000.00"),
        tax_amount=Decimal("2000.00"),
        total_amount=Decimal("22000.00"),
    )
    po_line1 = POLine.objects.create(
        po=po_receipt,
        item_description="Fiber Channel Switch 32Gb",
        quantity=Decimal("5.00"),
        unit_of_measure="EA",
        unit_price=Decimal("2000.00"),
        quantity_received=Decimal("0.00"),
    )
    po_line2 = POLine.objects.create(
        po=po_receipt,
        item_description="Cat6A Patch Cables 5m",
        quantity=Decimal("50.00"),
        unit_of_measure="EA",
        unit_price=Decimal("20.00"),
        quantity_received=Decimal("0.00"),
    )

    # GRN 1: Awaiting Inspection (Pending Inspection)
    grn_pending_insp = create_goods_receipt_service(
        po=po_receipt,
        received_by=receiver_user,
        receipt_items=[
            {
                "po_line_id": str(po_line1.id),
                "quantity_received": Decimal("2.00"),
                "quantity_accepted": Decimal("2.00"),
                "notes": "Dock receipt pending inspection",
            }
        ],
        delivery_note_number="DN-INSP-901",
        remarks="Shipment docked, awaiting quality gate",
    )

    # GRN 2: Inspected with passed line awaiting handoff AND rejected line
    grn_inspected = create_goods_receipt_service(
        po=po_receipt,
        received_by=receiver_user,
        receipt_items=[
            {
                "po_line_id": str(po_line2.id),
                "quantity_received": Decimal("50.00"),
                "quantity_accepted": Decimal("50.00"),
                "notes": "Delivered in full",
            }
        ],
        delivery_note_number="DN-INSP-902",
        remarks="Shipment for quality testing",
    )

    receipt_line2 = grn_inspected.lines.first()
    # Inspection: 45 passed (accepted), 5 rejected
    record_inspection_service(
        receipt=grn_inspected,
        inspected_by=receiver_user,
        inspection_items=[
            {
                "receipt_line_id": str(receipt_line2.id),
                "passed": False,
                "inspection_notes": "5 units damaged connectors",
                "rejected_quantity": Decimal("5.00"),
                "rejection_reason": "Damaged RJ45 Connector Pins",
                "returned_to_vendor": False,
            }
        ],
    )

    # GRN 3: Fully handed off to stock
    po_handed = PurchaseOrder.objects.create(
        po_number="PO-STO-HANDED-005",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ACKNOWLEDGED,
        total_amount=Decimal("3000.00"),
    )
    po_line_handed = POLine.objects.create(
        po=po_handed,
        item_description="SFP+ Transceiver 10G",
        quantity=Decimal("6.00"),
        unit_price=Decimal("500.00"),
    )
    grn_handed_off = create_goods_receipt_service(
        po=po_handed,
        received_by=receiver_user,
        receipt_items=[
            {
                "po_line_id": str(po_line_handed.id),
                "quantity_received": Decimal("6.00"),
                "quantity_accepted": Decimal("6.00"),
            }
        ],
        delivery_note_number="DN-HANDED-903",
    )
    record_inspection_service(
        receipt=grn_handed_off,
        inspected_by=receiver_user,
        inspection_items=[
            {
                "receipt_line_id": str(grn_handed_off.lines.first().id),
                "passed": True,
                "inspection_notes": "Passed QC",
            }
        ],
    )
    handoff_goods_to_stock_service(
        receipt=grn_handed_off,
        handed_off_by=receiver_user,
        storage_location="AISLE-A1-BIN4",
    )

    return {
        "receiver_user": receiver_user,
        "admin_user": admin_user,
        "auditor_user": auditor_user,
        "requester_user": requester_user,
        "vendor_user": vendor_user,
        "po_delivery": po_delivery,
        "po_receipt": po_receipt,
        "po_draft": po_draft,
        "po_completed": po_completed,
        "grn_pending_insp": grn_pending_insp,
        "grn_inspected": grn_inspected,
        "grn_handed_off": grn_handed_off,
        "receipt_line_rejected": receipt_line2,
    }


@pytest.mark.django_db
def test_data_provider_returns_all_four_operational_collections(stores_dashboard_test_world):
    """
    Verify get_stores_dashboard_data directly returns all 4 operational collections
    as lists, with preserved integer counts and the pending_stock_handoffs alias.
    """
    receiver = stores_dashboard_test_world["receiver_user"]
    data = get_stores_dashboard_data(receiver)

    assert "metrics" in data
    assert "recent_grns" in data
    assert "pending_deliveries" in data
    assert "pending_inspections" in data
    assert "pending_handoffs" in data
    assert "pending_rejections" in data

    metrics = data["metrics"]
    assert "pending_stock_handoff_grns" in metrics
    assert "pending_stock_handoffs" in metrics
    assert metrics["pending_stock_handoffs"] == metrics["pending_stock_handoff_grns"]
    assert isinstance(metrics["pending_deliveries"], int)
    assert isinstance(metrics["pending_inspections"], int)

    assert isinstance(data["pending_deliveries"], list)
    assert isinstance(data["pending_inspections"], list)
    assert isinstance(data["pending_handoffs"], list)
    assert isinstance(data["pending_rejections"], list)


@pytest.mark.django_db
def test_stores_dashboard_collections_populated(stores_dashboard_test_world):
    """
    Verify /receipts/dashboard/ supplies all 4 collections and renders table rows.
    """
    client = Client()
    client.force_login(stores_dashboard_test_world["receiver_user"])

    response = client.get(reverse("receipt_dashboard"))
    assert response.status_code == 200

    # 1. Assert context collections exist and contain the expected records
    pending_deliveries = response.context["pending_deliveries"]
    pending_inspections = response.context["pending_inspections"]
    pending_handoffs = response.context["pending_handoffs"]
    pending_rejections = response.context["pending_rejections"]

    assert len(pending_deliveries) >= 1
    assert any(po.po_number == "PO-STO-DELIV-001" for po in pending_deliveries)

    assert len(pending_inspections) >= 1
    assert any(
        g.grn_number == stores_dashboard_test_world["grn_pending_insp"].grn_number
        for g in pending_inspections
    )

    assert len(pending_handoffs) >= 1
    assert any(
        g.grn_number == stores_dashboard_test_world["grn_inspected"].grn_number
        for g in pending_handoffs
    )

    assert len(pending_rejections) >= 1
    assert any(
        line.id == stores_dashboard_test_world["receipt_line_rejected"].id
        for line in pending_rejections
    )

    # 2. Assert HTML content contains the item rows and values
    content = response.content.decode("utf-8")

    # Tab 1 rendered content
    assert "PO-STO-DELIV-001" in content
    assert "Apex Global Hardware Ltd" in content
    assert "Receive Goods" in content

    # Tab 2 rendered content
    assert stores_dashboard_test_world["grn_pending_insp"].grn_number in content
    assert "Inspect Goods" in content

    # Tab 3 rendered content
    assert stores_dashboard_test_world["grn_inspected"].grn_number in content
    assert "Hand Off to Stock" in content

    # Tab 4 rendered content
    assert "Damaged RJ45 Connector Pins" in content
    assert "Cat6A Patch Cables 5m" in content
    assert "Return Pending" in content


@pytest.mark.django_db
def test_role_dispatch_root_route_stores_dashboard_collections(stores_dashboard_test_world):
    """
    Verify root URL / (role_dispatch) supplies all 4 collections when accessed by STORES_RECEIVER.
    """
    client = Client()
    client.force_login(stores_dashboard_test_world["receiver_user"])

    response = client.get(reverse("home"))
    assert response.status_code == 200

    assert "pending_deliveries" in response.context
    assert "pending_inspections" in response.context
    assert "pending_handoffs" in response.context
    assert "pending_rejections" in response.context

    assert len(response.context["pending_deliveries"]) >= 1
    assert len(response.context["pending_inspections"]) >= 1
    assert len(response.context["pending_handoffs"]) >= 1
    assert len(response.context["pending_rejections"]) >= 1

    content = response.content.decode("utf-8")
    assert "PO-STO-DELIV-001" in content
    assert stores_dashboard_test_world["grn_pending_insp"].grn_number in content
    assert stores_dashboard_test_world["grn_inspected"].grn_number in content
    assert "Damaged RJ45 Connector Pins" in content


@pytest.mark.django_db
def test_stores_collections_eligibility_and_filtering(stores_dashboard_test_world):
    """
    Verify eligible records appear in collections and ineligible records are excluded:
    - Deliveries: ISSUED and PARTIAL_RECEIPT appear; DRAFT and COMPLETED do not.
    - Inspections: PENDING inspection appears; passed inspection does not.
    - Handoffs: PENDING_HANDOFF appears; HANDED_OFF does not.
    """
    receiver = stores_dashboard_test_world["receiver_user"]
    data = get_stores_dashboard_data(receiver)

    # 1. Deliveries: DRAFT and COMPLETED must be excluded
    delivery_ids = [po.id for po in data["pending_deliveries"]]
    assert stores_dashboard_test_world["po_delivery"].id in delivery_ids
    assert stores_dashboard_test_world["po_receipt"].id in delivery_ids
    assert stores_dashboard_test_world["po_draft"].id not in delivery_ids
    assert stores_dashboard_test_world["po_completed"].id not in delivery_ids

    # 2. Inspections: Already inspected GRNs must be excluded
    insp_ids = [g.id for g in data["pending_inspections"]]
    assert stores_dashboard_test_world["grn_pending_insp"].id in insp_ids
    assert stores_dashboard_test_world["grn_inspected"].id not in insp_ids
    assert stores_dashboard_test_world["grn_handed_off"].id not in insp_ids

    # 3. Handoffs: Already handed off GRNs must be excluded
    handoff_ids = [g.id for g in data["pending_handoffs"]]
    assert stores_dashboard_test_world["grn_inspected"].id in handoff_ids
    assert stores_dashboard_test_world["grn_handed_off"].id not in handoff_ids


@pytest.mark.django_db
def test_stock_handoff_kpi_card_and_badge_renders_correct_count(stores_dashboard_test_world):
    """
    Verify metrics.pending_stock_handoffs renders the expected count in the KPI card and badge.
    """
    client = Client()
    client.force_login(stores_dashboard_test_world["receiver_user"])

    response = client.get(reverse("receipt_dashboard"))
    assert response.status_code == 200

    data = get_stores_dashboard_data(stores_dashboard_test_world["receiver_user"])
    expected_handoffs = data["metrics"]["pending_stock_handoffs"]
    assert expected_handoffs >= 1

    content = response.content.decode("utf-8")
    assert f">{expected_handoffs}</h3>" in content or f">{expected_handoffs}</span>" in content


@pytest.mark.django_db
def test_stores_dashboard_empty_states_render(db, db_roles):
    """
    Verify that when no records exist, all 4 collections are empty and template empty-states render.
    """
    stores_user = User.objects.create_user(
        email="empty_stores@test.local",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    client = Client()
    client.force_login(stores_user)

    response = client.get(reverse("receipt_dashboard"))
    assert response.status_code == 200

    assert len(response.context["pending_deliveries"]) == 0
    assert len(response.context["pending_inspections"]) == 0
    assert len(response.context["pending_handoffs"]) == 0
    assert len(response.context["pending_rejections"]) == 0

    content = response.content.decode("utf-8")
    assert "No pending deliveries" in content
    assert "No inspections pending" in content
    assert "No stock handoffs pending" in content
    assert "No active rejections" in content


@pytest.mark.django_db
def test_stores_dashboard_authorization_and_blocking(stores_dashboard_test_world):
    """
    Verify RBAC enforcement on the Stores Dashboard:
    - Anonymous redirected to login
    - Vendor user redirected
    - Requester redirected
    - Super Admin allowed
    - Stores Receiver allowed
    """
    client = Client()

    # 1. Anonymous user
    anon_response = client.get(reverse("receipt_dashboard"))
    assert anon_response.status_code == 302
    assert "/login/" in anon_response.url

    # 2. Vendor user
    client.force_login(stores_dashboard_test_world["vendor_user"])
    vendor_response = client.get(reverse("receipt_dashboard"))
    assert vendor_response.status_code == 302
    assert reverse("vendor_dashboard") in vendor_response.url

    # 3. Requester user
    client.force_login(stores_dashboard_test_world["requester_user"])
    req_response = client.get(reverse("receipt_dashboard"))
    assert req_response.status_code == 302
    assert reverse("receipts_list") in req_response.url

    # 4. Super Admin user
    client.force_login(stores_dashboard_test_world["admin_user"])
    admin_response = client.get(reverse("receipt_dashboard"))
    assert admin_response.status_code == 200

    # 5. Auditor user
    client.force_login(stores_dashboard_test_world["auditor_user"])
    auditor_response = client.get(reverse("receipt_dashboard"))
    assert auditor_response.status_code == 200


@pytest.mark.django_db
def test_stores_dashboard_integer_metrics_preserved(stores_dashboard_test_world):
    """
    Verify that metrics card counters remain integers while collections are separate lists.
    """
    client = Client()
    client.force_login(stores_dashboard_test_world["receiver_user"])

    response = client.get(reverse("receipt_dashboard"))
    assert response.status_code == 200

    metrics = response.context["metrics"]
    assert isinstance(metrics["pending_deliveries"], int)
    assert isinstance(metrics["pending_inspections"], int)
    assert isinstance(metrics["pending_stock_handoff_grns"], int)
    assert isinstance(metrics["pending_stock_handoffs"], int)
    assert isinstance(metrics["total_rejections"], int)
    assert isinstance(metrics["total_grns"], int)


@pytest.mark.django_db
def test_inspection_dispatches_notifications_on_rejection_and_acceptance(stores_dashboard_test_world):
    """
    Verify quality inspection dispatches:
    - TYPE_EXCEPTION_RAISED to requester and PO creator when quantities are rejected.
    - TYPE_GOODS_RECEIVED to requester when all items pass inspection.
    - Prevents duplicate notification spam on re-inspection.
    """
    from apps.notifications.models import Notification
    from apps.requisitions.models import PurchaseRequisition

    receiver = stores_dashboard_test_world["receiver_user"]
    grn = stores_dashboard_test_world["grn_pending_insp"]
    requester = stores_dashboard_test_world["requester_user"]

    # Link requisition with requester to PO
    po = grn.po
    pr = PurchaseRequisition.objects.create(
        requester=requester,
        department=po.cost_center.department,
        cost_center=po.cost_center,
        title="Test Hardware Requisition",
        total_amount=po.total_amount,
        status=PurchaseRequisition.STATUS_APPROVED,
        requested_delivery_date=timezone.now().date() + datetime.timedelta(days=14),
    )
    po.requisition = pr
    po.save(update_fields=["requisition"])

    line = grn.lines.first()

    # 1. Inspection with rejection -> TYPE_EXCEPTION_RAISED
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(line.id),
                "passed": False,
                "rejected_quantity": Decimal("1.00"),
                "rejection_reason": "Defective port",
            }
        ],
    )

    notifs = Notification.objects.filter(
        recipient=requester,
        notification_type=Notification.TYPE_EXCEPTION_RAISED,
        target_url=f"/receipts/{grn.id}/",
    )
    assert notifs.count() == 1
    assert "Receipt Quality Rejection" in notifs.first().title

    # 2. Re-inspection does not duplicate the notification
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(line.id),
                "passed": False,
                "rejected_quantity": Decimal("1.00"),
                "rejection_reason": "Defective port confirmed",
            }
        ],
    )
    assert notifs.count() == 1

    # 3. Passed inspection -> TYPE_GOODS_RECEIVED
    po_line = line.po_line
    # Add ordered quantity if needed so quantity_received doesn't exceed
    po_line.quantity = Decimal("50.00")
    po_line.save(update_fields=["quantity"])

    grn_pass = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(po_line.id),
                "quantity_received": Decimal("1.00"),
                "quantity_accepted": Decimal("1.00"),
            }
        ],
        delivery_note_number="DN-PASS-TEST",
    )
    record_inspection_service(
        receipt=grn_pass,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(grn_pass.lines.first().id),
                "passed": True,
                "inspection_notes": "Passed all quality checks",
            }
        ],
    )
    pass_notifs = Notification.objects.filter(
        recipient=requester,
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__startswith="Receipt Inspection Passed",
        target_url=f"/receipts/{grn_pass.id}/",
    )
    assert pass_notifs.count() == 1

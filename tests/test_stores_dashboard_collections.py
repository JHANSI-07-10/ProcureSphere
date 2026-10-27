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
    record_inspection_service,
)
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

    return {
        "receiver_user": receiver_user,
        "admin_user": admin_user,
        "requester_user": requester_user,
        "vendor_user": vendor_user,
        "po_delivery": po_delivery,
        "po_receipt": po_receipt,
        "grn_pending_insp": grn_pending_insp,
        "grn_inspected": grn_inspected,
        "receipt_line_rejected": receipt_line2,
    }


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

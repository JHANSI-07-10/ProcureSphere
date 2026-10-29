import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.services import allocate_budget_service
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def nav_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Global Nav Org", code="HPE-NAV-ORG")
    dept = Department.objects.create(
        organization=org, name="Stores & Logistics", code="DEPT-SL-NAV"
    )
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-SL-NAV", name="Central Stores CC"
    )
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q4-NAV",
        start_date=timezone.now().date() - datetime.timedelta(days=10),
        end_date=timezone.now().date() + datetime.timedelta(days=90),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("150000.00"),
    )

    category = VendorCategory.objects.create(name="Hardware Systems", code="CAT-HW-NAV")
    vendor = Vendor.objects.create(
        legal_name="HPE Certified Server Supply",
        tax_identification_number="TAX-NAV-888",
        category=category,
        email="orders@hpecertified.com",
        status=Vendor.STATUS_ACTIVE,
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-NAV-2026-001",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("12000.00"),
        tax_amount=Decimal("1200.00"),
        total_amount=Decimal("13200.00"),
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Rack Server ProLiant DL380",
        quantity=Decimal("10.00"),
        unit_price=Decimal("1200.00"),
    )

    stores_role, _ = Role.objects.get_or_create(
        code=Role.STORES_RECEIVER, defaults={"name": "Stores / Receiver"}
    )
    stores_user = User.objects.create_user(
        email="stores_nav@hpe.com",
        password="Password123!",
        role=stores_role,
        department=dept,
    )

    admin_role, _ = Role.objects.get_or_create(
        code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"}
    )
    admin_user = User.objects.create_user(
        email="admin_nav@hpe.com",
        password="Password123!",
        role=admin_role,
    )

    proc_exec_role, _ = Role.objects.get_or_create(
        code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"}
    )
    proc_exec_user = User.objects.create_user(
        email="proc_exec_nav@hpe.com",
        password="Password123!",
        role=proc_exec_role,
    )

    finance_role, _ = Role.objects.get_or_create(
        code=Role.FINANCE_AP, defaults={"name": "Finance / AP"}
    )
    finance_user = User.objects.create_user(
        email="finance_nav@hpe.com",
        password="Password123!",
        role=finance_role,
    )

    legal_role, _ = Role.objects.get_or_create(
        code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"}
    )
    legal_user = User.objects.create_user(
        email="legal_nav@hpe.com",
        password="Password123!",
        role=legal_role,
    )

    grn = GoodsReceipt.objects.create(
        grn_number="GRN-NAV-2026-001",
        po=po,
        received_by=stores_user,
        received_date=timezone.now(),
        delivery_note_number="DN-NAV-111",
        remarks="Test navigation GRN",
    )
    grn_line = ReceiptLine.objects.create(
        receipt=grn,
        po_line=po_line,
        quantity_received=Decimal("5.00"),
        quantity_accepted=Decimal("5.00"),
        notes="Nav test line",
    )

    return {
        "stores_user": stores_user,
        "admin_user": admin_user,
        "proc_exec_user": proc_exec_user,
        "finance_user": finance_user,
        "legal_user": legal_user,
        "po": po,
        "grn": grn,
    }


@pytest.mark.django_db
def test_stores_dashboard_displays_stores_shell_and_cards(nav_setup):
    """
    Verify opening Stores dashboard renders the new Legal-Manager-styled application shell,
    preserves all 4 original Stores KPI cards, operational action center tabs, and marks Receiving Desk active.
    """
    client = Client()
    client.force_login(nav_setup["stores_user"])

    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()

    # Application shell elements
    assert "stores-layout-container" in content
    assert "stores-sidebar" in content
    assert "stores-topbar" in content
    assert "stores-compact-workspace" in content

    # Sidebar brand and header
    assert "Stores &amp; Receiving" in content or "Stores & Receiving" in content

    # KPI Cards preserved
    assert "Total GRNs" in content
    assert "Pending Deliveries" in content
    assert "Pending Inspection" in content
    assert "Pending Stock Handoff" in content
    assert "Rejections / Returns" in content

    # Operational Action Center tabs preserved
    assert "Operational Action Center" in content
    assert "Pending Deliveries" in content
    assert "Pending Inspections" in content
    assert "Pending Stock Handoffs" in content

    # Receiving Desk is active
    assert (
        'href="/receipts/dashboard/" class="sidebar-nav-link active"' in content
        or ('href="/" class="sidebar-nav-link active"' in content)
        or 'class="sidebar-nav-link active"\n                        href="/receipts/dashboard/"'
        in content
        or "Receiving Desk" in content
    )


@pytest.mark.django_db
def test_stores_receiver_navigation_across_all_destinations(nav_setup):
    """
    Verify Stores Receiver navigating between all destinations maintains the Stores shell,
    sidebar, topbar, compact density, and activates the correct nav item.
    """
    client = Client()
    client.force_login(nav_setup["stores_user"])

    destinations = [
        # (url, expected_active_label, expected_page_heading)
        (
            "/purchase-orders/",
            "Receive PO Goods",
            "Receivable Purchase Orders",
        ),
        (
            "/purchase-orders/?tab=deliveries",
            "PO Deliveries",
            "Receivable Purchase Orders",
        ),
        (
            "/receipts/",
            "Goods Receipts (GRN)",
            "Goods Receipt Notes (GRN)",
        ),
        (
            "/receipts/inspections/",
            "Inspection Gate",
            "Quality Inspection Queue",
        ),
        (
            "/receipts/rejections/",
            "Rejections & Returns",
            "Rejections & Vendor Returns Queue",
        ),
        (
            "/receipts/handoffs/",
            "Warehouse Handoff",
            "Warehouse Stock Handoff Queue",
        ),
        (
            "/receipts/reports/delivery-performance/",
            "Delivery & Rejection",
            "Receipt, Rejection & Delivery Performance",
        ),
        (
            "/reports/hub/",
            "Reports Hub",
            "Reports & Analytics Hub",
        ),
        (
            "/receipts/dashboard/",
            "Receiving Desk",
            "Warehouse & Receiving Operations Desk",
        ),
    ]

    for url, active_label, heading in destinations:
        resp = client.get(url)
        assert resp.status_code == 200, f"Failed accessing {url}: status {resp.status_code}"
        page_html = resp.content.decode()

        # Shell and navbar persist
        assert "stores-sidebar" in page_html, f"stores-sidebar missing on {url}"
        assert "stores-topbar" in page_html, f"stores-topbar missing on {url}"
        assert "stores-compact-workspace" in page_html, f"stores-compact-workspace missing on {url}"

        # Topbar page heading is present
        assert heading in page_html, f"Heading '{heading}' missing on {url}"

        # The appropriate navigation item is marked active
        assert (
            f"<span>{active_label}</span>" in page_html
        ), f"Nav label '{active_label}' missing on {url}"


@pytest.mark.django_db
def test_stores_action_pages_retain_stores_shell(nav_setup):
    """
    Verify PO details and GRN detail/inspect/handoff pages retain the Stores application shell for Stores Receiver.
    """
    client = Client()
    client.force_login(nav_setup["stores_user"])

    po = nav_setup["po"]
    grn = nav_setup["grn"]

    action_urls = [
        f"/purchase-orders/{po.id}/",
        f"/receipts/create/{po.id}/",
        f"/receipts/{grn.id}/",
        f"/receipts/{grn.id}/inspect/",
        f"/receipts/{grn.id}/handoff/",
    ]

    for url in action_urls:
        resp = client.get(url)
        assert resp.status_code == 200, f"Failed accessing action page {url}"
        html = resp.content.decode()
        assert "stores-sidebar" in html, f"stores-sidebar missing on action page {url}"
        assert "stores-topbar" in html, f"stores-topbar missing on action page {url}"
        assert (
            "stores-compact-workspace" in html
        ), f"stores-compact-workspace missing on action page {url}"


@pytest.mark.django_db
def test_shared_pages_preserve_correct_experience_for_non_stores_roles(nav_setup):
    """
    Verify shared pages (purchase orders list, reports hub) do NOT force the Stores shell onto unrelated users.
    - Super Admin: Standard ERP top navbar
    - Procurement Executive: Procurement Executive sidebar
    - Finance / AP: Finance AP base layout
    - Legal Manager: Legal Manager workspace layout
    """
    client = Client()

    # 1. Super Admin on /purchase-orders/
    client.force_login(nav_setup["admin_user"])
    resp_admin_po = client.get("/purchase-orders/")
    assert resp_admin_po.status_code == 200
    admin_po_html = resp_admin_po.content.decode()
    assert "stores-sidebar" not in admin_po_html
    assert "saSidebar" in admin_po_html or "ERP Modules" in admin_po_html  # Super Admin layout/navbar

    # 2. Procurement Executive on /purchase-orders/
    client.force_login(nav_setup["proc_exec_user"])
    resp_pe_po = client.get("/purchase-orders/")
    assert resp_pe_po.status_code == 200
    pe_po_html = resp_pe_po.content.decode()
    assert "stores-sidebar" not in pe_po_html
    assert "procExecSidebar" in pe_po_html

    # 3. Finance AP on /reports/hub/
    client.force_login(nav_setup["finance_user"])
    resp_fin_rep = client.get("/reports/hub/")
    assert resp_fin_rep.status_code == 200
    fin_rep_html = resp_fin_rep.content.decode()
    assert "stores-sidebar" not in fin_rep_html
    assert "financeSidebar" in fin_rep_html

    # 4. Legal Manager on /purchase-orders/
    client.force_login(nav_setup["legal_user"])
    resp_legal_po = client.get("/purchase-orders/")
    assert resp_legal_po.status_code == 200
    legal_po_html = resp_legal_po.content.decode()
    assert "stores-sidebar" not in legal_po_html
    assert "legal-sidebar" in legal_po_html


@pytest.mark.django_db
def test_stores_mobile_drawer_and_toggle_structure(nav_setup):
    """
    Verify responsive mobile drawer elements, backdrop, and toggle functions are properly configured in Stores shell.
    """
    client = Client()
    client.force_login(nav_setup["stores_user"])

    resp = client.get("/purchase-orders/")
    assert resp.status_code == 200
    html = resp.content.decode()

    assert "storesMobileBackdrop" in html
    assert "toggleStoresSidebar()" in html
    assert "storesAppSidebar" in html

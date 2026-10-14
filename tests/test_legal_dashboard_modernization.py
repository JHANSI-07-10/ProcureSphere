from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.contracts.models import Contract
from apps.contracts.services import (
    add_contract_obligation_service,
    create_contract_service,
    submit_for_legal_review_service,
)
from apps.vendors.models import VendorCategory
from apps.vendors.services import register_vendor_service


@pytest.fixture
def legal_modern_env(db):
    legal_role, _ = Role.objects.get_or_create(
        code=Role.LEGAL_MGR, defaults={"name": "Legal / Contract Manager"}
    )
    legal_user = User.objects.create_user(
        email="legal.designer@hpe.com", password="Password123!", role=legal_role
    )

    category = VendorCategory.objects.create(name="Legal Tech & Cloud", code="CAT-LEG-01")
    vendor = register_vendor_service(
        legal_name="Global Enterprise Data Systems LLC",
        tax_identification_number="TAX-LEG-9999",
        category=category,
        email="enterprise@globaldata.com",
        address="100 Enterprise Way, Suite 400, Austin, TX",
    )

    today = timezone.now().date()
    # Contract 1: Active
    c1 = create_contract_service(
        title="Master Cloud Governance Framework Agreement",
        vendor=vendor,
        contract_value=Decimal("750000.00"),
        start_date=today - timezone.timedelta(days=100),
        end_date=today + timezone.timedelta(days=265),
        contract_owner=legal_user,
    )
    c1.status = Contract.STATUS_ACTIVE
    c1.save()

    # Contract 2: Pending Legal Review
    c2 = create_contract_service(
        title="High-Value Network Infrastructure Lease",
        vendor=vendor,
        contract_value=Decimal("250000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=legal_user,
    )
    submit_for_legal_review_service(contract=c2, user=legal_user)

    # Contract 3: Expiring Soon
    c3 = create_contract_service(
        title="Annual Security Compliance Assessment Term",
        vendor=vendor,
        contract_value=Decimal("80000.00"),
        start_date=today - timezone.timedelta(days=340),
        end_date=today + timezone.timedelta(days=15),
        contract_owner=legal_user,
    )
    c3.status = Contract.STATUS_RENEWAL_DUE
    c3.save()

    # Obligation on c1
    add_contract_obligation_service(
        contract=c1,
        title="Quarterly SOC2 Compliance Audit Report",
        responsible_party="VENDOR",
        due_date=today + timezone.timedelta(days=20),
    )

    return {
        "legal_user": legal_user,
        "c1": c1,
        "c2": c2,
        "c3": c3,
    }


@pytest.mark.django_db
def test_legal_dashboard_clean_white_overview_banner(client, legal_modern_env):
    """
    Verify the Legal Manager dashboard features the clean white rounded overview panel
    matching the Stores Receiver design reference, with portfolio value and actions.
    """
    legal_user = legal_modern_env["legal_user"]
    client.force_login(legal_user)

    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()

    # Overview panel structure
    assert "Legal Compliance Desk" in html
    assert "Active Session" in html
    assert legal_user.email in html
    assert "Contract Lifecycle & SLA Workspace" in html

    # Portfolio value badge in clean white container
    assert "Portfolio Value" in html
    assert "$1080000.00" in html or "1,080,000" in html or "1080000" in html

    # Actions
    assert "Contract Register" in html
    assert "New Draft" in html
    assert "/contracts/" in html
    assert "/contracts/create/" in html


@pytest.mark.django_db
def test_legal_dashboard_stores_styled_kpi_cards(client, legal_modern_env):
    """
    Verify all 4 Legal KPI cards adopt the Stores visual treatment:
    - White rounded-4 cards with colored left accent borders
    - Soft colored icon backgrounds
    - Distinctive metric colors
    - Interactive links and hover elevation styling
    """
    legal_user = legal_modern_env["legal_user"]
    client.force_login(legal_user)

    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()

    # Check KPI Card Link classes and hover styling
    assert "kpi-card-link" in html
    assert "kpi-card" in html

    # 1. Pending Legal Review
    assert "Pending Legal Review" in html
    assert "border-warning" in html
    assert "fa-gavel" in html
    assert "Review Queue" in html
    assert "/contracts/?status=LEGAL_REVIEW" in html or "status=LEGAL_REVIEW" in html

    # 2. Active Contracts
    assert "Active Contracts" in html
    assert "border-primary" in html
    assert "fa-file-contract" in html
    assert "Active Agreements" in html
    assert "/contracts/?status=ACTIVE" in html or "status=ACTIVE" in html

    # 3. Expiring Contracts (<30 Days)
    assert "Expiring (&lt;30 Days)" in html or "Expiring (<30 Days)" in html
    assert "border-danger" in html
    assert "fa-clock-rotate-left" in html
    assert "Renewal Schedule" in html
    assert "/contracts/?status=RENEWAL_DUE" in html or "status=RENEWAL_DUE" in html

    # 4. Total Contract Value
    assert "Total Contract Value" in html
    assert "border-success" in html
    assert "fa-vault" in html
    assert "Committed Portfolio" in html


@pytest.mark.django_db
def test_legal_dashboard_compact_operational_panels_and_queues(client, legal_modern_env):
    """
    Verify operational panels:
    - Pending Legal Review Queue table
    - Status Distribution chart & SLA health breakdown
    - Renewal Notice Schedule table
    - Pending Obligations list
    """
    legal_user = legal_modern_env["legal_user"]
    client.force_login(legal_user)

    from django.urls import reverse

    response = client.get(reverse("contracts_dashboard"))
    assert response.status_code == 200
    html = response.content.decode()

    # 1. Pending Legal Review Queue
    assert "Pending Legal Review Queue" in html
    assert "High-Value Network Infrastructure Lease" in html
    assert "Review" in html

    # 2. Status Distribution & Chart
    assert "Status Distribution" in html
    assert "SLA Health" in html
    assert "contractStatusChart" in html

    # 3. Renewal Notice Schedule
    assert "Renewal Notice Schedule" in html
    assert "Annual Security Compliance Assessment Term" in html
    assert "RENEWAL DUE" in html
    assert "Renew" in html

    # 4. Pending Obligations
    assert "Pending Obligations" in html
    assert "Quarterly SOC2 Compliance Audit Report" in html


@pytest.mark.django_db
def test_legal_dashboard_preserves_legal_shell_and_navigation(client, legal_modern_env):
    """
    Verify the Legal Manager layout container, sidebar, and navbar remain preserved.
    """
    legal_user = legal_modern_env["legal_user"]
    client.force_login(legal_user)

    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()

    assert "legal-layout-container" in html
    assert "legal-sidebar" in html
    assert "LEGAL &amp; CONTRACTS" in html or "LEGAL & CONTRACTS" in html

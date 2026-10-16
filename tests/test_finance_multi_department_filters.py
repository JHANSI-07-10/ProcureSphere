from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.budgets.models import Budget, SpendLedger
from apps.budgets.services import allocate_budget_service
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PurchaseRequisition

User = get_user_model()


@pytest.fixture
def finance_env(db):
    org, _ = Organization.objects.get_or_create(
        code="HPE-GLOBAL", defaults={"name": "Hewlett Packard Enterprise"}
    )
    dept_it, _ = Department.objects.get_or_create(
        organization=org, code="IT-OPS", defaults={"name": "IT Infrastructure Operations"}
    )
    dept_ops, _ = Department.objects.get_or_create(
        organization=org, code="OPS", defaults={"name": "Global Operations"}
    )

    role_fin, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance & AP"})
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_vendor, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor"})

    finance_user = User.objects.create_user(
        email="fin.filter.test@hpe.com",
        password="TestPassword123!",
        role=role_fin,
    )
    requester_user = User.objects.create_user(
        email="req.filter.test@hpe.com",
        password="TestPassword123!",
        role=role_req,
        department=dept_it,
    )
    vendor_user = User.objects.create_user(
        email="vendor.filter.test@vendor.com",
        password="TestPassword123!",
        role=role_vendor,
    )

    cc_it = CostCenter.objects.create(
        department=dept_it,
        code="CC-IT-TEST",
        name="IT Cloud Center",
    )
    cc_ops = CostCenter.objects.create(
        department=dept_ops,
        code="CC-OPS-TEST",
        name="Ops Supply Chain",
    )

    today = timezone.now().date()
    period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q1",
        start_date=today - timezone.timedelta(days=10),
        end_date=today + timezone.timedelta(days=80),
    )

    budget_it = allocate_budget_service(
        cost_center=cc_it,
        fiscal_period=period,
        allocated_amount=Decimal("100000.00"),
    )
    budget_ops = allocate_budget_service(
        cost_center=cc_ops,
        fiscal_period=period,
        allocated_amount=Decimal("50000.00"),
    )

    # SpendLedger commitments
    SpendLedger.objects.create(
        budget=budget_it,
        entry_type=SpendLedger.ENTRY_COMMITMENT,
        amount=Decimal("15000.00"),
        reference_number="PO-IT-001",
        description="IT commitment",
    )
    SpendLedger.objects.create(
        budget=budget_ops,
        entry_type=SpendLedger.ENTRY_COMMITMENT,
        amount=Decimal("5000.00"),
        reference_number="PO-OPS-001",
        description="OPS commitment",
    )

    # Budget Exception PR
    pr_it = PurchaseRequisition.objects.create(
        requester=requester_user,
        department=dept_it,
        cost_center=cc_it,
        pr_number="PR-IT-OVER-01",
        total_amount=Decimal("120000.00"),
        requested_delivery_date=today + timezone.timedelta(days=14),
        status=PurchaseRequisition.STATUS_BUDGET_REVIEW,
    )

    return {
        "finance_user": finance_user,
        "vendor_user": vendor_user,
        "dept_it": dept_it,
        "dept_ops": dept_ops,
        "cc_it": cc_it,
        "cc_ops": cc_ops,
        "period": period,
        "budget_it": budget_it,
        "budget_ops": budget_ops,
        "pr_it": pr_it,
    }


@pytest.mark.django_db
class TestFinanceMultiDepartmentFilters:
    def test_finance_dashboard_global_default_shows_all_departments(self, client, finance_env):
        client.force_login(finance_env["finance_user"])
        resp = client.get(reverse("home"))
        assert resp.status_code == 200
        content = resp.content.decode()

        # Both departments appear in dropdown
        assert finance_env["dept_it"].name in content
        assert finance_env["dept_ops"].name in content
        assert "All Departments (Enterprise)" in content

        # Aggregate committed spend should be 15,000 + 5,000 = 20,000
        assert resp.context["metrics"]["committed_spend"] == 20000.0
        assert resp.context["metrics"]["pending_budget_exceptions"] == 1

    def test_finance_dashboard_filter_by_department(self, client, finance_env):
        client.force_login(finance_env["finance_user"])

        # Filter by IT department
        resp = client.get(reverse("home"), {"dept": finance_env["dept_it"].id})
        assert resp.status_code == 200
        assert resp.context["metrics"]["committed_spend"] == 15000.0
        assert resp.context["metrics"]["pending_budget_exceptions"] == 1

        # Filter by OPS department
        resp_ops = client.get(reverse("home"), {"dept": finance_env["dept_ops"].id})
        assert resp_ops.status_code == 200
        assert resp_ops.context["metrics"]["committed_spend"] == 5000.0
        assert resp_ops.context["metrics"]["pending_budget_exceptions"] == 0

    def test_budget_list_filtering_and_totals(self, client, finance_env):
        client.force_login(finance_env["finance_user"])
        url = reverse("budgets_list")

        # Global view
        resp_all = client.get(url)
        assert resp_all.status_code == 200
        assert len(resp_all.context["budgets"]) == 2
        assert resp_all.context["totals"]["allocated"] == Decimal("150000.00")

        # Filter by IT Department
        resp_it = client.get(url, {"dept": finance_env["dept_it"].id})
        assert resp_it.status_code == 200
        assert len(resp_it.context["budgets"]) == 1
        assert resp_it.context["budgets"][0].cost_center == finance_env["cc_it"]
        assert resp_it.context["totals"]["allocated"] == Decimal("100000.00")

        # Filter by Cost Center
        resp_cc = client.get(url, {"cost_center": finance_env["cc_ops"].id})
        assert resp_cc.status_code == 200
        assert len(resp_cc.context["budgets"]) == 1
        assert resp_cc.context["budgets"][0].cost_center == finance_env["cc_ops"]
        assert resp_cc.context["totals"]["allocated"] == Decimal("50000.00")

    def test_spend_ledger_filtering(self, client, finance_env):
        client.force_login(finance_env["finance_user"])
        url = reverse("budgets_ledger")

        # Global view
        resp_all = client.get(url)
        assert resp_all.status_code == 200
        assert resp_all.context["ledger"].count() == 2

        # Filter by OPS department
        resp_ops = client.get(url, {"dept": finance_env["dept_ops"].id})
        assert resp_ops.status_code == 200
        assert resp_ops.context["ledger"].count() == 1
        assert resp_ops.context["ledger"].first().reference_number == "PO-OPS-001"

    def test_budget_exceptions_filtering(self, client, finance_env):
        client.force_login(finance_env["finance_user"])
        url = reverse("budget_exceptions")

        # Global view
        resp_all = client.get(url)
        assert resp_all.status_code == 200
        assert resp_all.context["exceptions"].count() == 1

        # Filter by OPS (which has no exceptions)
        resp_ops = client.get(url, {"dept": finance_env["dept_ops"].id})
        assert resp_ops.status_code == 200
        assert resp_ops.context["exceptions"].count() == 0

    def test_vendor_user_cannot_access_budgets(self, client, finance_env):
        client.force_login(finance_env["vendor_user"])
        resp = client.get(reverse("budgets_list"))
        assert resp.status_code == 403

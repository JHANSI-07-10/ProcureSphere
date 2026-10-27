from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import Role
from apps.invoices.models import MatchTolerancePolicy

User = get_user_model()


@pytest.fixture
def finance_user(db):
    role, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance & AP"})
    return User.objects.create_user(
        email="finance.test@hpe.com",
        password="TestPassword123!",
        role=role,
    )


@pytest.fixture
def superadmin_user(db):
    role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    return User.objects.create_user(
        email="admin.test@hpe.com",
        password="TestPassword123!",
        role=role,
        is_superuser=True,
    )


@pytest.mark.django_db
class TestFinanceToleranceDecimalValidation:
    def test_tolerance_settings_rejects_decimal_values(self, client, finance_user):
        client.force_login(finance_user)
        url = reverse("tolerance_settings")

        # Initial baseline policy
        policy = MatchTolerancePolicy.get_current()
        policy.price_tolerance_pct = Decimal("0.0500")
        policy.qty_tolerance_pct = Decimal("0.0500")
        policy.save()

        # Attempt to post decimal values like 5.03 and 2.5
        resp = client.post(
            url,
            {
                "price_tolerance_pct": "5.03",
                "qty_tolerance_pct": "2.5",
            },
        )
        assert resp.status_code == 200  # Renders form with error message
        body = resp.content.decode()
        assert "must be whole numbers without decimal values" in body

        # Verify database was NOT changed to decimals
        policy.refresh_from_db()
        assert policy.price_tolerance_pct == Decimal("0.0500")
        assert policy.qty_tolerance_pct == Decimal("0.0500")

    def test_tolerance_settings_accepts_whole_numbers(self, client, finance_user):
        client.force_login(finance_user)
        url = reverse("tolerance_settings")

        resp = client.post(
            url,
            {
                "price_tolerance_pct": "8",
                "qty_tolerance_pct": "12",
            },
            follow=True,
        )
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Tolerance settings updated successfully" in body

        policy = MatchTolerancePolicy.get_current()
        assert policy.price_tolerance_pct == Decimal("0.08")
        assert policy.qty_tolerance_pct == Decimal("0.12")
        assert policy.price_tolerance_pct_int == 8
        assert policy.qty_tolerance_pct_int == 12

    def test_tolerance_settings_template_renders_integer_steps_and_attributes(self, client, finance_user):
        client.force_login(finance_user)
        url = reverse("tolerance_settings")
        resp = client.get(url)
        assert resp.status_code == 200
        body = resp.content.decode()

        # Must have step="1", no step="0.01"
        assert 'step="1"' in body
        assert 'step="0.01"' not in body
        assert "whole numbers only" in body


@pytest.mark.django_db
class TestReportsSidebarPersistence:
    def test_finance_user_reports_page_retains_finance_sidebar(self, client, finance_user):
        client.force_login(finance_user)

        for report_url in ["/reports/", "/reports/hub/"]:
            resp = client.get(report_url)
            assert resp.status_code == 200, f"{report_url} returned {resp.status_code}"
            body = resp.content.decode()

            # Must contain Finance sidebar elements
            assert "Finance Menu" in body
            assert "Finance & AP" in body
            assert "Invoices & AP" in body
            assert "Budget Control" in body
            assert "Reports & Exports" in body

            # Must NOT contain the generic top navbar ERP modules dropdown from base.html
            assert '<i class="fa-solid fa-cubes me-2"></i> ERP Modules' not in body

    def test_superadmin_user_reports_page_retains_superadmin_layout(self, client, superadmin_user):
        client.force_login(superadmin_user)
        resp = client.get("/reports/hub/")
        assert resp.status_code == 200
        body = resp.content.decode()

        # Super admin layout must be used, not generic top navbar fallback
        assert "SUPER ADMIN" in body

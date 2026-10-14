"""Procurement Executive dedicated reports (PR aging, sourcing cycle, PO status, audit).

Positive RBAC (PROC_EXEC -> HTTP 200 + exec sidebar, no finance layout),
negative RBAC (VENDOR_USER -> HTTP 403). No mock data.
"""

import pytest

from apps.accounts.models import Role

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture(autouse=True)
def _fast_password_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


REPORT_URLS = [
    "/reports/pr-aging/",
    "/reports/sourcing-cycle/",
    "/reports/po-status/",
    "/reports/audit-trail/",
]


def _login(client, user):
    client.force_login(user)


class TestProcExecReportsAccess:
    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_proc_exec_can_access_all_four(self, client, procurement_world, url):
        _login(client, procurement_world["exec"])
        resp = client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_proc_exec_layout_has_sidebar_not_finance(self, client, procurement_world, url):
        _login(client, procurement_world["exec"])
        body = client.get(url).content.decode()
        assert "procExecSidebar" in body
        assert "Finance Menu" not in body
        assert "Finance & AP" not in body

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_vendor_user_forbidden(self, client, procurement_world, url):
        _login(client, procurement_world["user_a"])
        assert procurement_world["user_a"].role.code == Role.VENDOR_USER
        assert client.get(url).status_code == 403

    @pytest.mark.parametrize("url", REPORT_URLS)
    def test_requester_forbidden(self, client, procurement_world, url):
        _login(client, procurement_world["requester"])
        assert client.get(url).status_code == 403


class TestProcExecReportsContent:
    def test_pr_aging_renders_backend_context(self, client, procurement_world):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from apps.requisitions.models import PurchaseRequisition

        PurchaseRequisition.objects.create(
            pr_number="PR-REPORT-001",
            title="Report widgets",
            justification="QA check",
            requester=procurement_world["requester"],
            department=procurement_world["cost_center"].department,
            cost_center=procurement_world["cost_center"],
            status=PurchaseRequisition.STATUS_SUBMITTED,
            total_amount=Decimal("1234.00"),
            requested_delivery_date=timezone.now().date() + timedelta(days=10),
        )
        _login(client, procurement_world["exec"])
        body = client.get("/reports/pr-aging/").content.decode()
        assert "PR-REPORT-001" in body

    def test_csv_export(self, client, procurement_world):
        _login(client, procurement_world["exec"])
        resp = client.get("/reports/pr-aging/?export=csv")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv"

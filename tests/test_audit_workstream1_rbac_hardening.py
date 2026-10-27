from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.orders.models import PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PurchaseRequisition


@pytest.fixture
def ws1_setup(db):
    org = Organization.objects.create(name="HPE Global", code="HPE")
    dept_it = Department.objects.create(name="IT Operations", code="IT-OPS", organization=org)
    dept_hr = Department.objects.create(name="Human Resources", code="HR", organization=org)

    cc_it = CostCenter.objects.create(name="IT Infrastructure", code="CC-IT-01", department=dept_it)
    cc_hr = CostCenter.objects.create(name="HR Recruitment", code="CC-HR-01", department=dept_hr)

    # Roles
    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_requester, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_auditor, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Compliance Auditor"})
    role_finance, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance AP"})
    role_vendor, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})

    # Users
    admin_user = User.objects.create_user(
        email="admin@hpe.test",
        password="Password123!",
        role=role_admin,
        is_staff=True,
        is_superuser=True,
    )
    requester_user = User.objects.create_user(
        email="requester.it@hpe.test",
        password="Password123!",
        role=role_requester,
        department=dept_it,
        cost_center=cc_it,
    )
    auditor_user = User.objects.create_user(
        email="auditor@hpe.test",
        password="Password123!",
        role=role_auditor,
    )
    finance_user = User.objects.create_user(
        email="finance@hpe.test",
        password="Password123!",
        role=role_finance,
        department=dept_it,
    )
    vendor_user = User.objects.create_user(
        email="vendor@cisco.test",
        password="Password123!",
        role=role_vendor,
    )

    return {
        "org": org,
        "dept_it": dept_it,
        "dept_hr": dept_hr,
        "cc_it": cc_it,
        "cc_hr": cc_hr,
        "admin": admin_user,
        "requester": requester_user,
        "auditor": auditor_user,
        "finance": finance_user,
        "vendor": vendor_user,
    }


def test_a1_superadmin_views_block_unauthorized_roles(client, ws1_setup):
    """A-1: Non-superadmin roles receive 403 Forbidden on superadmin read and export views."""
    superadmin_urls = [
        "/superadmin/users/",
        "/superadmin/organizations/",
        "/superadmin/budgets/",
        "/superadmin/policies/",
        "/superadmin/audit/",
        "/superadmin/audit/export/?format=csv",
    ]

    # Test as Vendor (Unauthorized)
    client.force_login(ws1_setup["vendor"])
    for url in superadmin_urls:
        resp = client.get(url)
        assert resp.status_code == 403, f"Vendor unexpectedly accessed {url}"

    # Test as Requester (Unauthorized)
    client.force_login(ws1_setup["requester"])
    for url in superadmin_urls:
        resp = client.get(url)
        assert resp.status_code == 403, f"Requester unexpectedly accessed {url}"

    # Test as Super Admin (Authorized)
    client.force_login(ws1_setup["admin"])
    for url in superadmin_urls:
        resp = client.get(url)
        assert resp.status_code == 200, f"Superadmin failed to access {url}"


def test_a2_auditor_dashboard_blocks_non_auditors(client, ws1_setup):
    """A-2: External vendors and standard requesters cannot access /audit/dashboard/."""
    client.force_login(ws1_setup["vendor"])
    resp = client.get("/audit/dashboard/")
    assert resp.status_code == 403

    client.force_login(ws1_setup["requester"])
    resp = client.get("/audit/dashboard/")
    assert resp.status_code == 403

    # Auditor can access
    client.force_login(ws1_setup["auditor"])
    resp = client.get("/audit/dashboard/")
    assert resp.status_code == 200


def test_a3_vendor_blocked_from_internal_staff_pages(client, ws1_setup):
    """A-3: Vendor users receive 403 when accessing internal contracts, invoices, and approval inbox."""
    staff_urls = [
        "/contracts/",
        "/contracts/create/",
        "/invoices/",
        "/invoices/create/",
        "/invoices/settings/",
        "/approvals/inbox/",
    ]
    client.force_login(ws1_setup["vendor"])
    for url in staff_urls:
        resp = client.get(url)
        assert resp.status_code in [403, 302], f"Vendor improperly accessed {url} (status={resp.status_code})"


def test_smk_auth_005_requester_cannot_enumerate_user_directory(client, ws1_setup):
    """SMK-AUTH-005 / SMK-RB-026: Requesters cannot enumerate user list via API."""
    client.force_login(ws1_setup["requester"])
    resp = client.get("/api/v1/accounts/users/")
    assert resp.status_code == 403

    # Super Admin can list users
    client.force_login(ws1_setup["admin"])
    resp = client.get("/api/v1/accounts/users/")
    assert resp.status_code == 200


def test_org_cr_003_requester_only_sees_own_department_cost_centers(client, ws1_setup):
    """ORG-CR-003: Cost center API scopes to requester's own department."""
    client.force_login(ws1_setup["requester"])
    resp = client.get("/api/v1/organization/cost-centers/")
    assert resp.status_code == 200
    results = resp.json().get("results", resp.json())
    cc_ids = [str(r["id"]) for r in results]
    assert str(ws1_setup["cc_it"].id) in cc_ids
    assert str(ws1_setup["cc_hr"].id) not in cc_ids  # Other department cost center excluded!


def test_smk_pr_064_requester_cannot_create_pr_in_other_department(client, ws1_setup):
    """SMK-PR-064: Requester creating PR in another department receives 400 validation error."""
    from django.utils import timezone
    client.force_login(ws1_setup["requester"])
    payload = {
        "title": "Cross Department Attempt",
        "justification": "Test justification",
        "requested_delivery_date": str(timezone.now().date()),
        "department": str(ws1_setup["dept_hr"].id),  # Requester is in IT-OPS, trying to use HR!
        "cost_center": str(ws1_setup["cc_hr"].id),
        "lines": [
            {
                "item_description": "Laptop",
                "quantity": 1,
                "unit_of_measure": "EA",
                "estimated_unit_price": "1000.00",
            }
        ],
    }
    resp = client.post("/api/v1/requisitions/", data=payload, content_type="application/json")
    assert resp.status_code == 400
    err_details = resp.json().get("error", {}).get("details", resp.json())
    assert "department" in err_details
    assert "Requesters can only raise purchase requisitions for their assigned department" in str(err_details["department"])

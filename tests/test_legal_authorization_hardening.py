from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.contracts.models import Contract, ContractDocument
from apps.contracts.services import (
    approve_business_service,
    approve_legal_review_service,
    create_contract_service,
    create_contract_version_service,
    submit_for_legal_review_service,
)
from apps.vendors.models import Vendor, VendorCategory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_fixture(db):
    """
    Sets up users across all relevant roles, plus two vendors for object-level scoping tests.
    """
    roles = {}
    for code, name in [
        (Role.SUPER_ADMIN, "Super Admin"),
        (Role.LEGAL_MGR, "Legal / Contract Manager"),
        (Role.PROC_MGR, "Procurement Manager"),
        (Role.PROC_EXEC, "Procurement Executive"),
        (Role.FINANCE_AP, "Finance / AP"),
        (Role.AUDITOR, "Compliance / Auditor"),
        (Role.REQUESTER, "Requester"),
        (Role.STORES_RECEIVER, "Stores / Receiver"),
        (Role.DEPT_APPROVER, "Department Approver"),
        (Role.VENDOR_USER, "Vendor User"),
    ]:
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        roles[code] = role

    category, _ = VendorCategory.objects.get_or_create(
        code="CAT-LEGAL-HARDEN", defaults={"name": "Legal Hardening Category"}
    )

    from apps.vendors.services import register_vendor_service

    vendor_a = register_vendor_service(
        legal_name="Acme Legal Tech Services A",
        tax_identification_number="TAX-AUTH-A-100",
        category=category,
        email="contact@acme-a.local",
        address="100 Legal Way, Suite 10, Austin, TX",
    )
    vendor_b = register_vendor_service(
        legal_name="Apex Global Hardware B",
        tax_identification_number="TAX-AUTH-B-200",
        category=category,
        email="contact@apex-b.local",
        address="200 Hardware Blvd, Suite 20, Dallas, TX",
    )

    super_admin = User.objects.create_user(
        email="super.admin.harden@test.local",
        password="Password123!",
        role=roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    legal_mgr = User.objects.create_user(
        email="legal.mgr.harden@test.local",
        password="Password123!",
        role=roles[Role.LEGAL_MGR],
    )
    proc_mgr = User.objects.create_user(
        email="proc.mgr.harden@test.local",
        password="Password123!",
        role=roles[Role.PROC_MGR],
    )
    proc_exec = User.objects.create_user(
        email="proc.exec.harden@test.local",
        password="Password123!",
        role=roles[Role.PROC_EXEC],
    )
    finance_ap = User.objects.create_user(
        email="finance.ap.harden@test.local",
        password="Password123!",
        role=roles[Role.FINANCE_AP],
    )
    auditor = User.objects.create_user(
        email="auditor.harden@test.local",
        password="Password123!",
        role=roles[Role.AUDITOR],
    )
    requester = User.objects.create_user(
        email="requester.harden@test.local",
        password="Password123!",
        role=roles[Role.REQUESTER],
    )
    stores_receiver = User.objects.create_user(
        email="stores.receiver.harden@test.local",
        password="Password123!",
        role=roles[Role.STORES_RECEIVER],
    )
    dept_approver = User.objects.create_user(
        email="dept.approver.harden@test.local",
        password="Password123!",
        role=roles[Role.DEPT_APPROVER],
    )
    vendor_user_a = User.objects.create_user(
        email="vendor.user.a@test.local",
        password="Password123!",
        role=roles[Role.VENDOR_USER],
        vendor=vendor_a,
    )
    vendor_user_b = User.objects.create_user(
        email="vendor.user.b@test.local",
        password="Password123!",
        role=roles[Role.VENDOR_USER],
        vendor=vendor_b,
    )

    today = timezone.now().date()
    contract_a = create_contract_service(
        title="Enterprise Software Master License A",
        vendor=vendor_a,
        contract_value=Decimal("150000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=legal_mgr,
    )
    contract_b = create_contract_service(
        title="Warehouse Facilities Agreement B",
        vendor=vendor_b,
        contract_value=Decimal("80000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        contract_owner=proc_mgr,
    )

    return {
        "super_admin": super_admin,
        "legal_mgr": legal_mgr,
        "proc_mgr": proc_mgr,
        "proc_exec": proc_exec,
        "finance_ap": finance_ap,
        "auditor": auditor,
        "requester": requester,
        "stores_receiver": stores_receiver,
        "dept_approver": dept_approver,
        "vendor_user_a": vendor_user_a,
        "vendor_user_b": vendor_user_b,
        "contract_a": contract_a,
        "contract_b": contract_b,
    }


# ==============================================================================
# 1. UNAUTHENTICATED REQUESTS TEST
# ==============================================================================


def test_unauthenticated_requests_redirect_to_login(client, auth_fixture):
    """
    Unauthenticated requests to legal dashboard, contract register, and contract
    details must redirect to login with next parameter.
    """
    contract_a = auth_fixture["contract_a"]

    # Dashboard
    res_dash = client.get(reverse("contracts_dashboard"))
    assert res_dash.status_code == 302
    assert "/login/" in res_dash.url
    assert "next=" in res_dash.url

    # Register
    res_list = client.get(reverse("contracts_list"))
    assert res_list.status_code == 302
    assert "/login/" in res_list.url

    # Detail
    res_detail = client.get(reverse("contract_detail", kwargs={"contract_id": contract_a.id}))
    assert res_detail.status_code == 302
    assert "/login/" in res_detail.url


# ==============================================================================
# 2. LEGAL DASHBOARD ACCESS AUTHORIZATION
# ==============================================================================


def test_legal_dashboard_authorized_roles(client, auth_fixture):
    """
    Legal Managers and Super Admins are explicitly authorized to access the Legal Dashboard.
    """
    # Legal Manager
    client.force_login(auth_fixture["legal_mgr"])
    res_legal = client.get(reverse("contracts_dashboard"))
    assert res_legal.status_code == 200
    assert "Legal Compliance Desk" in res_legal.content.decode()

    # Super Admin
    client.force_login(auth_fixture["super_admin"])
    res_admin = client.get(reverse("contracts_dashboard"))
    assert res_admin.status_code == 200
    assert "Legal Compliance Desk" in res_admin.content.decode()


def test_legal_dashboard_blocked_for_unauthorized_roles(client, auth_fixture):
    """
    Requesters, Stores Receivers, Dept Approvers, Proc Execs, and Vendor Users
    cannot access the Legal Dashboard; they are redirected with Permission Denied.
    """
    unauthorized_users = [
        auth_fixture["requester"],
        auth_fixture["stores_receiver"],
        auth_fixture["dept_approver"],
        auth_fixture["vendor_user_a"],
        auth_fixture["proc_exec"],
    ]

    for user in unauthorized_users:
        client.force_login(user)
        response = client.get(reverse("contracts_dashboard"), follow=False)
        assert response.status_code == 302, f"User {user.email} was not redirected from dashboard"
        assert response.url == reverse("home")

        # Verify error message
        response_with_follow = client.get(reverse("contracts_dashboard"), follow=True)
        messages_list = list(response_with_follow.context["messages"])
        assert any(
            "Permission Denied" in str(m) for m in messages_list
        ), f"No Permission Denied message for {user.email}"


# ==============================================================================
# 3. CONTRACT REGISTER (LIST VIEW) AUTHORIZATION
# ==============================================================================


def test_contract_register_authorized_roles(client, auth_fixture):
    """
    All documented contract-viewing roles per RBAC matrix can view the register:
    Super Admin, Legal Mgr, Proc Mgr, Proc Exec, Finance/AP, Auditor, and Vendor User (scoped).
    """
    authorized_users = [
        auth_fixture["super_admin"],
        auth_fixture["legal_mgr"],
        auth_fixture["proc_mgr"],
        auth_fixture["proc_exec"],
        auth_fixture["finance_ap"],
        auth_fixture["auditor"],
        auth_fixture["vendor_user_a"],
    ]

    for user in authorized_users:
        client.force_login(user)
        response = client.get(reverse("contracts_list"))
        assert (
            response.status_code == 200
        ), f"Role {user.role_code} failed to access contract register"


def test_contract_register_blocked_for_unauthorized_roles(client, auth_fixture):
    """
    Roles with '-' access in RBAC matrix (Requester, Stores Receiver, Dept Approver)
    are strictly blocked from accessing the contract register.
    """
    unauthorized_users = [
        auth_fixture["requester"],
        auth_fixture["stores_receiver"],
        auth_fixture["dept_approver"],
    ]

    for user in unauthorized_users:
        client.force_login(user)
        response = client.get(reverse("contracts_list"), follow=False)
        assert (
            response.status_code == 302
        ), f"User {user.email} was not redirected from contracts list"
        assert response.url == reverse("home")

        response_follow = client.get(reverse("contracts_list"), follow=True)
        messages_list = list(response_follow.context["messages"])
        assert any("Permission Denied" in str(m) for m in messages_list)


# ==============================================================================
# 4. CONTRACT DETAIL (OBJECT-LEVEL) AUTHORIZATION
# ==============================================================================


def test_contract_detail_authorized_roles(client, auth_fixture):
    """
    Legitimate viewing roles can view contract details:
    Super Admin, Legal Mgr, Proc Mgr, Proc Exec, Finance/AP, Auditor, and the contract's own Vendor User.
    """
    contract_a = auth_fixture["contract_a"]
    authorized_users = [
        auth_fixture["super_admin"],
        auth_fixture["legal_mgr"],
        auth_fixture["proc_mgr"],
        auth_fixture["proc_exec"],
        auth_fixture["finance_ap"],
        auth_fixture["auditor"],
        auth_fixture["vendor_user_a"],
    ]

    for user in authorized_users:
        client.force_login(user)
        response = client.get(reverse("contract_detail", kwargs={"contract_id": contract_a.id}))
        assert response.status_code == 200, f"User {user.email} could not view contract detail"
        assert contract_a.contract_number in response.content.decode()


def test_contract_detail_blocked_by_guessing_id(client, auth_fixture):
    """
    Unauthorized roles (Requester, Stores Receiver) cannot view contract details
    even if they guess or obtain the contract UUID.
    """
    contract_a = auth_fixture["contract_a"]
    unauthorized_users = [
        auth_fixture["requester"],
        auth_fixture["stores_receiver"],
        auth_fixture["dept_approver"],
    ]

    for user in unauthorized_users:
        client.force_login(user)
        response = client.get(
            reverse("contract_detail", kwargs={"contract_id": contract_a.id}), follow=False
        )
        assert response.status_code == 302
        assert response.url == reverse("home")

        response_follow = client.get(
            reverse("contract_detail", kwargs={"contract_id": contract_a.id}), follow=True
        )
        messages_list = list(response_follow.context["messages"])
        assert any("Permission Denied" in str(m) for m in messages_list)


def test_contract_detail_cross_vendor_isolation(client, auth_fixture):
    """
    Vendor User B cannot view Contract A belonging to Vendor A.
    They are redirected to the contract register with a Permission Denied error.
    """
    contract_a = auth_fixture["contract_a"]
    vendor_user_b = auth_fixture["vendor_user_b"]

    client.force_login(vendor_user_b)
    response = client.get(
        reverse("contract_detail", kwargs={"contract_id": contract_a.id}), follow=False
    )
    assert response.status_code == 302
    assert response.url == reverse("contracts_list")

    response_follow = client.get(
        reverse("contract_detail", kwargs={"contract_id": contract_a.id}), follow=True
    )
    messages_list = list(response_follow.context["messages"])
    assert any("Permission Denied" in str(m) for m in messages_list)


# ==============================================================================
# 5. REST API CONTRACT AUTHORIZATION CONSISTENCY
# ==============================================================================


def test_api_contract_authorization_consistency(auth_fixture):
    """
    Verify DRF API /api/v1/contracts/ enforces consistent RBAC:
    - Finance/AP can list and retrieve contracts
    - Requester and Stores Receiver get 403 Forbidden
    - Vendor User B gets 403 Forbidden on Contract A
    """
    api = APIClient()
    contract_a = auth_fixture["contract_a"]

    # 1. Finance/AP can access API
    api.force_authenticate(user=auth_fixture["finance_ap"])
    res_list = api.get("/api/v1/contracts/")
    assert res_list.status_code == status.HTTP_200_OK

    res_retrieve = api.get(f"/api/v1/contracts/{contract_a.id}/")
    assert res_retrieve.status_code == status.HTTP_200_OK

    # 2. Requester gets 403
    api.force_authenticate(user=auth_fixture["requester"])
    res_req_list = api.get("/api/v1/contracts/")
    assert res_req_list.status_code == status.HTTP_403_FORBIDDEN

    res_req_detail = api.get(f"/api/v1/contracts/{contract_a.id}/")
    assert res_req_detail.status_code == status.HTTP_403_FORBIDDEN

    # 3. Stores Receiver gets 403
    api.force_authenticate(user=auth_fixture["stores_receiver"])
    res_stores_list = api.get("/api/v1/contracts/")
    assert res_stores_list.status_code == status.HTTP_403_FORBIDDEN

    # 4. Cross-vendor API retrieval gets 403 or 404
    api.force_authenticate(user=auth_fixture["vendor_user_b"])
    res_cross_vendor = api.get(f"/api/v1/contracts/{contract_a.id}/")
    assert res_cross_vendor.status_code in [
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ]


# ==============================================================================
# 6. WORKFLOW CONTINUITY
# ==============================================================================


def test_contract_workflow_lifecycle_continuity(auth_fixture):
    """
    Verify existing contract creation, submit for legal review, legal approval,
    and amendment services continue to operate without regression.
    """
    legal_mgr = auth_fixture["legal_mgr"]
    contract_a = auth_fixture["contract_a"]

    # 1. Submit for legal review
    c_submitted = submit_for_legal_review_service(contract=contract_a, user=legal_mgr)
    assert c_submitted.status == Contract.STATUS_LEGAL_REVIEW

    # 2. Approve legal review
    c_approved = approve_legal_review_service(
        contract=c_submitted, user=legal_mgr, notes="Approved for business sign-off"
    )
    assert c_approved.status == Contract.STATUS_BUSINESS_APPROVAL

    # 3. Business approval
    c_active = approve_business_service(
        contract=c_approved, user=auth_fixture["proc_mgr"], notes="Business confirmed"
    )
    assert c_active.status == Contract.STATUS_ACTIVE

    # 4. Amendment creates new version
    today = timezone.now().date()
    version = create_contract_version_service(
        contract=c_active,
        user=legal_mgr,
        amendment_summary="Scope expansion Q4",
        contract_value=Decimal("175000.00"),
        start_date=c_active.start_date,
        end_date=c_active.end_date + timezone.timedelta(days=90),
    )
    assert version.version_number == 2
    assert version.contract_value == Decimal("175000.00")
    c_active.refresh_from_db()
    assert c_active.contract_value == Decimal("175000.00")

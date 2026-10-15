import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.core.superadmin_forms import UserAdminForm
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import create_purchase_requisition_service


@pytest.fixture
def org(db):
    return Organization.objects.create(name="HPE Global", code="HPE-GLB")


@pytest.fixture
def dept_hr(db, org):
    return Department.objects.create(name="Human Resources", code="HR-OPS", organization=org)


@pytest.fixture
def dept_it(db, org):
    return Department.objects.create(name="Information Technology", code="IT-OPS", organization=org)


@pytest.fixture
def cc_hr_101(db, dept_hr):
    return CostCenter.objects.create(name="HR Operations", code="CC-HR-101", department=dept_hr)


@pytest.fixture
def cc_hr_102(db, dept_hr):
    return CostCenter.objects.create(name="HR Talent & Acquisition", code="CC-HR-102", department=dept_hr)


@pytest.fixture
def cc_it_101(db, dept_it):
    return CostCenter.objects.create(name="IT Infrastructure", code="CC-IT-101", department=dept_it)


@pytest.fixture
def role_requester(db):
    return Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})[0]


@pytest.fixture
def role_superadmin(db):
    return Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})[0]


@pytest.fixture
def role_approver(db):
    return Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})[0]


@pytest.fixture
def superadmin_user(db, role_superadmin):
    return User.objects.create_user(
        email="superadmin@procuresphere.com",
        password="Password123!",
        role=role_superadmin,
        is_superuser=True,
    )


@pytest.mark.django_db
class TestRequesterCostCenterAssignment:
    """
    Test suite verifying Requester-to-Cost-Center assignment, validations,
    isolation of other roles, PR creation restrictions, and audit trails.
    """

    def test_01_create_requester_assigned_to_cost_center(
        self, client, superadmin_user, role_requester, dept_hr, cc_hr_101
    ):
        """1. Create a Requester assigned to HR-OPS / CC-HR-101."""
        client.force_login(superadmin_user)
        response = client.post(
            "/superadmin/users/create/",
            {
                "email": "hr_requester@hpe.com",
                "phone_number": "+1234567890",
                "role": str(role_requester.id),
                "department": str(dept_hr.id),
                "cost_center": str(cc_hr_101.id),
                "is_active": True,
            },
        )
        assert response.status_code == 200

        user = User.objects.get(email="hr_requester@hpe.com")
        assert user.role == role_requester
        assert user.department == dept_hr
        assert user.cost_center == cc_hr_101

    def test_02_edit_requester_and_verify_saved_cost_center(
        self, client, superadmin_user, role_requester, dept_hr, cc_hr_101, cc_hr_102
    ):
        """2. Edit the Requester and verify the saved Cost Center."""
        user = User.objects.create_user(
            email="edit_requester@hpe.com",
            password="Password123!",
            role=role_requester,
            department=dept_hr,
            cost_center=cc_hr_101,
        )

        client.force_login(superadmin_user)

        # GET edit form - verify initial values
        get_res = client.get(f"/superadmin/users/{user.pk}/edit/")
        assert get_res.status_code == 200
        assert str(cc_hr_101.id) in get_res.content.decode()

        # Update to another valid cost center in the same department
        post_res = client.post(
            f"/superadmin/users/{user.pk}/edit/",
            {
                "email": user.email,
                "phone_number": user.phone_number,
                "role": str(role_requester.id),
                "department": str(dept_hr.id),
                "cost_center": str(cc_hr_102.id),
                "is_active": True,
            },
        )
        assert post_res.status_code == 200

        user.refresh_from_db()
        assert user.cost_center == cc_hr_102

    def test_03_prevent_assignment_of_it_cost_center_to_hr_requester(
        self, client, superadmin_user, role_requester, dept_hr, cc_it_101
    ):
        """3. Prevent assignment of an IT Cost Center to an HR Requester."""
        form_data = {
            "email": "mismatched_requester@hpe.com",
            "role": role_requester.id,
            "department": dept_hr.id,
            "cost_center": cc_it_101.id,  # Cross-department invalid cost center
            "is_active": True,
        }
        form = UserAdminForm(data=form_data)
        assert not form.is_valid()
        assert "cost_center" in form.errors
        assert any(
            "does not belong to the selected Department" in str(err)
            for err in form.errors["cost_center"]
        )

        # Check via client POST view as well
        client.force_login(superadmin_user)
        response = client.post(
            "/superadmin/users/create/",
            {
                "email": "mismatched_view@hpe.com",
                "role": str(role_requester.id),
                "department": str(dept_hr.id),
                "cost_center": str(cc_it_101.id),
                "is_active": True,
            },
        )
        assert not User.objects.filter(email="mismatched_view@hpe.com").exists()

    def test_04_prevent_duplicate_designated_requesters_for_same_cost_center(
        self, client, superadmin_user, role_requester, dept_hr, cc_hr_101
    ):
        """4. Prevent duplicate designated Requesters for the same Cost Center."""
        # Create first requester assigned to cc_hr_101
        User.objects.create_user(
            email="existing_requester@hpe.com",
            password="Password123!",
            role=role_requester,
            department=dept_hr,
            cost_center=cc_hr_101,
        )

        # Attempt to create a second requester for the same cost center
        form_data = {
            "email": "second_requester@hpe.com",
            "role": role_requester.id,
            "department": dept_hr.id,
            "cost_center": cc_hr_101.id,
            "is_active": True,
        }
        form = UserAdminForm(data=form_data)
        assert not form.is_valid()
        assert "cost_center" in form.errors
        assert any(
            "already has a designated Requester" in str(err)
            for err in form.errors["cost_center"]
        )

    def test_05_dynamic_cost_center_options_endpoint(
        self, client, superadmin_user, dept_hr, dept_it, cc_hr_101, cc_hr_102, cc_it_101
    ):
        """5. Verify changing Department refreshes the Cost Center dropdown options."""
        client.force_login(superadmin_user)

        # Query options for HR department
        res_hr = client.get(f"/superadmin/cost-centers/options/?department={dept_hr.id}")
        assert res_hr.status_code == 200
        content_hr = res_hr.content.decode()
        assert "CC-HR-101" in content_hr
        assert "CC-HR-102" in content_hr
        assert "CC-IT-101" not in content_hr

        # Query options for IT department
        res_it = client.get(f"/superadmin/cost-centers/options/?department={dept_it.id}")
        assert res_it.status_code == 200
        content_it = res_it.content.decode()
        assert "CC-IT-101" in content_it
        assert "CC-HR-101" not in content_it

    def test_06_non_requester_roles_unaffected(
        self, client, superadmin_user, role_approver, dept_hr, cc_hr_101
    ):
        """6. Verify non-Requester roles are unaffected (cost center is optional/cleared)."""
        form_data = {
            "email": "approver_user@hpe.com",
            "role": role_approver.id,
            "department": dept_hr.id,
            "cost_center": "",  # Blank cost center for approver
            "is_active": True,
        }
        form = UserAdminForm(data=form_data)
        assert form.is_valid()
        user = form.save()
        assert user.cost_center is None
        assert user.department == dept_hr
        assert user.role == role_approver

        # Even if a cost center was mistakenly posted for a non-requester, form cleans it to None
        form_data_with_cc = {
            "email": "approver_cleared@hpe.com",
            "role": role_approver.id,
            "department": dept_hr.id,
            "cost_center": cc_hr_101.id,
            "is_active": True,
        }
        form2 = UserAdminForm(data=form_data_with_cc)
        assert form2.is_valid()
        user2 = form2.save()
        assert user2.cost_center is None

    def test_07_requester_pr_creation_restricted_to_authorized_cost_center(
        self, client, role_requester, dept_hr, dept_it, cc_hr_101, cc_it_101
    ):
        """7. Verify Requester PR creation uses only the authorized Cost Center."""
        user = User.objects.create_user(
            email="hr_req_strict@hpe.com",
            password="Password123!",
            role=role_requester,
            department=dept_hr,
            cost_center=cc_hr_101,
        )

        # GET request scopes dropdowns to authorized department and cost center
        client.force_login(user)
        get_res = client.get("/requisitions/create/")
        assert get_res.status_code == 200
        content = get_res.content.decode()
        assert "CC-HR-101" in content
        assert "CC-IT-101" not in content

        # Service-level backend validation: attempting to create PR with unauthorized cost center raises ValidationError
        today = timezone.now().date()
        with pytest.raises(ValidationError) as exc_info:
            create_purchase_requisition_service(
                title="Unauthorized Purchase",
                justification="Testing cross-department restriction",
                requester=user,
                department=dept_it,
                cost_center=cc_it_101,
                requested_delivery_date=today + timezone.timedelta(days=10),
                line_items=[
                    {
                        "item_description": "Network Switch",
                        "quantity": 1,
                        "estimated_unit_price": Decimal("1000.00"),
                    }
                ],
            )
        assert "only authorized to create requisitions for Cost Center 'CC-HR-101'" in str(exc_info.value)

        # View-level POST rejection
        post_res = client.post(
            "/requisitions/create/",
            {
                "title": "Hacked Cross-Cost-Center PR",
                "justification": "Testing security",
                "department": str(dept_it.id),
                "cost_center": str(cc_it_101.id),
                "requested_delivery_date": str(today + timezone.timedelta(days=10)),
                "item_description[]": ["Hacked Item"],
                "quantity[]": ["1"],
                "estimated_unit_price[]": ["500.00"],
                "unit_of_measure[]": ["EA"],
            },
            follow=True,
        )
        assert not PurchaseRequisition.objects.filter(title="Hacked Cross-Cost-Center PR").exists()
        assert "Authorization Error" in post_res.content.decode()

    def test_08_existing_requesters_without_cost_center_remain_functional(
        self, client, role_requester, dept_hr, cc_hr_101
    ):
        """8. Verify existing user accounts without cost center assignments remain functional."""
        legacy_user = User.objects.create_user(
            email="legacy_requester@hpe.com",
            password="Password123!",
            role=role_requester,
            department=dept_hr,
            cost_center=None,  # Legacy requester without cost center
        )

        today = timezone.now().date()
        # Should create PR without exception
        pr = create_purchase_requisition_service(
            title="Legacy Requisition",
            justification="Preserving backward compatibility",
            requester=legacy_user,
            department=dept_hr,
            cost_center=cc_hr_101,
            requested_delivery_date=today + timezone.timedelta(days=15),
            line_items=[
                {
                    "item_description": "Printer Paper",
                    "quantity": 5,
                    "estimated_unit_price": Decimal("20.00"),
                }
            ],
        )
        assert pr.pk is not None
        assert pr.requester == legacy_user

    def test_09_backend_authorization_and_audit_logging(
        self, client, superadmin_user, role_requester, dept_hr, cc_hr_101, cc_hr_102
    ):
        """9. Verify backend authorization and audit logging for user creation and editing."""
        # Non-superadmin access to user creation is forbidden (HTTP 403)
        regular_user = User.objects.create_user(
            email="regular_requester@hpe.com",
            password="Password123!",
            role=role_requester,
            department=dept_hr,
            cost_center=cc_hr_102,
        )
        client.force_login(regular_user)
        forbidden_res = client.get("/superadmin/users/create/")
        assert forbidden_res.status_code == 403

        # Super admin action logs audit entry
        client.force_login(superadmin_user)
        client.post(
            "/superadmin/users/create/",
            {
                "email": "audit_logged_user@hpe.com",
                "phone_number": "+1999888777",
                "role": str(role_requester.id),
                "department": str(dept_hr.id),
                "cost_center": str(cc_hr_101.id),
                "is_active": True,
            },
        )
        new_user = User.objects.filter(email="audit_logged_user@hpe.com").first()
        assert new_user is not None

        audit_entry = AuditLog.objects.filter(
            target_model="User",
            target_object_id=str(new_user.id),
            action=AuditLog.ACTION_CREATE,
        ).first()
        assert audit_entry is not None
        assert audit_entry.actor == superadmin_user
        assert audit_entry.new_state["cost_center"] == "CC-HR-101"

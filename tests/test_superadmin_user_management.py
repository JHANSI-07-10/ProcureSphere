import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import Department, Organization


@pytest.fixture
def superadmin_client(db):
    role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    admin_user = User.objects.create_superuser(
        email="superadmin_test@procuresphere.local",
        password="SuperPassword123!",
        role=role,
    )
    client = Client()
    client.force_login(admin_user)
    return client, admin_user


@pytest.fixture
def requester_client(db):
    role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    req_user = User.objects.create_user(
        email="requester_test@procuresphere.local",
        password="ReqPassword123!",
        role=role,
    )
    client = Client()
    client.force_login(req_user)
    return client, req_user


@pytest.fixture
def sample_roles_and_departments(db):
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_app, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    org = Organization.objects.create(name="Hewlett Packard Enterprise", code="HPE", is_active=True)
    dept_hr = Department.objects.create(name="Human Resources", code="HR-OPS", organization=org)
    return role_req, role_app, dept_hr


@pytest.mark.django_db
def test_user_list_view_renders_successfully(superadmin_client):
    client, admin = superadmin_client
    response = client.get(reverse("superadmin:users"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "User & Role Management" in content
    assert "Add New User" in content
    assert admin.email in content


@pytest.mark.django_db
def test_user_create_modal_has_correct_create_action(superadmin_client, sample_roles_and_departments):
    client, _ = superadmin_client
    response = client.get(reverse("superadmin:user_create"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    # Crucial bugfix check: modal title must be Create User, and form action MUST be /superadmin/users/create/
    assert "Create User" in content
    assert 'hx-post="/superadmin/users/create/"' in content
    assert "/edit/" not in content
    assert "Email Address" in content
    assert "Phone Number" in content
    assert "Role" in content
    assert "Department" in content
    assert "Active User Account" in content


@pytest.mark.django_db
def test_successful_user_creation_and_audit_logging(superadmin_client, sample_roles_and_departments):
    client, admin_user = superadmin_client
    role_req, _, dept_hr = sample_roles_and_departments

    # Reproduce exact user from screenshot: hr.requester01@example.com
    response = client.post(
        reverse("superadmin:user_create"),
        {
            "email": "hr.requester01@example.com",
            "phone_number": "9876501001",
            "role": str(role_req.pk),
            "department": str(dept_hr.pk),
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    # Verify user exists in database
    new_user = User.objects.get(email="hr.requester01@example.com")
    assert new_user.username == "hr.requester01@example.com"
    assert new_user.phone_number == "9876501001"
    assert new_user.role == role_req
    assert new_user.department == dept_hr
    assert new_user.is_active is True
    assert new_user.has_usable_password()

    # Verify AuditLog creation
    audit_log = AuditLog.objects.filter(target_model="User", target_object_id=str(new_user.id)).first()
    assert audit_log is not None
    assert audit_log.actor == admin_user
    assert audit_log.action == AuditLog.ACTION_CREATE
    assert audit_log.new_state["email"] == "hr.requester01@example.com"
    assert audit_log.new_state["role"] == "REQUESTER"
    assert audit_log.new_state["department"] == "HR-OPS"


@pytest.mark.django_db
def test_duplicate_email_rejected(superadmin_client):
    client, admin_user = superadmin_client
    response = client.post(
        reverse("superadmin:user_create"),
        {
            "email": admin_user.email,
            "phone_number": "1112223333",
        },
    )
    assert response.status_code == 200
    assert "already exists" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_user_edit_modal_and_update(superadmin_client, sample_roles_and_departments):
    client, admin_user = superadmin_client
    role_req, role_app, dept_hr = sample_roles_and_departments

    target_user = User.objects.create_user(
        email="test_edit@procuresphere.local",
        password="Password123!",
        role=role_req,
        phone_number="1234567890",
    )

    # 1. GET edit modal
    response = client.get(reverse("superadmin:user_edit", kwargs={"user_id": target_user.pk}))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Edit User" in content
    assert f'hx-post="/superadmin/users/{target_user.pk}/edit/"' in content
    assert "test_edit@procuresphere.local" in content

    # 2. POST update
    response = client.post(
        reverse("superadmin:user_edit", kwargs={"user_id": target_user.pk}),
        {
            "email": "test_edit@procuresphere.local",
            "phone_number": "9998887777",
            "role": str(role_app.pk),
            "department": str(dept_hr.pk),
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    target_user.refresh_from_db()
    assert target_user.phone_number == "9998887777"
    assert target_user.role == role_app
    assert target_user.department == dept_hr

    # Verify AuditLog update
    audit_log = AuditLog.objects.filter(
        target_model="User",
        target_object_id=str(target_user.id),
        action=AuditLog.ACTION_UPDATE,
    ).first()
    assert audit_log is not None
    assert audit_log.actor == admin_user
    assert audit_log.previous_state["phone_number"] == "1234567890"
    assert audit_log.new_state["phone_number"] == "9998887777"
    assert audit_log.new_state["role"] == "DEPT_APPROVER"


@pytest.mark.django_db
def test_non_superadmin_access_denied_for_users(requester_client):
    client, _ = requester_client
    res_get = client.get(reverse("superadmin:user_create"))
    assert res_get.status_code == 403

    res_post = client.post(reverse("superadmin:user_create"), {"email": "hack@example.com"})
    assert res_post.status_code == 403

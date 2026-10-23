import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import CostCenter, Department, Organization


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
def sample_org_and_dept(db):
    org = Organization.objects.create(name="Hewlett Packard Enterprise", code="HPE", is_active=True)
    dept_it = Department.objects.create(name="IT Operations", code="IT-OPS", organization=org)
    dept_hr = Department.objects.create(name="Human Resources", code="HR", organization=org)
    return org, dept_it, dept_hr


@pytest.mark.django_db
def test_cost_center_section_and_add_button_rendered(superadmin_client, sample_org_and_dept):
    client, _ = superadmin_client
    _, dept_it, dept_hr = sample_org_and_dept

    CostCenter.objects.create(name="Cloud Infrastructure", code="CC-IT-101", department=dept_it)
    CostCenter.objects.create(name="Talent Acquisition", code="CC-HR-201", department=dept_hr)

    response = client.get(reverse("superadmin:organizations"))
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "Cost Centers" in content
    assert "Add Cost Center" in content
    assert 'hx-get="/superadmin/cost-centers/create/"' in content
    assert "CC-IT-101" in content
    assert "Cloud Infrastructure" in content
    assert "CC-HR-201" in content
    assert "Talent Acquisition" in content
    assert "All Departments" in content


@pytest.mark.django_db
def test_cost_center_department_filter(superadmin_client, sample_org_and_dept):
    client, _ = superadmin_client
    _, dept_it, dept_hr = sample_org_and_dept

    CostCenter.objects.create(name="Cloud Infrastructure", code="CC-IT-101", department=dept_it)
    CostCenter.objects.create(name="Talent Acquisition", code="CC-HR-201", department=dept_hr)

    # Filter by IT department
    response = client.get(reverse("superadmin:organizations"), {"department": str(dept_it.pk)})
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "CC-IT-101" in content
    assert "CC-HR-201" not in content

    # Filter by HR department
    response = client.get(reverse("superadmin:organizations"), {"department": str(dept_hr.pk)})
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "CC-HR-201" in content
    assert "CC-IT-101" not in content


@pytest.mark.django_db
def test_cost_center_create_modal_fields_and_dropdowns(superadmin_client, sample_org_and_dept):
    client, admin_user = superadmin_client
    _, dept_it, dept_hr = sample_org_and_dept

    manager_user = User.objects.create_user(
        email="manager@procuresphere.local",
        password="ManagerPassword123!",
        first_name="Alice",
        last_name="Manager",
    )

    response = client.get(reverse("superadmin:cost_center_create"))
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "Create Cost Center" in content
    assert "Cost Center Code" in content
    assert "Cost Center Name" in content
    assert "Department" in content
    assert "Manager" in content
    assert "IT Operations" in content
    assert "Human Resources" in content
    assert 'type="email"' in content
    assert 'placeholder="Enter manager email"' in content
    assert "-- Select Manager (Optional) --" not in content
    assert "manager@procuresphere.local" not in content


@pytest.mark.django_db
def test_cost_center_validation_and_uniqueness(superadmin_client, sample_org_and_dept):
    client, _ = superadmin_client
    _, dept_it, _ = sample_org_and_dept

    CostCenter.objects.create(name="Existing CC", code="CC-EXIST-01", department=dept_it)

    # 1. Missing required fields
    response = client.post(reverse("superadmin:cost_center_create"), {})
    assert response.status_code == 200
    assert "This field is required." in response.content.decode("utf-8") or "required" in response.content.decode("utf-8")

    # 2. Duplicate code
    response = client.post(
        reverse("superadmin:cost_center_create"),
        {
            "code": "cc-exist-01",  # Test lower-case duplicate check
            "name": "Another CC",
            "department": str(dept_it.pk),
        },
    )
    assert response.status_code == 200
    assert "already exists" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_successful_cost_center_creation_and_audit_logging(superadmin_client, sample_org_and_dept):
    client, admin_user = superadmin_client
    _, dept_it, _ = sample_org_and_dept

    manager_user = User.objects.create_user(
        email="it_manager@procuresphere.local",
        password="Password123!",
    )

    response = client.post(
        reverse("superadmin:cost_center_create"),
        {
            "code": "cc-devops-01",
            "name": "DevOps Operations",
            "department": str(dept_it.pk),
            "manager": manager_user.email,
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    # Verify database persistence
    cc = CostCenter.objects.get(code="CC-DEVOPS-01")
    assert cc.name == "DevOps Operations"
    assert cc.department == dept_it
    assert cc.manager == manager_user

    # Verify audit log
    audit_log = AuditLog.objects.filter(target_model="CostCenter", target_object_id=str(cc.id)).first()
    assert audit_log is not None
    assert audit_log.actor == admin_user
    assert audit_log.action == AuditLog.ACTION_CREATE
    assert audit_log.new_state["code"] == "CC-DEVOPS-01"
    assert audit_log.new_state["department"] == "IT-OPS"
    assert audit_log.new_state["manager"] == "it_manager@procuresphere.local"

    listing = client.get(reverse("superadmin:organizations"))
    assert listing.status_code == 200
    assert "it_manager@procuresphere.local" in listing.content.decode("utf-8")


@pytest.mark.django_db
def test_cost_center_edit_modal_and_update(superadmin_client, sample_org_and_dept):
    client, admin_user = superadmin_client
    _, dept_it, dept_hr = sample_org_and_dept

    cc = CostCenter.objects.create(name="Old Name", code="CC-EDIT-01", department=dept_it)

    # 1. GET edit modal
    response = client.get(reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc.pk}))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Edit Cost Center" in content
    assert "CC-EDIT-01" in content
    assert "Old Name" in content

    # 2. POST update
    response = client.post(
        reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc.pk}),
        {
            "code": "CC-EDIT-01",  # Same code should pass uniqueness
            "name": "Updated CC Name",
            "department": str(dept_hr.pk),
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    cc.refresh_from_db()
    assert cc.name == "Updated CC Name"
    assert cc.department == dept_hr

    # Verify audit log for UPDATE
    audit_log = AuditLog.objects.filter(
        target_model="CostCenter",
        target_object_id=str(cc.id),
        action=AuditLog.ACTION_UPDATE,
    ).first()
    assert audit_log is not None
    assert audit_log.actor == admin_user
    assert audit_log.previous_state["name"] == "Old Name"
    assert audit_log.previous_state["department"] == "IT-OPS"
    assert audit_log.new_state["name"] == "Updated CC Name"
    assert audit_log.new_state["department"] == "HR"

    manager_user = User.objects.create_user(
        email="assigned.manager@procuresphere.local",
        password="Password123!",
    )
    unassigned = CostCenter.objects.create(name="Still Open", code="CC-OPEN-01", department=dept_hr)

    response = client.post(
        reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc.pk}),
        {
            "code": "CC-EDIT-01",
            "name": "Updated CC Name",
            "department": str(dept_hr.pk),
            "manager": "assigned.manager@procuresphere.local",
        },
    )
    assert response.status_code == 200
    cc.refresh_from_db()
    assert cc.manager == manager_user

    listing = client.get(reverse("superadmin:organizations"))
    page = listing.content.decode("utf-8")
    assert "assigned.manager@procuresphere.local" in page
    assert "Unassigned" in page
    unassigned.refresh_from_db()
    assert unassigned.manager is None

    edit_form = client.get(reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc.pk}))
    assert 'value="assigned.manager@procuresphere.local"' in edit_form.content.decode("utf-8")

    rejected = client.post(
        reverse("superadmin:cost_center_edit", kwargs={"cc_id": unassigned.pk}),
        {
            "code": "CC-OPEN-01",
            "name": "Still Open",
            "department": str(dept_hr.pk),
            "manager": "nobody@procuresphere.local",
        },
    )
    assert rejected.status_code == 200
    assert "No active user exists with this email." in rejected.content.decode("utf-8")
    unassigned.refresh_from_db()
    assert unassigned.manager is None


@pytest.mark.django_db
def test_non_superadmin_access_denied_for_cost_centers(requester_client, sample_org_and_dept):
    client, _ = requester_client
    _, dept_it, _ = sample_org_and_dept
    cc = CostCenter.objects.create(name="Restricted CC", code="CC-RESTRICT-01", department=dept_it)

    # Create GET & POST
    res_create_get = client.get(reverse("superadmin:cost_center_create"))
    assert res_create_get.status_code == 403

    res_create_post = client.post(reverse("superadmin:cost_center_create"), {"code": "FAIL", "name": "Fail"})
    assert res_create_post.status_code == 403

    # Edit GET & POST
    res_edit_get = client.get(reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc.pk}))
    assert res_edit_get.status_code == 403

    res_edit_post = client.post(reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc.pk}), {"name": "Fail"})
    assert res_edit_post.status_code == 403


@pytest.mark.django_db
def test_cost_center_hr_ops_scenario_complete(superadmin_client, requester_client):
    """
    Specific PRD/Task verification:
    1. Seed existing cost center CC-IT-101 with department IT-OPS and manager approver@hpe.com.
    2. Seed department HR-OPS.
    3. Super Admin creates CC-HR-101 under HR-OPS.
    4. Verify duplicate code rejection.
    5. Verify department filter shows CC-HR-101 under HR-OPS and CC-IT-101 under IT-OPS.
    6. Verify Edit Cost Center updates CC-HR-101.
    7. Verify CC-IT-101 remains completely unchanged.
    8. Verify unauthorized users cannot create or edit cost centers.
    """
    admin_client, _ = superadmin_client
    req_client, _ = requester_client

    org = Organization.objects.create(name="Hewlett Packard Enterprise", code="HPE", is_active=True)
    dept_it = Department.objects.create(name="IT Infrastructure Operations", code="IT-OPS", organization=org)
    dept_hr = Department.objects.create(name="Human Resources Operations", code="HR-OPS", organization=org)

    manager = User.objects.create_user(
        email="approver@hpe.com",
        password="Password123!",
        first_name="IT",
        last_name="Approver",
    )

    # 1. Existing Cost Center CC-IT-101
    cc_it = CostCenter.objects.create(
        code="CC-IT-101",
        name="IT Infrastructure",
        department=dept_it,
        manager=manager,
    )

    # 2. Super Admin creates CC-HR-101 under HR-OPS
    res_create = admin_client.post(
        reverse("superadmin:cost_center_create"),
        {
            "code": "cc-hr-101",
            "name": "HR Talent Operations",
            "department": str(dept_hr.pk),
        },
    )
    assert res_create.status_code == 200
    assert "<script>window.location.reload();</script>" in res_create.content.decode("utf-8")

    cc_hr = CostCenter.objects.get(code="CC-HR-101")
    assert cc_hr.name == "HR Talent Operations"
    assert cc_hr.department == dept_hr
    assert cc_hr.manager is None

    # 3. Duplicate code is rejected
    res_dup = admin_client.post(
        reverse("superadmin:cost_center_create"),
        {
            "code": "CC-HR-101",
            "name": "Duplicate HR CC",
            "department": str(dept_hr.pk),
        },
    )
    assert res_dup.status_code == 200
    assert "already exists" in res_dup.content.decode("utf-8")

    # 4. Department filter: HR-OPS shows CC-HR-101, hides CC-IT-101
    res_filter_hr = admin_client.get(reverse("superadmin:organizations"), {"department": str(dept_hr.pk)})
    assert res_filter_hr.status_code == 200
    content_hr = res_filter_hr.content.decode("utf-8")
    assert "CC-HR-101" in content_hr
    assert "CC-IT-101" not in content_hr

    # 5. Department filter: IT-OPS shows CC-IT-101, hides CC-HR-101
    res_filter_it = admin_client.get(reverse("superadmin:organizations"), {"department": str(dept_it.pk)})
    assert res_filter_it.status_code == 200
    content_it = res_filter_it.content.decode("utf-8")
    assert "CC-IT-101" in content_it
    assert "CC-HR-101" not in content_it

    # 6. Edit Cost Center works
    res_edit = admin_client.post(
        reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc_hr.pk}),
        {
            "code": "CC-HR-101",
            "name": "People & Talent Operations",
            "department": str(dept_hr.pk),
        },
    )
    assert res_edit.status_code == 200
    cc_hr.refresh_from_db()
    assert cc_hr.name == "People & Talent Operations"

    # 7. CC-IT-101 remains completely unchanged
    cc_it.refresh_from_db()
    assert cc_it.code == "CC-IT-101"
    assert cc_it.name == "IT Infrastructure"
    assert cc_it.department == dept_it
    assert cc_it.manager == manager

    # 8. Unauthorized user cannot create or edit
    res_unauth_create = req_client.post(
        reverse("superadmin:cost_center_create"),
        {"code": "CC-HACK", "name": "Hack", "department": str(dept_it.pk)},
    )
    assert res_unauth_create.status_code == 403

    res_unauth_edit = req_client.post(
        reverse("superadmin:cost_center_edit", kwargs={"cc_id": cc_hr.pk}),
        {"name": "Hacked"},
    )
    assert res_unauth_edit.status_code == 403

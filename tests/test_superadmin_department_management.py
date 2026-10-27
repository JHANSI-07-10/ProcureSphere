import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import CostCenter, Department, Organization


@pytest.mark.django_db
def test_superadmin_organizations_page_renders_departments_section(client, super_admin_user):
    """
    1. Super Admin can open the Org & Cost Centers page.
    2. The Departments section is visible between Legal Entities and Cost Centers.
    3. The Add Department button is present with correct HTMX modal attributes.
    """
    org = Organization.objects.create(name="Hewlett Packard Enterprise", code="HPE-GLOBAL", is_active=True)
    dept = Department.objects.create(
        organization=org, name="IT Infrastructure Operations", code="IT-OPS", description="Core IT services"
    )
    cc = CostCenter.objects.create(department=dept, code="CC-IT-101", name="Compute & Cloud Center")

    client.force_login(super_admin_user)
    response = client.get(reverse("superadmin:organizations"))

    assert response.status_code == 200
    content = response.content.decode()

    # Section 1: Legal Entities
    assert "Legal Entities" in content
    assert "HPE-GLOBAL" in content
    assert "Add Organization" in content

    # Section 2: Departments
    assert "Departments" in content
    assert "Operational units and functional divisions" in content
    assert "Add Department" in content
    assert 'hx-get="/superadmin/departments/create/"' in content
    assert "IT-OPS" in content
    assert "IT Infrastructure Operations" in content
    assert f'/superadmin/departments/{dept.pk}/edit/' in content

    # Section 3: Cost Centers
    assert "Cost Centers" in content
    assert "CC-IT-101" in content
    assert "Compute &amp; Cloud Center" in content


@pytest.mark.django_db
def test_add_department_modal_renders_real_organizations(client, super_admin_user):
    """
    3. The Add Department button endpoint opens the modal form.
    4. The organization dropdown loads real database records (not hardcoded).
    """
    org1 = Organization.objects.create(name="HPE Global", code="HPE-GL", is_active=True)
    org2 = Organization.objects.create(name="HPE Americas", code="HPE-AMER", is_active=True)
    inactive_org = Organization.objects.create(name="HPE Deprecated", code="HPE-OLD", is_active=False)

    client.force_login(super_admin_user)
    response = client.get(reverse("superadmin:dept_create"))

    assert response.status_code == 200
    content = response.content.decode()

    assert "Create Department" in content
    assert 'name="name"' in content
    assert 'name="code"' in content
    assert 'name="organization"' in content
    assert 'name="description"' in content

    # Real database organizations in dropdown
    assert org1.name in content
    assert org2.name in content
    # Inactive organization should not be in the selection dropdown
    assert inactive_org.name not in content


@pytest.mark.django_db
def test_department_creation_validation_and_uniqueness(client, super_admin_user):
    """
    5. Required field validation works.
    6. Duplicate department codes are rejected.
    """
    org = Organization.objects.create(name="HPE Global", code="HPE-GL", is_active=True)
    Department.objects.create(organization=org, name="Human Resources", code="HR")

    client.force_login(super_admin_user)

    # Missing required fields
    response = client.post(reverse("superadmin:dept_create"), {})
    assert response.status_code == 200
    content = response.content.decode()
    assert "This field is required" in content

    # Duplicate code submission (case-insensitive normalization)
    duplicate_payload = {
        "organization": str(org.id),
        "name": "HR Talent Acquisition",
        "code": "hr",  # Lowercase should be normalized to uppercase and detect collision
        "description": "Talent team",
    }
    response = client.post(reverse("superadmin:dept_create"), duplicate_payload)
    assert response.status_code == 200
    content = response.content.decode()
    assert "already exists" in content


@pytest.mark.django_db
def test_successful_department_creation_and_audit_logging(client, super_admin_user):
    """
    7. A valid department can be created.
    8. The created department appears in the database and audit trail.
    """
    org = Organization.objects.create(name="HPE Global", code="HPE-GL", is_active=True)

    client.force_login(super_admin_user)
    payload = {
        "organization": str(org.id),
        "name": "Global Finance Operations",
        "code": "FIN-OPS",
        "description": "Corporate finance and accounting",
    }

    response = client.post(reverse("superadmin:dept_create"), payload)
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode()

    # Verify Department created in DB
    dept = Department.objects.filter(code="FIN-OPS").first()
    assert dept is not None
    assert dept.name == "Global Finance Operations"
    assert dept.organization == org
    assert dept.description == "Corporate finance and accounting"
    assert dept.created_by == super_admin_user

    # Verify AuditLog recorded
    audit = AuditLog.objects.filter(target_model="Department", target_object_id=str(dept.id)).first()
    assert audit is not None
    assert audit.action == AuditLog.ACTION_CREATE
    assert audit.actor == super_admin_user
    assert audit.new_state["code"] == "FIN-OPS"


@pytest.mark.django_db
def test_department_edit_modal_and_update(client, super_admin_user):
    """
    9. The Edit button opens the correct department in modal.
    10. Updating a department works and records audit history.
    """
    org = Organization.objects.create(name="HPE Global", code="HPE-GL", is_active=True)
    dept = Department.objects.create(
        organization=org, name="Procurement & Sourcing", code="PROC", description="Initial description"
    )

    client.force_login(super_admin_user)

    # GET Edit Form
    response = client.get(reverse("superadmin:dept_edit", kwargs={"dept_id": dept.pk}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Edit Department" in content
    assert 'value="Procurement &amp; Sourcing"' in content
    assert 'value="PROC"' in content

    # POST Update
    update_payload = {
        "organization": str(org.id),
        "name": "Global Sourcing & Vendor Governance",
        "code": "PROC",
        "description": "Updated functional description",
    }
    update_response = client.post(reverse("superadmin:dept_edit", kwargs={"dept_id": dept.pk}), update_payload)
    assert update_response.status_code == 200
    assert "<script>window.location.reload();</script>" in update_response.content.decode()

    # Verify changes
    dept.refresh_from_db()
    assert dept.name == "Global Sourcing & Vendor Governance"
    assert dept.description == "Updated functional description"

    # Verify AuditLog update recorded
    audit = AuditLog.objects.filter(
        target_model="Department", target_object_id=str(dept.id), action=AuditLog.ACTION_UPDATE
    ).first()
    assert audit is not None
    assert audit.previous_state["name"] == "Procurement & Sourcing"
    assert audit.new_state["name"] == "Global Sourcing & Vendor Governance"


@pytest.mark.django_db
def test_non_superadmin_access_denied(client, db_roles):
    """
    11. Non-Super Admin users cannot create or edit departments through these routes.
    """
    org = Organization.objects.create(name="HPE Global", code="HPE-GL", is_active=True)
    dept = Department.objects.create(organization=org, name="Legal", code="LEG")

    requester = User.objects.create_user(
        email="requester.user@hpe.com", password="Password123!", role=db_roles[Role.REQUESTER]
    )

    client.force_login(requester)

    # Attempt GET create
    res_get_create = client.get(reverse("superadmin:dept_create"))
    assert res_get_create.status_code == 403

    # Attempt POST create
    res_post_create = client.post(
        reverse("superadmin:dept_create"),
        {"organization": str(org.id), "name": "Hacked Dept", "code": "HACK"},
    )
    assert res_post_create.status_code == 403

    # Attempt GET edit
    res_get_edit = client.get(reverse("superadmin:dept_edit", kwargs={"dept_id": dept.pk}))
    assert res_get_edit.status_code == 403

    # Attempt POST edit
    res_post_edit = client.post(
        reverse("superadmin:dept_edit", kwargs={"dept_id": dept.pk}),
        {"organization": str(org.id), "name": "Modified Legal", "code": "LEG"},
    )
    assert res_post_edit.status_code == 403


@pytest.mark.django_db
def test_non_superadmin_user_and_org_management_denied(client, db_roles):
    """
    Non-Super Admin users cannot list, create, or edit users or organizations.
    """
    org = Organization.objects.create(name="HPE Global", code="HPE-GL", is_active=True)
    requester = User.objects.create_user(
        email="req.tester@hpe.com", password="Password123!", role=db_roles[Role.REQUESTER]
    )

    client.force_login(requester)

    assert client.get(reverse("superadmin:users")).status_code == 403
    assert client.get(reverse("superadmin:user_create")).status_code == 403
    assert client.post(reverse("superadmin:user_create"), {"email": "bad@hpe.com"}).status_code == 403
    assert client.get(reverse("superadmin:user_edit", kwargs={"user_id": requester.id})).status_code == 403
    assert client.get(reverse("superadmin:organizations")).status_code == 403
    assert client.get(reverse("superadmin:org_create")).status_code == 403
    assert client.post(reverse("superadmin:org_create"), {"name": "Bad Org", "code": "BAD"}).status_code == 403
    assert client.get(reverse("superadmin:org_edit", kwargs={"org_id": org.id})).status_code == 403

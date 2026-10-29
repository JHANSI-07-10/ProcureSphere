import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import Department, Organization
from apps.organization.services import create_organization_with_admin_service


@pytest.mark.django_db
def test_create_organization_with_admin_service():
    """
    Test service layer function creates organization, provisions admin user,
    and sets mutual foreign keys.
    """
    org, admin_user, raw_pwd = create_organization_with_admin_service(
        name="Acme Global Corporation",
        code="ACME-GL",
        tax_identifier="US-99-8877665",
        address="100 Innovation Way, Silicon Valley, CA",
        is_active=True,
        admin_email="admin@acme.com",
        admin_first_name="Alice",
        admin_last_name="Manager",
        admin_password="Password123!",
        send_welcome_email_flag=False,
    )

    assert org is not None
    assert org.code == "ACME-GL"
    assert org.name == "Acme Global Corporation"
    assert org.tax_identifier == "US-99-8877665"

    assert admin_user is not None
    assert admin_user.email == "admin@acme.com"
    assert admin_user.first_name == "Alice"
    assert admin_user.last_name == "Manager"
    assert admin_user.role.code == Role.SUPER_ADMIN
    assert admin_user.is_staff is True
    assert admin_user.is_active is True
    assert admin_user.check_password("Password123!") is True

    # Check mutual linkage
    assert org.admin_user == admin_user
    assert admin_user.organization == org
    assert admin_user.effective_organization == org

    # Verify Audit log
    audit_org = AuditLog.objects.filter(target_model="Organization", target_object_id=str(org.id)).first()
    assert audit_org is not None
    assert audit_org.action == AuditLog.ACTION_CREATE
    assert audit_org.new_state["code"] == "ACME-GL"
    assert audit_org.new_state["admin_user"] == "admin@acme.com"

    audit_user = AuditLog.objects.filter(target_model="User", target_object_id=str(admin_user.id)).first()
    assert audit_user is not None
    assert audit_user.action == AuditLog.ACTION_CREATE
    assert audit_user.new_state["email"] == "admin@acme.com"


@pytest.mark.django_db
def test_django_admin_create_organization_with_admin(client, super_admin_user):
    """
    Test creating an Organization in Django standard administration (/admin/)
    with initial Administrator fields.
    """
    client.force_login(super_admin_user)

    # 1. GET the add organization page in Django Admin
    add_url = reverse("admin:organization_organization_add")
    res_get = client.get(add_url)
    assert res_get.status_code == 200
    content = res_get.content.decode()
    assert "Initial Organization Administrator" in content
    assert "admin_email" in content

    # 2. POST to create organization and admin user
    payload = {
        "name": "Tesla Energy Operations",
        "code": "TESLA-ENG",
        "tax_identifier": "US-44-3322110",
        "address": "1 Tesla Road, Austin, TX",
        "is_active": "on",
        "admin_email": "admin@tesla.com",
        "admin_first_name": "Elon",
        "admin_last_name": "Admin",
        "admin_password": "TeslaSecure2026!",
        "_save": "Save",
    }
    res_post = client.post(add_url, payload, follow=True)
    assert res_post.status_code == 200

    # 3. Verify in database
    org = Organization.objects.filter(code="TESLA-ENG").first()
    assert org is not None
    assert org.name == "Tesla Energy Operations"

    admin_user = User.objects.filter(email="admin@tesla.com").first()
    assert admin_user is not None
    assert admin_user.first_name == "Elon"
    assert admin_user.role.code == Role.SUPER_ADMIN
    assert admin_user.check_password("TeslaSecure2026!") is True

    # Verify mutual linkage
    assert org.admin_user == admin_user
    assert admin_user.organization == org

    # 4. Verify Django Admin list display shows the Admin user email
    changelist_url = reverse("admin:organization_organization_changelist")
    res_list = client.get(changelist_url)
    assert res_list.status_code == 200
    assert "admin@tesla.com" in res_list.content.decode()


@pytest.mark.django_db
def test_admin_save_buttons_email_credentials_and_redirect(client, super_admin_user):
    """Save, Save and add another, and Save and continue editing stay on the Django admin paths and mail the typed password."""
    client.force_login(super_admin_user)
    org = Organization.objects.create(name="Button Org", code="BTN-ORG")
    change_url = reverse("admin:organization_organization_change", args=[org.pk])
    base = {
        "name": org.name,
        "code": org.code,
        "tax_identifier": "",
        "address": "",
        "is_active": "on",
        "admin_user": "",
        "admin_email": "org.admin@example.com",
        "admin_password": "TempPass#2026",
        "send_welcome_email": "on",
    }

    saved = client.post(change_url, {**base, "_save": "Save"})
    assert saved.status_code == 302
    assert saved.url == reverse("admin:organization_organization_changelist")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["org.admin@example.com"]
    assert "TempPass#2026" in mail.outbox[0].body
    assert "org.admin@example.com" in mail.outbox[0].body

    another = client.post(
        change_url,
        {**base, "admin_password": "OtherPass#2026", "_addanother": "Save and add another"},
    )
    assert another.status_code == 302
    assert another.url == reverse("admin:organization_organization_add")

    continued = client.post(
        change_url,
        {**base, "admin_password": "KeepPass#2026", "_continue": "Save and continue editing"},
    )
    assert continued.status_code == 302
    assert continued.url == change_url

    deleted = client.get(reverse("admin:organization_organization_delete", args=[org.pk]))
    assert deleted.status_code == 200
    assert "Button Org" in deleted.content.decode()


@pytest.mark.django_db
def test_superadmin_modal_create_organization_with_admin(client, super_admin_user):
    """
    Test creating an Organization in the Super Admin web dashboard modal
    (/superadmin/organizations/create/) automatically provisions the Admin user.
    """
    client.force_login(super_admin_user)

    # 1. GET the modal form
    create_url = reverse("superadmin:org_create")
    res_get = client.get(create_url)
    assert "Organization Administrator" in res_get.content.decode()
    assert "Send Welcome Email" in res_get.content.decode()
    assert "toggleAdminPasswordBtn" in res_get.content.decode()
    assert "toggleOrgAdminPassword" in res_get.content.decode()

    # 2. POST to create organization and administrator
    payload = {
        "name": "Oracle Cloud Infrastructure",
        "code": "ORCL-OCI",
        "tax_identifier": "US-11-2233445",
        "address": "500 Oracle Parkway, Redwood City, CA",
        "is_active": "on",
        "admin_email": "admin@oracle.com",
        "admin_first_name": "Larry",
        "admin_last_name": "Admin",
        "admin_password": "OraclePassword123!",
    }
    res_post = client.post(create_url, payload)
    assert res_post.status_code == 200
    assert "<script>window.location.reload();</script>" in res_post.content.decode()

    # 3. Verify in DB
    org = Organization.objects.filter(code="ORCL-OCI").first()
    assert org is not None
    admin_user = User.objects.filter(email="admin@oracle.com").first()
    assert admin_user is not None
    assert admin_user.organization == org
    assert org.admin_user == admin_user

    # 4. Verify that the newly created Admin user has correct credentials and access
    assert admin_user.check_password("OraclePassword123!") is True
    assert admin_user.must_change_password is True
    client.logout()
    client.force_login(admin_user)

    # First request prompts password change redirection
    redirect_res = client.get(reverse("superadmin:organizations"))
    assert redirect_res.status_code == 302
    assert "/accounts/change-password/" in redirect_res.url

    # Once password is confirmed/updated, administrator accesses dashboard
    admin_user.must_change_password = False
    admin_user.save(update_fields=["must_change_password"])

    dashboard_res = client.get(reverse("superadmin:organizations"))
    assert dashboard_res.status_code == 200
    assert "Oracle Cloud Infrastructure" in dashboard_res.content.decode()
    assert "admin@oracle.com" in dashboard_res.content.decode()


@pytest.mark.django_db
def test_create_organization_assigning_existing_admin_user(client, super_admin_user, db_roles):
    """
    Test creating an organization by selecting an existing administrator from
    the assigned admin user dropdown (without creating a new user).
    """
    existing_admin = User.objects.create_user(
        email="existing.admin@hpe.com",
        password="ExistingPassword#1",
        first_name="HPE",
        last_name="Lead",
        role=db_roles[Role.SUPER_ADMIN],
        is_staff=True,
    )

    client.force_login(super_admin_user)
    create_url = reverse("superadmin:org_create")

    # Select existing_admin via admin_user dropdown, leaving admin_email blank
    payload = {
        "name": "Hewlett Packard Enterprise",
        "code": "HPE-GLOBAL-2",
        "tax_identifier": "US-55-998877",
        "address": "Spring, TX",
        "is_active": "on",
        "admin_user": str(existing_admin.pk),
        "admin_email": "",
        "admin_password": "",
    }
    res = client.post(create_url, payload)
    assert res.status_code == 200

    org = Organization.objects.get(code="HPE-GLOBAL-2")
    assert org.admin_user == existing_admin
    existing_admin.refresh_from_db()
    assert existing_admin.organization == org
    # Existing password was NOT overwritten
    assert existing_admin.check_password("ExistingPassword#1") is True


@pytest.mark.django_db
def test_superadmin_dashboard_shows_admin_user_column_and_edit_action(client, super_admin_user, db_roles):
    """
    Verifies that the Super Admin Dashboard (/) renders:
    1. The 'ADMIN USER' column header.
    2. The email of the assigned Organization Administrator.
    3. The 'Edit' button linking to the modal.
    """
    org1 = Organization.objects.create(name="Hewlett Packard Enterprise", code="HPE-MAIN", is_active=True)
    admin1 = User.objects.create_user(
        email="admin@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        organization=org1,
        is_staff=True,
    )
    org1.admin_user = admin1
    org1.save(update_fields=["admin_user"])

    org2 = Organization.objects.create(name="Nova Corp", code="NOVA-MAIN", is_active=True)
    admin2 = User.objects.create_user(
        email="kadiyalaabhishek33@gmail.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        organization=org2,
        is_staff=True,
    )
    org2.admin_user = admin2
    org2.save(update_fields=["admin_user"])

    client.force_login(super_admin_user)
    res = client.get("/")
    assert res.status_code == 200
    html = res.content.decode()

    # Column header
    assert "ADMIN USER" in html
    # Both admin user emails displayed
    assert "admin@hpe.com" in html
    assert "kadiyalaabhishek33@gmail.com" in html
    # Edit buttons present for both
    assert f"/superadmin/organizations/{org1.pk}/edit/" in html
    assert f"/superadmin/organizations/{org2.pk}/edit/" in html


@pytest.mark.django_db
def test_superadmin_edit_organization_and_admin_details(client, super_admin_user, db_roles):
    """
    Verifies that editing an organization updates both organization attributes
    and the assigned administrator details.
    """
    org = Organization.objects.create(name="Alpha Corp", code="ALPHA", is_active=True)
    admin = User.objects.create_user(
        email="admin@alpha.com",
        password="Password123!",
        first_name="Alpha",
        last_name="One",
        role=db_roles[Role.SUPER_ADMIN],
        organization=org,
        is_staff=True,
    )
    org.admin_user = admin
    org.save(update_fields=["admin_user"])

    client.force_login(super_admin_user)
    edit_url = reverse("superadmin:org_edit", args=[org.pk])

    # 1. GET edit form
    res_get = client.get(edit_url)
    assert res_get.status_code == 200
    form_html = res_get.content.decode()
    assert "Edit Organization" in form_html
    assert "admin@alpha.com" in form_html

    # 2. POST update to org details and admin names
    payload = {
        "name": "Alpha Technologies Ltd",
        "code": "ALPHA",
        "tax_identifier": "TAX-ALPHA-888",
        "address": "456 Silicon Ave, San Jose, CA",
        "is_active": "on",
        "admin_email": "admin@alpha.com",
        "admin_first_name": "Alexander",
        "admin_last_name": "Chief",
        "admin_password": "NewSecretPass!99",
    }
    res_post = client.post(edit_url, payload)
    assert res_post.status_code == 200

    org.refresh_from_db()
    assert org.name == "Alpha Technologies Ltd"
    assert org.tax_identifier == "TAX-ALPHA-888"

    admin.refresh_from_db()
    assert admin.first_name == "Alexander"
    assert admin.last_name == "Chief"
    assert admin.check_password("NewSecretPass!99") is True


@pytest.mark.django_db
def test_create_organization_welcome_email_marked_sends_credentials(client, super_admin_user, db_roles, mailoutbox):
    """
    When 'Send Welcome Email' is marked (checked), credentials (email & password)
    are dispatched to the newly provisioned organization administrator.
    """
    client.force_login(super_admin_user)
    create_url = reverse("superadmin:org_create")

    payload = {
        "name": "Beta Cloud Corp",
        "code": "BETA-CLOUD",
        "tax_identifier": "US-BETA-7788",
        "address": "123 Beta Way, Seattle, WA",
        "is_active": "on",
        "admin_email": "admin@betacloud.com",
        "admin_first_name": "Beta",
        "admin_last_name": "Admin",
        "admin_password": "BetaSecretPassword!2026",
        "send_welcome_email": "on",
    }
    res = client.post(create_url, payload)
    assert res.status_code == 200

    org = Organization.objects.get(code="BETA-CLOUD")
    assert org.admin_user is not None
    assert org.admin_user.email == "admin@betacloud.com"
    assert org.admin_user.check_password("BetaSecretPassword!2026") is True

    # Email sent verification
    assert len(mailoutbox) >= 1
    sent_mail = [m for m in mailoutbox if "admin@betacloud.com" in m.to]
    assert len(sent_mail) == 1
    email = sent_mail[0]
    assert "admin@betacloud.com" in email.to
    assert "BetaSecretPassword!2026" in email.body or "BetaSecretPassword!2026" in str(email.alternatives)


@pytest.mark.django_db
def test_create_organization_welcome_email_unmarked_skips_email(client, super_admin_user, db_roles, mailoutbox):
    """
    When 'Send Welcome Email' is unmarked (unchecked), the organization and admin user
    are created, but NO welcome email is dispatched.
    """
    client.force_login(super_admin_user)
    create_url = reverse("superadmin:org_create")

    # send_welcome_email omitted / unmarked
    payload = {
        "name": "Gamma Solutions",
        "code": "GAMMA-SOL",
        "tax_identifier": "US-GAMMA-9900",
        "address": "789 Gamma Rd, Austin, TX",
        "is_active": "on",
        "admin_email": "admin@gammasol.com",
        "admin_first_name": "Gamma",
        "admin_last_name": "Admin",
        "admin_password": "GammaSecretPassword!2026",
    }
    res = client.post(create_url, payload)
    assert res.status_code == 200

    org = Organization.objects.get(code="GAMMA-SOL")
    assert org.admin_user is not None
    assert org.admin_user.email == "admin@gammasol.com"

    # No email sent
    sent_mail = [m for m in mailoutbox if "admin@gammasol.com" in m.to]
    assert len(sent_mail) == 0


@pytest.mark.django_db
def test_edit_organization_welcome_email_marked_dispatches_credentials(client, super_admin_user, db_roles, mailoutbox):
    """
    When editing an organization and marking 'Send Welcome Email', new credentials
    are updated and dispatched via email.
    """
    org = Organization.objects.create(name="Delta Networks", code="DELTA-NET", is_active=True)
    admin = User.objects.create_user(
        email="admin@deltanet.com",
        password="InitialPassword!1",
        first_name="Delta",
        last_name="Manager",
        role=db_roles[Role.SUPER_ADMIN],
        organization=org,
        is_staff=True,
    )
    org.admin_user = admin
    org.save(update_fields=["admin_user"])

    client.force_login(super_admin_user)
    edit_url = reverse("superadmin:org_edit", args=[org.pk])

    payload = {
        "name": "Delta Networks Inc",
        "code": "DELTA-NET",
        "tax_identifier": "US-DELTA-1234",
        "address": "Updated Address",
        "is_active": "on",
        "admin_email": "admin@deltanet.com",
        "admin_first_name": "Delta",
        "admin_last_name": "Manager",
        "admin_password": "UpdatedSecretPass!2026",
        "send_welcome_email": "on",
    }
    res = client.post(edit_url, payload)
    assert res.status_code == 200

    admin.refresh_from_db()
    assert admin.check_password("UpdatedSecretPass!2026") is True

    # Email sent verification
    sent_mail = [m for m in mailoutbox if "admin@deltanet.com" in m.to]
    assert len(sent_mail) >= 1
    assert "UpdatedSecretPass!2026" in sent_mail[-1].body or "UpdatedSecretPass!2026" in str(sent_mail[-1].alternatives)


@pytest.mark.django_db
def test_superadmin_view_organization_detail_modal(client, super_admin_user, db_roles):
    """
    Super Admin can view detailed organization profile in a modal.
    """
    org = Organization.objects.create(
        name="Global Cloud Services",
        code="GCS-CLOUD",
        tax_identifier="US-GCS-7766",
        address="100 Cloud Way, Seattle, WA",
        is_active=True,
    )
    admin = User.objects.create_user(
        email="lead@gcscloud.com",
        first_name="Cloud",
        last_name="Leader",
        role=db_roles[Role.SUPER_ADMIN],
        organization=org,
    )
    org.admin_user = admin
    org.save(update_fields=["admin_user"])

    Department.objects.create(organization=org, name="Infrastructure", code="INFRA-01")

    client.force_login(super_admin_user)
    view_url = reverse("superadmin:org_detail", args=[org.pk])
    res = client.get(view_url)

    assert res.status_code == 200
    content = res.content.decode()
    assert "Global Cloud Services" in content
    assert "GCS-CLOUD" in content
    assert "US-GCS-7766" in content
    assert "lead@gcscloud.com" in content
    assert "Infrastructure" in content


@pytest.mark.django_db
def test_superadmin_delete_organization_success(client, super_admin_user, db_roles):
    """
    Global Super Admin can delete an organization with confirmation, which records
    an immutable AuditLog entry with ACTION_DELETE.
    """
    from apps.audit.models import AuditLog

    org = Organization.objects.create(
        name="Temporary Legacy Org",
        code="TEMP-LEGACY",
        tax_identifier="TEMP-9988",
        is_active=False,
    )
    Department.objects.create(organization=org, name="Archived Dept", code="ARCH-99")

    client.force_login(super_admin_user)
    delete_url = reverse("superadmin:org_delete", args=[org.pk])

    # 1. GET shows confirmation modal
    get_res = client.get(delete_url)
    assert get_res.status_code == 200
    assert "Temporary Legacy Org" in get_res.content.decode()
    assert "Permanent Platform Action" in get_res.content.decode()

    # 2. POST executes deletion
    post_res = client.post(delete_url, HTTP_HX_REQUEST="true")
    assert post_res.status_code == 200

    # 3. Assert removed from DB
    assert not Organization.objects.filter(code="TEMP-LEGACY").exists()
    assert not Department.objects.filter(code="ARCH-99").exists()

    # 4. Assert AuditLog entry
    audit = AuditLog.objects.filter(target_model="Organization", target_object_id=str(org.id)).last()
    assert audit is not None
    assert audit.action == AuditLog.ACTION_DELETE
    assert audit.previous_state["code"] == "TEMP-LEGACY"


@pytest.mark.django_db
def test_superadmin_delete_organization_with_purchase_requisitions_blocked(client, super_admin_user, db_roles):
    """
    Deleting an organization with operational purchase requisitions is safely blocked
    with a clear explanatory message.
    """
    from apps.requisitions.models import PurchaseRequisition
    from apps.organization.models import CostCenter
    from decimal import Decimal

    org = Organization.objects.create(name="Active Production Org", code="ACT-PROD", is_active=True)
    dept = Department.objects.create(organization=org, name="Operations", code="OPS-ACT")
    cc = CostCenter.objects.create(department=dept, code="CC-OPS-01", name="Ops CC")
    requester = User.objects.create_user(
        email="requester@prod.com",
        password="Pass!1",
        role=db_roles[Role.REQUESTER],
        organization=org,
    )

    from datetime import date
    PurchaseRequisition.objects.create(
        pr_number="PR-TEST-BLOCK-01",
        title="Server Equipment",
        justification="Critical servers for datacenter",
        requester=requester,
        department=dept,
        cost_center=cc,
        total_amount=Decimal("15000.00"),
        requested_delivery_date=date.today(),
        status=PurchaseRequisition.STATUS_SUBMITTED,
    )

    client.force_login(super_admin_user)
    delete_url = reverse("superadmin:org_delete", args=[org.pk])

    post_res = client.post(delete_url, HTTP_HX_REQUEST="true")
    assert post_res.status_code == 200
    assert "Cannot delete organization" in post_res.content.decode()
    assert "purchase requisitions" in post_res.content.decode()

    # Verify organization still exists
    assert Organization.objects.filter(code="ACT-PROD").exists()


@pytest.mark.django_db
def test_scoped_tenant_admin_cannot_delete_organization(client, db_roles):
    """
    Tenant-scoped administrators cannot delete organizations (403 Forbidden).
    """
    tenant_org = Organization.objects.create(name="Tenant A", code="TENANT-A", is_active=True)
    tenant_admin = User.objects.create_user(
        email="admin@tenanta.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        organization=tenant_org,
        is_staff=True,
    )

    client.force_login(tenant_admin)
    delete_url = reverse("superadmin:org_delete", args=[tenant_org.pk])

    res = client.get(delete_url)
    assert res.status_code == 403
    assert "Permission Denied" in res.content.decode()


@pytest.mark.django_db
def test_scoped_tenant_admin_ui_and_permissions(client, db_roles):
    """
    Tenant-scoped administrators:
    1. See single combined 'Departments & Cost Centers' link in sidebar.
    2. See only 'View' button in Action column (Edit button removed).
    3. Are blocked with 403 when trying to access org_edit_view.
    """
    tenant_org = Organization.objects.create(name="Tenant Alpha", code="TENANT-ALP", is_active=True)
    tenant_admin = User.objects.create_user(
        email="admin@tenantalpha.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        organization=tenant_org,
        is_staff=True,
    )
    tenant_org.admin_user = tenant_admin
    tenant_org.save(update_fields=["admin_user"])

    client.force_login(tenant_admin)

    # 1. Test organizations page
    res_orgs = client.get(reverse("superadmin:organizations"))
    assert res_orgs.status_code == 200
    content = res_orgs.content.decode()

    # Sidebar has single 'Departments & Cost Centers' link
    assert "Departments &amp; Cost Centers" in content or "Departments & Cost Centers" in content
    assert "Manage Departments" not in content
    assert "Manage Cost Centers" not in content

    # In Legal Entities action column: 'View' is present, 'Edit' is removed
    assert f"/superadmin/organizations/{tenant_org.pk}/view/" in content
    assert f"/superadmin/organizations/{tenant_org.pk}/edit/" not in content

    # 2. Test dashboard page
    res_dash = client.get(reverse("superadmin:home"))
    assert res_dash.status_code == 200
    dash_content = res_dash.content.decode()
    assert f"/superadmin/organizations/{tenant_org.pk}/view/" in dash_content
    assert f"/superadmin/organizations/{tenant_org.pk}/edit/" not in dash_content

    # 3. Direct GET to org_edit is forbidden
    edit_url = reverse("superadmin:org_edit", args=[tenant_org.pk])
    res_edit = client.get(edit_url)
    assert res_edit.status_code == 403
    assert "Permission Denied" in res_edit.content.decode()





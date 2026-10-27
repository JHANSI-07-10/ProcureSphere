import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import Organization
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
    assert res_get.status_code == 200
    assert "Initial Organization Administrator" in res_get.content.decode()

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

import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization


@pytest.fixture
def org_isolation_setup(db):
    """
    Sets up two isolated organizations (HPE and Nova) along with their respective
    administrators, departments, and cost centers, plus a global platform superuser.
    """
    super_admin_role, _ = Role.objects.get_or_create(
        code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"}
    )

    # 1. Hewlett Packard Enterprise (HPE)
    org_hpe = Organization.objects.create(
        name="Hewlett Packard Enterprise",
        code="HPE-GLOBAL",
        tax_identifier="US-HPE-112233",
        is_active=True,
    )
    admin_hpe = User.objects.create_user(
        email="admin@hpe.com",
        password="Password123!",
        role=super_admin_role,
        organization=org_hpe,
        is_staff=True,
        is_superuser=False,
    )
    admin_hpe.must_change_password = False
    admin_hpe.save()
    org_hpe.admin_user = admin_hpe
    org_hpe.save(update_fields=["admin_user"])

    dept_hpe = Department.objects.create(
        organization=org_hpe,
        code="HPE-AUDIT",
        name="HPE Compliance & Audit",
    )
    cc_hpe = CostCenter.objects.create(
        department=dept_hpe,
        code="HPE-CC-001",
        name="HPE Global Audit Cost Center",
        manager=admin_hpe,
    )

    # 2. Nova Technologies (Nova)
    org_nova = Organization.objects.create(
        name="Nova Technologies — Test",
        code="NOVA-TEST",
        tax_identifier="US-NOVA-445566",
        is_active=True,
    )
    admin_nova = User.objects.create_user(
        email="kadiyalaabhishek33@gmail.com",
        password="Password123!",
        role=super_admin_role,
        organization=org_nova,
        is_staff=True,
        is_superuser=False,
    )
    admin_nova.must_change_password = False
    admin_nova.save()
    org_nova.admin_user = admin_nova
    org_nova.save(update_fields=["admin_user"])

    dept_nova = Department.objects.create(
        organization=org_nova,
        code="NOVA-ENG",
        name="Nova Engineering Labs",
    )
    cc_nova = CostCenter.objects.create(
        department=dept_nova,
        code="NOVA-CC-001",
        name="Nova Core Eng Cost Center",
        manager=admin_nova,
    )

    # 3. Global Platform Superuser
    global_superuser = User.objects.create_superuser(
        email="superadmin@procuresphere.com",
        password="SuperPassword123!",
        role=super_admin_role,
    )
    global_superuser.must_change_password = False
    global_superuser.save()

    return {
        "org_hpe": org_hpe,
        "admin_hpe": admin_hpe,
        "dept_hpe": dept_hpe,
        "cc_hpe": cc_hpe,
        "org_nova": org_nova,
        "admin_nova": admin_nova,
        "dept_nova": dept_nova,
        "cc_nova": cc_nova,
        "global_superuser": global_superuser,
    }


@pytest.mark.django_db
def test_superadmin_dashboard_organization_isolation(client, org_isolation_setup):
    """
    Tenant Administrator (Nova) must ONLY see Nova's legal entity, departments,
    cost centers, and users. Must not see HPE's data or have access to modify it.
    """
    data = org_isolation_setup
    admin_nova = data["admin_nova"]
    client.force_login(admin_nova)

    # 1. Organization List View: /superadmin/organizations/
    res = client.get(reverse("superadmin:organizations"))
    assert res.status_code == 200
    content = res.content.decode()

    # Must contain Nova data
    assert "Nova Technologies — Test" in content
    assert "NOVA-TEST" in content
    assert "Nova Engineering Labs" in content
    assert "NOVA-CC-001" in content

    # Must NOT contain HPE data
    assert "Hewlett Packard Enterprise" not in content
    assert "HPE-GLOBAL" not in content
    assert "HPE Compliance & Audit" not in content
    assert "HPE-AUDIT" not in content
    assert "HPE-CC-001" not in content

    # The "+ Add Organization" button must not be visible to tenant admins
    assert "+ Add Organization" not in content

    # 2. Prevent tenant admin from creating a new legal entity (403)
    res_create = client.post(
        reverse("superadmin:org_create"),
        {
            "name": "Intruder Corp",
            "code": "INTRUDER",
            "admin_email": "intruder@test.com",
        },
    )
    assert res_create.status_code == 403

    # 3. Prevent tenant admin from editing HPE's organization (403)
    res_edit_hpe = client.get(
        reverse("superadmin:org_edit", args=[data["org_hpe"].id])
    )
    assert res_edit_hpe.status_code == 403

    # 4. Prevent tenant admin from editing HPE's department (403)
    res_edit_dept = client.get(
        reverse("superadmin:dept_edit", args=[data["dept_hpe"].id])
    )
    assert res_edit_dept.status_code == 403

    # 5. Prevent tenant admin from editing HPE's cost center (403)
    res_edit_cc = client.get(
        reverse("superadmin:cost_center_edit", args=[data["cc_hpe"].id])
    )
    assert res_edit_cc.status_code == 403

    # 6. User list: Nova admin only sees Nova users
    res_users = client.get(reverse("superadmin:users"))
    assert res_users.status_code == 200
    users_content = res_users.content.decode()
    assert "kadiyalaabhishek33@gmail.com" in users_content
    assert "admin@hpe.com" not in users_content

    # 7. Prevent modifying HPE user (403)
    res_edit_user = client.get(
        reverse("superadmin:user_edit", args=[data["admin_hpe"].id])
    )
    assert res_edit_user.status_code == 403


@pytest.mark.django_db
def test_django_admin_organization_isolation(client, org_isolation_setup):
    """
    In standard Django Admin (/admin/):
    Tenant Admin (Nova) must ONLY see Nova legal entities, departments, cost centers,
    and users. Access to HPE objects must return 404/403.
    """
    data = org_isolation_setup
    admin_nova = data["admin_nova"]
    client.force_login(admin_nova)

    # 1. Organization changelist: /admin/organization/organization/
    org_list_url = reverse("admin:organization_organization_changelist")
    res_orgs = client.get(org_list_url)
    assert res_orgs.status_code == 200
    orgs_html = res_orgs.content.decode()
    assert "Nova Technologies — Test" in orgs_html
    assert "NOVA-TEST" in orgs_html
    assert "Hewlett Packard Enterprise" not in orgs_html
    assert "HPE-GLOBAL" not in orgs_html

    # 2. Add Organization permission denied in Django admin
    add_org_url = reverse("admin:organization_organization_add")
    res_add_org = client.get(add_org_url)
    assert res_add_org.status_code == 403

    # 3. Direct access to HPE organization in Django Admin redirects or returns 404 (out of scope)
    change_hpe_url = reverse("admin:organization_organization_change", args=[data["org_hpe"].id])
    res_change_hpe = client.get(change_hpe_url)
    assert res_change_hpe.status_code in (302, 404)

    # 4. Department changelist: /admin/organization/department/
    dept_list_url = reverse("admin:organization_department_changelist")
    res_depts = client.get(dept_list_url)
    assert res_depts.status_code == 200
    depts_html = res_depts.content.decode()
    assert "Nova Engineering Labs" in depts_html
    assert "HPE Compliance & Audit" not in depts_html

    # 5. CostCenter changelist: /admin/organization/costcenter/
    cc_list_url = reverse("admin:organization_costcenter_changelist")
    res_ccs = client.get(cc_list_url)
    assert res_ccs.status_code == 200
    ccs_html = res_ccs.content.decode()
    assert "NOVA-CC-001" in ccs_html
    assert "HPE-CC-001" not in ccs_html

    # 6. User changelist: /admin/accounts/user/
    user_list_url = reverse("admin:accounts_user_changelist")
    res_users = client.get(user_list_url)
    assert res_users.status_code == 200
    users_html = res_users.content.decode()
    assert "kadiyalaabhishek33@gmail.com" in users_html
    assert "admin@hpe.com" not in users_html

    # 7. Django Admin Index: dashboard_organizations only has Nova
    admin_index_url = reverse("admin:index")
    res_index = client.get(admin_index_url)
    assert res_index.status_code == 200
    index_html = res_index.content.decode()
    assert "NOVA-TEST" in index_html
    assert "HPE-GLOBAL" not in index_html


@pytest.mark.django_db
def test_global_superuser_unrestricted_access(client, org_isolation_setup):
    """
    Global superusers (is_superuser=True) retain unrestricted visibility
    across all organizations in both Superadmin portal and Django Admin.
    """
    data = org_isolation_setup
    global_superuser = data["global_superuser"]
    client.force_login(global_superuser)

    # 1. Superadmin Dashboard: sees both organizations
    res = client.get(reverse("superadmin:organizations"))
    assert res.status_code == 200
    content = res.content.decode()
    assert "Nova Technologies — Test" in content
    assert "Hewlett Packard Enterprise" in content
    assert "Add Organization" in content

    # 2. Django Admin: sees both organizations
    org_list_url = reverse("admin:organization_organization_changelist")
    res_orgs = client.get(org_list_url)
    assert res_orgs.status_code == 200
    orgs_html = res_orgs.content.decode()
    assert "Nova Technologies — Test" in orgs_html
    assert "Hewlett Packard Enterprise" in orgs_html


@pytest.mark.django_db
def test_vendor_management_organization_isolation(client, org_isolation_setup):
    """
    Vendor Management Isolation:
    - Nova Admin on /vendors/ must ONLY see Nova vendors.
    - Nova Admin must NOT see HPE vendors (e.g. Acme, Cisco).
    - Nova Admin accessing HPE vendor detail must get 404.
    - Nova Admin registering a vendor gets associated with Nova organization.
    - Global superuser sees all vendors across all organizations.
    """
    from apps.vendors.models import Vendor, VendorCategory
    from apps.vendors.services import register_vendor_service

    data = org_isolation_setup
    org_hpe = data["org_hpe"]
    org_nova = data["org_nova"]
    admin_hpe = data["admin_hpe"]
    admin_nova = data["admin_nova"]
    global_superuser = data["global_superuser"]

    cat = VendorCategory.objects.create(name="IT Services", code="IT-SERV")

    # 1. Create HPE vendor
    vendor_hpe = register_vendor_service(
        legal_name="Acme Technology Solutions Inc",
        trade_name="Acme Technologies",
        tax_identification_number="TAX-HPE-ACME-999",
        category=cat,
        email="contact@acme.com",
        address="100 HPE Way, Palo Alto, CA",
        created_by_user=admin_hpe,
        organization=org_hpe,
    )

    # 2. Create Nova vendor
    vendor_nova = register_vendor_service(
        legal_name="Nova Cyber Systems LLC",
        trade_name="Nova Cyber",
        tax_identification_number="TAX-NOVA-CYBER-111",
        category=cat,
        email="info@novacyber.com",
        address="200 Nova Blvd, Austin, TX",
        created_by_user=admin_nova,
        organization=org_nova,
    )

    # 3. Test Nova Admin perspective on /vendors/
    client.force_login(admin_nova)
    res_list = client.get(reverse("vendor_list"))
    assert res_list.status_code == 200
    list_html = res_list.content.decode()

    # Nova vendor MUST be present
    assert "Nova Cyber Systems LLC" in list_html
    assert vendor_nova.vendor_number in list_html

    # HPE vendor MUST NOT be present
    assert "Acme Technology Solutions Inc" not in list_html
    assert vendor_hpe.vendor_number not in list_html

    # Governance register & metrics
    res_gov = client.get(reverse("vendor_governance"))
    assert res_gov.status_code == 200
    gov_html = res_gov.content.decode()
    assert "Nova Cyber Systems LLC" in gov_html
    assert "Acme Technology Solutions Inc" not in gov_html

    # Direct detail access: Nova vendor -> 200; HPE vendor -> 404
    res_nova_detail = client.get(reverse("vendor_detail", args=[vendor_nova.id]))
    assert res_nova_detail.status_code == 200

    res_hpe_detail = client.get(reverse("vendor_detail", args=[vendor_hpe.id]))
    assert res_hpe_detail.status_code == 404

    # 4. Django Admin vendor changelist: /admin/vendors/vendor/
    admin_vendor_url = reverse("admin:vendors_vendor_changelist")
    res_admin_vendors = client.get(admin_vendor_url)
    assert res_admin_vendors.status_code == 200
    admin_vendors_html = res_admin_vendors.content.decode()
    assert "Nova Cyber Systems LLC" in admin_vendors_html
    assert "Acme Technology Solutions Inc" not in admin_vendors_html

    # 5. Global platform superuser sees both vendors
    client.force_login(global_superuser)
    res_super_list = client.get(reverse("vendor_list"))
    assert res_super_list.status_code == 200
    super_list_html = res_super_list.content.decode()
    assert "Nova Cyber Systems LLC" in super_list_html
    assert "Acme Technology Solutions Inc" in super_list_html


@pytest.mark.django_db
def test_hpe_admin_sees_only_hpe_organization_and_no_add_button(client, org_isolation_setup):
    """
    Verifies that when admin@hpe.com logs into /superadmin/organizations/:
    1. Only Hewlett Packard Enterprise (HPE-GLOBAL) is displayed in the Legal Entities table.
    2. Nova Technologies (NOVA-TEST) is NOT displayed.
    3. The '+ Add Organization' button is NOT visible.
    4. Only HPE departments and cost centers are displayed.
    """
    data = org_isolation_setup
    admin_hpe = data["admin_hpe"]
    client.force_login(admin_hpe)

    res = client.get(reverse("superadmin:organizations"))
    assert res.status_code == 200
    content = res.content.decode()

    # Must contain HPE data
    assert "Hewlett Packard Enterprise" in content
    assert "HPE-GLOBAL" in content
    assert ("HPE Compliance & Audit" in content or "HPE Compliance &amp; Audit" in content)
    assert "HPE-AUDIT" in content
    assert "HPE-CC-001" in content

    # Must NOT contain Nova data
    assert "Nova Technologies — Test" not in content
    assert "NOVA-TEST" not in content
    assert "Nova Engineering Labs" not in content
    assert "NOVA-CC-001" not in content

    # The '+ Add Organization' button must NOT be visible
    assert "+ Add Organization" not in content



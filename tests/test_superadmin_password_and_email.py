import re
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.password_utils import generate_secure_password
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
def sample_roles_and_departments(db):
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_app, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    org, _ = Organization.objects.get_or_create(name="HPE Global", code="HPE-GLB", defaults={"is_active": True})
    dept_hr, _ = Department.objects.get_or_create(name="Human Resources", code="HR-01", organization=org)
    cc_hr, _ = CostCenter.objects.get_or_create(name="HR Operations", code="CC-HR-01", department=dept_hr)
    return role_req, role_app, dept_hr, cc_hr


def test_generate_secure_password_strength_and_randomness():
    """Validates that generate_secure_password satisfies all length, character, and uniqueness criteria."""
    passwords = [generate_secure_password() for _ in range(25)]

    for pwd in passwords:
        assert len(pwd) >= 12
        assert re.search(r"[A-Z]", pwd), "Must contain uppercase characters"
        assert re.search(r"[a-z]", pwd), "Must contain lowercase characters"
        assert re.search(r"[0-9]", pwd), "Must contain digits"
        assert re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", pwd), "Must contain special characters"

    # All generated passwords must be distinct
    assert len(set(passwords)) == len(passwords)


@pytest.mark.django_db
def test_user_create_modal_contains_password_and_action_controls(superadmin_client):
    """Verifies modal HTML contains the Password field, eye icon, Generate Password, and Copy buttons."""
    client, _ = superadmin_client
    response = client.get(reverse("superadmin:user_create"))
    assert response.status_code == 200
    html = response.content.decode("utf-8")

    assert "Create User" in html
    assert 'id="id_password"' in html
    assert "Generate Password" in html
    assert "Copy" in html
    assert "togglePasswordBtn" in html
    assert "Must contain at least 12 characters" in html


@pytest.mark.django_db
def test_create_user_with_manual_password_and_welcome_email(superadmin_client, sample_roles_and_departments):
    """Verifies creating a user with manual password hashes password, enforces first login flag, and sends email."""
    client, admin = superadmin_client
    _, role_app, dept_hr, _ = sample_roles_and_departments
    mail.outbox.clear()

    manual_password = "SuperSecretPassword123!@#"
    response = client.post(
        reverse("superadmin:user_create"),
        {
            "email": "manual.user@example.com",
            "phone_number": "9123456780",
            "role": str(role_app.pk),
            "department": str(dept_hr.pk),
            "password": manual_password,
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    user = User.objects.get(email="manual.user@example.com")
    assert user.is_active is True
    assert user.must_change_password is True
    assert check_password(manual_password, user.password) is True
    assert user.password != manual_password  # Must be hashed, not plaintext

    # Verify welcome email sent
    assert len(mail.outbox) == 1
    sent_mail = mail.outbox[0]
    assert sent_mail.to == ["manual.user@example.com"]
    assert "Welcome to ProcureSphere 360 – Your Account Details" in sent_mail.subject

    # Check content in text or html alternatives
    all_content = sent_mail.body
    if hasattr(sent_mail, "alternatives"):
        for alt_body, _ in sent_mail.alternatives:
            all_content += alt_body

    assert "manual.user@example.com" in all_content
    assert manual_password in all_content
    assert "Human Resources" in all_content
    assert "Department Approver" in all_content
    assert "change your temporary password" in all_content.lower()


@pytest.mark.django_db
def test_create_user_with_generated_password_when_empty_in_payload(superadmin_client, sample_roles_and_departments):
    """Verifies backward compatibility: if password is not supplied, a secure password is automatically generated."""
    client, _ = superadmin_client
    _, role_app, dept_hr, _ = sample_roles_and_departments
    mail.outbox.clear()

    response = client.post(
        reverse("superadmin:user_create"),
        {
            "email": "auto.generated@example.com",
            "phone_number": "9876543210",
            "role": str(role_app.pk),
            "department": str(dept_hr.pk),
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    user = User.objects.get(email="auto.generated@example.com")
    assert user.has_usable_password()
    assert user.must_change_password is True
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_create_requester_user_with_cost_center(superadmin_client, sample_roles_and_departments):
    """Verifies creating a Requester user with mandatory Cost Center and password."""
    client, _ = superadmin_client
    role_req, _, dept_hr, cc_hr = sample_roles_and_departments
    mail.outbox.clear()

    response = client.post(
        reverse("superadmin:user_create"),
        {
            "email": "requester.new@example.com",
            "phone_number": "9876543210",
            "role": str(role_req.pk),
            "department": str(dept_hr.pk),
            "cost_center": str(cc_hr.pk),
            "password": "RequesterPassword2026!@",
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    user = User.objects.get(email="requester.new@example.com")
    assert user.cost_center == cc_hr
    assert user.department == dept_hr
    assert user.role == role_req
    assert user.must_change_password is True
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_invalid_password_rejected(superadmin_client, sample_roles_and_departments):
    """Verifies password validation failure prevents user creation and stops email dispatch."""
    client, _ = superadmin_client
    _, role_app, dept_hr, _ = sample_roles_and_departments
    mail.outbox.clear()

    response = client.post(
        reverse("superadmin:user_create"),
        {
            "email": "bad.password@example.com",
            "phone_number": "9876543210",
            "role": str(role_app.pk),
            "department": str(dept_hr.pk),
            "password": "123",  # Invalid password (too short)
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "too short" in response.content.decode("utf-8")
    assert not User.objects.filter(email="bad.password@example.com").exists()
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_email_delivery_failure_handled_gracefully(superadmin_client, sample_roles_and_departments):
    """Verifies that if SMTP/email sending throws an error, the user is still saved, transaction does not abort, and error is handled."""
    client, _ = superadmin_client
    _, role_app, dept_hr, _ = sample_roles_and_departments

    with patch("apps.notifications.email_service.EmailMultiAlternatives.send", side_effect=Exception("SMTP Relay Timeout")):
        response = client.post(
            reverse("superadmin:user_create"),
            {
                "email": "smtp.fail@example.com",
                "phone_number": "9876543210",
                "role": str(role_app.pk),
                "department": str(dept_hr.pk),
                "password": "ValidSecretPassword123!@",
                "is_active": "on",
            },
        )
        assert response.status_code == 200
        # User is still created and committed in DB
        assert User.objects.filter(email="smtp.fail@example.com").exists()


@pytest.mark.django_db
def test_resend_welcome_email_superadmin_action(superadmin_client, sample_roles_and_departments):
    """Verifies Super Admin can resend the welcome email with a refreshed temporary password."""
    client, admin = superadmin_client
    _, role_app, dept_hr, _ = sample_roles_and_departments
    mail.outbox.clear()

    user = User.objects.create_user(
        email="resend.user@example.com",
        password="InitialPassword123!",
        role=role_app,
        department=dept_hr,
    )
    user.must_change_password = True
    user.save()

    response = client.post(reverse("superadmin:user_resend_welcome", kwargs={"user_id": user.pk}))
    assert response.status_code == 200
    assert "<script>window.location.reload();</script>" in response.content.decode("utf-8")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["resend.user@example.com"]
    user.refresh_from_db()
    assert user.must_change_password is True


@pytest.mark.django_db
def test_first_login_password_change_workflow():
    """
    End-to-end integration test of first-login enforcement:
    1. Newly created user logs in with temporary password.
    2. User is redirected to Change Password page.
    3. User cannot access dashboard or requisitions before changing password.
    4. User changes password using standard PasswordChangeForm.
    5. User is unblocked and can access normal dashboard pages.
    """
    role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    temp_pwd = "TempSecretPassword123!@"
    new_pwd = "MyNewSecurePassword456!#"

    user = User.objects.create_user(
        email="approver.firstlogin@example.com",
        password=temp_pwd,
        role=role,
    )
    user.must_change_password = True
    user.save()

    client = Client()

    # 1. Login with temporary password
    login_res = client.post("/login/", {"email": user.email, "password": temp_pwd})
    assert login_res.status_code == 302
    assert "/accounts/change-password/" in login_res.url

    # 2. Attempt to bypass and access dashboard directly -> Middleware must redirect
    dash_res = client.get("/")
    assert dash_res.status_code == 302
    assert "/accounts/change-password/" in dash_res.url

    # 3. GET change password page
    change_page_res = client.get("/accounts/change-password/")
    assert change_page_res.status_code == 200
    assert "Change Password" in change_page_res.content.decode("utf-8")

    # 4. POST password change
    change_post_res = client.post(
        "/accounts/change-password/",
        {
            "old_password": temp_pwd,
            "new_password1": new_pwd,
            "new_password2": new_pwd,
        },
    )
    assert change_post_res.status_code == 302
    assert change_post_res.url == "/"

    # 5. Verify user must_change_password is now False and new password is active
    user.refresh_from_db()
    assert user.must_change_password is False
    assert check_password(new_pwd, user.password) is True

    # 6. Now access dashboard -> must succeed (200 OK or 302 to role dashboard, not /accounts/change-password/)
    allowed_res = client.get("/")
    assert "/accounts/change-password/" not in getattr(allowed_res, "url", "")


@pytest.mark.django_db
def test_existing_users_login_without_password_change_prompt():
    """Verifies existing users with must_change_password=False login directly without interruption."""
    role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    pwd = "ExistingUserPassword123!"

    user = User.objects.create_user(
        email="regular.user@example.com",
        password=pwd,
        role=role,
    )
    user.must_change_password = False
    user.save()

    client = Client()
    login_res = client.post("/login/", {"email": user.email, "password": pwd})
    assert login_res.status_code == 302
    assert login_res.url == "/"


@pytest.mark.django_db
def test_user_edit_with_custom_password_sends_email(superadmin_client, sample_roles_and_departments):
    """Verifies that updating a user's password in user_edit_view sends that exact typed password via email."""
    client, _ = superadmin_client
    _, role_app, dept_hr, _ = sample_roles_and_departments
    mail.outbox.clear()

    user = User.objects.create_user(
        email="test_edit_user@example.com",
        password="InitialPassword123!",
        role=role_app,
        department=dept_hr,
        organization=dept_hr.organization,
    )

    custom_new_password = "MyCustomPassword#2026!"
    response = client.post(
        reverse("superadmin:user_edit", kwargs={"user_id": user.id}),
        {
            "email": user.email,
            "role": str(role_app.pk),
            "department": str(dept_hr.pk),
            "password": custom_new_password,
            "is_active": "on",
        },
    )
    assert response.status_code == 200

    user.refresh_from_db()
    assert check_password(custom_new_password, user.password) is True

    # Email verification
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [user.email]
    all_content = sent.body
    if hasattr(sent, "alternatives"):
        for alt_body, _ in sent.alternatives:
            all_content += alt_body
    assert custom_new_password in all_content


@pytest.mark.django_db
def test_org_edit_with_custom_password_sends_email(superadmin_client):
    """Verifies that entering a custom admin password in org_edit_view sends that exact password to the admin."""
    client, admin = superadmin_client
    mail.outbox.clear()

    org = Organization.objects.create(name="Delta Tech", code="DELTA-TECH", is_active=True)
    admin_user = User.objects.create_user(
        email="delta_admin@delta.local",
        password="DeltaOldPassword123!",
        role=admin.role,
        organization=org,
        is_staff=True,
    )
    org.admin_user = admin_user
    org.save()

    custom_admin_pwd = "DeltaAdminSecurePass#999!"
    response = client.post(
        reverse("superadmin:org_edit", kwargs={"org_id": org.id}),
        {
            "name": org.name,
            "code": org.code,
            "is_active": "on",
            "admin_email": admin_user.email,
            "admin_password": custom_admin_pwd,
        },
    )
    assert response.status_code == 200

    admin_user.refresh_from_db()
    assert check_password(custom_admin_pwd, admin_user.password) is True

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [admin_user.email]
    all_content = sent.body
    if hasattr(sent, "alternatives"):
        for alt_body, _ in sent.alternatives:
            all_content += alt_body
    assert custom_admin_pwd in all_content


@pytest.mark.django_db
def test_superadmin_sidebar_profile_alignment_and_non_clipping_signout(superadmin_client):
    """Verifies that the Superadmin sidebar profile container includes the circular sign out button and truncation."""
    client, _ = superadmin_client
    response = client.get(reverse("superadmin:home"))
    assert response.status_code == 200
    html = response.content.decode("utf-8")

    # Check for non-clipping circular button and min-width constraints
    assert "rounded-circle flex-shrink-0 d-inline-flex align-items-center justify-content-center" in html
    assert "min-width: 0" in html
    assert "text-truncate" in html
    assert "title=" in html


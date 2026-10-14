import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.vendors.models import Vendor, VendorCategory, VendorContact, VendorDocument
from apps.vendors.selectors import get_vendor_onboarding_progress
from apps.vendors.services import (
    create_vendor_contact_service,
    delete_vendor_contact_service,
    request_vendor_changes_service,
    set_vendor_status_governance_service,
    submit_vendor_kyc_service,
    update_vendor_contact_service,
    update_vendor_profile_service,
)


@pytest.fixture
def vendor_category(db):
    return VendorCategory.objects.create(name="IT Services", code="IT-001")


@pytest.fixture
def vendor_a(db, vendor_category):
    return Vendor.objects.create(
        legal_name="Alpha Tech Solutions Pvt Ltd",
        trade_name="Alpha Tech",
        vendor_number="VND-2026-00001",
        tax_identification_number="TIN-ALPHA-001",
        registration_number="REG-ALPHA-001",
        category=vendor_category,
        email="contact@alphatech.com",
        phone="+919876543210",
        address="123 Tech Park, Bengaluru, India",
        bank_name="State Bank of India",
        bank_account_number="SBIN00012345678",
        bank_routing_code="SBIN0001234",
        status=Vendor.STATUS_DRAFT,
    )


@pytest.fixture
def vendor_b(db, vendor_category):
    return Vendor.objects.create(
        legal_name="Beta Logistics Pvt Ltd",
        trade_name="Beta Logistics",
        vendor_number="VND-2026-00002",
        tax_identification_number="TIN-BETA-002",
        registration_number="REG-BETA-002",
        category=vendor_category,
        email="contact@betalogistics.com",
        phone="+919123456789",
        address="456 Freight Hub, Mumbai, India",
        bank_name="HDFC Bank",
        bank_account_number="HDFC00098765432",
        bank_routing_code="HDFC0009876",
        status=Vendor.STATUS_DRAFT,
    )


@pytest.fixture
def vendor_role(db):
    role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})
    return role


@pytest.fixture
def proc_exec_role(db):
    role, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    return role


@pytest.fixture
def requester_role(db):
    role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    return role


@pytest.fixture
def vendor_user_a(db, vendor_role, vendor_a):
    user = User.objects.create_user(
        email="vendor_a@alphatech.com",
        password="Password123!",
        role=vendor_role,
        vendor=vendor_a,
    )
    return user


@pytest.fixture
def vendor_user_b(db, vendor_role, vendor_b):
    user = User.objects.create_user(
        email="vendor_b@betalogistics.com",
        password="Password123!",
        role=vendor_role,
        vendor=vendor_b,
    )
    return user


@pytest.fixture
def proc_exec_user(db, proc_exec_role):
    return User.objects.create_user(
        email="proc_exec@hpe.com",
        password="Password123!",
        role=proc_exec_role,
    )


@pytest.fixture
def requester_user(db, requester_role):
    return User.objects.create_user(
        email="requester@hpe.com",
        password="Password123!",
        role=requester_role,
    )


# ==============================================================================
# 1. VENDOR LOGIN LANDING PAGE ROUTING
# ==============================================================================


@pytest.mark.django_db
def test_authenticated_vendor_redirected_to_vendor_portal_from_login(client, vendor_user_a):
    client.force_login(vendor_user_a)
    response = client.get("/login/")
    assert response.status_code == 302
    assert response.url == "/vendor/"


@pytest.mark.django_db
def test_vendor_login_without_next_redirects_to_vendor_portal(client, vendor_user_a):
    response = client.post(
        "/login/",
        {"email": vendor_user_a.email, "password": "Password123!"},
    )
    assert response.status_code == 302
    assert response.url == "/vendor/"


@pytest.mark.django_db
def test_vendor_user_accessing_root_home_renders_vendor_dashboard(client, vendor_user_a):
    client.force_login(vendor_user_a)
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Vendor Portal Dashboard" in content
    assert "Alpha Tech Solutions" in content


# ==============================================================================
# 2. ONBOARDING PROGRESS CHECKLIST
# ==============================================================================


@pytest.mark.django_db
def test_onboarding_progress_selector_calculation(vendor_a):
    # Initial vendor: Step 1 (profile) done, Step 2 (banking) done, contacts empty, docs empty, review DRAFT
    progress = get_vendor_onboarding_progress(vendor_a)
    assert progress["total_count"] == 5
    assert progress["steps"][0]["is_complete"] is True  # Profile
    assert progress["steps"][1]["is_complete"] is True  # Banking
    assert progress["steps"][2]["is_complete"] is False  # Contacts
    assert progress["steps"][3]["is_complete"] is False  # KYC Docs
    assert progress["steps"][4]["is_complete"] is False  # Review
    assert progress["percentage"] == 40
    assert progress["can_submit"] is False  # Missing docs

    # Add contact
    VendorContact.objects.create(
        vendor=vendor_a,
        first_name="Ramesh",
        last_name="Kumar",
        email="ramesh@alphatech.com",
        phone="+919876543210",
        is_primary=True,
    )
    # Add KYC doc
    from django.core.files.uploadedfile import SimpleUploadedFile
    test_file = SimpleUploadedFile("cert.pdf", b"%PDF-dummy-content", content_type="application/pdf")
    VendorDocument.objects.create(
        vendor=vendor_a,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Certificate",
        file=test_file,
    )

    updated_progress = get_vendor_onboarding_progress(vendor_a)
    assert updated_progress["steps"][2]["is_complete"] is True
    assert updated_progress["steps"][3]["is_complete"] is True
    assert updated_progress["can_submit"] is True
    assert updated_progress["percentage"] == 80


@pytest.mark.django_db
def test_vendor_dashboard_and_profile_render_progress_checklist(client, vendor_user_a):
    client.force_login(vendor_user_a)

    # Dashboard overview
    res_dash = client.get("/vendor/")
    assert res_dash.status_code == 200
    assert "Onboarding &amp; Compliance Progress" in res_dash.content.decode()
    assert "Milestones Completed" in res_dash.content.decode()

    # Profile page
    res_profile = client.get("/vendor/profile/")
    assert res_profile.status_code == 200
    assert "Onboarding &amp; Compliance Progress Checklist" in res_profile.content.decode()
    assert "Authorized Company Contacts" in res_profile.content.decode()


# ==============================================================================
# 3. AUTHORIZED CONTACT MANAGEMENT & VENDOR ISOLATION
# ==============================================================================


@pytest.mark.django_db
def test_create_and_update_vendor_contact_with_primary_flag(client, vendor_user_a, vendor_a):
    client.force_login(vendor_user_a)

    # Add primary contact
    res = client.post(
        "/vendor/profile/",
        {
            "action": "add_contact",
            "first_name": "Anita",
            "last_name": "Sharma",
            "email": "anita@alphatech.com",
            "phone": "+919876543210",
            "designation": "Chief Financial Officer",
            "is_primary": "true",
        },
    )
    assert res.status_code == 302
    assert vendor_a.contacts.count() == 1
    c1 = vendor_a.contacts.first()
    assert c1.first_name == "Anita"
    assert c1.is_primary is True

    # Add second contact as primary -> demotes c1
    res2 = client.post(
        "/vendor/profile/",
        {
            "action": "add_contact",
            "first_name": "Vikram",
            "last_name": "Singh",
            "email": "vikram@alphatech.com",
            "phone": "+919876543211",
            "designation": "Sales Director",
            "is_primary": "true",
        },
    )
    assert res2.status_code == 302
    assert vendor_a.contacts.count() == 2
    c1.refresh_from_db()
    c2 = vendor_a.contacts.get(email="vikram@alphatech.com")
    assert c1.is_primary is False
    assert c2.is_primary is True


@pytest.mark.django_db
def test_cross_vendor_contact_isolation_anti_idor(client, vendor_user_a, vendor_a, vendor_b):
    # Vendor B creates a contact
    contact_b = VendorContact.objects.create(
        vendor=vendor_b,
        first_name="Beta",
        last_name="Manager",
        email="manager@betalogistics.com",
        phone="+919876543210",
    )

    # Vendor A attempts to edit Vendor B's contact via POST
    client.force_login(vendor_user_a)
    res_edit = client.post(
        "/vendor/profile/",
        {
            "action": "edit_contact",
            "contact_id": str(contact_b.id),
            "first_name": "Hacked",
            "last_name": "Contact",
            "email": "hacked@alphatech.com",
            "phone": "+919876543210",
        },
    )
    assert res_edit.status_code == 404  # Not found for Vendor A
    contact_b.refresh_from_db()
    assert contact_b.first_name == "Beta"

    # Vendor A attempts to delete Vendor B's contact via POST
    res_delete = client.post(
        "/vendor/profile/",
        {
            "action": "delete_contact",
            "contact_id": str(contact_b.id),
        },
    )
    assert res_delete.status_code == 404
    assert VendorContact.objects.filter(pk=contact_b.pk).exists()

    # Direct service call anti-IDOR check
    with pytest.raises(PermissionDenied):
        update_vendor_contact_service(
            contact=contact_b,
            vendor=vendor_a,
            user=vendor_user_a,
            data={"first_name": "Malicious"},
        )

    with pytest.raises(PermissionDenied):
        delete_vendor_contact_service(
            contact=contact_b,
            vendor=vendor_a,
            user=vendor_user_a,
        )


# ==============================================================================
# 4. SAVE DRAFT AND SUBMIT FOR REVIEW (NO DUPLICATES)
# ==============================================================================


@pytest.mark.django_db
def test_save_draft_profile_preserves_single_vendor_record(client, vendor_user_a, vendor_a):
    client.force_login(vendor_user_a)
    initial_count = Vendor.objects.count()

    res = client.post(
        "/vendor/profile/",
        {
            "action": "update_profile",
            "trade_name": "Alpha Solutions Brand Updated",
            "address": "789 New Tech Hub, Bengaluru, India",
            "phone": "+919876543210",
            "bank_name": "HDFC Bank",
            "bank_account_number": "112233445566",
            "bank_routing_code": "HDFC0001234",
        },
    )
    assert res.status_code == 302
    assert Vendor.objects.count() == initial_count  # No duplicate created

    vendor_a.refresh_from_db()
    assert vendor_a.trade_name == "Alpha Solutions Brand Updated"
    assert vendor_a.bank_account_number == "112233445566"


@pytest.mark.django_db
def test_submit_for_review_requires_kyc_document(client, vendor_user_a, vendor_a):
    client.force_login(vendor_user_a)
    assert vendor_a.documents.count() == 0

    res = client.post("/vendor/profile/", {"action": "submit_kyc"})
    assert res.status_code == 302
    vendor_a.refresh_from_db()
    assert vendor_a.status == Vendor.STATUS_DRAFT  # Blocked from submitting without KYC


# ==============================================================================
# 5. CHANGES REQUIRED WORKFLOW & RESUBMISSION
# ==============================================================================


@pytest.mark.django_db
def test_changes_required_workflow_end_to_end(
    client, vendor_user_a, proc_exec_user, vendor_a
):
    # 1. Vendor uploads document and submits for review
    from django.core.files.uploadedfile import SimpleUploadedFile
    test_file = SimpleUploadedFile("cert.pdf", b"%PDF-dummy-content", content_type="application/pdf")
    VendorDocument.objects.create(
        vendor=vendor_a,
        document_type=VendorDocument.DOC_TYPE_CERT,
        title="Incorporation Certificate",
        file=test_file,
    )
    submit_vendor_kyc_service(vendor=vendor_a, user=vendor_user_a)
    vendor_a.refresh_from_db()
    assert vendor_a.status == Vendor.STATUS_SUBMITTED

    # 2. Procurement Executive requests changes with notes
    client.force_login(proc_exec_user)
    res_request = client.post(
        f"/vendors/{vendor_a.id}/kyc/request-changes/",
        {"notes": "Please provide your updated GST registration certificate and correct IFSC code."},
    )
    assert res_request.status_code == 302
    vendor_a.refresh_from_db()
    assert vendor_a.status == Vendor.STATUS_CHANGES_REQUIRED
    assert "updated GST registration" in vendor_a.status_notes

    # Notification sent to vendor user
    notif = Notification.objects.filter(recipient=vendor_user_a).last()
    assert notif is not None
    assert "Changes Required" in notif.title or "Vendor status changed" in notif.title

    # 3. Vendor visits dashboard and profile, sees Changes Required banner
    client.force_login(vendor_user_a)
    res_dash = client.get("/vendor/")
    assert res_dash.status_code == 200
    assert "Corrections Requested by Procurement Reviewer" in res_dash.content.decode()

    res_profile = client.get("/vendor/profile/")
    assert res_profile.status_code == 200
    assert "Resubmit for Review" in res_profile.content.decode()

    # 4. Vendor updates fields and resubmits
    res_resubmit = client.post("/vendor/profile/", {"action": "submit_kyc"})
    assert res_resubmit.status_code == 302
    vendor_a.refresh_from_db()
    assert vendor_a.status == Vendor.STATUS_SUBMITTED


@pytest.mark.django_db
def test_invalid_status_transitions_from_changes_required(vendor_a, proc_exec_user):
    vendor_a.status = Vendor.STATUS_CHANGES_REQUIRED
    vendor_a.save()

    # CHANGES_REQUIRED cannot directly jump to ACTIVE without PE review/approval
    with pytest.raises(ValidationError):
        set_vendor_status_governance_service(
            vendor=vendor_a,
            actor=proc_exec_user,
            new_status=Vendor.STATUS_ACTIVE,
            notes="Direct active bypass attempt",
        )


@pytest.mark.django_db
def test_unauthorized_user_cannot_request_changes(vendor_a, requester_user):
    vendor_a.status = Vendor.STATUS_SUBMITTED
    vendor_a.save()

    # Requester cannot perform vendor governance
    with pytest.raises(PermissionDenied):
        request_vendor_changes_service(
            vendor=vendor_a,
            reviewer=requester_user,
            notes="Unauthorized review attempt",
        )

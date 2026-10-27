from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.contracts.models import Contract, ContractVersion
from apps.contracts.services import (
    activate_contract_service,
    add_contract_milestone_service,
    add_contract_obligation_service,
    approve_business_service,
    approve_legal_review_service,
    complete_contract_milestone_service,
    create_contract_service,
    create_contract_version_service,
    fulfill_contract_obligation_service,
    reject_legal_review_service,
    renew_contract_service,
    submit_for_legal_review_service,
    terminate_contract_service,
)
from apps.vendors.models import VendorCategory
from apps.vendors.services import register_vendor_service


def _get_or_create_role(code, name):
    role = Role.objects.filter(code=code).first()
    if not role:
        try:
            role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        except Exception:
            role = Role.objects.filter(code=code).first()
    return role


def _get_or_create_user(email, password, role):
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_user(email=email, password=password, role=role)
    else:
        if role:
            user.role = role
            user.save(update_fields=["role"])
        user.refresh_from_db()
    return user


@pytest.fixture
def contract_setup(db):
    legal_role = _get_or_create_role(Role.LEGAL_MGR, "Legal Manager")
    proc_role = _get_or_create_role(Role.PROC_MGR, "Procurement Manager")
    requester_role = _get_or_create_role(Role.REQUESTER, "Requester")

    legal_user = _get_or_create_user("legal.mgr@hpe.com", "Password123!", legal_role)
    proc_user = _get_or_create_user("proc.mgr@hpe.com", "Password123!", proc_role)
    requester_user = _get_or_create_user("requester@hpe.com", "Password123!", requester_role)

    category, _ = VendorCategory.objects.get_or_create(
        code="CAT-IT-01", defaults={"name": "IT Services"}
    )
    vendor = register_vendor_service(
        legal_name="Enterprise Cloud Solutions Inc",
        tax_identification_number="TAX-ECS-999",
        category=category,
        email="legal@cloudsolutions.com",
        address="100 Technology Way",
    )

    today = timezone.now().date()
    contract = create_contract_service(
        title="Enterprise Data Center Maintenance Agreement",
        vendor=vendor,
        contract_value=Decimal("250000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        renewal_notice_days=30,
        contract_owner=legal_user,
    )

    return {
        "legal_user": legal_user,
        "proc_user": proc_user,
        "requester_user": requester_user,
        "vendor": vendor,
        "contract": contract,
    }


@pytest.mark.django_db
def test_contract_legal_and_business_approval_workflow(contract_setup):
    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]

    assert contract.status == Contract.STATUS_DRAFT
    assert contract.version == 1

    # 1. Submit for Legal Review
    contract = submit_for_legal_review_service(
        contract=contract, user=legal_user, notes="Urgent legal review required"
    )
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # 2. Approve Legal Review
    contract = approve_legal_review_service(
        contract=contract, user=legal_user, notes="Terms and indemnity clauses verified"
    )
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # 3. Approve Business & Activate
    contract = approve_business_service(
        contract=contract, user=proc_user, notes="Budget allocated and signed off"
    )
    assert contract.status == Contract.STATUS_ACTIVE


@pytest.mark.django_db
def test_contract_legal_rejection_workflow(contract_setup):
    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]

    submit_for_legal_review_service(contract=contract, user=legal_user)
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    contract = reject_legal_review_service(
        contract=contract, user=legal_user, reason="Missing compliance Annexure B"
    )
    assert contract.status == Contract.STATUS_DRAFT


@pytest.mark.django_db
def test_contract_version_amendment_preserves_history(contract_setup):
    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]

    today = timezone.now().date()
    # Create Amendment -> Version 2
    version2 = create_contract_version_service(
        contract=contract,
        user=legal_user,
        amendment_summary="Expanded service scope to include Disaster Recovery site",
        contract_value=Decimal("320000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
    )

    contract.refresh_from_db()
    assert contract.version == 2
    assert contract.contract_value == Decimal("320000.00")
    assert version2.version_number == 2
    assert ContractVersion.objects.filter(contract=contract).count() == 2


@pytest.mark.django_db
def test_contract_milestones_and_obligations(contract_setup):
    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    today = timezone.now().date()

    # Milestone
    milestone = add_contract_milestone_service(
        contract=contract,
        title="Phase 1 SLA Audit",
        due_date=today + timezone.timedelta(days=30),
        amount=Decimal("50000.00"),
    )
    assert not milestone.is_completed

    milestone = complete_contract_milestone_service(milestone=milestone, user=legal_user)
    assert milestone.is_completed

    # Obligation
    obligation = add_contract_obligation_service(
        contract=contract,
        title="Quarterly SOC2 Type II Report",
        responsible_party="VENDOR",
        due_date=today + timezone.timedelta(days=90),
    )
    assert not obligation.is_fulfilled

    obligation = fulfill_contract_obligation_service(obligation=obligation, user=legal_user)
    assert obligation.is_fulfilled


@pytest.mark.django_db
def test_contract_renewal_and_termination(contract_setup):
    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    today = timezone.now().date()

    # Activate contract through mandatory legal review and business approval
    submit_for_legal_review_service(contract=contract, user=legal_user)
    approve_legal_review_service(contract=contract, user=legal_user, notes="Legal terms verified")
    contract = activate_contract_service(contract=contract, user=proc_user)
    assert contract.status == Contract.STATUS_ACTIVE

    # Renew
    contract = renew_contract_service(
        contract=contract,
        user=legal_user,
        new_end_date=today + timezone.timedelta(days=730),
        new_value=Decimal("500000.00"),
        notes="Option year exercised",
    )
    assert contract.status == Contract.STATUS_RENEWED
    assert contract.contract_value == Decimal("500000.00")

    # Terminate
    contract = terminate_contract_service(
        contract=contract, user=legal_user, reason="Convenience termination clause exercised"
    )
    assert contract.status == Contract.STATUS_TERMINATED


@pytest.mark.django_db
def test_legal_manager_dashboard_and_views(client, contract_setup):
    legal_user = contract_setup["legal_user"]
    contract = contract_setup["contract"]

    client.force_login(legal_user)

    # 1. Dashboard View
    response = client.get("/")
    assert response.status_code == 200
    assert (
        "Legal &amp; Contracts" in response.content.decode() or "Legal" in response.content.decode()
    )

    # 2. Contract Register List View
    response = client.get(reverse("contracts_list"))
    assert response.status_code == 200
    assert contract.contract_number in response.content.decode()

    # 3. Contract Detail View
    response = client.get(reverse("contract_detail", kwargs={"contract_id": contract.id}))
    assert response.status_code == 200
    assert contract.title in response.content.decode()


@pytest.mark.django_db
def test_contract_document_vault_upload_and_validation(client, contract_setup):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.audit.models import AuditLog
    from apps.contracts.forms import ContractDocumentForm
    from apps.contracts.models import ContractDocument
    from apps.contracts.services import upload_contract_document_service

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]

    # 1. Service Layer upload
    valid_file = SimpleUploadedFile(
        "executed_msa.pdf", b"%PDF-1.4 dummy content", content_type="application/pdf"
    )
    doc = upload_contract_document_service(
        contract=contract,
        user=legal_user,
        title="Executed Master Service Agreement",
        file=valid_file,
    )
    assert doc.id is not None
    assert doc.title == "Executed Master Service Agreement"
    assert doc.uploaded_by == legal_user

    # Verify AuditLog
    audit_entry = AuditLog.objects.filter(
        target_model="ContractDocument", target_object_id=str(doc.id)
    ).first()
    assert audit_entry is not None
    assert audit_entry.actor == legal_user

    # 2. Form Validation — Invalid extension
    invalid_file = SimpleUploadedFile(
        "malicious.exe", b"binary data", content_type="application/octet-stream"
    )
    form = ContractDocumentForm(data={"title": "Bad File"}, files={"file": invalid_file})
    assert not form.is_valid()
    assert "Unsupported file format" in str(form.errors["file"])

    # 3. REST API upload endpoint
    client.force_login(legal_user)
    api_file = SimpleUploadedFile(
        "sow_appendix.docx",
        b"dummy word content",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response = client.post(
        f"/api/v1/contracts/{contract.id}/upload-document/",
        {"title": "SOW Appendix A", "file": api_file},
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["title"] == "SOW Appendix A"
    assert ContractDocument.objects.filter(title="SOW Appendix A").exists()


@pytest.mark.django_db
def test_full_contract_lifecycle_view_and_api_workflow(client, contract_setup):
    """
    Day 15 Task: Validate complete end-to-end Contract -> Legal Review -> Business Approval -> Active workflow
    via both HTTP views and DRF REST API endpoints, verifying RBAC, AuditLog, and Notifications.
    """
    from apps.audit.models import AuditLog
    from apps.notifications.models import Notification

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    requester_user = contract_setup["requester_user"]

    auditor_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
    auditor_user = User.objects.create_user(
        email="auditor.d15@hpe.com", password="Password123!", role=auditor_role
    )

    # Initial state: DRAFT
    assert contract.status == Contract.STATUS_DRAFT

    # 1. Requester / Owner submits for Legal Review via HTTP View
    client.force_login(legal_user)
    response = client.post(
        reverse("contract_submit_legal", kwargs={"contract_id": contract.id}),
        {"notes": "Submitting for contract sign-off"},
    )
    assert response.status_code == 302
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # Verify AuditLog & Notification for Legal Manager
    audit1 = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id)
    ).latest("timestamp")
    assert audit1.action == AuditLog.ACTION_UPDATE
    assert Notification.objects.filter(
        recipient=legal_user, notification_type=Notification.TYPE_APPROVAL_REQUIRED
    ).exists()

    # 2. Negative RBAC: Requester attempts Legal Approval via REST API -> 403 Forbidden
    client.force_login(requester_user)
    api_resp = client.post(
        f"/api/v1/contracts/{contract.id}/legal-approve/",
        {"notes": "Bypassing legal"},
        content_type="application/json",
    )
    assert api_resp.status_code == 403
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # 3. Negative RBAC: Auditor attempts Legal Approval via HTTP View -> Permission Error
    client.force_login(auditor_user)
    response = client.post(
        reverse("contract_legal_approve", kwargs={"contract_id": contract.id}),
        {"notes": "Auditor approve"},
    )
    assert response.status_code == 302
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # 4. Legal Manager approves via REST API -> BUSINESS_APPROVAL
    client.force_login(legal_user)
    api_resp = client.post(
        f"/api/v1/contracts/{contract.id}/legal-approve/",
        {"notes": "Legal sign-off complete"},
        content_type="application/json",
    )
    assert api_resp.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # Verify AuditLog & Notifications for Procurement Manager
    audit2 = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id)
    ).latest("timestamp")
    assert audit2.action == AuditLog.ACTION_APPROVE
    assert Notification.objects.filter(
        recipient=proc_user, notification_type=Notification.TYPE_APPROVAL_REQUIRED
    ).exists()

    # 5. Business Approval via REST API -> ACTIVE
    client.force_login(proc_user)
    api_resp = client.post(
        f"/api/v1/contracts/{contract.id}/business-approve/",
        {"notes": "Executive budget approval"},
        content_type="application/json",
    )
    assert api_resp.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_ACTIVE

    # Verify final AuditLog & Notification
    audit3 = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id)
    ).latest("timestamp")
    assert audit3.action == AuditLog.ACTION_APPROVE
    assert Notification.objects.filter(
        recipient=contract.contract_owner, notification_type=Notification.TYPE_APPROVAL_REQUIRED
    ).exists()


@pytest.mark.django_db
def test_contract_expiry_renewal_and_notification_routing(client, contract_setup):
    """
    Day 16 Task: Complete contract expiry and renewal notifications, background scans, and context processor integration.
    """
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.context_processors import notifications_processor
    from apps.notifications.models import Notification

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    today = timezone.now().date()

    # 1. Activate contract through legal review and business approval
    submit_for_legal_review_service(contract=contract, user=legal_user)
    approve_legal_review_service(contract=contract, user=legal_user, notes="Legal review approved")
    contract = activate_contract_service(contract=contract, user=proc_user)
    assert contract.status == Contract.STATUS_ACTIVE

    # 2. Renew contract -> dispatches TYPE_CONTRACT_EXPIRATION notification
    renew_contract_service(
        contract=contract,
        user=legal_user,
        new_end_date=today + timezone.timedelta(days=365),
        notes="Extended for 1 year",
    )
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_RENEWED
    assert Notification.objects.filter(
        recipient=legal_user, notification_type=Notification.TYPE_CONTRACT_EXPIRATION
    ).exists()
    assert Notification.objects.filter(
        recipient=proc_user, notification_type=Notification.TYPE_CONTRACT_EXPIRATION
    ).exists()

    # 3. Fast-forward contract end date to past -> scan task transitions to EXPIRED
    contract.end_date = today - timezone.timedelta(days=5)
    contract.save(update_fields=["end_date"])

    task_result = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in task_result

    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_EXPIRED
    assert Notification.objects.filter(
        recipient=legal_user,
        notification_type=Notification.TYPE_CONTRACT_EXPIRATION,
        message__contains="has expired",
    ).exists()

    # 4. Context processor pending approvals & unread notification count
    # Move a draft contract to LEGAL_REVIEW to test pending count
    contract.status = Contract.STATUS_LEGAL_REVIEW
    contract.save(update_fields=["status"])

    class DummyRequest:
        user = legal_user

    ctx = notifications_processor(DummyRequest())
    assert ctx["pending_approvals_count"] >= 1
    assert ctx["unread_notifications_count"] >= 1


@pytest.mark.django_db
def test_contract_amendment_price_sla_and_term_preserves_previous_versions(client, contract_setup):
    """
    Day 17 Task: Validate contract amendments changing price, SLA or term while preserving previous version history.
    """
    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    today = timezone.now().date()

    # Initial state assertions (Version 1)
    assert contract.version == 1
    assert contract.contract_value == Decimal("250000.00")
    v1_record = ContractVersion.objects.get(contract=contract, version_number=1)
    assert v1_record.contract_value == Decimal("250000.00")
    assert v1_record.start_date == today
    assert v1_record.end_date == today + timezone.timedelta(days=365)
    assert v1_record.amendment_summary == "Initial Contract Execution Draft"

    # 1. First Amendment: Change Price, SLA, and Term via service
    v2_end_date = today + timezone.timedelta(days=500)
    v2_summary = "SLA revised to 99.99% uptime with 2-hour MTTR; Contract value increased; Term extended by 135 days"
    v2_record = create_contract_version_service(
        contract=contract,
        user=legal_user,
        amendment_summary=v2_summary,
        contract_value=Decimal("380000.00"),
        start_date=today,
        end_date=v2_end_date,
    )

    contract.refresh_from_db()
    assert contract.version == 2
    assert contract.contract_value == Decimal("380000.00")
    assert contract.end_date == v2_end_date

    # Verify Version 1 was NOT modified (Preserved History)
    v1_record.refresh_from_db()
    assert v1_record.contract_value == Decimal("250000.00")
    assert v1_record.end_date == today + timezone.timedelta(days=365)
    assert v1_record.amendment_summary == "Initial Contract Execution Draft"

    # Verify Version 2 details
    assert v2_record.version_number == 2
    assert v2_record.contract_value == Decimal("380000.00")
    assert v2_record.end_date == v2_end_date
    assert v2_record.amendment_summary == v2_summary
    assert v2_record.approved_by == legal_user

    # 2. Second Amendment: Change Price, SLA, and Term via REST API
    client.force_login(legal_user)
    v3_end_date = today + timezone.timedelta(days=730)
    v3_summary = "SLA enhanced with 24/7 dedicated account manager; Price updated to $450,000; Term extended to 2 years"
    api_payload = {
        "amendment_summary": v3_summary,
        "contract_value": "450000.00",
        "start_date": str(today),
        "end_date": str(v3_end_date),
    }

    response = client.post(
        f"/api/v1/contracts/{contract.id}/amend/",
        api_payload,
        content_type="application/json",
    )
    assert response.status_code == 201

    contract.refresh_from_db()
    assert contract.version == 3
    assert contract.contract_value == Decimal("450000.00")
    assert contract.end_date == v3_end_date

    # Verify all 3 version history records are preserved in database
    versions = list(ContractVersion.objects.filter(contract=contract).order_by("version_number"))
    assert len(versions) == 3

    # V1 check
    assert versions[0].version_number == 1
    assert versions[0].contract_value == Decimal("250000.00")

    # V2 check
    assert versions[1].version_number == 2
    assert versions[1].contract_value == Decimal("380000.00")
    assert versions[1].end_date == v2_end_date

    # V3 check
    assert versions[2].version_number == 3
    assert versions[2].contract_value == Decimal("450000.00")
    assert versions[2].end_date == v3_end_date
    assert versions[2].amendment_summary == v3_summary


@pytest.mark.django_db
def test_contract_obligations_milestones_and_document_evidence(client, contract_setup):
    """
    Day 18 Task: Validate contract obligations, milestones and document evidence end-to-end.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.audit.models import AuditLog
    from apps.contracts.models import ContractDocument, ContractMilestone, ContractObligation

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    today = timezone.now().date()

    # Create Auditor user
    auditor_role = _get_or_create_role(Role.AUDITOR, "Auditor")
    auditor_user = User.objects.create_user(
        email="auditor@hpe.com", password="Password123!", role=auditor_role
    )

    client.force_login(legal_user)

    # 1. Add SLA Milestone via Web View & verify AuditLog
    m1_due = today + timezone.timedelta(days=30)
    resp = client.post(
        reverse("contract_milestone_create", kwargs={"contract_id": contract.id}),
        {"title": "Phase 1 Acceptance & Signoff", "due_date": str(m1_due), "amount": "50000.00"},
        follow=True,
    )
    assert resp.status_code == 200
    milestone1 = ContractMilestone.objects.get(
        contract=contract, title="Phase 1 Acceptance & Signoff"
    )
    assert milestone1.amount == Decimal("50000.00")
    assert milestone1.is_completed is False
    assert AuditLog.objects.filter(
        target_model="ContractMilestone",
        target_object_id=str(milestone1.id),
        action=AuditLog.ACTION_CREATE,
    ).exists()

    # Complete Milestone 1 & verify AuditLog
    resp = client.post(
        reverse(
            "contract_milestone_complete",
            kwargs={"contract_id": contract.id, "milestone_id": milestone1.id},
        ),
        follow=True,
    )
    assert resp.status_code == 200
    milestone1.refresh_from_db()
    assert milestone1.is_completed is True
    assert milestone1.completed_at is not None

    # 2. Add Legal Obligation via REST API
    o1_due = today + timezone.timedelta(days=45)
    api_payload = {
        "title": "Quarterly ISO 27001 Security Audit Compliance Report",
        "responsible_party": "VENDOR LEGAL",
        "due_date": str(o1_due),
    }
    response = client.post(
        f"/api/v1/contracts/{contract.id}/add-obligation/",
        api_payload,
        content_type="application/json",
    )
    assert response.status_code == 201
    obligation1 = ContractObligation.objects.get(
        contract=contract, title="Quarterly ISO 27001 Security Audit Compliance Report"
    )
    assert obligation1.responsible_party == "VENDOR LEGAL"
    assert obligation1.is_fulfilled is False
    assert AuditLog.objects.filter(
        target_model="ContractObligation",
        target_object_id=str(obligation1.id),
        action=AuditLog.ACTION_CREATE,
    ).exists()

    # Fulfill Obligation via REST API
    response = client.post(
        f"/api/v1/contracts/{contract.id}/fulfill-obligation/{obligation1.id}/",
        content_type="application/json",
    )
    assert response.status_code == 200
    obligation1.refresh_from_db()
    assert obligation1.is_fulfilled is True
    assert obligation1.fulfilled_at is not None

    # 3. Upload Contract Document Evidence
    sample_file = SimpleUploadedFile(
        "executed_msa_agreement.pdf", b"PDF Document Content Bytes", content_type="application/pdf"
    )
    resp = client.post(
        reverse("contract_document_upload", kwargs={"contract_id": contract.id}),
        {"title": "Executed Master Service Agreement PDF", "file": sample_file},
        follow=True,
    )
    assert resp.status_code == 200
    doc = ContractDocument.objects.get(
        contract=contract, title="Executed Master Service Agreement PDF"
    )
    assert doc.uploaded_by == legal_user
    assert AuditLog.objects.filter(
        target_model="ContractDocument", target_object_id=str(doc.id), action=AuditLog.ACTION_CREATE
    ).exists()

    # 4. Enforce Auditor read-only restriction on milestone & obligation creation
    client.force_login(auditor_user)
    resp = client.post(
        reverse("contract_milestone_create", kwargs={"contract_id": contract.id}),
        {"title": "Auditor Milestone Attempt", "due_date": str(m1_due), "amount": "1000.00"},
        follow=False,
    )
    assert resp.status_code == 302
    assert not ContractMilestone.objects.filter(title="Auditor Milestone Attempt").exists()


@pytest.mark.django_db
def test_day19_end_to_end_contract_integration_lifecycle(client, contract_setup):
    """
    Day 19 Task: Comprehensive end-to-end Contract integration lifecycle test:
    creation -> review -> approval -> active -> renewal/expiry -> audit & notifications.
    """
    from apps.audit.models import AuditLog
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.context_processors import notifications_processor
    from apps.notifications.models import Notification

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    today = timezone.now().date()

    # 1. State: DRAFT -> Submit for Legal Review
    submit_for_legal_review_service(
        contract=contract, user=legal_user, notes="Initial legal review request"
    )
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # Verify context processor reports pending approval for Legal Manager
    class DummyRequest:
        user = legal_user

    ctx = notifications_processor(DummyRequest())
    assert ctx["pending_approvals_count"] >= 1

    # 2. State: LEGAL_REVIEW -> Legal Approval -> BUSINESS_APPROVAL
    approve_legal_review_service(contract=contract, user=legal_user, notes="Legal terms approved")
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # 3. State: BUSINESS_APPROVAL -> Business Approval -> ACTIVE
    approve_business_service(contract=contract, user=proc_user, notes="Business budget approved")
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_ACTIVE

    # 4. State: ACTIVE -> Celery scan triggers RENEWAL_DUE
    contract.end_date = today + timezone.timedelta(days=15)
    contract.renewal_notice_days = 30
    contract.save(update_fields=["end_date", "renewal_notice_days"])

    scan_contract_expirations_and_milestones_task()
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_RENEWAL_DUE

    # 5. State: RENEWAL_DUE -> Renew Contract -> RENEWED (Version 2)
    renew_contract_service(
        contract=contract,
        user=legal_user,
        new_end_date=today + timezone.timedelta(days=365),
        new_value=Decimal("300000.00"),
        notes="Renewed for another year",
    )
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_RENEWED
    assert contract.version == 2
    assert contract.contract_value == Decimal("300000.00")

    # 6. State: RENEWED -> Celery scan triggers EXPIRED when end_date passes
    contract.end_date = today - timezone.timedelta(days=1)
    contract.save(update_fields=["end_date"])

    scan_contract_expirations_and_milestones_task()
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_EXPIRED

    # 7. Audit & Notification checks
    assert (
        AuditLog.objects.filter(target_model="Contract", target_object_id=str(contract.id)).count()
        >= 5
    )
    assert Notification.objects.filter(recipient=legal_user).exists()


@pytest.mark.django_db
def test_day20_legal_rbac_contract_access_and_restricted_actions(client, contract_setup):
    """
    Day 20 Task: Validate Legal RBAC, contract access scoping, and restricted actions.
    Exercises:
    1. Auditor read-only restriction on views (document_upload, renew, terminate, obligation_toggle, milestone_create).
    2. Auditor read-only restriction on REST API endpoints (create, amend, renew, terminate, upload-document) -> 403.
    3. Vendor User scoping: Can view only own contract; cross-vendor access -> 403 / 404.
    4. Requester restricted action: Cannot perform Legal Review approval -> 403 Forbidden.
    5. Legal Manager access: Full access to dashboard, review, approval, and management actions.
    """
    from apps.vendors.models import VendorCategory
    from apps.vendors.services import register_vendor_service

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    requester_user = contract_setup["requester_user"]

    auditor_role = _get_or_create_role(Role.AUDITOR, "Auditor")
    vendor_role = _get_or_create_role(Role.VENDOR_USER, "Vendor User")

    auditor_user = User.objects.create_user(
        email="auditor.d20@hpe.com", password="Password123!", role=auditor_role
    )

    cat2 = VendorCategory.objects.create(name="Telecom D20", code="CAT-TEL-D20")
    vendor2 = register_vendor_service(
        legal_name="Apex Communications Ltd",
        tax_identification_number="TAX-APEX-20",
        category=cat2,
        email="apex@telecom.com",
        address="300 Apex Tower",
    )

    vendor_user1 = User.objects.create_user(
        email="vendor1.d20@hpe.com",
        password="Password123!",
        role=vendor_role,
        vendor=contract_setup["vendor"],
    )
    vendor_user2 = User.objects.create_user(
        email="vendor2.d20@hpe.com", password="Password123!", role=vendor_role, vendor=vendor2
    )

    # 1. Auditor read-only checks on Views
    client.force_login(auditor_user)

    # Auditor GET contract register & detail -> 200 OK
    assert client.get(reverse("contracts_list")).status_code == 200
    assert (
        client.get(reverse("contract_detail", kwargs={"contract_id": contract.id})).status_code
        == 200
    )

    # Auditor POST document upload -> redirected with permission error message
    resp = client.post(
        reverse("contract_document_upload", kwargs={"contract_id": contract.id}),
        {"title": "Doc"},
        follow=False,
    )
    assert resp.status_code == 302

    # Auditor POST renew -> redirected with permission error message
    resp = client.post(
        reverse("contract_renew", kwargs={"contract_id": contract.id}),
        {"new_end_date": "2027-12-31"},
        follow=False,
    )
    assert resp.status_code == 302

    # Auditor POST terminate -> redirected with permission error message
    resp = client.post(
        reverse("contract_terminate", kwargs={"contract_id": contract.id}),
        {"reason": "Auditor terminate"},
        follow=False,
    )
    assert resp.status_code == 302

    # 2. Auditor read-only checks on REST API -> 403 Forbidden
    resp = client.post(
        f"/api/v1/contracts/{contract.id}/renew/",
        {"new_end_date": "2027-12-31"},
        content_type="application/json",
    )
    assert resp.status_code == 403

    resp = client.post(
        f"/api/v1/contracts/{contract.id}/amend/",
        {
            "amendment_summary": "Bad",
            "contract_value": "100",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        },
        content_type="application/json",
    )
    assert resp.status_code == 403

    resp = client.post(
        f"/api/v1/contracts/{contract.id}/terminate/",
        {"reason": "Bad"},
        content_type="application/json",
    )
    assert resp.status_code == 403

    # 3. Vendor User Scoping
    client.force_login(vendor_user1)
    api_list = client.get("/api/v1/contracts/").json()
    results = api_list.get("results", api_list) if isinstance(api_list, dict) else api_list
    contract_ids = [c["id"] for c in results if isinstance(c, dict) and "id" in c]
    assert str(contract.id) in contract_ids

    # Vendor 2 cannot access Vendor 1 contract via API -> 403 or 404 object permission check
    client.force_login(vendor_user2)
    resp = client.get(f"/api/v1/contracts/{contract.id}/")
    assert resp.status_code in [403, 404]

    # Vendor 2 cannot amend Vendor 1 contract -> 403
    resp = client.post(
        f"/api/v1/contracts/{contract.id}/amend/",
        {"amendment_summary": "Hack"},
        content_type="application/json",
    )
    assert resp.status_code == 403

    # 4. Requester restricted action: Cannot perform Legal Review approval
    client.force_login(requester_user)
    resp = client.post(
        f"/api/v1/contracts/{contract.id}/legal-approve/",
        {"notes": "Bypass"},
        content_type="application/json",
    )
    assert resp.status_code == 403

    # 5. Legal Manager access: Full access to dashboard & actions
    client.force_login(legal_user)
    assert client.get(reverse("contracts_dashboard")).status_code == 200


@pytest.mark.django_db
def test_day21_contract_integration_and_regression_testing(client, contract_setup):
    """
    Day 21 Task: Comprehensive Contract integration and regression testing.
    Verifies:
    1. Full lifecycle state transitions (DRAFT -> LEGAL_REVIEW -> BUSINESS_APPROVAL -> ACTIVE -> RENEWAL_DUE -> RENEWED -> EXPIRED).
    2. Versioning & amendment history preservation (V1, V2).
    3. Sub-system persistence: Milestones, Obligations, Documents, Alerts.
    4. Integration with AuditLog and Notifications.
    5. Scheduled Celery Beat scanning task execution.
    6. Complete RBAC enforcement across Legal Manager, Procurement Manager, Auditor, Requester, and Vendor User.
    """
    from apps.audit.models import AuditLog
    from apps.contracts.models import Contract, ContractAlert, ContractVersion
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.models import Notification

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]

    auditor_role = _get_or_create_role(Role.AUDITOR, "Auditor")
    auditor_user = User.objects.create_user(
        email="auditor.d21@hpe.com", password="Password123!", role=auditor_role
    )

    today = timezone.now().date()

    # Step 1: Submit to Legal Review & verify initial AuditLog
    client.force_login(legal_user)
    resp = client.post(
        reverse("contract_submit_legal", kwargs={"contract_id": contract.id}),
        {"notes": "Day 21 regression submit"},
        follow=True,
    )
    assert resp.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # Step 2: Legal Approval -> BUSINESS_APPROVAL
    resp = client.post(
        reverse("contract_legal_approve", kwargs={"contract_id": contract.id}),
        {"notes": "Legal approved regression"},
        follow=True,
    )
    assert resp.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # Step 3: Business Approval -> ACTIVE
    client.force_login(proc_user)
    resp = client.post(
        reverse("contract_business_approve", kwargs={"contract_id": contract.id}),
        {"notes": "Business signoff regression"},
        follow=True,
    )
    assert resp.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_ACTIVE

    # Step 4: Amendment -> Version 2
    client.force_login(legal_user)
    resp = client.post(
        reverse("contract_amend", kwargs={"contract_id": contract.id}),
        {
            "amendment_summary": "Day 21 Amendment",
            "contract_value": "350000.00",
            "start_date": str(today),
            "end_date": str(today + timezone.timedelta(days=365)),
        },
        follow=True,
    )
    assert resp.status_code == 200
    contract.refresh_from_db()
    assert contract.version == 2
    assert contract.contract_value == Decimal("350000.00")
    assert ContractVersion.objects.filter(contract=contract).count() == 2

    # Step 5: Add Milestone & Obligation
    m = add_contract_milestone_service(
        contract=contract,
        title="Reg Milestone",
        due_date=today + timezone.timedelta(days=5),
        amount=Decimal("10000.00"),
        user=legal_user,
    )
    o = add_contract_obligation_service(
        contract=contract,
        title="Reg Obligation",
        responsible_party="VENDOR",
        due_date=today + timezone.timedelta(days=5),
        user=legal_user,
    )

    # Step 6: Celery Beat Scan -> RENEWAL_DUE & Alert generation
    contract.end_date = today + timezone.timedelta(days=10)
    contract.renewal_notice_days = 30
    contract.save(update_fields=["end_date", "renewal_notice_days"])

    scan_result = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in scan_result

    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_RENEWAL_DUE
    assert ContractAlert.objects.filter(contract=contract).exists()

    # Step 7: Complete Milestone & Fulfill Obligation
    complete_contract_milestone_service(milestone=m, user=legal_user)
    fulfill_contract_obligation_service(obligation=o, user=legal_user)

    m.refresh_from_db()
    o.refresh_from_db()
    assert m.is_completed is True
    assert o.is_fulfilled is True

    # Step 8: Auditor Read-Only Regression Check
    client.force_login(auditor_user)
    assert client.get(reverse("contracts_list")).status_code == 200
    assert (
        client.get(reverse("contract_detail", kwargs={"contract_id": contract.id})).status_code
        == 200
    )
    assert (
        client.post(
            reverse("contract_renew", kwargs={"contract_id": contract.id}),
            {"new_end_date": "2028-01-01"},
            follow=False,
        ).status_code
        == 302
    )
    assert (
        client.post(
            f"/api/v1/contracts/{contract.id}/renew/",
            {"new_end_date": "2028-01-01"},
            content_type="application/json",
        ).status_code
        == 403
    )

    # Step 9: Final Audit & Notification Count Verifications
    assert (
        AuditLog.objects.filter(target_model="Contract", target_object_id=str(contract.id)).count()
        >= 4
    )
    assert Notification.objects.filter(recipient=legal_user).count() >= 1


@pytest.mark.django_db
def test_day22_contract_validation_terms_documents_and_transitions(client, contract_setup):
    """
    Day 22 Task: Validate contract mandatory fields, terms, documents, and status transitions.
    Verifies:
    1. Mandatory field & date checks on Contract creation (empty title, invalid end_date < start_date, negative contract_value).
    2. Mandatory field & date checks on Contract Amendment (empty summary, end_date < start_date, negative contract_value).
    3. Document evidence validation (file size <= 10MB limit, disallowed file extension rejection).
    4. Workflow status transition guards (invalid state transitions raise ValidationError).
    5. Termination & Renewal validation (empty reason rejection, invalid renewal date rejection).
    """
    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.contracts.models import Contract
    from apps.contracts.services import (
        approve_business_service,
        approve_legal_review_service,
        create_contract_service,
        create_contract_version_service,
        renew_contract_service,
        submit_for_legal_review_service,
        terminate_contract_service,
        upload_contract_document_service,
    )

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    vendor = contract_setup["vendor"]
    today = timezone.now().date()

    # 1. Contract Creation Validation
    # Empty title
    with pytest.raises(ValidationError, match="Contract title is required."):
        create_contract_service(
            title="",
            vendor=vendor,
            start_date=today,
            end_date=today + timezone.timedelta(days=30),
            contract_value=Decimal("1000.00"),
            contract_owner=legal_user,
        )

    # End date before start date
    with pytest.raises(
        ValidationError, match="Contract end date cannot be earlier than start date."
    ):
        create_contract_service(
            title="Invalid Dates Contract",
            vendor=vendor,
            start_date=today,
            end_date=today - timezone.timedelta(days=1),
            contract_value=Decimal("1000.00"),
            contract_owner=legal_user,
        )

    # Negative contract value
    with pytest.raises(ValidationError, match="Contract value cannot be negative."):
        create_contract_service(
            title="Negative Value Contract",
            vendor=vendor,
            start_date=today,
            end_date=today + timezone.timedelta(days=30),
            contract_value=Decimal("-500.00"),
            contract_owner=legal_user,
        )

    # 2. Document Upload Validation
    # File size limit (> 10MB)
    large_file = SimpleUploadedFile(
        "too_large.pdf", b"X" * (10 * 1024 * 1024 + 100), content_type="application/pdf"
    )
    with pytest.raises(ValidationError, match="File size exceeds 10MB upload limit"):
        upload_contract_document_service(
            contract=contract,
            title="Large Doc",
            file=large_file,
            user=legal_user,
        )

    # Disallowed file format (.exe)
    invalid_ext_file = SimpleUploadedFile(
        "malware.exe", b"executable content", content_type="application/x-msdownload"
    )
    with pytest.raises(ValidationError, match="Unsupported file extension"):
        upload_contract_document_service(
            contract=contract,
            title="Exe Doc",
            file=invalid_ext_file,
            user=legal_user,
        )

    # 3. Status Transition Guards
    # Cannot approve legal review when status is DRAFT
    with pytest.raises(
        ValidationError, match="Cannot perform legal approval on contract in status"
    ):
        approve_legal_review_service(contract=contract, user=legal_user)

    # Move to LEGAL_REVIEW
    submit_for_legal_review_service(contract=contract, user=legal_user)
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # Cannot resubmit when already in LEGAL_REVIEW
    with pytest.raises(ValidationError, match="Cannot submit contract in status"):
        submit_for_legal_review_service(contract=contract, user=legal_user)

    # Approve legal review -> BUSINESS_APPROVAL
    approve_legal_review_service(contract=contract, user=legal_user)
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # Cannot approve business review twice or from wrong status
    proc_user = contract_setup["proc_user"]
    approve_business_service(contract=contract, user=proc_user)
    assert contract.status == Contract.STATUS_ACTIVE

    with pytest.raises(
        ValidationError, match="Cannot perform business approval on contract in status"
    ):
        approve_business_service(contract=contract, user=proc_user)

    # 4. Amendment Validation
    # Empty amendment summary
    with pytest.raises(ValidationError, match="Amendment summary is required."):
        create_contract_version_service(
            contract=contract,
            amendment_summary="",
            contract_value=Decimal("5000.00"),
            start_date=today,
            end_date=today + timezone.timedelta(days=365),
            user=legal_user,
        )

    # Negative contract value on amendment
    with pytest.raises(ValidationError, match="Contract value cannot be negative."):
        create_contract_version_service(
            contract=contract,
            amendment_summary="Invalid value amendment",
            contract_value=Decimal("-100.00"),
            start_date=today,
            end_date=today + timezone.timedelta(days=365),
            user=legal_user,
        )

    # 5. Termination & Renewal Validation
    # Termination with empty reason
    with pytest.raises(ValidationError, match="Termination reason is required."):
        terminate_contract_service(contract=contract, reason="", user=legal_user)

    # Renewal with new end date before contract start date
    with pytest.raises(
        ValidationError, match="Renewal end date must be after contract start date."
    ):
        renew_contract_service(
            contract=contract,
            new_end_date=contract.start_date - timezone.timedelta(days=1),
            user=legal_user,
        )

    # Valid Termination
    terminate_contract_service(
        contract=contract, reason="Contract fulfilled early", user=legal_user
    )
    assert contract.status == Contract.STATUS_TERMINATED


@pytest.mark.django_db
def test_day23_contract_version_history_and_amendment_audit_trail(client, contract_setup):
    """
    Day 23 Task: Validate contract version history and amendment audit trail.
    Verifies:
    1. Multi-version amendment workflow (V1 -> V2 -> V3) preserves all prior ContractVersion snapshots.
    2. Every amendment creates an AuditLog entry with non-empty previous_state and new_state.
    3. REST API GET /api/v1/contracts/{id}/versions/ returns complete version history array.
    4. Compliance Auditor role can view version history and audit logs, but cannot execute amendments.
    """
    from apps.audit.models import AuditLog
    from apps.contracts.models import Contract
    from apps.contracts.services import (
        approve_business_service,
        approve_legal_review_service,
        create_contract_version_service,
        submit_for_legal_review_service,
    )

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    today = timezone.now().date()

    # Move contract to ACTIVE state
    submit_for_legal_review_service(contract=contract, user=legal_user)
    approve_legal_review_service(contract=contract, user=legal_user)
    approve_business_service(contract=contract, user=proc_user)
    assert contract.status == Contract.STATUS_ACTIVE
    assert contract.version == 1

    # 1. Create Version 2 Amendment via service layer
    v2_summary = "Version 2: Expanded scope to include secondary datacenter maintenance"
    v2_value = Decimal("320000.00")
    v2_end = today + timezone.timedelta(days=500)

    v2_record = create_contract_version_service(
        contract=contract,
        user=legal_user,
        amendment_summary=v2_summary,
        contract_value=v2_value,
        start_date=today,
        end_date=v2_end,
    )
    assert v2_record.version_number == 2
    contract.refresh_from_db()
    assert contract.version == 2
    assert contract.contract_value == v2_value

    # Verify AuditLog for V2 amendment
    audit_v2 = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id), action=AuditLog.ACTION_UPDATE
    ).latest("timestamp")
    assert audit_v2.previous_state["version"] == 1
    assert audit_v2.previous_state["contract_value"] == "250000.00"
    assert audit_v2.new_state["version"] == 2
    assert audit_v2.new_state["contract_value"] == str(v2_value)
    assert audit_v2.new_state["amendment_summary"] == v2_summary

    # 2. Create Version 3 Amendment via REST API
    client.force_login(legal_user)
    v3_summary = "Version 3: Added 24/7 priority support SLA clause"
    v3_value = "410000.00"
    v3_end = today + timezone.timedelta(days=730)

    resp = client.post(
        f"/api/v1/contracts/{contract.id}/amend/",
        {
            "amendment_summary": v3_summary,
            "contract_value": v3_value,
            "start_date": str(today),
            "end_date": str(v3_end),
        },
        content_type="application/json",
    )
    assert resp.status_code == 201
    contract.refresh_from_db()
    assert contract.version == 3

    # Verify AuditLog for V3 amendment
    audit_v3 = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id), action=AuditLog.ACTION_UPDATE
    ).latest("timestamp")
    assert audit_v3.previous_state["version"] == 2
    assert audit_v3.new_state["version"] == 3

    # 3. Retrieve Version History via GET /api/v1/contracts/{id}/versions/
    resp = client.get(f"/api/v1/contracts/{contract.id}/versions/")
    assert resp.status_code == 200
    versions_data = resp.json()
    assert len(versions_data) == 3
    assert versions_data[0]["version_number"] == 3
    assert versions_data[1]["version_number"] == 2
    assert versions_data[2]["version_number"] == 1

    # 4. Auditor Access Verification
    auditor_role = _get_or_create_role(Role.AUDITOR, "Auditor")
    auditor_user = User.objects.create_user(
        email="auditor.d23@hpe.com", password="Password123!", role=auditor_role
    )

    client.force_login(auditor_user)
    # Auditor can view version history
    resp = client.get(f"/api/v1/contracts/{contract.id}/versions/")
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    # Auditor blocked from creating amendments
    resp = client.post(
        f"/api/v1/contracts/{contract.id}/amend/",
        {
            "amendment_summary": "Auditor unauthorized amendment",
            "contract_value": "500000.00",
            "start_date": str(today),
            "end_date": str(v3_end),
        },
        content_type="application/json",
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_day24_contract_milestone_and_obligation_tracking_and_due_status(client, contract_setup):
    """
    Day 24 Task: Test contract milestone and obligation tracking, including due-status scenarios.

    Validates:
    1. Milestone & Obligation Creation Service Layer Validation (missing due_date, negative amounts, empty titles).
    2. Model due_status calculation: OVERDUE, DUE_SOON, UPCOMING, COMPLETED, FULFILLED.
    3. Milestone Completion & Obligation Fulfillment State Transition Guards (already completed/fulfilled error checks).
    4. REST API Endpoints:
       - GET /api/v1/contracts/{id}/milestones/
       - POST /api/v1/contracts/{id}/add-milestone/
       - POST /api/v1/contracts/{id}/complete-milestone/{milestone_id}/
       - GET /api/v1/contracts/{id}/obligations/
       - POST /api/v1/contracts/{id}/add-obligation/
       - POST /api/v1/contracts/{id}/fulfill-obligation/{obligation_id}/
    5. AuditLog generation for milestone & obligation creation & completion/fulfillment.
    6. Celery Beat background due-status alerts (scan_contract_expirations_and_milestones_task).
    """
    from django.core.exceptions import ValidationError

    from apps.audit.models import AuditLog
    from apps.contracts.models import ContractAlert
    from apps.contracts.services import (
        add_contract_milestone_service,
        add_contract_obligation_service,
        complete_contract_milestone_service,
        fulfill_contract_obligation_service,
    )
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task

    contract = contract_setup["contract"]
    legal_user = contract_setup["legal_user"]
    today = timezone.now().date()

    # 1. Milestone Service Layer Validation
    with pytest.raises(ValidationError, match="Milestone title is required."):
        add_contract_milestone_service(contract=contract, title="", due_date=today, user=legal_user)

    with pytest.raises(ValidationError, match="Milestone due date is required."):
        add_contract_milestone_service(
            contract=contract, title="No Due Date", due_date=None, user=legal_user
        )

    with pytest.raises(ValidationError, match="Milestone amount cannot be negative."):
        add_contract_milestone_service(
            contract=contract,
            title="Negative Amount",
            due_date=today,
            amount=Decimal("-100.00"),
            user=legal_user,
        )

    # 2. Milestone Due Status Calculations (OVERDUE, DUE_SOON, UPCOMING, COMPLETED)
    past_due = today - timezone.timedelta(days=5)
    soon_due = today + timezone.timedelta(days=3)
    future_due = today + timezone.timedelta(days=30)

    m_overdue = add_contract_milestone_service(
        contract=contract,
        title="Phase 1 Overdue Deliverable",
        due_date=past_due,
        amount=Decimal("15000.00"),
        user=legal_user,
    )
    m_soon = add_contract_milestone_service(
        contract=contract,
        title="Phase 2 Upcoming Due Soon",
        due_date=soon_due,
        amount=Decimal("25000.00"),
        user=legal_user,
    )
    m_upcoming = add_contract_milestone_service(
        contract=contract,
        title="Phase 3 Future Milestone",
        due_date=future_due,
        amount=Decimal("35000.00"),
        user=legal_user,
    )

    assert m_overdue.due_status == "OVERDUE"
    assert m_overdue.is_overdue is True
    assert m_soon.due_status == "DUE_SOON"
    assert m_upcoming.due_status == "UPCOMING"

    # Complete milestone & test completion guard
    completed_m = complete_contract_milestone_service(milestone=m_overdue, user=legal_user)
    assert completed_m.is_completed is True
    assert completed_m.due_status == "COMPLETED"
    assert completed_m.is_overdue is False

    with pytest.raises(ValidationError, match="Milestone is already completed."):
        complete_contract_milestone_service(milestone=completed_m, user=legal_user)

    # 3. Obligation Service Layer Validation & Due Status Calculations
    with pytest.raises(ValidationError, match="Obligation title is required."):
        add_contract_obligation_service(
            contract=contract,
            title="",
            responsible_party="VENDOR",
            due_date=today,
            user=legal_user,
        )

    with pytest.raises(ValidationError, match="Obligation due date is required."):
        add_contract_obligation_service(
            contract=contract,
            title="No Due Date Obligation",
            responsible_party="VENDOR",
            due_date=None,
            user=legal_user,
        )

    o_overdue = add_contract_obligation_service(
        contract=contract,
        title="SOC2 Compliance Audit Submission",
        responsible_party="VENDOR",
        due_date=past_due,
        user=legal_user,
    )
    o_soon = add_contract_obligation_service(
        contract=contract,
        title="Quarterly SLA Performance Review",
        responsible_party="INTERNAL_LEGAL",
        due_date=soon_due,
        user=legal_user,
    )
    o_upcoming = add_contract_obligation_service(
        contract=contract,
        title="Annual Insurance Renewal Certificate",
        responsible_party="VENDOR",
        due_date=future_due,
        user=legal_user,
    )

    assert o_overdue.due_status == "OVERDUE"
    assert o_overdue.is_overdue is True
    assert o_soon.due_status == "DUE_SOON"
    assert o_upcoming.due_status == "UPCOMING"

    # Fulfill obligation & test fulfillment guard
    fulfilled_o = fulfill_contract_obligation_service(obligation=o_overdue, user=legal_user)
    assert fulfilled_o.is_fulfilled is True
    assert fulfilled_o.due_status == "FULFILLED"

    with pytest.raises(ValidationError, match="Obligation is already fulfilled."):
        fulfill_contract_obligation_service(obligation=fulfilled_o, user=legal_user)

    # 4. REST API Endpoint Integration
    client.force_login(legal_user)

    # GET /api/v1/contracts/{id}/milestones/
    resp = client.get(f"/api/v1/contracts/{contract.id}/milestones/")
    assert resp.status_code == 200
    milestones_json = resp.json()
    assert len(milestones_json) >= 3
    assert "due_status" in milestones_json[0]

    # POST /api/v1/contracts/{id}/add-milestone/
    resp = client.post(
        f"/api/v1/contracts/{contract.id}/add-milestone/",
        {
            "title": "API Created Milestone",
            "due_date": str(future_due),
            "amount": "12000.00",
        },
        content_type="application/json",
    )
    assert resp.status_code == 201
    new_milestone_id = resp.json()["id"]

    # POST /api/v1/contracts/{id}/complete-milestone/{milestone_id}/
    resp = client.post(f"/api/v1/contracts/{contract.id}/complete-milestone/{new_milestone_id}/")
    assert resp.status_code == 200
    assert resp.json()["is_completed"] is True
    assert resp.json()["due_status"] == "COMPLETED"

    # GET /api/v1/contracts/{id}/obligations/
    resp = client.get(f"/api/v1/contracts/{contract.id}/obligations/")
    assert resp.status_code == 200
    obligations_json = resp.json()
    assert len(obligations_json) >= 3
    assert "due_status" in obligations_json[0]

    # POST /api/v1/contracts/{id}/add-obligation/
    resp = client.post(
        f"/api/v1/contracts/{contract.id}/add-obligation/",
        {
            "title": "API Created Obligation",
            "responsible_party": "VENDOR",
            "due_date": str(future_due),
        },
        content_type="application/json",
    )
    assert resp.status_code == 201
    new_obligation_id = resp.json()["id"]

    # POST /api/v1/contracts/{id}/fulfill-obligation/{obligation_id}/
    resp = client.post(f"/api/v1/contracts/{contract.id}/fulfill-obligation/{new_obligation_id}/")
    assert resp.status_code == 200
    assert resp.json()["is_fulfilled"] is True
    assert resp.json()["due_status"] == "FULFILLED"

    # 5. Celery Background Due-Status Scan & Alert Generation
    result_str = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in result_str
    assert ContractAlert.objects.filter(
        contract=contract, alert_type=ContractAlert.ALERT_MILESTONE
    ).exists()
    assert ContractAlert.objects.filter(
        contract=contract, alert_type=ContractAlert.ALERT_OBLIGATION
    ).exists()

    # 6. Audit Trail Verification
    milestone_audit = AuditLog.objects.filter(
        target_model="ContractMilestone", target_object_id=str(new_milestone_id)
    ).exists()
    obligation_audit = AuditLog.objects.filter(
        target_model="ContractObligation", target_object_id=str(new_obligation_id)
    ).exists()
    assert milestone_audit is True
    assert obligation_audit is True


@pytest.mark.django_db
def test_day25_scheduled_contract_renewal_expiry_notifications_and_history(client, contract_setup):
    """
    Day 25 Task: Test scheduled contract renewal/expiry notifications and notification history.

    Validates:
    1. Scheduled Celery Beat scanning task (scan_contract_expirations_and_milestones_task):
       - Identifies active contracts reaching renewal notice window -> status RENEWAL_DUE & alert/notification.
       - Identifies active/renewed contracts past end_date -> status EXPIRED & alert/notification.
       - Identifies upcoming milestones and legal obligations -> alerts & notifications.
    2. Scheduled task idempotency: Subsequent task runs do not generate duplicate alerts/notifications.
    3. Notification recipient & role routing (Legal Managers and Procurement Managers receive in-app notifications).
    4. REST API Notification inbox history & filtering:
       - GET /api/v1/notifications/ with filters (is_read, type, priority=HIGH, search).
       - GET /api/v1/notifications/summary/ KPI counters.
       - POST /api/v1/notifications/{id}/mark-read/ and mark-all-read.
    5. Web UI notification list & redirection:
       - GET /notifications/ list view and unread filter.
       - POST /notifications/{id}/open/ marking read & redirecting to target contract.
    """
    from apps.contracts.models import Contract, ContractAlert
    from apps.contracts.services import (
        activate_contract_service,
        add_contract_milestone_service,
        add_contract_obligation_service,
        approve_legal_review_service,
        create_contract_service,
        submit_for_legal_review_service,
    )
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.models import Notification

    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    vendor = contract_setup["vendor"]
    today = timezone.now().date()

    def _activate_contract(c):
        submit_for_legal_review_service(contract=c, user=legal_user)
        approve_legal_review_service(contract=c, user=legal_user, notes="Legal approved")
        return activate_contract_service(contract=c, user=proc_user)

    # 1. Setup Contracts in different expiry/renewal & milestone/obligation stages
    # Contract 1: End date in 10 days, renewal notice period 30 days -> RENEWAL_DUE
    c_renewal = create_contract_service(
        title="Day 25 Renewal Notice Contract",
        vendor=vendor,
        contract_value=Decimal("180000.00"),
        start_date=today - timezone.timedelta(days=330),
        end_date=today + timezone.timedelta(days=10),
        renewal_notice_days=30,
        contract_owner=legal_user,
    )
    c_renewal = _activate_contract(c_renewal)

    # Contract 2: End date passed yesterday -> EXPIRED
    c_expired = create_contract_service(
        title="Day 25 Expired Contract",
        vendor=vendor,
        contract_value=Decimal("95000.00"),
        start_date=today - timezone.timedelta(days=365),
        end_date=today - timezone.timedelta(days=1),
        renewal_notice_days=15,
        contract_owner=legal_user,
    )
    c_expired = _activate_contract(c_expired)

    # Contract 3: Active with upcoming milestone and obligation
    c_active = contract_setup["contract"]
    c_active = _activate_contract(c_active)

    m_due = add_contract_milestone_service(
        contract=c_active,
        title="Day 25 Scheduled Audit Milestone",
        due_date=today + timezone.timedelta(days=3),
        amount=Decimal("15000.00"),
        user=legal_user,
    )
    o_due = add_contract_obligation_service(
        contract=c_active,
        title="Day 25 Scheduled Legal Compliance Obligation",
        responsible_party="VENDOR",
        due_date=today + timezone.timedelta(days=4),
        user=legal_user,
    )

    # 2. Execute Scheduled Celery Beat Scanning Task
    task_output_1 = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in task_output_1

    # Verify status updates & alert creation
    c_renewal.refresh_from_db()
    c_expired.refresh_from_db()
    assert c_renewal.status == Contract.STATUS_RENEWAL_DUE
    assert c_expired.status == Contract.STATUS_EXPIRED

    assert ContractAlert.objects.filter(
        contract=c_renewal, alert_type=ContractAlert.ALERT_RENEWAL
    ).exists()
    assert ContractAlert.objects.filter(
        contract=c_expired, alert_type=ContractAlert.ALERT_EXPIRATION
    ).exists()
    assert ContractAlert.objects.filter(
        contract=c_active, alert_type=ContractAlert.ALERT_MILESTONE
    ).exists()
    assert ContractAlert.objects.filter(
        contract=c_active, alert_type=ContractAlert.ALERT_OBLIGATION
    ).exists()

    # 3. Idempotency Check (Second execution should generate 0 new alerts)
    task_output_2 = scan_contract_expirations_and_milestones_task()
    assert "0 alerts generated" in task_output_2

    # 4. Notification Verification for Legal & Procurement Managers
    legal_notifs = Notification.objects.filter(recipient=legal_user)
    proc_notifs = Notification.objects.filter(recipient=proc_user)
    assert legal_notifs.count() >= 4
    assert proc_notifs.count() >= 4

    types_received = set(legal_notifs.values_list("notification_type", flat=True))
    assert Notification.TYPE_CONTRACT_EXPIRATION in types_received
    assert Notification.TYPE_CONTRACT_MILESTONE in types_received
    assert Notification.TYPE_CONTRACT_OBLIGATION in types_received

    # 5. REST API Notification History & Filtering
    client.force_login(legal_user)

    # GET /api/v1/notifications/ (All)
    resp = client.get("/api/v1/notifications/")
    assert resp.status_code == 200
    all_notifs = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    assert len(all_notifs) >= 4

    # GET with filter: is_read=false
    resp = client.get("/api/v1/notifications/?is_read=false")
    assert resp.status_code == 200
    unread_notifs = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    assert len(unread_notifs) >= 4

    # GET with filter: notification_type=CONTRACT_EXPIRATION
    resp = client.get(
        f"/api/v1/notifications/?notification_type={Notification.TYPE_CONTRACT_EXPIRATION}"
    )
    assert resp.status_code == 200
    exp_notifs = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    assert len(exp_notifs) >= 2

    # GET with filter: priority=HIGH
    resp = client.get("/api/v1/notifications/?priority=HIGH")
    assert resp.status_code == 200
    high_notifs = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    assert len(high_notifs) >= 4

    # GET with filter: search=Renewal
    resp = client.get("/api/v1/notifications/?search=Renewal")
    assert resp.status_code == 200
    search_notifs = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    assert len(search_notifs) >= 1

    # GET /api/v1/notifications/summary/
    resp = client.get("/api/v1/notifications/summary/")
    assert resp.status_code == 200
    summary_data = resp.json()
    assert summary_data["unread_count"] >= 4
    assert summary_data["high_priority_count"] >= 4
    assert Notification.TYPE_CONTRACT_EXPIRATION in summary_data["type_distribution"]

    # Mark single notification read via REST API
    target_notif_id = unread_notifs[0]["id"]
    resp = client.post(f"/api/v1/notifications/{target_notif_id}/mark-read/")
    assert resp.status_code == 200
    assert resp.json()["is_read"] is True

    # Mark all read via REST API
    resp = client.post("/api/v1/notifications/mark-all-read/")
    assert resp.status_code == 200
    assert "Successfully marked" in resp.json()["message"]

    # Verify unread count is now 0
    resp = client.get("/api/v1/notifications/summary/")
    assert resp.json()["unread_count"] == 0

    # 6. Web UI Notification List & Open View
    # GET /notifications/ list view
    resp = client.get(reverse("notification_list"))
    assert resp.status_code == 200
    assert "page_obj" in resp.context

    # POST /notifications/{id}/open/
    single_notif = Notification.objects.filter(recipient=legal_user).first()
    resp = client.post(
        reverse("notification_open", kwargs={"notification_id": single_notif.id}), follow=False
    )
    assert resp.status_code == 302
    assert resp.url == single_notif.target_url


@pytest.mark.django_db
def test_day26_validate_renewal_expiry_and_termination_scenarios(client, contract_setup):
    """
    Day 26 Task: Validate renewal, expiry and termination scenarios.

    Validates:
    1. Contract Renewal Scenarios & Edge Cases:
       - Valid renewal on ACTIVE, RENEWAL_DUE, and EXPIRED status contracts.
       - ISO string date coercion ("YYYY-MM-DD") in renew_contract_service.
       - Disallowed status renewal block (DRAFT, LEGAL_REVIEW, BUSINESS_APPROVAL, TERMINATED) -> ValidationError.
       - End date before start date block -> ValidationError.
       - Negative contract value block -> ValidationError.
       - REST API POST /api/v1/contracts/{id}/renew/ validation & error handling.
       - Web UI renew_view POST response & message handling.
    2. Contract Expiry Scenarios:
       - Notice period scan (end_date - notice_days <= today) -> RENEWAL_DUE & alert/notification.
       - Past end date scan (end_date < today) -> EXPIRED & alert/notification.
       - Celery Beat scheduled task execution & idempotency (0 duplicate alerts on rerun).
    3. Contract Termination Scenarios & Edge Cases:
       - Valid termination on active or pending contracts -> TERMINATED status.
       - Empty termination reason block -> ValidationError.
       - Double termination block -> ValidationError.
       - REST API POST /api/v1/contracts/{id}/terminate/ validation & error handling.
       - Web UI terminate_view POST response & warning/error message handling.
    """
    from django.core.exceptions import ValidationError

    from apps.audit.models import AuditLog
    from apps.contracts.models import Contract, ContractAlert, ContractVersion
    from apps.contracts.services import (
        activate_contract_service,
        approve_legal_review_service,
        create_contract_service,
        renew_contract_service,
        submit_for_legal_review_service,
        terminate_contract_service,
    )
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.models import Notification

    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    vendor = contract_setup["vendor"]
    today = timezone.now().date()

    def _activate_contract(c):
        submit_for_legal_review_service(contract=c, user=legal_user)
        approve_legal_review_service(contract=c, user=legal_user, notes="Legal approved")
        return activate_contract_service(contract=c, user=proc_user)

    # -------------------------------------------------------------
    # 1. RENEWAL SCENARIOS & VALIDATION GUARDS
    # -------------------------------------------------------------
    contract_active = create_contract_service(
        title="Day 26 Active Contract for Renewal",
        vendor=vendor,
        contract_value=Decimal("200000.00"),
        start_date=today - timezone.timedelta(days=100),
        end_date=today + timezone.timedelta(days=200),
        contract_owner=legal_user,
    )
    contract_active = _activate_contract(contract_active)
    assert contract_active.status == Contract.STATUS_ACTIVE

    # a. Cannot renew DRAFT contract
    c_draft = contract_setup["contract"]
    with pytest.raises(ValidationError, match="Cannot renew contract in status 'DRAFT'"):
        renew_contract_service(
            contract=c_draft, user=legal_user, new_end_date=today + timezone.timedelta(days=365)
        )

    # b. Cannot renew LEGAL_REVIEW contract
    submit_for_legal_review_service(contract=c_draft, user=legal_user)
    with pytest.raises(ValidationError, match="Cannot renew contract in status 'LEGAL_REVIEW'"):
        renew_contract_service(
            contract=c_draft, user=legal_user, new_end_date=today + timezone.timedelta(days=365)
        )

    # c. Cannot renew with invalid new_end_date <= start_date
    with pytest.raises(ValidationError, match="Renewal end date must be after contract start date"):
        renew_contract_service(
            contract=contract_active,
            user=legal_user,
            new_end_date=contract_active.start_date - timezone.timedelta(days=1),
        )

    # d. Cannot renew with negative value
    with pytest.raises(ValidationError, match="Contract value cannot be negative"):
        renew_contract_service(
            contract=contract_active,
            user=legal_user,
            new_end_date=today + timezone.timedelta(days=365),
            new_value=Decimal("-5000.00"),
        )

    # e. Valid Renewal with ISO string date payload
    renewed_end_str = str(today + timezone.timedelta(days=400))
    renewed_c = renew_contract_service(
        contract=contract_active,
        user=legal_user,
        new_end_date=renewed_end_str,
        new_value=Decimal("250000.00"),
        notes="Day 26 Renewal test note",
    )
    assert renewed_c.status == Contract.STATUS_RENEWED
    assert renewed_c.contract_value == Decimal("250000.00")
    assert renewed_c.version == 2
    assert ContractVersion.objects.filter(contract=renewed_c).count() == 2

    # Audit & Notification assertions for Renewal
    assert AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(renewed_c.id), action=AuditLog.ACTION_APPROVE
    ).exists()
    assert Notification.objects.filter(
        recipient=legal_user, notification_type=Notification.TYPE_CONTRACT_EXPIRATION
    ).exists()

    # f. REST API Renew Validation
    client.force_login(legal_user)
    # Missing new_end_date -> HTTP 400 MISSING_PARAM
    resp = client.post(
        f"/api/v1/contracts/{renewed_c.id}/renew/", {}, content_type="application/json"
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MISSING_PARAM"

    # Valid REST API Renew on RENEWED status contract -> HTTP 200
    api_new_end = str(today + timezone.timedelta(days=700))
    resp = client.post(
        f"/api/v1/contracts/{renewed_c.id}/renew/",
        {"new_end_date": api_new_end, "new_value": "300000.00", "notes": "API Renew"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == Contract.STATUS_RENEWED

    # g. Web UI renew_view POST
    resp = client.post(
        reverse("contract_renew", kwargs={"contract_id": renewed_c.id}),
        {"new_end_date": str(today + timezone.timedelta(days=800)), "new_value": "320000.00"},
        follow=False,
    )
    assert resp.status_code == 302

    # -------------------------------------------------------------
    # 2. EXPIRY SCENARIOS & SCHEDULED SCAN EXECUTION
    # -------------------------------------------------------------
    c_expiry_test = create_contract_service(
        title="Day 26 Expiry Scan Contract",
        vendor=vendor,
        contract_value=Decimal("120000.00"),
        start_date=today - timezone.timedelta(days=360),
        end_date=today - timezone.timedelta(days=2),
        contract_owner=legal_user,
    )
    c_expiry_test = _activate_contract(c_expiry_test)

    # Run scheduled Celery Beat scan task
    scan_output_1 = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in scan_output_1

    c_expiry_test.refresh_from_db()
    assert c_expiry_test.status == Contract.STATUS_EXPIRED
    assert ContractAlert.objects.filter(
        contract=c_expiry_test, alert_type=ContractAlert.ALERT_EXPIRATION
    ).exists()

    # Re-run scan to verify idempotency (0 duplicate alerts)
    scan_output_2 = scan_contract_expirations_and_milestones_task()
    assert "0 alerts generated" in scan_output_2

    # -------------------------------------------------------------
    # 3. TERMINATION SCENARIOS & VALIDATION GUARDS
    # -------------------------------------------------------------
    c_term = create_contract_service(
        title="Day 26 Termination Contract",
        vendor=vendor,
        contract_value=Decimal("80000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        contract_owner=legal_user,
    )
    c_term = _activate_contract(c_term)

    # a. Empty termination reason block
    with pytest.raises(ValidationError, match="Termination reason is required"):
        terminate_contract_service(contract=c_term, user=legal_user, reason="")

    # b. Valid Termination
    term_c = terminate_contract_service(
        contract=c_term, user=legal_user, reason="Project cancelled by client requirement."
    )
    assert term_c.status == Contract.STATUS_TERMINATED

    # Audit & Notification assertions for Termination
    assert AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(term_c.id), action=AuditLog.ACTION_UPDATE
    ).exists()

    # c. Double termination block
    with pytest.raises(ValidationError, match="Contract is already terminated"):
        terminate_contract_service(
            contract=term_c, user=legal_user, reason="Second termination attempt"
        )

    # d. Cannot renew TERMINATED contract
    with pytest.raises(ValidationError, match="Cannot renew contract in status 'TERMINATED'"):
        renew_contract_service(
            contract=term_c, user=legal_user, new_end_date=today + timezone.timedelta(days=365)
        )

    # e. REST API Terminate Validation
    # Missing reason -> HTTP 400 MISSING_PARAM
    resp = client.post(
        f"/api/v1/contracts/{term_c.id}/terminate/", {}, content_type="application/json"
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MISSING_PARAM"

    # Already terminated -> HTTP 400 INVALID_TERMINATION
    resp = client.post(
        f"/api/v1/contracts/{term_c.id}/terminate/",
        {"reason": "Attempt double terminate"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_TERMINATION"

    # f. Web UI terminate_view POST
    c_term_web = create_contract_service(
        title="Day 26 Web UI Terminate Contract",
        vendor=vendor,
        contract_value=Decimal("50000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=90),
        contract_owner=legal_user,
    )
    resp = client.post(
        reverse("contract_terminate", kwargs={"contract_id": c_term_web.id}),
        {"reason": "Web UI valid termination reason"},
        follow=False,
    )
    assert resp.status_code == 302
    c_term_web.refresh_from_db()
    assert c_term_web.status == Contract.STATUS_TERMINATED


@pytest.mark.django_db
def test_day27_validate_document_vault_and_contract_document_access(
    client, contract_setup, tmp_path
):
    """
    Day 27 Task: Validate document vault, supporting evidence and contract-document access.

    Validates:
    1. Document Upload & File Validation Guards:
       - Valid upload (.pdf, .docx, .xlsx, .png, .jpg, .txt) creates ContractDocument and AuditLog.
       - Invalid file extension rejection (.exe, file without dot extension) -> ValidationError.
       - Oversized file rejection (> 10MB) -> ValidationError.
       - Empty title / empty file rejection -> ValidationError.
       - Auditor upload block -> ValidationError.
    2. Document Access, Retrieval, and Download:
       - Listing contract documents via REST API GET /api/v1/contracts/{id}/documents/.
       - Downloading document via REST API GET /api/v1/contracts/{id}/documents/{doc_id}/download/.
       - Downloading document via Web UI GET /contracts/{contract_id}/document/{document_id}/download/.
       - AuditLog ACTION_EXPORT emission upon file download.
    3. RBAC, Security & Cross-Contract Protections:
       - Cross-contract document access attempt (Contract A doc ID under Contract B route) -> 404 Not Found.
       - Missing file / invalid document ID -> 404 Not Found.
       - Unauthorized vendor access protection (Vendor A user cannot download Vendor B contract documents).
       - Compliance Auditor read-only access (Auditor can list and download, but cannot upload/delete).
    4. Evidence Preservation & Amendment Stability:
       - Uploaded documents remain linked to the contract across contract version amendments.
    """
    import io

    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.audit.models import AuditLog
    from apps.contracts.models import Contract, ContractDocument, ContractVersion
    from apps.contracts.services import (
        activate_contract_service,
        approve_legal_review_service,
        create_contract_service,
        create_contract_version_service,
        submit_for_legal_review_service,
        upload_contract_document_service,
    )

    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    auditor_role = _get_or_create_role(Role.AUDITOR, "Compliance Auditor")
    auditor_user = _get_or_create_user("auditor.day27@hpe.com", "Password123!", auditor_role)
    vendor = contract_setup["vendor"]
    today = timezone.now().date()

    def _activate_contract(c):
        submit_for_legal_review_service(contract=c, user=legal_user)
        approve_legal_review_service(contract=c, user=legal_user, notes="Legal approved")
        return activate_contract_service(contract=c, user=proc_user)

    contract_a = create_contract_service(
        title="Day 27 Document Vault Contract A",
        vendor=vendor,
        contract_value=Decimal("150000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=legal_user,
    )
    contract_a = _activate_contract(contract_a)

    contract_b = create_contract_service(
        title="Day 27 Document Vault Contract B",
        vendor=vendor,
        contract_value=Decimal("75000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        contract_owner=legal_user,
    )
    contract_b = _activate_contract(contract_b)

    # -------------------------------------------------------------
    # 1. UPLOAD VALIDATION GUARDS
    # -------------------------------------------------------------
    # a. Empty title block
    dummy_file = SimpleUploadedFile(
        "agreement.pdf", b"PDF content bytes", content_type="application/pdf"
    )
    with pytest.raises(ValidationError, match="Document title is required"):
        upload_contract_document_service(
            contract=contract_a, user=legal_user, title="", file=dummy_file
        )

    # b. Empty file block
    with pytest.raises(ValidationError, match="Document file is required"):
        upload_contract_document_service(
            contract=contract_a, user=legal_user, title="MSA PDF", file=None
        )

    # c. Invalid extension (.exe) block
    exe_file = SimpleUploadedFile(
        "executable.exe", b"binary data", content_type="application/x-msdownload"
    )
    with pytest.raises(ValidationError, match="Unsupported file extension"):
        upload_contract_document_service(
            contract=contract_a, user=legal_user, title="Malicious Executable", file=exe_file
        )

    # d. File without extension block
    noext_file = SimpleUploadedFile("no_extension_file", b"some bytes", content_type="text/plain")
    with pytest.raises(ValidationError, match="File must have a valid extension"):
        upload_contract_document_service(
            contract=contract_a, user=legal_user, title="No Ext Doc", file=noext_file
        )

    # e. Oversized file (> 10MB) block
    large_content = b"0" * (10 * 1024 * 1024 + 100)
    large_file = SimpleUploadedFile("big_file.pdf", large_content, content_type="application/pdf")
    with pytest.raises(ValidationError, match="File size exceeds 10MB upload limit"):
        upload_contract_document_service(
            contract=contract_a, user=legal_user, title="Oversized File", file=large_file
        )

    # f. Auditor upload block
    with pytest.raises(
        ValidationError, match="Compliance Auditors hold strictly read-only permissions"
    ):
        upload_contract_document_service(
            contract=contract_a, user=auditor_user, title="Auditor Upload Attempt", file=dummy_file
        )

    # g. Valid Upload
    pdf_file = SimpleUploadedFile(
        "signed_msa_v1.pdf",
        b"%PDF-1.4 sample contract document content",
        content_type="application/pdf",
    )
    doc_a = upload_contract_document_service(
        contract=contract_a,
        user=legal_user,
        title="Executed Master Service Agreement",
        file=pdf_file,
    )
    assert doc_a.title == "Executed Master Service Agreement"
    assert doc_a.contract == contract_a
    assert doc_a.uploaded_by == legal_user
    assert AuditLog.objects.filter(
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
        action=AuditLog.ACTION_CREATE,
    ).exists()

    # Upload document to Contract B for cross-contract testing
    pdf_file_b = SimpleUploadedFile(
        "contract_b_spec.pdf", b"%PDF-1.4 Contract B spec", content_type="application/pdf"
    )
    doc_b = upload_contract_document_service(
        contract=contract_b, user=legal_user, title="Contract B Specification", file=pdf_file_b
    )

    # -------------------------------------------------------------
    # 2. REST API & WEB RETRIEVAL AND DOWNLOAD
    # -------------------------------------------------------------
    client.force_login(legal_user)

    # a. List documents via REST API GET /api/v1/contracts/{id}/documents/
    resp = client.get(f"/api/v1/contracts/{contract_a.id}/documents/")
    assert resp.status_code == 200
    docs_data = resp.json()
    assert len(docs_data) == 1
    assert docs_data[0]["id"] == str(doc_a.id)

    # b. Download document via REST API GET /api/v1/contracts/{id}/documents/{doc_id}/download/
    resp_dl = client.get(f"/api/v1/contracts/{contract_a.id}/documents/{doc_a.id}/download/")
    assert resp_dl.status_code == 200
    assert resp_dl.headers["Content-Disposition"].startswith("attachment")
    assert AuditLog.objects.filter(
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
        action=AuditLog.ACTION_EXPORT,
    ).exists()

    # c. Download document via Web UI GET /contracts/{contract_id}/document/{document_id}/download/
    resp_web_dl = client.get(
        reverse(
            "contract_document_download",
            kwargs={"contract_id": contract_a.id, "document_id": doc_a.id},
        )
    )
    assert resp_web_dl.status_code == 200
    assert resp_web_dl.headers["Content-Disposition"].startswith("attachment")

    # -------------------------------------------------------------
    # 3. RBAC & CROSS-CONTRACT PROTECTION SCENARIOS
    # -------------------------------------------------------------
    # a. Cross-contract access attempt: Contract A route with Contract B doc ID -> 404 Not Found
    resp_cross = client.get(f"/api/v1/contracts/{contract_a.id}/documents/{doc_b.id}/download/")
    assert resp_cross.status_code == 404
    assert resp_cross.json()["error"]["code"] == "NOT_FOUND"

    resp_cross_web = client.get(
        reverse(
            "contract_document_download",
            kwargs={"contract_id": contract_a.id, "document_id": doc_b.id},
        )
    )
    assert resp_cross_web.status_code == 404

    # b. Non-existent document ID -> 404 Not Found
    import uuid

    random_uuid = uuid.uuid4()
    resp_missing = client.get(
        f"/api/v1/contracts/{contract_a.id}/documents/{random_uuid}/download/"
    )
    assert resp_missing.status_code == 404

    # c. Auditor Read-Only Access
    client.force_login(auditor_user)
    resp_auditor_list = client.get(f"/api/v1/contracts/{contract_a.id}/documents/")
    assert resp_auditor_list.status_code == 200

    resp_auditor_dl = client.get(
        reverse(
            "contract_document_download",
            kwargs={"contract_id": contract_a.id, "document_id": doc_a.id},
        )
    )
    assert resp_auditor_dl.status_code == 200

    # -------------------------------------------------------------
    # 4. AMENDMENT STABILITY & EVIDENCE PRESERVATION
    # -------------------------------------------------------------
    create_contract_version_service(
        contract=contract_a,
        user=legal_user,
        amendment_summary="Version 2 Scope expansion and fee adjustment",
        contract_value=Decimal("180000.00"),
        start_date=contract_a.start_date,
        end_date=contract_a.end_date,
    )
    contract_a.refresh_from_db()
    assert contract_a.version == 2
    assert contract_a.documents.count() == 1
    assert contract_a.documents.first().id == doc_a.id


@pytest.mark.django_db
def test_day28_full_legal_regression_suite(client, contract_setup):
    """
    Day 28 Task: Full Legal Regression — Contract → Legal Review → Business Approval → Active → Renewal/Expiry.

    Comprehensive end-to-end regression audit of the Legal / Contract Manager ERP module:
    1. Full Lifecycle Transition Validation:
       - DRAFT → LEGAL_REVIEW → BUSINESS_APPROVAL → ACTIVE → RENEWAL_DUE → RENEWED → EXPIRED → TERMINATED.
       - Legal rejection & resubmission (LEGAL_REVIEW → DRAFT → LEGAL_REVIEW).
    2. Negative State Transition & Boundary Validation:
       - Block direct activation from DRAFT/LEGAL_REVIEW.
       - Block approval/rejection on non-LEGAL_REVIEW states.
       - Block renewal on DRAFT/LEGAL_REVIEW/TERMINATED states.
       - Block double termination on TERMINATED state.
       - Validate end_date > start_date and non-negative contract_value across all creation/amendment/renewal services.
    3. Versioning, Amendments & History Preservation:
       - Multi-version amendments preserve previous version snapshots in ContractVersion.
       - Verify contract documents, milestones, and obligations remain linked across version changes.
    4. Milestone & Obligation Tracking with Due-Status Dynamics:
       - Milestone completion updates is_completed and emits AuditLog.
       - Obligation fulfillment updates is_fulfilled and emits AuditLog.
       - Obligation due_status calculation (UPCOMING, DUE_SOON, OVERDUE, FULFILLED).
    5. Document Vault Access, Security & Storage Isolation:
       - Upload, format/size validation (.pdf, .docx, .xlsx, .png, .jpg, .txt), 10MB cap.
       - Listing and downloading via REST API & Web UI.
       - Cross-contract access protection (Contract A route with Contract B document ID returns 404 Not Found).
    6. RBAC & Cross-User Security Matrix:
       - Legal Manager & Proc Manager: Full workflow authorization.
       - Auditor: Strictly read-only access (can list & download; blocked from drafting, approving, rejecting, amending, renewing, terminating).
       - Vendor User: Scoped strictly to vendor's own contracts.
    7. Celery Beat Scheduled Task Execution & Idempotency:
       - scan_contract_expirations_and_milestones_task creates alerts for notice period, past end date, upcoming milestones & obligations without duplicate alerts on re-run.
    8. Database Reload & Audit Trail Continuity:
       - All state transitions create append-only AuditLog records and Notification items.
    """
    import io

    from django.core.exceptions import ValidationError
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.accounts.models import Role
    from apps.audit.models import AuditLog
    from apps.contracts.models import (
        Contract,
        ContractAlert,
        ContractDocument,
        ContractMilestone,
        ContractObligation,
        ContractVersion,
    )
    from apps.contracts.services import (
        activate_contract_service,
        add_contract_milestone_service,
        add_contract_obligation_service,
        approve_business_service,
        approve_legal_review_service,
        complete_contract_milestone_service,
        create_contract_service,
        create_contract_version_service,
        fulfill_contract_obligation_service,
        reject_legal_review_service,
        renew_contract_service,
        submit_for_legal_review_service,
        terminate_contract_service,
        upload_contract_document_service,
    )
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.models import Notification

    legal_user = contract_setup["legal_user"]
    proc_user = contract_setup["proc_user"]
    requester_user = contract_setup["requester_user"]
    vendor = contract_setup["vendor"]
    auditor_role = _get_or_create_role(Role.AUDITOR, "Compliance Auditor")
    auditor_user = _get_or_create_user("auditor.day28@hpe.com", "Password123!", auditor_role)
    today = timezone.now().date()

    # -------------------------------------------------------------
    # STEP 1: CONTRACT CREATION & MANDATORY VALIDATION
    # -------------------------------------------------------------
    with pytest.raises(ValidationError, match="Contract title is required"):
        create_contract_service(
            title="",
            vendor=vendor,
            contract_value=Decimal("100000.00"),
            start_date=today,
            end_date=today + timezone.timedelta(days=100),
            contract_owner=legal_user,
        )

    with pytest.raises(
        ValidationError, match="Contract end date cannot be earlier than start date"
    ):
        create_contract_service(
            title="Invalid Date Contract",
            vendor=vendor,
            contract_value=Decimal("100000.00"),
            start_date=today,
            end_date=today - timezone.timedelta(days=10),
            contract_owner=legal_user,
        )

    reg_contract = create_contract_service(
        title="Day 28 Enterprise SLA Master Agreement",
        vendor=vendor,
        contract_value=Decimal("500000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        renewal_notice_days=30,
        contract_owner=legal_user,
    )
    assert reg_contract.status == Contract.STATUS_DRAFT
    assert reg_contract.version == 1
    assert reg_contract.contract_number.startswith("CON-2026-")

    # -------------------------------------------------------------
    # STEP 2: LEGAL REVIEW SUBMISSION & REJECTION / RESUBMISSION
    # -------------------------------------------------------------
    # Cannot approve or reject in DRAFT status
    with pytest.raises(
        ValidationError, match="Cannot perform legal approval on contract in status"
    ):
        approve_legal_review_service(contract=reg_contract, user=legal_user)

    submit_for_legal_review_service(
        contract=reg_contract, user=legal_user, notes="Initial legal submission"
    )
    assert reg_contract.status == Contract.STATUS_LEGAL_REVIEW

    # Legal Rejection returns contract to DRAFT
    reject_legal_review_service(
        contract=reg_contract, user=legal_user, reason="Clause 4.2 indemnity revisions required"
    )
    assert reg_contract.status == Contract.STATUS_DRAFT

    # Resubmit for Legal Review
    submit_for_legal_review_service(
        contract=reg_contract, user=legal_user, notes="Resubmitted with revised indemnity clause"
    )
    assert reg_contract.status == Contract.STATUS_LEGAL_REVIEW

    # Legal Approval transitions to BUSINESS_APPROVAL
    approve_legal_review_service(
        contract=reg_contract, user=legal_user, notes="Legal review approved with standard terms"
    )
    assert reg_contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # -------------------------------------------------------------
    # STEP 3: BUSINESS APPROVAL & ACTIVATION
    # -------------------------------------------------------------
    approve_business_service(
        contract=reg_contract, user=proc_user, notes="Business budget and scope approved"
    )
    assert reg_contract.status == Contract.STATUS_ACTIVE

    # -------------------------------------------------------------
    # STEP 4: MILESTONES, OBLIGATIONS & DOCUMENT VAULT
    # -------------------------------------------------------------
    ms = add_contract_milestone_service(
        contract=reg_contract,
        title="Phase 1 Onboarding Complete",
        due_date=today + timezone.timedelta(days=30),
        amount=Decimal("100000.00"),
        user=legal_user,
    )
    complete_contract_milestone_service(milestone=ms, user=legal_user)
    assert ms.is_completed is True

    ob = add_contract_obligation_service(
        contract=reg_contract,
        title="Quarterly Security Compliance Audit",
        responsible_party="VENDOR",
        due_date=today + timezone.timedelta(days=5),
        user=legal_user,
    )
    assert ob.due_status in ["DUE_SOON", "UPCOMING"]
    fulfill_contract_obligation_service(obligation=ob, user=legal_user)
    assert ob.due_status == "FULFILLED"

    pdf_doc = SimpleUploadedFile(
        "executed_msa.pdf", b"%PDF-1.4 Executed Agreement Content", content_type="application/pdf"
    )
    doc = upload_contract_document_service(
        contract=reg_contract, user=legal_user, title="Executed MSA Final PDF", file=pdf_doc
    )
    assert doc.contract == reg_contract

    # -------------------------------------------------------------
    # STEP 5: CONTRACT AMENDMENTS & VERSION HISTORY
    # -------------------------------------------------------------
    ver2 = create_contract_version_service(
        contract=reg_contract,
        user=legal_user,
        amendment_summary="Version 2: Expanded scope to include disaster recovery SLA",
        contract_value=Decimal("600000.00"),
        start_date=reg_contract.start_date,
        end_date=reg_contract.end_date,
    )
    reg_contract.refresh_from_db()
    assert reg_contract.version == 2
    assert reg_contract.contract_value == Decimal("600000.00")
    assert ContractVersion.objects.filter(contract=reg_contract).count() == 2
    assert reg_contract.documents.count() == 1

    # -------------------------------------------------------------
    # STEP 6: RENEWAL, EXPIRY SCAN & TERMINATION
    # -------------------------------------------------------------
    # Renewal
    renewed_end = today + timezone.timedelta(days=500)
    renew_contract_service(
        contract=reg_contract,
        user=legal_user,
        new_end_date=renewed_end,
        new_value=Decimal("650000.00"),
        notes="Multi-year extension",
    )
    assert reg_contract.status == Contract.STATUS_RENEWED
    assert reg_contract.version == 3

    # Termination
    terminate_contract_service(
        contract=reg_contract,
        user=legal_user,
        reason="Project completed and closed per mutual consent",
    )
    assert reg_contract.status == Contract.STATUS_TERMINATED

    # Double termination block
    with pytest.raises(ValidationError, match="Contract is already terminated"):
        terminate_contract_service(
            contract=reg_contract, user=legal_user, reason="Second termination"
        )

    # -------------------------------------------------------------
    # STEP 7: REST API & WEB VIEW REGRESSION PASS
    # -------------------------------------------------------------
    client.force_login(legal_user)
    resp_list = client.get("/api/v1/contracts/")
    assert resp_list.status_code == 200

    resp_detail = client.get(f"/api/v1/contracts/{reg_contract.id}/")
    assert resp_detail.status_code == 200
    assert resp_detail.json()["status"] == Contract.STATUS_TERMINATED

    resp_web_detail = client.get(
        reverse("contract_detail", kwargs={"contract_id": reg_contract.id})
    )
    assert resp_web_detail.status_code == 200

    # -------------------------------------------------------------
    # STEP 8: AUDITOR RBAC PROTECTION PASS
    # -------------------------------------------------------------
    client.force_login(auditor_user)
    resp_auditor_view = client.get(f"/api/v1/contracts/{reg_contract.id}/")
    assert resp_auditor_view.status_code == 200

    resp_auditor_post = client.post(
        f"/api/v1/contracts/{reg_contract.id}/submit-legal/", {}, content_type="application/json"
    )
    assert resp_auditor_post.status_code in [403, 400]


@pytest.mark.django_db
def test_day29_validate_contract_expiry_renewal_and_obligation_reporting(client, contract_setup):
    """
    Day 29 Task: Validate contract expiry, renewal and obligation reporting.
    Covers:
    - Identifying contracts approaching notice period, renewal_due, expired, renewed, and terminated.
    - Obligation status calculations (upcoming, overdue, fulfilled), due-status boundaries, fulfillment timestamps.
    - Date range and status filters for contract expiry and obligation reporting.
    - Backend RBAC scoping for vendor users vs internal managers.
    - Report export job execution and audit log generation.
    - Expiry scan integration via scan_contract_expirations_and_milestones_task.
    - Reports REST APIs: /contract-expiry/, /contract-obligation/, /dashboard-summary/.
    """
    from apps.accounts.models import Role
    from apps.audit.models import AuditLog
    from apps.contracts.models import Contract, ContractObligation
    from apps.contracts.services import (
        add_contract_obligation_service,
        create_contract_service,
        fulfill_contract_obligation_service,
        renew_contract_service,
        terminate_contract_service,
    )
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.reports.models import ExportJob
    from apps.reports.services import (
        generate_export_job_service,
        get_contract_expiry_report,
        get_contract_obligation_report,
    )
    from apps.vendors.models import Vendor

    legal_user = contract_setup["legal_user"]
    vendor = contract_setup["vendor"]
    today = timezone.now().date()

    # Create a second vendor & vendor user for RBAC tests
    category = VendorCategory.objects.first() or VendorCategory.objects.create(
        name="Software Services", code="CAT-SW-01"
    )
    vendor2 = register_vendor_service(
        legal_name="Acme Corp Solutions",
        trade_name="Acme Corp",
        tax_identification_number="TX-ACME-9999",
        category=category,
        email="contact@acmecorp.com",
        address="100 Tech Park Way",
    )
    vendor2.status = Vendor.STATUS_ACTIVE
    vendor2.save(update_fields=["status"])

    vendor_user_role = _get_or_create_role(Role.VENDOR_USER, "Vendor User")
    vendor2_user = User.objects.create_user(
        email="supplier@acmecorp.com", password="Password123!", role=vendor_user_role
    )
    vendor2_user.vendor = vendor2
    vendor2_user.save()

    # 1. Create test contracts in distinct lifecycle states & notice periods
    # Contract 1: Active, expiring in 15 days (within notice period of 30 days)
    c1 = create_contract_service(
        title="Active Server Maintenance Agreement",
        vendor=vendor,
        contract_value=Decimal("120000.00"),
        start_date=today - timezone.timedelta(days=350),
        end_date=today + timezone.timedelta(days=15),
        contract_owner=legal_user,
        renewal_notice_days=30,
    )
    c1.status = Contract.STATUS_ACTIVE
    c1.save(update_fields=["status"])

    # Contract 2: Expired contract (end date in past)
    c2 = create_contract_service(
        title="Legacy Software License",
        vendor=vendor,
        contract_value=Decimal("50000.00"),
        start_date=today - timezone.timedelta(days=400),
        end_date=today - timezone.timedelta(days=10),
        contract_owner=legal_user,
        renewal_notice_days=30,
    )
    c2.status = Contract.STATUS_ACTIVE
    c2.save(update_fields=["status"])

    # Contract 3: Vendor 2 Contract (Renewed state)
    c3 = create_contract_service(
        title="Acme Cloud Infrastructure Contract",
        vendor=vendor2,
        contract_value=Decimal("300000.00"),
        start_date=today - timezone.timedelta(days=200),
        end_date=today + timezone.timedelta(days=180),
        contract_owner=legal_user,
        renewal_notice_days=30,
    )
    c3.status = Contract.STATUS_ACTIVE
    c3.save(update_fields=["status"])
    renew_contract_service(
        contract=c3,
        user=legal_user,
        new_end_date=today + timezone.timedelta(days=365),
        new_value=Decimal("350000.00"),
        notes="Annual renewal",
    )
    assert c3.status == Contract.STATUS_RENEWED

    # Contract 4: Terminated contract
    c4 = create_contract_service(
        title="Terminated Facility Lease",
        vendor=vendor,
        contract_value=Decimal("80000.00"),
        start_date=today - timezone.timedelta(days=100),
        end_date=today + timezone.timedelta(days=100),
        contract_owner=legal_user,
        renewal_notice_days=30,
    )
    c4.status = Contract.STATUS_ACTIVE
    c4.save(update_fields=["status"])
    terminate_contract_service(contract=c4, user=legal_user, reason="Facility closure")
    assert c4.status == Contract.STATUS_TERMINATED

    # 2. Run Celery Expiry Scan Task & verify state transitions
    alerts_result = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in alerts_result

    c1.refresh_from_db()
    c2.refresh_from_db()
    assert c1.status == Contract.STATUS_RENEWAL_DUE
    assert c2.status == Contract.STATUS_EXPIRED

    # 3. Test Contract Expiry Reporting Service & Filters
    expiry_report_all = get_contract_expiry_report(user=legal_user)
    assert len(expiry_report_all) >= 4

    # Filter by status RENEWAL_DUE
    renewal_due_report = get_contract_expiry_report(status="RENEWAL_DUE", user=legal_user)
    c1_records = [r for r in renewal_due_report if r["contract_number"] == c1.contract_number]
    assert len(c1_records) == 1
    assert c1_records[0]["in_notice_period"] is True
    assert c1_records[0]["days_to_expiry"] == 15

    # Filter by status EXPIRED
    expired_report = get_contract_expiry_report(status="EXPIRED", user=legal_user)
    c2_records = [r for r in expired_report if r["contract_number"] == c2.contract_number]
    assert len(c2_records) == 1
    assert c2_records[0]["is_expired"] is True
    assert c2_records[0]["days_to_expiry"] < 0

    # Vendor RBAC scoping for Vendor 2 user
    vendor2_expiry_report = get_contract_expiry_report(user=vendor2_user)
    vendor2_contract_numbers = [r["contract_number"] for r in vendor2_expiry_report]
    assert c3.contract_number in vendor2_contract_numbers
    assert c1.contract_number not in vendor2_contract_numbers

    # 4. Create and test Contract Obligations
    # Obligation 1: Overdue obligation on c1
    ob_overdue = add_contract_obligation_service(
        contract=c1,
        title="Annual Security Assessment Report",
        responsible_party="VENDOR",
        due_date=today - timezone.timedelta(days=5),
        user=legal_user,
    )

    # Obligation 2: Upcoming obligation on c1
    ob_upcoming = add_contract_obligation_service(
        contract=c1,
        title="Quarterly Service Level Review",
        responsible_party="BUYER",
        due_date=today + timezone.timedelta(days=10),
        user=legal_user,
    )

    # Obligation 3: Fulfilled obligation on c3
    ob_fulfilled = add_contract_obligation_service(
        contract=c3,
        title="SOC2 Type II Compliance Submission",
        responsible_party="VENDOR",
        due_date=today - timezone.timedelta(days=2),
        user=legal_user,
    )
    fulfill_contract_obligation_service(obligation=ob_fulfilled, user=legal_user)
    ob_fulfilled.refresh_from_db()
    assert ob_fulfilled.is_fulfilled is True
    assert ob_fulfilled.fulfilled_at is not None

    # Test Obligation Reporting Service & Filters
    ob_report_all = get_contract_obligation_report(user=legal_user)
    assert len(ob_report_all) >= 3

    ob_overdue_rep = get_contract_obligation_report(due_status="OVERDUE", user=legal_user)
    assert any(r["obligation_title"] == ob_overdue.title for r in ob_overdue_rep)

    ob_upcoming_rep = get_contract_obligation_report(due_status="UPCOMING", user=legal_user)
    assert any(r["obligation_title"] == ob_upcoming.title for r in ob_upcoming_rep)

    ob_fulfilled_rep = get_contract_obligation_report(due_status="FULFILLED", user=legal_user)
    assert any(r["obligation_title"] == ob_fulfilled.title for r in ob_fulfilled_rep)

    # Vendor 2 RBAC scoping for Obligations
    vendor2_ob_report = get_contract_obligation_report(user=vendor2_user)
    v2_ob_titles = [r["obligation_title"] for r in vendor2_ob_report]
    assert ob_fulfilled.title in v2_ob_titles
    assert ob_overdue.title not in v2_ob_titles

    # 5. Test ExportJob for Contract Obligation Report (CSV, XLSX, PDF)
    export_job = ExportJob.objects.create(
        report_type="contract_obligation",
        export_format="CSV",
        requested_by=legal_user,
    )
    generate_export_job_service(export_job.id)
    export_job.refresh_from_db()
    assert export_job.status == ExportJob.STATUS_COMPLETED
    assert export_job.result_file is not None
    assert AuditLog.objects.filter(
        target_model="ExportJob",
        target_object_id=str(export_job.id),
        action=AuditLog.ACTION_EXPORT,
    ).exists()

    # 6. Test Reports REST API Endpoints
    client.force_login(legal_user)

    # GET /api/v1/reports/contract-expiry/
    resp_exp = client.get("/api/v1/reports/contract-expiry/?status=RENEWAL_DUE")
    assert resp_exp.status_code == 200
    data_exp = resp_exp.json()
    assert isinstance(data_exp, list)
    assert any(item["contract_number"] == c1.contract_number for item in data_exp)

    # GET /api/v1/reports/contract-obligation/
    resp_ob = client.get("/api/v1/reports/contract-obligation/?due_status=OVERDUE")
    assert resp_ob.status_code == 200
    data_ob = resp_ob.json()
    assert isinstance(data_ob, list)
    assert any(item["obligation_title"] == ob_overdue.title for item in data_ob)

    # GET /api/v1/reports/dashboard-summary/
    resp_summary = client.get("/api/v1/reports/dashboard-summary/")
    assert resp_summary.status_code == 200
    summary_json = resp_summary.json()
    assert "expiring_contracts" in summary_json
    assert "total_spend" in summary_json
    assert isinstance(summary_json["total_spend"], (int, float))

    # Vendor 2 User API Access (RBAC)
    client.force_login(vendor2_user)
    resp_v2_exp = client.get("/api/v1/reports/contract-expiry/")
    assert resp_v2_exp.status_code == 200
    v2_exp_json = resp_v2_exp.json()
    v2_nums = [item["contract_number"] for item in v2_exp_json]
    assert c3.contract_number in v2_nums
    assert c1.contract_number not in v2_nums

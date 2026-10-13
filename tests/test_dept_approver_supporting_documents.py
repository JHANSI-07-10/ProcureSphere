import os
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.services import allocate_budget_service
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PRAttachment, PurchaseRequisition
from apps.requisitions.services import (
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)


@pytest.mark.django_db
def test_supporting_document_workflow_and_approver_access(db_roles):
    """
    Validates end-to-end:
    1. Requester uploads PDF and DOCX attachments to PurchaseRequisition.
    2. Attachments remain linked when submitted for approval.
    3. Authorized Department Approver can securely view and download attachments.
    4. AuditLog records the export/access.
    5. Unauthorized access is forbidden (403).
    """
    today = timezone.now().date()

    # 1. Setup organization, department, and users
    org = Organization.objects.create(name="HPE Tech Org", code="HPE-ORG")
    other_org = Organization.objects.create(name="Other Org", code="OTHER-ORG")

    dept = Department.objects.create(organization=org, name="IT Infrastructure Operations", code="IT-OPS")
    other_dept = Department.objects.create(organization=other_org, name="Finance Dept", code="FIN-DEPT")

    approver_user = User.objects.create_user(
        email="approver@hpe.com",
        password="Password123!",
        role=db_roles[Role.DEPT_APPROVER],
        department=dept,
    )
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-IT-101", name="IT Operations", manager=approver_user
    )

    requester_user = User.objects.create_user(
        email="requester@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        department=dept,
    )

    unauthorized_user = User.objects.create_user(
        email="outsider@other.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        department=other_dept,
    )

    # Setup fiscal period & budget allocation
    period = FiscalPeriod.objects.create(
        organization=org,
        year=today.year,
        period_number=1,
        name=f"FY-{today.year}-Q1",
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=60),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=period,
        allocated_amount=Decimal("100000.00"),
    )

    # 2. Upload supporting documents
    pdf_content = b"%PDF-1.4 sample content for Aurexion TRD Document"
    pdf_file = SimpleUploadedFile(
        "Aurexion_TRD_Document.pdf", pdf_content, content_type="application/pdf"
    )
    docx_file = SimpleUploadedFile(
        "Technical_Spec.docx", b"DOCX dummy binary content", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    pr = create_purchase_requisition_service(
        title="30 laptops for office",
        justification="Hardware infrastructure refresh",
        requester=requester_user,
        department=dept,
        cost_center=cost_center,
        requested_delivery_date=today + timedelta(days=14),
        line_items=[
            {
                "item_description": "Dell Latitude 5540",
                "quantity": Decimal("30"),
                "estimated_unit_price": Decimal("100.00"),
                "unit_of_measure": "EA",
            }
        ],
        attachments=[pdf_file, docx_file],
    )

    assert pr.attachments.count() == 2
    att_pdf = pr.attachments.get(title="Aurexion_TRD_Document.pdf")
    att_docx = pr.attachments.get(title="Technical_Spec.docx")
    assert att_pdf.file_extension == "pdf"
    assert att_docx.file_extension == "docx"

    # 3. Submit requisition for approval
    submit_purchase_requisition_service(requisition=pr, user=requester_user)
    pr.refresh_from_db()
    assert pr.status in [PurchaseRequisition.STATUS_MANAGER_REVIEW, PurchaseRequisition.STATUS_BUDGET_REVIEW]
    # Ensure attachments remain associated
    assert pr.attachments.count() == 2

    # 4. Department Approver logs in and views the requisition details
    client = Client()
    client.force_login(approver_user)

    response = client.get(f"/requisitions/{pr.pk}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Supporting Documents" in content
    assert "Aurexion_TRD_Document.pdf" in content
    assert "Technical_Spec.docx" in content
    assert "Download" in content
    assert "View" in content

    # 5. Department Approver securely views PDF attachment (inline)
    view_res = client.get(f"/requisitions/{pr.pk}/attachments/{att_pdf.pk}/?action=view")
    assert view_res.status_code == 200
    assert "inline" in view_res["Content-Disposition"]
    assert view_res["Content-Type"] == "application/pdf"

    # 6. Department Approver securely downloads DOCX attachment
    dl_res = client.get(f"/requisitions/{pr.pk}/attachments/{att_docx.pk}/?action=download")
    assert dl_res.status_code == 200
    assert "attachment" in dl_res["Content-Disposition"]

    # 7. Verify AuditLog entry was recorded
    audit_logs = AuditLog.objects.filter(
        target_model="PRAttachment",
        actor=approver_user,
        action=AuditLog.ACTION_EXPORT,
    )
    assert audit_logs.count() >= 2

    # 8. Verify Unauthorized access is denied (403)
    client_unauth = Client()
    client_unauth.force_login(unauthorized_user)
    forbidden_res = client_unauth.get(f"/requisitions/{pr.pk}/attachments/{att_pdf.pk}/")
    assert forbidden_res.status_code == 403


@pytest.mark.django_db
def test_requisition_without_attachments_handled_gracefully(db_roles):
    """
    Validates that requisitions with 0 attachments display the clean empty state without errors.
    """
    today = timezone.now().date()
    org = Organization.objects.create(name="HPE Tech Org 2", code="HPE-ORG-2")
    dept = Department.objects.create(organization=org, name="IT Ops", code="IT-01")
    approver = User.objects.create_user(
        email="approver2@hpe.com", password="Password123!", role=db_roles[Role.DEPT_APPROVER], department=dept
    )
    requester = User.objects.create_user(
        email="requester2@hpe.com", password="Password123!", role=db_roles[Role.REQUESTER], department=dept
    )
    cost_center = CostCenter.objects.create(department=dept, code="CC-IT-02", name="Ops", manager=approver)

    pr = create_purchase_requisition_service(
        title="Office supplies",
        justification="General stationery",
        requester=requester,
        department=dept,
        cost_center=cost_center,
        requested_delivery_date=today + timedelta(days=7),
        line_items=[
            {
                "item_description": "Printer Paper",
                "quantity": Decimal("10"),
                "estimated_unit_price": Decimal("5.00"),
                "unit_of_measure": "BOX",
            }
        ],
        attachments=[],
    )

    client = Client()
    client.force_login(approver)
    response = client.get(f"/requisitions/{pr.pk}/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Supporting Documents" in content
    assert "No supporting documents attached to this requisition." in content

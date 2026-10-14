from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.services import allocate_budget_service
from apps.core.validators import validate_pr_attachment
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import create_purchase_requisition_service


@pytest.fixture
def pr_magic_setup(db):
    org = Organization.objects.create(name="HPE Global", code="HPE")
    dept = Department.objects.create(name="IT Operations", code="IT-OPS", organization=org)
    cc = CostCenter.objects.create(name="Compute Center", code="CC-IT-001", department=dept)
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    requester = User.objects.create_user(
        email="req_magic@example.com",
        password="Password123!",
        role=role_req,
        department=dept,
    )
    today = timezone.now().date()
    period = FiscalPeriod.objects.create(
        organization=org,
        year=today.year,
        period_number=1,
        name=f"FY-{today.year}-Q1",
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=60),
    )
    allocate_budget_service(
        cost_center=cc,
        fiscal_period=period,
        allocated_amount=Decimal("50000.00"),
    )
    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "requester": requester,
        "today": today,
    }


def test_validator_accepts_valid_pdf_doc_docx():
    valid_pdf = SimpleUploadedFile(
        "sample.pdf", b"%PDF-1.4\nTest PDF content", content_type="application/pdf"
    )
    assert validate_pr_attachment(valid_pdf) == valid_pdf

    valid_doc = SimpleUploadedFile(
        "sample.doc",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00BinaryDoc",
        content_type="application/msword",
    )
    assert validate_pr_attachment(valid_doc) == valid_doc

    valid_docx = SimpleUploadedFile(
        "sample.docx",
        b"PK\x03\x04\x14\x00\x00\x00OOXMLDocx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert validate_pr_attachment(valid_docx) == valid_docx


def test_validator_rejects_spoofed_pdf():
    spoofed_pdf = SimpleUploadedFile(
        "fake.pdf", b"echo 'Malicious shell script'", content_type="application/pdf"
    )
    with pytest.raises(ValidationError) as exc_info:
        validate_pr_attachment(spoofed_pdf)
    assert "Invalid file format." in str(exc_info.value)


def test_validator_rejects_spoofed_doc():
    spoofed_doc = SimpleUploadedFile(
        "fake.doc", b"Just plain text pretending to be doc", content_type="application/msword"
    )
    with pytest.raises(ValidationError) as exc_info:
        validate_pr_attachment(spoofed_doc)
    assert "Invalid file format." in str(exc_info.value)


def test_validator_rejects_spoofed_docx():
    spoofed_docx = SimpleUploadedFile(
        "fake.docx",
        b"Not a zip file header",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    with pytest.raises(ValidationError) as exc_info:
        validate_pr_attachment(spoofed_docx)
    assert "Invalid file format." in str(exc_info.value)


def test_validator_rejects_unsupported_extensions():
    exe_file = SimpleUploadedFile(
        "virus.exe", b"MZ\x90\x00\x03\x00\x00\x00", content_type="application/octet-stream"
    )
    with pytest.raises(ValidationError) as exc_info:
        validate_pr_attachment(exe_file)
    assert "Invalid file format." in str(exc_info.value)

    png_file = SimpleUploadedFile("image.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
    with pytest.raises(ValidationError) as exc_info:
        validate_pr_attachment(png_file)
    assert "Invalid file format." in str(exc_info.value)


def test_validator_rejects_empty_file():
    empty_file = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
    with pytest.raises(ValidationError) as exc_info:
        validate_pr_attachment(empty_file)
    assert "Invalid file format." in str(exc_info.value)


@pytest.mark.django_db
def test_create_service_with_valid_and_invalid_attachments(pr_magic_setup):
    data = pr_magic_setup
    future_date = data["today"] + timedelta(days=7)
    valid_pdf = SimpleUploadedFile(
        "spec.pdf", b"%PDF-1.4 valid specs", content_type="application/pdf"
    )
    valid_docx = SimpleUploadedFile(
        "notes.docx",
        b"PK\x03\x04 notes content",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pr = create_purchase_requisition_service(
        title="Hardware Purchase",
        justification="Specs attached",
        requester=data["requester"],
        department=data["dept"],
        cost_center=data["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Network Adapter",
                "quantity": 2,
                "estimated_unit_price": Decimal("500.00"),
                "unit_of_measure": "EA",
            }
        ],
        attachments=[valid_pdf, valid_docx],
    )
    assert pr.attachments.count() == 2

    # Attempting to create with spoofed attachment must raise ValidationError("Invalid file format.")
    fake_pdf = SimpleUploadedFile(
        "malware.pdf", b"<script>alert('xss')</script>", content_type="application/pdf"
    )
    with pytest.raises(ValidationError) as exc_info:
        create_purchase_requisition_service(
            title="Fake PR",
            justification="Testing rejection",
            requester=data["requester"],
            department=data["dept"],
            cost_center=data["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Item",
                    "quantity": 1,
                    "estimated_unit_price": Decimal("10.00"),
                }
            ],
            attachments=[fake_pdf],
        )
    assert "Invalid file format." in str(exc_info.value)


@pytest.mark.django_db
def test_create_view_rejects_invalid_file_format(pr_magic_setup):
    data = pr_magic_setup
    client = Client()
    client.force_login(data["requester"])

    future_date = (data["today"] + timedelta(days=10)).isoformat()
    spoofed_file = SimpleUploadedFile(
        "fake_resume.pdf", b"not a real pdf header", content_type="application/pdf"
    )

    response = client.post(
        "/requisitions/create/",
        {
            "title": "PR with Bad File",
            "justification": "Testing bad file attachment",
            "department": str(data["dept"].id),
            "cost_center": str(data["cc"].id),
            "requested_delivery_date": future_date,
            "item_description[]": ["SSD Drive"],
            "quantity[]": ["4"],
            "estimated_unit_price[]": ["150.00"],
            "unit_of_measure[]": ["EA"],
            "attachments": [spoofed_file],
            "submit_action": "draft",
        },
        follow=True,
    )
    from django.contrib.messages import get_messages

    msgs = [str(m) for m in get_messages(response.wsgi_request)]
    assert "Invalid file format." in msgs

"""
Comprehensive automated tests covering file upload format validation and rejection
across all upload modules in ProcureSphere 360:
1. Purchase Requisition Attachments (validate_pr_attachment - magic bytes & format)
2. Vendor KYC Documents (validate_file_upload - extension & size)
3. Sourcing Bid Attachments (validate_file_upload - extension & size)
4. Contract Documents (upload_contract_document_service - extension & size)
"""

from datetime import timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter
from apps.vendors.models import Vendor, VendorDocument
from apps.vendors.services import upload_vendor_document_service
from apps.sourcing.models import SourcingEvent, VendorBid, BidAttachment
from apps.sourcing.services import upload_bid_attachment_service
from apps.contracts.models import Contract, ContractDocument
from apps.contracts.services import upload_contract_document_service
from apps.requisitions.services import create_purchase_requisition_service
from apps.core.validators import validate_pr_attachment, validate_file_upload


@pytest.fixture
def upload_test_setup(db):
    org = Organization.objects.create(name="HPE Test Org", code="HPE-TST")
    dept = Department.objects.create(name="Procurement Operations", code="PROC-OPS", organization=org)
    cc = CostCenter.objects.create(name="Central IT", code="CC-IT-999", department=dept)
    
    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    role_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    role_vendor, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})

    admin_user = User.objects.create_user(
        email="admin_upload@test.com", password="Password123!", role=role_admin, department=dept
    )
    requester_user = User.objects.create_user(
        email="requester_upload@test.com", password="Password123!", role=role_req, department=dept
    )
    proc_exec_user = User.objects.create_user(
        email="exec_upload@test.com", password="Password123!", role=role_exec, department=dept
    )
    legal_user = User.objects.create_user(
        email="legal_upload@test.com", password="Password123!", role=role_legal, department=dept
    )

    from apps.vendors.models import VendorCategory
    category, _ = VendorCategory.objects.get_or_create(code="IT_SUPPLIES", defaults={"name": "IT Supplies"})

    vendor = Vendor.objects.create(
        legal_name="Apex Global Tech Supplies",
        vendor_number="VEN-TST-001",
        tax_identification_number="TAX-TST-001",
        category=category,
        email="sales@apexglobal.test",
        address="123 Tech Park, Bangalore",
        status=Vendor.STATUS_KYC_REVIEW,
    )
    vendor_user = User.objects.create_user(
        email="vendor_upload@test.com",
        password="Password123!",
        role=role_vendor,
        vendor=vendor,
    )

    # Sourcing event and bid
    today = timezone.now().date()
    event = SourcingEvent.objects.create(
        event_number="RFQ-TST-001",
        title="Server Racks RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        status=SourcingEvent.STATUS_BID_WINDOW,
        bid_start_date=timezone.now() - timedelta(days=1),
        bid_end_date=timezone.now() + timedelta(days=5),
        description="Test RFQ for server hardware",
        created_by=proc_exec_user,
    )
    bid = VendorBid.objects.create(
        event=event,
        vendor=vendor,
        bid_number="BID-TST-001",
        status=VendorBid.STATUS_DRAFT,
        version=1,
    )

    # Contract
    contract = Contract.objects.create(
        contract_number="CON-TST-001",
        title="Hardware Maintenance SLA",
        vendor=vendor,
        status=Contract.STATUS_ACTIVE,
        contract_value=Decimal("50000.00"),
        start_date=today,
        end_date=today + timedelta(days=365),
        contract_owner=legal_user,
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "admin_user": admin_user,
        "requester_user": requester_user,
        "proc_exec_user": proc_exec_user,
        "legal_user": legal_user,
        "vendor": vendor,
        "vendor_user": vendor_user,
        "event": event,
        "bid": bid,
        "contract": contract,
        "today": today,
    }


# =====================================================================
# 1. PURCHASE REQUISITION ATTACHMENTS (Magic bytes & strict validation)
# =====================================================================
class TestRequisitionAttachmentUploadRejections:
    """Tests invalid file formats for Requisitions."""

    def test_pr_rejects_spoofed_pdf(self):
        fake_pdf = SimpleUploadedFile("spoofed.pdf", b"Plain text disguised as pdf", content_type="application/pdf")
        with pytest.raises(ValidationError, match="Invalid file format."):
            validate_pr_attachment(fake_pdf)

    def test_pr_rejects_spoofed_docx(self):
        fake_docx = SimpleUploadedFile("fake.docx", b"\x89PNG\r\n\x1a\n", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        with pytest.raises(ValidationError, match="Invalid file format."):
            validate_pr_attachment(fake_docx)

    def test_pr_rejects_spoofed_doc(self):
        fake_doc = SimpleUploadedFile("fake.doc", b"MZ\x90\x00\x03\x00\x00\x00", content_type="application/msword")
        with pytest.raises(ValidationError, match="Invalid file format."):
            validate_pr_attachment(fake_doc)

    def test_pr_rejects_disallowed_extensions(self):
        for bad_ext, mime in [("run.exe", "application/octet-stream"), ("notes.txt", "text/plain"), ("script.bat", "text/plain"), ("data.xlsx", "application/vnd.ms-excel")]:
            bad_file = SimpleUploadedFile(bad_ext, b"sample content", content_type=mime)
            with pytest.raises(ValidationError, match="Invalid file format."):
                validate_pr_attachment(bad_file)

    def test_pr_rejects_empty_file(self):
        empty_file = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        with pytest.raises(ValidationError, match="Invalid file format."):
            validate_pr_attachment(empty_file)

    def test_pr_rejects_oversized_file(self):
        oversized_data = b"%PDF-" + b"0" * (10 * 1024 * 1024 + 50)
        oversized_file = SimpleUploadedFile("oversized.pdf", oversized_data, content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds the maximum allowed limit"):
            validate_pr_attachment(oversized_file)


# =====================================================================
# 2. VENDOR KYC DOCUMENT UPLOADS
# =====================================================================
class TestVendorKycUploadRejections:
    """Tests invalid file rejection on Vendor KYC uploads."""

    def test_vendor_kyc_rejects_disallowed_extensions(self, upload_test_setup):
        setup = upload_test_setup
        disallowed_files = [
            ("malicious.exe", b"MZ\x00\x00"),
            ("script.bat", b"@echo off"),
            ("exploit.sh", b"#!/bin/bash"),
            ("payload.html", b"<script>alert(1)</script>"),
            ("config.py", b"import os"),
        ]
        for fname, content in disallowed_files:
            file_obj = SimpleUploadedFile(fname, content)
            with pytest.raises(ValidationError) as exc:
                upload_vendor_document_service(
                    vendor=setup["vendor"],
                    user=setup["vendor_user"],
                    file=file_obj,
                    document_type=VendorDocument.DOC_TYPE_TAX,
                    title="KYC Tax Doc",
                )
            assert "is not permitted" in str(exc.value)

    def test_vendor_kyc_rejects_oversized_file(self, upload_test_setup):
        setup = upload_test_setup
        oversized = SimpleUploadedFile("large_tax.pdf", b"A" * (10 * 1024 * 1024 + 100), content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds maximum allowed limit"):
            upload_vendor_document_service(
                vendor=setup["vendor"],
                user=setup["vendor_user"],
                file=oversized,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Oversized Tax Doc",
            )

    def test_vendor_kyc_accepts_valid_documents(self, upload_test_setup):
        setup = upload_test_setup
        valid_pdf = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 tax certificate", content_type="application/pdf")
        doc = upload_vendor_document_service(
            vendor=setup["vendor"],
            user=setup["vendor_user"],
            file=valid_pdf,
            document_type=VendorDocument.DOC_TYPE_CERT,
            title="Certificate of Incorporation",
        )
        assert doc.id is not None
        assert doc.document_type == VendorDocument.DOC_TYPE_CERT


# =====================================================================
# 3. SOURCING BID ATTACHMENTS
# =====================================================================
class TestSourcingBidAttachmentUploadRejections:
    """Tests invalid file rejection on RFQ/RFP vendor bid attachments."""

    def test_bid_attachment_rejects_disallowed_extensions(self, upload_test_setup):
        setup = upload_test_setup
        disallowed = [
            ("backdoor.exe", b"MZ fake"),
            ("macro.vbs", b"WScript.Echo 1"),
            ("script.sh", b"#!/bin/sh"),
        ]
        for fname, content in disallowed:
            file_obj = SimpleUploadedFile(fname, content)
            with pytest.raises(ValidationError) as exc:
                upload_bid_attachment_service(
                    bid=setup["bid"],
                    user=setup["vendor_user"],
                    file=file_obj,
                    title="Proposal File",
                    document_type="TECHNICAL",
                )
            assert "is not permitted" in str(exc.value)

    def test_bid_attachment_rejects_oversized_file(self, upload_test_setup):
        setup = upload_test_setup
        oversized = SimpleUploadedFile("huge_tech_proposal.pdf", b"B" * (10 * 1024 * 1024 + 50), content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds maximum allowed limit"):
            upload_bid_attachment_service(
                bid=setup["bid"],
                user=setup["vendor_user"],
                file=oversized,
                title="Huge File",
                document_type="TECHNICAL",
            )

    def test_bid_attachment_accepts_valid_files(self, upload_test_setup):
        setup = upload_test_setup
        valid_file = SimpleUploadedFile("tech_specs.docx", b"PK\x03\x04 Valid docx content")
        attachment = upload_bid_attachment_service(
            bid=setup["bid"],
            user=setup["vendor_user"],
            file=valid_file,
            title="Technical Proposal Specs",
            document_type="TECHNICAL",
        )
        assert attachment.id is not None
        assert attachment.document_type == "TECHNICAL"


# =====================================================================
# 4. CONTRACT DOCUMENT UPLOADS
# =====================================================================
class TestContractDocumentUploadRejections:
    """Tests invalid file rejection on Contract document uploads."""

    def test_contract_upload_rejects_disallowed_extensions(self, upload_test_setup):
        setup = upload_test_setup
        bad_files = [
            ("malicious.exe", b"MZ binary payload"),
            ("script.bat", b"@echo off"),
            ("exploit.sh", b"rm -rf /"),
        ]
        for fname, content in bad_files:
            file_obj = SimpleUploadedFile(fname, content)
            with pytest.raises(ValidationError, match="Unsupported file extension"):
                upload_contract_document_service(
                    contract=setup["contract"],
                    user=setup["legal_user"],
                    title="Signed Amendment",
                    file=file_obj,
                )

    def test_contract_upload_rejects_missing_extension(self, upload_test_setup):
        setup = upload_test_setup
        no_ext_file = SimpleUploadedFile("raw_contract_file", b"some bytes")
        with pytest.raises(ValidationError, match="File must have a valid extension"):
            upload_contract_document_service(
                contract=setup["contract"],
                user=setup["legal_user"],
                title="No Ext File",
                file=no_ext_file,
            )

    def test_contract_upload_rejects_oversized_file(self, upload_test_setup):
        setup = upload_test_setup
        oversized = SimpleUploadedFile("massive_contract.pdf", b"C" * (10 * 1024 * 1024 + 100), content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds 10MB upload limit"):
            upload_contract_document_service(
                contract=setup["contract"],
                user=setup["legal_user"],
                title="Massive Contract",
                file=oversized,
            )

    def test_contract_upload_accepts_valid_documents(self, upload_test_setup):
        setup = upload_test_setup
        valid_pdf = SimpleUploadedFile("signed_contract.pdf", b"%PDF-1.4 signed contract")
        doc = upload_contract_document_service(
            contract=setup["contract"],
            user=setup["legal_user"],
            title="Executed Master Agreement",
            file=valid_pdf,
        )
        assert doc.id is not None
        assert doc.title == "Executed Master Agreement"

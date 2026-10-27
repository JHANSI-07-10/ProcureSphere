"""
Comprehensive automated tests covering file upload format validation and rejection
across all upload modules in ProcureSphere 360:
1. Purchase Requisition Attachments (validate_pr_attachment - magic bytes & format)
2. Vendor KYC Documents (validate_file_upload - extension & size)
3. Sourcing Bid Attachments (validate_file_upload - extension & size)
4. Contract Documents (upload_contract_document_service - extension & size)
"""

import io
import zipfile
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.contracts.models import Contract, ContractDocument
from apps.contracts.services import upload_contract_document_service
from apps.core.validators import validate_file_upload, validate_pr_attachment
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.services import create_purchase_requisition_service
from apps.sourcing.models import BidAttachment, SourcingEvent, VendorBid
from apps.sourcing.services import upload_bid_attachment_service
from apps.vendors.models import Vendor, VendorDocument
from apps.vendors.services import upload_vendor_document_service

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def _valid_docx_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("word/document.xml", '<?xml version="1.0"?><w:document/>')
    return buf.getvalue()


def _valid_xlsx_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook/>')
    return buf.getvalue()


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

    def test_vendor_kyc_rejects_disallowed_extensions(self, procurement_world):
        w = procurement_world
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
                    vendor=w["vendor_a"],
                    user=w["user_a"],
                    file=file_obj,
                    document_type=VendorDocument.DOC_TYPE_TAX,
                    title="KYC Tax Doc",
                )
            assert "is not permitted" in str(exc.value)

    def test_vendor_kyc_rejects_oversized_file(self, procurement_world):
        w = procurement_world
        oversized = SimpleUploadedFile("large_tax.pdf", b"A" * (10 * 1024 * 1024 + 100), content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds maximum allowed limit"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=oversized,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Oversized Tax Doc",
            )

    def test_vendor_kyc_rejects_empty_file(self, procurement_world):
        w = procurement_world
        empty_file = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        with pytest.raises(ValidationError, match="Uploaded file is empty"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=empty_file,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Empty File",
            )

    def test_vendor_kyc_rejects_spoofed_pdf(self, procurement_world):
        w = procurement_world
        spoofed = SimpleUploadedFile("fake.pdf", b"MZ\x90\x00Not a real pdf", content_type="application/pdf")
        with pytest.raises(ValidationError, match="Invalid file content for .pdf"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=spoofed,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Spoofed PDF",
            )

    def test_vendor_kyc_rejects_spoofed_png(self, procurement_world):
        w = procurement_world
        spoofed = SimpleUploadedFile("fake.png", b"GIF89aNot a png", content_type="image/png")
        with pytest.raises(ValidationError, match="Invalid file content for .png"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=spoofed,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Spoofed PNG",
            )

    def test_vendor_kyc_rejects_spoofed_jpeg(self, procurement_world):
        w = procurement_world
        spoofed = SimpleUploadedFile("fake.jpg", b"BM\x00\x00Not a jpeg", content_type="image/jpeg")
        with pytest.raises(ValidationError, match="Invalid file content for JPEG"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=spoofed,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Spoofed JPG",
            )

    def test_vendor_kyc_rejects_spoofed_doc(self, procurement_world):
        w = procurement_world
        spoofed = SimpleUploadedFile("fake.doc", b"MZ\x90\x00Not CFBF", content_type="application/msword")
        with pytest.raises(ValidationError, match="Invalid file content for .doc"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=spoofed,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Spoofed DOC",
            )

    def test_vendor_kyc_rejects_spoofed_docx(self, procurement_world):
        w = procurement_world
        # Zip without OpenXML word/ directory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notes.txt", "not docx")
        spoofed = SimpleUploadedFile("fake.docx", buf.getvalue())
        with pytest.raises(ValidationError, match="Invalid file content for .docx"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=spoofed,
                document_type=VendorDocument.DOC_TYPE_TAX,
                title="Spoofed DOCX",
            )

    def test_vendor_kyc_rejects_spoofed_xlsx(self, procurement_world):
        w = procurement_world
        # Zip without OpenXML xl/ directory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notes.txt", "not xlsx")
        spoofed = SimpleUploadedFile("fake.xlsx", buf.getvalue())
        with pytest.raises(ValidationError, match="Invalid file content for .xlsx"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=spoofed,
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title="Spoofed XLSX",
            )

    def test_vendor_kyc_rejects_csv_with_binary_or_null_bytes(self, procurement_world):
        w = procurement_world
        null_csv = SimpleUploadedFile("bad.csv", b"col1,col2\x00payload\n1,2\n", content_type="text/csv")
        with pytest.raises(ValidationError, match="Binary content and null bytes are not permitted"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=null_csv,
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title="Null Byte CSV",
            )

        binary_csv = SimpleUploadedFile("fake.csv", b"MZ\x90\x00binary executable disguised as csv", content_type="text/csv")
        with pytest.raises(ValidationError, match="Binary file disguised with .csv extension"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=binary_csv,
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title="Binary CSV",
            )

    def test_vendor_kyc_accepts_all_valid_formats(self, procurement_world):
        w = procurement_world
        cache.clear()
        valid_files = [
            ("cert.pdf", b"%PDF-1.4 tax certificate", VendorDocument.DOC_TYPE_CERT),
            ("seal.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", VendorDocument.DOC_TYPE_CERT),
            ("photo.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", VendorDocument.DOC_TYPE_CERT),
            ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1word", VendorDocument.DOC_TYPE_OTHER),
            ("modern.docx", _valid_docx_bytes(), VendorDocument.DOC_TYPE_CERT),
            ("sheet.xlsx", _valid_xlsx_bytes(), VendorDocument.DOC_TYPE_OTHER),
            ("data.csv", b"col1,col2\nval1,val2\n", VendorDocument.DOC_TYPE_OTHER),
        ]
        for fname, content, dtype in valid_files:
            doc = upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=SimpleUploadedFile(fname, content),
                document_type=dtype,
                title=f"Valid {fname}",
            )
            assert doc.id is not None
            assert doc.file.name is not None

    def test_vendor_kyc_enforces_document_count_quota(self, procurement_world):
        w = procurement_world
        vendor = w["vendor_a"]
        cache.clear()
        # Seed 25 existing documents
        for i in range(25):
            VendorDocument.objects.create(
                vendor=vendor,
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title=f"Doc {i}",
                file=SimpleUploadedFile(f"doc_{i}.pdf", b"%PDF-1.4 sample content"),
            )
        # Attempting 26th upload raises document quota error
        with pytest.raises(ValidationError, match="Maximum document quota reached"):
            upload_vendor_document_service(
                vendor=vendor,
                user=w["user_a"],
                file=SimpleUploadedFile("doc_26.pdf", b"%PDF-1.4 test"),
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title="Quota Breaker",
            )

    def test_vendor_kyc_enforces_total_storage_quota(self, procurement_world, monkeypatch):
        w = procurement_world
        vendor = w["vendor_a"]
        cache.clear()

        doc = VendorDocument.objects.create(
            vendor=vendor,
            document_type=VendorDocument.DOC_TYPE_OTHER,
            title="Existing Large Doc",
            file=SimpleUploadedFile("existing.pdf", b"%PDF-1.4 test"),
        )
        # Mock FieldFile.size property to simulate 49MB on file
        from django.db.models.fields.files import FieldFile
        monkeypatch.setattr(FieldFile, "size", 49 * 1024 * 1024)

        # 2MB incoming file exceeds 50MB storage quota
        incoming = SimpleUploadedFile("new.pdf", b"%PDF-1.4 " + b"0" * (2 * 1024 * 1024))
        with pytest.raises(ValidationError, match="Vendor storage quota exceeded"):
            upload_vendor_document_service(
                vendor=vendor,
                user=w["user_a"],
                file=incoming,
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title="Storage Breaker",
            )

    def test_vendor_kyc_upload_rate_limiting(self, procurement_world):
        w = procurement_world
        cache.clear()
        # 10 uploads within window succeed
        for i in range(10):
            valid_pdf = SimpleUploadedFile(f"rate_{i}.pdf", b"%PDF-1.4 test")
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=valid_pdf,
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title=f"Rate Test {i}",
            )
        # 11th upload triggers rate limit ValidationError
        with pytest.raises(ValidationError, match="Upload rate limit exceeded"):
            upload_vendor_document_service(
                vendor=w["vendor_a"],
                user=w["user_a"],
                file=SimpleUploadedFile("rate_11.pdf", b"%PDF-1.4 test"),
                document_type=VendorDocument.DOC_TYPE_OTHER,
                title="Rate Breaker",
            )


# =====================================================================
# 3. SOURCING BID ATTACHMENTS
# =====================================================================
class TestSourcingBidAttachmentUploadRejections:
    """Tests invalid file rejection on RFQ/RFP vendor bid attachments."""

    def _create_bid(self, w):
        event = SourcingEvent.objects.create(
            event_number=f"RFQ-TEST-{timezone.now().timestamp()}",
            title="Server RFQ",
            event_type=SourcingEvent.TYPE_RFQ,
            status=SourcingEvent.STATUS_BID_WINDOW,
            bid_start_date=timezone.now() - timedelta(days=1),
            bid_end_date=timezone.now() + timedelta(days=5),
            description="Server RFQ test",
            created_by=w["exec"],
        )
        bid = VendorBid.objects.create(
            event=event,
            vendor=w["vendor_a"],
            bid_number=f"BID-TEST-{timezone.now().timestamp()}",
            status=VendorBid.STATUS_DRAFT,
            version=1,
        )
        return bid

    def test_bid_attachment_rejects_disallowed_extensions(self, procurement_world):
        w = procurement_world
        bid = self._create_bid(w)
        disallowed = [
            ("backdoor.exe", b"MZ fake"),
            ("macro.vbs", b"WScript.Echo 1"),
            ("script.sh", b"#!/bin/sh"),
        ]
        for fname, content in disallowed:
            file_obj = SimpleUploadedFile(fname, content)
            with pytest.raises(ValidationError) as exc:
                upload_bid_attachment_service(
                    bid=bid,
                    user=w["user_a"],
                    file=file_obj,
                    title="Proposal File",
                    document_type="TECHNICAL",
                )
            assert "is not permitted" in str(exc.value)

    def test_bid_attachment_rejects_oversized_file(self, procurement_world):
        w = procurement_world
        bid = self._create_bid(w)
        oversized = SimpleUploadedFile("huge_tech_proposal.pdf", b"A" * (10 * 1024 * 1024 + 50), content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds maximum allowed limit"):
            upload_bid_attachment_service(
                bid=bid,
                user=w["user_a"],
                file=oversized,
                title="Huge File",
                document_type="TECHNICAL",
            )

    def test_bid_attachment_accepts_valid_files(self, procurement_world):
        w = procurement_world
        bid = self._create_bid(w)
        valid_file = SimpleUploadedFile("tech_specs.docx", _valid_docx_bytes())
        attachment = upload_bid_attachment_service(
            bid=bid,
            user=w["user_a"],
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

    def _create_contract(self, w):
        return Contract.objects.create(
            contract_number=f"CON-TEST-{timezone.now().timestamp()}",
            title="Hardware Maintenance SLA",
            vendor=w["vendor_a"],
            status=Contract.STATUS_ACTIVE,
            contract_value=Decimal("50000.00"),
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=365),
            contract_owner=w["exec"],
        )

    def test_contract_upload_rejects_disallowed_extensions(self, procurement_world):
        w = procurement_world
        contract = self._create_contract(w)
        bad_files = [
            ("malicious.exe", b"MZ binary payload"),
            ("script.bat", b"@echo off"),
            ("exploit.sh", b"rm -rf /"),
        ]
        for fname, content in bad_files:
            file_obj = SimpleUploadedFile(fname, content)
            with pytest.raises(ValidationError, match="Unsupported file extension"):
                upload_contract_document_service(
                    contract=contract,
                    user=w["exec"],
                    title="Signed Amendment",
                    file=file_obj,
                )

    def test_contract_upload_rejects_missing_extension(self, procurement_world):
        w = procurement_world
        contract = self._create_contract(w)
        no_ext_file = SimpleUploadedFile("raw_contract_file", b"some bytes")
        with pytest.raises(ValidationError, match="File must have a valid extension"):
            upload_contract_document_service(
                contract=contract,
                user=w["exec"],
                title="No Ext File",
                file=no_ext_file,
            )

    def test_contract_upload_rejects_oversized_file(self, procurement_world):
        w = procurement_world
        contract = self._create_contract(w)
        oversized = SimpleUploadedFile("massive_contract.pdf", b"C" * (10 * 1024 * 1024 + 100), content_type="application/pdf")
        with pytest.raises(ValidationError, match="File size exceeds 10MB upload limit"):
            upload_contract_document_service(
                contract=contract,
                user=w["exec"],
                title="Massive Contract",
                file=oversized,
            )

    def test_contract_upload_accepts_valid_documents(self, procurement_world):
        w = procurement_world
        contract = self._create_contract(w)
        valid_pdf = SimpleUploadedFile("signed_contract.pdf", b"%PDF-1.4 signed contract")
        doc = upload_contract_document_service(
            contract=contract,
            user=w["exec"],
            title="Executed Master Agreement",
            file=valid_pdf,
        )
        assert doc.id is not None
        assert doc.title == "Executed Master Agreement"

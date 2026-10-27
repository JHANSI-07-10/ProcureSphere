"""
Provisional Vendor KYC Compliance, Verification & Event Eligibility Policy.

NOTICE / DISCLAIMER:
The official legal approval policy has not yet been formally issued by HPE Team.
This module defines a PROVISIONAL, CONFIGURABLE compliance ruleset based on PRD Section 3.2
(Vendor Onboarding & KYC) and PRD Section 4 (Sourcing & Sealed Bids).
These rules isolate policy-dependent behavior and must be reviewed and officially ratified
by the Legal & Compliance Team before being treated as binding policy.

Distinction of Rulesets:
1. Confirmed Project Requirements:
   - Vendors must be in ACTIVE status to be invited to sourcing events or submit bids.
   - Legal Manager or Super Admin must review and record the final verification decision.
   - Suspended vendors cannot participate in sourcing events or receive purchase orders.
2. Provisional Internal Business Rules:
   - Mandatory document types per vendor category (Default: INC_CERT, TAX_CLEARANCE, BANK_LETTER).
   - Document validity: Physical file attached, non-expired (expiry_date >= today).
   - Expiry reminder intervals: 30, 15, and 7 days before expiry.
3. Legal / Regulatory Requirements Awaiting Authorized Confirmation:
   - Jurisdiction-specific tax documentation requirements.
   - Required re-verification frequency for non-expiring incorporation certificates.
   - Exact exception approval delegation when a non-mandatory document is missing.
"""

from django.utils import timezone

from apps.vendors.models import Vendor, VendorDocument


class ProvisionalKYCPolicy:
    """
    Configurable rules engine for Vendor KYC compliance and event eligibility.
    """

    POLICY_VERSION = "VER-1.0-PROVISIONAL-2026"

    # Default mandatory document types required for general vendor categories
    DEFAULT_MANDATORY_DOC_TYPES = [
        VendorDocument.DOC_TYPE_CERT,  # Certificate of Incorporation
        VendorDocument.DOC_TYPE_TAX,   # Tax Clearance Certificate
        VendorDocument.DOC_TYPE_BANK,  # Bank Verification Document
    ]

    # Category-specific mandatory document overrides
    CATEGORY_MANDATORY_DOC_TYPES = {
        "IT": [
            VendorDocument.DOC_TYPE_CERT,
            VendorDocument.DOC_TYPE_TAX,
            VendorDocument.DOC_TYPE_BANK,
        ],
        "LOGISTICS": [
            VendorDocument.DOC_TYPE_CERT,
            VendorDocument.DOC_TYPE_TAX,
            VendorDocument.DOC_TYPE_BANK,
        ],
        "CONSULTING": [
            VendorDocument.DOC_TYPE_CERT,
            VendorDocument.DOC_TYPE_TAX,
            VendorDocument.DOC_TYPE_BANK,
        ],
        "FACILITIES": [
            VendorDocument.DOC_TYPE_CERT,
            VendorDocument.DOC_TYPE_TAX,
            VendorDocument.DOC_TYPE_BANK,
        ],
    }

    # Document validity check: warning intervals in days
    EXPIRY_REMINDER_DAYS = [30, 15, 7]

    @classmethod
    def get_mandatory_doc_types(cls, vendor: Vendor) -> list[str]:
        """
        Returns the list of mandatory KYC document type codes required for the vendor's category.
        """
        if vendor.category and vendor.category.code:
            code = vendor.category.code.upper().strip()
            if code in cls.CATEGORY_MANDATORY_DOC_TYPES:
                return cls.CATEGORY_MANDATORY_DOC_TYPES[code]
        return list(cls.DEFAULT_MANDATORY_DOC_TYPES)

    @classmethod
    def evaluate_vendor_kyc_compliance(cls, vendor: Vendor, as_of_date=None) -> dict:
        """
        Evaluates vendor's uploaded KYC documents against the provisional compliance policy.

        Returns a detailed evaluation dictionary:
        - is_compliant: bool (True if all mandatory documents are present, verified, and not expired)
        - eligible_for_legal_approval: bool
        - mandatory_types: list of required document types with readable labels
        - missing_mandatory_types: list of missing document types
        - unverified_mandatory_types: list of uploaded mandatory documents not yet verified
        - expired_documents: list of expired documents
        - document_summaries: list of per-document status dictionaries
        - provisional_policy_version: str
        """
        today = as_of_date or timezone.now().date()
        mandatory_codes = cls.get_mandatory_doc_types(vendor)
        doc_type_labels = dict(VendorDocument.DOC_TYPE_CHOICES)

        documents = list(vendor.documents.all())

        # Categorize uploaded documents
        uploaded_by_type = {}
        for doc in documents:
            uploaded_by_type.setdefault(doc.document_type, []).append(doc)

        document_summaries = []
        expired_docs = []
        action_required_docs = []
        rejected_docs = []
        verified_mandatory_codes = set()
        unverified_mandatory_codes = set()

        for doc in documents:
            is_expired = bool(doc.expiry_date and doc.expiry_date < today)
            is_expiring_soon = bool(
                doc.expiry_date and today <= doc.expiry_date <= today + timezone.timedelta(days=30)
            )
            doc_status = getattr(doc, "status", "VERIFIED" if doc.is_verified else "PENDING")

            if is_expired:
                expired_docs.append(doc)
            if doc_status == "CHANGES_REQUIRED":
                action_required_docs.append(doc)
            elif doc_status == "REJECTED":
                rejected_docs.append(doc)

            is_valid_verification = (
                doc.is_verified
                and not is_expired
                and doc_status not in ["REJECTED", "CHANGES_REQUIRED"]
                and bool(doc.file)
            )

            if doc.document_type in mandatory_codes:
                if is_valid_verification:
                    verified_mandatory_codes.add(doc.document_type)
                else:
                    unverified_mandatory_codes.add(doc.document_type)

            document_summaries.append({
                "id": str(doc.id),
                "title": doc.title,
                "document_type": doc.document_type,
                "type_label": doc_type_labels.get(doc.document_type, doc.document_type),
                "is_mandatory": doc.document_type in mandatory_codes,
                "status": doc_status,
                "is_verified": doc.is_verified,
                "is_expired": is_expired,
                "is_expiring_soon": is_expiring_soon,
                "has_file": bool(doc.file),
                "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
                "verified_by": doc.verified_by.email if doc.verified_by else None,
                "verified_at": getattr(doc, "verified_at", None),
                "reviewer_notes": getattr(doc, "reviewer_notes", ""),
                "version": getattr(doc, "version", 1),
            })

        missing_mandatory = [
            code for code in mandatory_codes if code not in uploaded_by_type
        ]

        # Identify superseded documents (replaced by a verified active document)
        superseded_doc_ids = set()
        for doc in documents:
            if doc.replaces_document_id and doc.is_verified and getattr(doc, "status", None) == "VERIFIED":
                superseded_doc_ids.add(doc.replaces_document_id)
            if doc.document_type in verified_mandatory_codes and doc.is_verified:
                for other in documents:
                    if other.document_type == doc.document_type and other.id != doc.id:
                        if other.status in ["REJECTED", "EXPIRED", "CHANGES_REQUIRED"] or (other.expiry_date and other.expiry_date < today):
                            superseded_doc_ids.add(other.id)

        # All mandatory types must have at least one verified, non-expired copy
        all_mandatory_verified = set(mandatory_codes).issubset(verified_mandatory_codes)
        has_blocking_rejections = any(
            doc.document_type in mandatory_codes and doc.id not in superseded_doc_ids for doc in rejected_docs
        )
        has_blocking_action_required = any(
            doc.document_type in mandatory_codes and doc.id not in superseded_doc_ids for doc in action_required_docs
        )
        has_blocking_expirations = any(
            doc.document_type in mandatory_codes and doc.id not in superseded_doc_ids for doc in expired_docs
        )

        is_compliant = (
            all_mandatory_verified
            and not missing_mandatory
            and not has_blocking_rejections
            and not has_blocking_action_required
            and not has_blocking_expirations
        )

        return {
            "is_compliant": is_compliant,
            "eligible_for_legal_approval": is_compliant,
            "mandatory_types": [doc_type_labels.get(c, c) for c in mandatory_codes],
            "verified_mandatory_types": [doc_type_labels.get(c, c) for c in verified_mandatory_codes],
            "missing_mandatory_types": [doc_type_labels.get(c, c) for c in missing_mandatory],
            "unverified_mandatory_types": [
                doc_type_labels.get(c, c)
                for c in unverified_mandatory_codes
                if c not in missing_mandatory
            ],
            "expired_documents_count": len(expired_docs),
            "action_required_count": len(action_required_docs),
            "rejected_count": len(rejected_docs),
            "total_documents": len(documents),
            "document_summaries": document_summaries,
            "provisional_policy_version": cls.POLICY_VERSION,
        }


# Module-level convenience functions
evaluate_vendor_kyc_compliance = ProvisionalKYCPolicy.evaluate_vendor_kyc_compliance
get_mandatory_doc_types = ProvisionalKYCPolicy.get_mandatory_doc_types

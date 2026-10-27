from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.validators import (
    normalize_tax_identifier,
    validate_file_upload,
    validate_gstin,
    validate_pan_gstin_consistency,
    validate_pan_number,
)


class VendorCategory(TimeStampedModel):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Vendor(TimeStampedModel):
    STATUS_DRAFT = "DRAFT"
    STATUS_SUBMITTED = "SUBMITTED"
    STATUS_KYC_REVIEW = "KYC_REVIEW"
    STATUS_CHANGES_REQUIRED = "CHANGES_REQUIRED"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_ACTIVE = "ACTIVE"
    STATUS_ON_HOLD = "ON_HOLD"
    STATUS_SUSPENDED = "SUSPENDED"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_KYC_REVIEW, "KYC Review"),
        (STATUS_CHANGES_REQUIRED, "Changes Required"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ON_HOLD, "On Hold"),
        (STATUS_SUSPENDED, "Suspended / Blacklisted"),
    ]

    legal_name = models.CharField(max_length=255)
    trade_name = models.CharField(max_length=255, blank=True)
    vendor_number = models.CharField(max_length=50, unique=True)
    tax_identification_number = models.CharField(max_length=100, unique=True)
    pan_number = models.CharField(
        max_length=10, blank=True, default="", validators=[validate_pan_number]
    )
    gstin = models.CharField(
        max_length=15, blank=True, default="", validators=[validate_gstin]
    )
    registration_number = models.CharField(max_length=100, blank=True)

    category = models.ForeignKey(VendorCategory, on_delete=models.PROTECT, related_name="vendors")
    status = models.CharField(
        max_length=50, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True
    )

    # Banking details
    bank_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=100, blank=True)
    bank_routing_code = models.CharField(max_length=100, blank=True)

    # Contact & Address
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField()

    # Hold/Suspend notes
    status_notes = models.TextField(blank=True)

    # Event Eligibility & Legal Governance
    ELIGIBILITY_PENDING = "PENDING"
    ELIGIBILITY_ELIGIBLE = "ELIGIBLE"
    ELIGIBILITY_INELIGIBLE = "INELIGIBLE"
    ELIGIBILITY_SUSPENDED = "SUSPENDED"
    ELIGIBILITY_EXPIRED = "EXPIRED"

    ELIGIBILITY_CHOICES = [
        (ELIGIBILITY_PENDING, "Pending Legal Review"),
        (ELIGIBILITY_ELIGIBLE, "Eligible to Participate in Events"),
        (ELIGIBILITY_INELIGIBLE, "Ineligible"),
        (ELIGIBILITY_SUSPENDED, "Suspended from Events"),
        (ELIGIBILITY_EXPIRED, "Compliance Expired / Renewal Required"),
    ]

    is_event_eligible = models.BooleanField(default=False, db_index=True)
    event_eligibility_status = models.CharField(
        max_length=30,
        choices=ELIGIBILITY_CHOICES,
        default=ELIGIBILITY_PENDING,
        db_index=True,
    )
    legal_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_vendors",
    )
    legal_approved_at = models.DateTimeField(null=True, blank=True)
    legal_approval_notes = models.TextField(blank=True, default="")

    def clean(self):
        super().clean()
        if self.pan_number:
            self.pan_number = normalize_tax_identifier(self.pan_number)
            validate_pan_number(self.pan_number)
        if self.gstin:
            self.gstin = normalize_tax_identifier(self.gstin)
            validate_gstin(self.gstin)
        if self.pan_number and self.gstin:
            validate_pan_gstin_consistency(self.pan_number, self.gstin)

    def save(self, *args, **kwargs):
        if self.pan_number:
            self.pan_number = normalize_tax_identifier(self.pan_number)
        if self.gstin:
            self.gstin = normalize_tax_identifier(self.gstin)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.legal_name} ({self.vendor_number}) [{self.status}]"


class VendorContact(TimeStampedModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="contacts")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    is_primary = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.vendor.legal_name}"


class VendorDocument(TimeStampedModel):
    DOC_TYPE_PAN = "PAN"
    DOC_TYPE_GST = "GST_CERT"
    DOC_TYPE_INC = "INC_CERT"
    DOC_TYPE_CERT = "INC_CERT"  # Backwards compatibility alias
    DOC_TYPE_BANK = "BANK_LETTER"
    DOC_TYPE_ADDRESS = "ADDRESS_PROOF"
    DOC_TYPE_SIGNATORY = "SIGNATORY_PROOF"
    DOC_TYPE_TAX = "TAX_CLEARANCE"
    DOC_TYPE_OTHER = "OTHER"

    DOC_TYPE_CHOICES = [
        (DOC_TYPE_PAN, "Permanent Account Number (PAN) Card"),
        (DOC_TYPE_GST, "GST Registration Certificate"),
        (DOC_TYPE_INC, "Certificate of Incorporation"),
        (DOC_TYPE_BANK, "Bank Verification Document"),
        (DOC_TYPE_ADDRESS, "Business Address Proof"),
        (DOC_TYPE_SIGNATORY, "Authorized Signatory Proof"),
        (DOC_TYPE_TAX, "Tax Clearance Certificate"),
        (DOC_TYPE_OTHER, "Other KYC Document"),
    ]

    STATUS_PENDING = "PENDING"
    STATUS_VERIFIED = "VERIFIED"
    STATUS_REJECTED = "REJECTED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_CHANGES_REQUIRED = "CHANGES_REQUIRED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending Review"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CHANGES_REQUIRED, "Correction Required"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=50, choices=DOC_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="vendor_documents/%Y/%m/", validators=[validate_file_upload])
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_vendor_docs",
    )
    # KYC review outcome. is_verified is kept in sync (True only for REVIEW_VERIFIED).
    REVIEW_PENDING = "PENDING"
    REVIEW_VERIFIED = "VERIFIED"
    REVIEW_REJECTED = "REJECTED"
    REVIEW_CHOICES = [
        (REVIEW_PENDING, "Pending"),
        (REVIEW_VERIFIED, "Verified"),
        (REVIEW_REJECTED, "Rejected"),
    ]
    review_status = models.CharField(
        max_length=10, choices=REVIEW_CHOICES, default=REVIEW_PENDING, db_index=True
    )
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    reviewer_notes = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    replaces_document = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaced_by_documents",
    )

    def save(self, *args, **kwargs):
        # Synchronize lifecycle status with review_status and is_verified
        if self.status == self.STATUS_EXPIRED:
            self.is_verified = False
            self.review_status = self.REVIEW_PENDING
        elif self.status == self.STATUS_CHANGES_REQUIRED:
            self.is_verified = False
            self.review_status = self.REVIEW_REJECTED
        elif self.status == self.STATUS_REJECTED or self.review_status == self.REVIEW_REJECTED:
            self.is_verified = False
            self.review_status = self.REVIEW_REJECTED
            if self.status not in (self.STATUS_REJECTED, self.STATUS_CHANGES_REQUIRED):
                self.status = self.STATUS_REJECTED
        elif self.status == self.STATUS_VERIFIED:
            self.is_verified = True
            self.review_status = self.REVIEW_VERIFIED
        elif self.review_status == self.REVIEW_VERIFIED:
            self.is_verified = True
            self.status = self.STATUS_VERIFIED
        else:
            self.is_verified = False
            self.review_status = self.REVIEW_PENDING
            self.status = self.STATUS_PENDING

        if not self.reviewer_notes and self.review_notes:
            self.reviewer_notes = self.review_notes
        elif not self.review_notes and self.reviewer_notes:
            self.review_notes = self.reviewer_notes

        if not self.verified_at and self.reviewed_at:
            self.verified_at = self.reviewed_at
        elif not self.reviewed_at and self.verified_at:
            self.reviewed_at = self.verified_at

        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            fields = set(kwargs["update_fields"])
            fields.add("review_status")
            fields.add("status")
            fields.add("is_verified")
            kwargs["update_fields"] = list(fields)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_document_type_display()} - {self.vendor.legal_name} [{self.status}]"


class VendorDocumentReviewHistory(TimeStampedModel):
    """
    Append-only audit and decision history for individual KYC document verification events.
    """

    document = models.ForeignKey(
        VendorDocument, on_delete=models.CASCADE, related_name="review_history"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doc_review_history",
    )
    action = models.CharField(max_length=50)
    notes = models.TextField(blank=True, default="")
    previous_status = models.CharField(max_length=30, blank=True, default="")
    new_status = models.CharField(max_length=30, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.document.title} - {self.action} ({self.created_at:%Y-%m-%d})"


class VendorRiskRecord(TimeStampedModel):
    RISK_LEVEL_LOW = "LOW"
    RISK_LEVEL_MEDIUM = "MEDIUM"
    RISK_LEVEL_HIGH = "HIGH"

    RISK_CHOICES = [
        (RISK_LEVEL_LOW, "Low Risk"),
        (RISK_LEVEL_MEDIUM, "Medium Risk"),
        (RISK_LEVEL_HIGH, "High Risk"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="risk_records")
    # PRD 3.3 Vendor Master & Risk: "risk flags". Codes from RISK_FLAG_CHOICES.
    FLAG_FINANCIAL = "FINANCIAL"
    FLAG_COMPLIANCE = "COMPLIANCE"
    FLAG_DELIVERY = "DELIVERY"
    FLAG_QUALITY = "QUALITY"
    FLAG_KYC_EXPIRY = "KYC_EXPIRY"
    FLAG_LEGAL = "LEGAL"
    FLAG_SANCTIONS = "SANCTIONS"
    FLAG_CONCENTRATION = "CONCENTRATION"

    RISK_FLAG_CHOICES = [
        (FLAG_FINANCIAL, "Financial stability"),
        (FLAG_COMPLIANCE, "Compliance / regulatory"),
        (FLAG_DELIVERY, "Delivery performance"),
        (FLAG_QUALITY, "Quality performance"),
        (FLAG_KYC_EXPIRY, "KYC document expiry"),
        (FLAG_LEGAL, "Legal / litigation"),
        (FLAG_SANCTIONS, "Sanctions / watchlist"),
        (FLAG_CONCENTRATION, "Supply concentration"),
    ]

    risk_level = models.CharField(
        max_length=20, choices=RISK_CHOICES, default=RISK_LEVEL_LOW, db_index=True
    )
    risk_flags = models.JSONField(default=list, blank=True)
    assessment_notes = models.TextField()
    assessed_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="assessed_vendor_risks"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.vendor.legal_name} - {self.risk_level} Risk"

    @property
    def risk_flag_labels(self):
        labels = dict(self.RISK_FLAG_CHOICES)
        return [labels.get(code, code) for code in (self.risk_flags or [])]


class VendorCredentialDispatch(TimeStampedModel):
    """Evidence that portal credentials were issued and emailed to a vendor (no password stored)."""

    STATUS_PENDING = "PENDING"
    STATUS_SENT = "SENT"
    STATUS_FAILED = "FAILED"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    LEGAL_STATUS_PENDING = "PENDING"
    LEGAL_STATUS_APPROVED = "APPROVED"
    LEGAL_STATUS_REJECTED = "REJECTED"
    LEGAL_STATUS_CHOICES = [
        (LEGAL_STATUS_PENDING, "Pending Legal Review"),
        (LEGAL_STATUS_APPROVED, "Approved by Legal"),
        (LEGAL_STATUS_REJECTED, "Rejected by Legal"),
    ]

    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name="credential_dispatches"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="credential_dispatches"
    )
    sent_to = models.EmailField()
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Legal Manager Verification & Portal Activation
    legal_verification_status = models.CharField(
        max_length=20,
        choices=LEGAL_STATUS_CHOICES,
        default=LEGAL_STATUS_PENDING,
        db_index=True,
    )
    legal_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_credential_dispatches",
    )
    legal_verified_at = models.DateTimeField(null=True, blank=True)
    legal_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.vendor_id} -> {self.sent_to} [{self.status}]"

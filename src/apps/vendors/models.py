from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.validators import validate_file_upload


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
    DOC_TYPE_CERT = "INC_CERT"
    DOC_TYPE_TAX = "TAX_CLEARANCE"
    DOC_TYPE_BANK = "BANK_LETTER"
    DOC_TYPE_OTHER = "OTHER"

    DOC_TYPE_CHOICES = [
        (DOC_TYPE_CERT, "Certificate of Incorporation"),
        (DOC_TYPE_TAX, "Tax Clearance Certificate"),
        (DOC_TYPE_BANK, "Bank Verification Document"),
        (DOC_TYPE_OTHER, "Other KYC Document"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=50, choices=DOC_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="vendor_documents/%Y/%m/", validators=[validate_file_upload])
    expiry_date = models.DateField(null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        "accounts.User",
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

    def __str__(self):
        return f"{self.get_document_type_display()} - {self.vendor.legal_name}"


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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.vendor_id} -> {self.sent_to} [{self.status}]"

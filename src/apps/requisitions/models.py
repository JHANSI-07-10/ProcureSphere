from decimal import Decimal

from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.validators import validate_pr_attachment


class PurchaseRequisition(TimeStampedModel):
    STATUS_DRAFT = "DRAFT"
    STATUS_SUBMITTED = "SUBMITTED"
    STATUS_MANAGER_REVIEW = "MANAGER_REVIEW"
    STATUS_BUDGET_REVIEW = "BUDGET_REVIEW"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_SOURCING = "SOURCING"
    STATUS_PO_ISSUED = "PO_ISSUED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_MANAGER_REVIEW, "Manager Review"),
        (STATUS_BUDGET_REVIEW, "Budget Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_SOURCING, "In Sourcing"),
        (STATUS_PO_ISSUED, "PO Issued"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    pr_number = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=255)
    justification = models.TextField()

    requester = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="requisitions"
    )
    department = models.ForeignKey(
        "organization.Department", on_delete=models.PROTECT, related_name="requisitions"
    )
    cost_center = models.ForeignKey(
        "organization.CostCenter", on_delete=models.PROTECT, related_name="requisitions"
    )

    status = models.CharField(
        max_length=50, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    requested_delivery_date = models.DateField()

    def __str__(self):
        return f"{self.pr_number} - {self.title} (${self.total_amount})"

    def delete(self, using=None, keep_parents=False):
        """
        Hard deletion of PurchaseRequisitions is strictly prohibited (PR-02).
        Transitions status to CANCELLED instead of destroying the record and audit trail.
        """
        self.status = self.STATUS_CANCELLED
        self.save(update_fields=["status", "updated_at"])
        return (1, {self._meta.label: 1})


class PRLine(TimeStampedModel):
    requisition = models.ForeignKey(
        PurchaseRequisition, on_delete=models.CASCADE, related_name="lines"
    )
    item_description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_of_measure = models.CharField(max_length=30, default="EA")
    estimated_unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    estimated_total = models.DecimalField(max_digits=14, decimal_places=2)
    specifications = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        self.estimated_total = self.quantity * self.estimated_unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item_description} (Qty: {self.quantity} @ ${self.estimated_unit_price})"


class PRAttachment(TimeStampedModel):
    requisition = models.ForeignKey(
        PurchaseRequisition, on_delete=models.CASCADE, related_name="attachments"
    )
    uploaded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pr_attachments_uploaded",
    )
    title = models.CharField(max_length=200)
    file = models.FileField(
        upload_to="pr_attachments/%Y/%m/",
        validators=[validate_pr_attachment],
    )
    file_size = models.PositiveIntegerField(default=0, help_text="File size in bytes")

    def save(self, *args, **kwargs):
        if self.file and hasattr(self.file, "size"):
            self.file_size = self.file.size
        super().save(*args, **kwargs)

    @property
    def file_extension(self):
        import os

        return os.path.splitext(self.file.name)[1][1:].lower() if self.file else ""

    @property
    def file_size_display(self):
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024 * 1024):.1f} MB"

    def __str__(self):
        return f"{self.title} - {self.requisition.pr_number}"

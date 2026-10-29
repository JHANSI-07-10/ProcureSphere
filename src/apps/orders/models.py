from decimal import Decimal

from django.db import models

from apps.core.models import TimeStampedModel


class PurchaseOrder(TimeStampedModel):
    STATUS_DRAFT = "DRAFT"
    STATUS_APPROVAL = "APPROVAL"
    STATUS_ISSUED = "ISSUED"
    STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"
    STATUS_PARTIAL_RECEIPT = "PARTIAL_RECEIPT"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_APPROVAL, "In Approval"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_ACKNOWLEDGED, "Acknowledged by Vendor"),
        (STATUS_PARTIAL_RECEIPT, "Partial Receipt"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    po_number = models.CharField(max_length=50, unique=True)
    version = models.PositiveIntegerField(default=1)

    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.PROTECT, related_name="purchase_orders"
    )
    requisition = models.ForeignKey(
        "requisitions.PurchaseRequisition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_orders",
    )
    sourcing_event = models.ForeignKey(
        "sourcing.SourcingEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_orders",
    )
    cost_center = models.ForeignKey(
        "organization.CostCenter", on_delete=models.PROTECT, related_name="purchase_orders"
    )

    status = models.CharField(
        max_length=50, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    terms_and_conditions = models.TextField(blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_pos",
    )
    acknowledgement_notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.po_number} V{self.version} - {self.vendor.legal_name} (${self.total_amount})"


class POLine(TimeStampedModel):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    item_description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_received = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    unit_of_measure = models.CharField(max_length=30, default="EA")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    def save(self, *args, **kwargs):
        self.line_total = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    @property
    def remaining_quantity(self):
        return max(Decimal("0.00"), self.quantity - (self.quantity_received or Decimal("0.00")))

    def __str__(self):
        return f"{self.item_description} (Qty: {self.quantity}, Recv: {self.quantity_received})"


class POAmendment(TimeStampedModel):
    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending Approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="amendments")
    amendment_number = models.PositiveIntegerField()
    reason = models.TextField()
    previous_version_snapshot = models.JSONField(
        help_text="Immutable JSON snapshot of PO prior to change order"
    )
    pending_line_items = models.JSONField(
        default=list,
        help_text="Proposed line item changes awaiting approval",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="requested_po_amendments"
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_po_amendments",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        unique_together = ("po", "amendment_number")

    def __str__(self):
        return f"Amendment #{self.amendment_number} for {self.po.po_number} [{self.status}]"


class DeliverySchedule(TimeStampedModel):
    po = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="delivery_schedules"
    )
    po_line = models.ForeignKey(POLine, on_delete=models.CASCADE, related_name="schedules")
    expected_delivery_date = models.DateField()
    quantity_expected = models.DecimalField(max_digits=12, decimal_places=2)
    destination_address = models.TextField(blank=True)
    status = models.CharField(max_length=30, default="SCHEDULED")

    def __str__(self):
        return f"Delivery Schedule for {self.po.po_number} Line {self.po_line.id} on {self.expected_delivery_date}"

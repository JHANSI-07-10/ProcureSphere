from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class SupplierInvoice(TimeStampedModel):
    STATUS_RECEIVED = "RECEIVED"
    STATUS_VALIDATION = "VALIDATION"
    STATUS_MATCHING = "MATCHING"
    STATUS_EXCEPTION = "EXCEPTION"
    STATUS_APPROVAL = "APPROVAL"
    STATUS_READY_FOR_PAYMENT = "READY_FOR_PAYMENT"
    STATUS_PAID = "PAID"
    STATUS_REJECTED = "REJECTED"

    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_VALIDATION, "Validation"),
        (STATUS_MATCHING, "3-Way Matching"),
        (STATUS_EXCEPTION, "Match Exception"),
        (STATUS_APPROVAL, "Finance Approval"),
        (STATUS_READY_FOR_PAYMENT, "Ready For Payment"),
        (STATUS_PAID, "Paid"),
        (STATUS_REJECTED, "Rejected"),
    ]

    invoice_number = models.CharField(max_length=100, help_text="Supplier invoice reference number")
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="invoices")
    po = models.ForeignKey(
        "orders.PurchaseOrder", on_delete=models.PROTECT, related_name="invoices"
    )

    invoice_date = models.DateField()
    due_date = models.DateField()

    status = models.CharField(
        max_length=50, choices=STATUS_CHOICES, default=STATUS_RECEIVED, db_index=True
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)

    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ("vendor", "invoice_number")

    def __str__(self):
        return f"Invoice {self.invoice_number} from {self.vendor.legal_name} (${self.total_amount})"


class InvoiceLine(TimeStampedModel):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey("orders.POLine", on_delete=models.SET_NULL, null=True, blank=True)
    item_description = models.CharField(max_length=255)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(
                Decimal("0.01"), message="Quantity must be greater than zero."
            )
        ],
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.00"), message="Unit price cannot be negative.")
        ],
    )
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= Decimal("0.00"):
            raise ValidationError(
                {"quantity": "Quantity must be greater than zero. Negative or zero counts are not permitted."}
            )
        if self.unit_price is not None and self.unit_price < Decimal("0.00"):
            raise ValidationError(
                {"unit_price": "Unit price cannot be negative."}
            )

    def save(self, *args, **kwargs):
        self.clean()
        self.line_total = self.quantity * self.unit_price
        super().save(*args, **kwargs)


class MatchException(TimeStampedModel):
    TYPE_PRICE_VARIANCE = "PRICE_VARIANCE"
    TYPE_QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    TYPE_UNMATCHED_LINE = "UNMATCHED_LINE"
    TYPE_TOTAL_VARIANCE = "TOTAL_VARIANCE"

    TYPE_CHOICES = [
        (TYPE_PRICE_VARIANCE, "Price Variance Exceeds Tolerance"),
        (TYPE_QUANTITY_VARIANCE, "Billed Qty Exceeds Received Qty"),
        (TYPE_UNMATCHED_LINE, "Unmatched Line Item"),
        (TYPE_TOTAL_VARIANCE, "Total Amount Mismatch"),
    ]

    STATUS_OPEN = "OPEN"
    STATUS_RESOLVED = "RESOLVED"
    STATUS_REJECTED = "REJECTED"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_RESOLVED, "Resolved / Overridden"),
        (STATUS_REJECTED, "Invoice Rejected"),
    ]

    invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.CASCADE, related_name="exceptions"
    )
    exception_type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True
    )
    description = models.TextField()
    variance_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_exceptions",
    )

    def __str__(self):
        return (
            f"{self.get_exception_type_display()} on {self.invoice.invoice_number} [{self.status}]"
        )


class MatchResult(TimeStampedModel):
    invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.CASCADE, related_name="match_results"
    )
    is_matched = models.BooleanField(default=False)
    price_variance_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    quantity_variance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    tolerance_applied_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("2.00")
    )
    performed_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="performed_matches"
    )

    def __str__(self):
        return f"Match Result for {self.invoice.invoice_number}: {'PASSED' if self.is_matched else 'FAILED'}"


class PaymentStatus(TimeStampedModel):
    invoice = models.OneToOneField(
        SupplierInvoice, on_delete=models.CASCADE, related_name="payment_record"
    )
    payment_reference = models.CharField(max_length=100, unique=True)
    payment_date = models.DateField()
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(max_length=50, default="ACH_TRANSFER")
    paid_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="processed_payments"
    )

    def __str__(self):
        return f"Payment {self.payment_reference} for {self.invoice.invoice_number} (${self.amount_paid})"


class MatchTolerancePolicy(TimeStampedModel):
    """
    Singleton-like model to hold global 3-way match tolerance percentages.
    """

    is_active = models.BooleanField(
        default=True, help_text="Only one policy can be active at a time."
    )
    price_tolerance_pct = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.0500"), help_text="e.g. 0.0500 for 5%"
    )
    qty_tolerance_pct = models.DecimalField(
        max_digits=5, decimal_places=4, default=Decimal("0.0500"), help_text="e.g. 0.0500 for 5%"
    )

    def save(self, *args, **kwargs):
        if self.is_active:
            MatchTolerancePolicy.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_current(cls):
        policy = cls.objects.filter(is_active=True).first()
        if not policy:
            policy = cls.objects.create(is_active=True)
        return policy

    @property
    def price_tolerance_pct_int(self):
        return int(round(self.price_tolerance_pct * 100))

    @property
    def qty_tolerance_pct_int(self):
        return int(round(self.qty_tolerance_pct * 100))

    def __str__(self):
        return f"Tolerance Policy (Price: {self.price_tolerance_pct*100}%, Qty: {self.qty_tolerance_pct*100}%)"

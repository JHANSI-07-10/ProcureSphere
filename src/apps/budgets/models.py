from decimal import Decimal

from django.db import models

from apps.core.models import TimeStampedModel


class Budget(TimeStampedModel):
    cost_center = models.ForeignKey(
        "organization.CostCenter", on_delete=models.CASCADE, related_name="budgets"
    )
    fiscal_period = models.ForeignKey(
        "organization.FiscalPeriod", on_delete=models.CASCADE, related_name="budgets"
    )

    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reserved_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    committed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    actual_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    allow_overspend = models.BooleanField(default=False)

    class Meta:
        unique_together = ("cost_center", "fiscal_period")

    def __str__(self):
        return f"Budget: {self.cost_center.code} [{self.fiscal_period.name}] - Alloc: ${self.allocated_amount}"

    @property
    def available_amount(self):
        diff = self.allocated_amount - (
            self.reserved_amount + self.committed_amount + self.actual_amount
        )
        if not self.allow_overspend:
            return max(Decimal("0.00"), diff)
        return diff


class BudgetReservation(TimeStampedModel):
    STATUS_RESERVED = "RESERVED"
    STATUS_RELEASED = "RELEASED"
    STATUS_COMMITTED = "COMMITTED"

    STATUS_CHOICES = [
        (STATUS_RESERVED, "Reserved"),
        (STATUS_RELEASED, "Released"),
        (STATUS_COMMITTED, "Converted to Commitment"),
    ]

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="reservations")
    requisition = models.ForeignKey(
        "requisitions.PurchaseRequisition",
        on_delete=models.CASCADE,
        related_name="budget_reservations",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_RESERVED)

    def __str__(self):
        return f"Reservation: ${self.amount} for PR {self.requisition.pr_number} [{self.status}]"


class SpendLedger(TimeStampedModel):
    ENTRY_RESERVATION = "RESERVATION"
    ENTRY_COMMITMENT = "COMMITMENT"
    ENTRY_ACTUAL = "ACTUAL"

    ENTRY_CHOICES = [
        (ENTRY_RESERVATION, "Budget Reservation"),
        (ENTRY_COMMITMENT, "PO Commitment"),
        (ENTRY_ACTUAL, "Invoice Actual Spend"),
    ]

    budget = models.ForeignKey(Budget, on_delete=models.PROTECT, related_name="ledger_entries")
    entry_type = models.CharField(max_length=30, choices=ENTRY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference_number = models.CharField(max_length=100)
    description = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.entry_type}: ${self.amount} ({self.reference_number})"

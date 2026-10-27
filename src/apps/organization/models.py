from django.db import models

from apps.core.models import TimeStampedModel


class Organization(TimeStampedModel):
    class Meta:
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    tax_identifier = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    admin_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="administered_organizations",
        help_text="Primary administrator user for this organization",
    )

    def __str__(self):
        return f"{self.name} ({self.code})"


class Department(TimeStampedModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="departments"
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} [{self.code}]"


class CostCenter(TimeStampedModel):
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="cost_centers"
    )
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=150)
    manager = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_cost_centers",
    )

    def __str__(self):
        return f"{self.code} - {self.name}"


class FiscalPeriod(TimeStampedModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="fiscal_periods"
    )
    year = models.PositiveIntegerField()
    period_number = models.PositiveIntegerField()
    name = models.CharField(max_length=50)  # e.g., Q1-2026, OCT-2026
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)

    class Meta:
        unique_together = ("organization", "year", "period_number")

    def __str__(self):
        return f"{self.name} ({self.start_date} to {self.end_date})"

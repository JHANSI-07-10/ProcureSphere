from django.contrib import admin

from apps.organization.models import CostCenter, FiscalPeriod
from .models import Budget, BudgetReservation, SpendLedger


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        "cost_center",
        "fiscal_period",
        "allocated_amount",
        "reserved_amount",
        "committed_amount",
        "actual_amount",
        "available_amount",
        "allow_overspend",
    )
    list_filter = ("cost_center", "fiscal_period", "allow_overspend")
    search_fields = ("cost_center__code", "cost_center__name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(cost_center__department__organization=request.user.effective_organization)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            if db_field.name == "cost_center":
                kwargs["queryset"] = CostCenter.objects.filter(department__organization=org)
            elif db_field.name == "fiscal_period":
                kwargs["queryset"] = FiscalPeriod.objects.filter(organization=org)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(BudgetReservation)
class BudgetReservationAdmin(admin.ModelAdmin):
    list_display = ("budget", "requisition", "amount", "status", "created_at")
    list_filter = ("status", "budget__cost_center")
    search_fields = ("requisition__pr_number",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(budget__cost_center__department__organization=request.user.effective_organization)
        return qs


@admin.register(SpendLedger)
class SpendLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "budget",
        "entry_type",
        "amount",
        "reference_number",
        "description",
        "created_at",
    )
    list_filter = ("entry_type", "budget__cost_center")
    search_fields = ("reference_number", "description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(budget__cost_center__department__organization=request.user.effective_organization)
        return qs


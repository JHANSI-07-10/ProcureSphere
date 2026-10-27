from django.contrib import admin
from django.db.models import Q

from apps.contracts.models import (
    Contract,
    ContractAlert,
    ContractMilestone,
    ContractVersion,
)


class ContractVersionInline(admin.TabularInline):
    model = ContractVersion
    extra = 0


class ContractMilestoneInline(admin.TabularInline):
    model = ContractMilestone
    extra = 1


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "contract_number",
        "title",
        "version",
        "vendor",
        "status",
        "contract_value",
        "start_date",
        "end_date",
        "contract_owner",
    )
    list_filter = ("status", "vendor")
    search_fields = (
        "contract_number",
        "title",
        "vendor__legal_name",
    )
    inlines = [ContractVersionInline, ContractMilestoneInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            return qs.filter(
                Q(contract_owner__organization=org)
                | Q(contract_owner__department__organization=org)
                | Q(po__cost_center__department__organization=org)
            )
        return qs


@admin.register(ContractAlert)
class ContractAlertAdmin(admin.ModelAdmin):
    list_display = (
        "contract",
        "alert_type",
        "triggered_at",
        "is_processed",
    )
    list_filter = ("alert_type", "is_processed")
    search_fields = ("contract__contract_number", "message")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            return qs.filter(
                Q(contract__contract_owner__organization=org)
                | Q(contract__contract_owner__department__organization=org)
                | Q(contract__po__cost_center__department__organization=org)
            )
        return qs


admin.site.register(ContractVersion)
admin.site.register(ContractMilestone)

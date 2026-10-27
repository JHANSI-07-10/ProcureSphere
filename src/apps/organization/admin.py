from django.contrib import admin

from .models import CostCenter, Department, FiscalPeriod, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "tax_identifier", "is_active", "created_at")
    search_fields = ("name", "code")
    fields = ("name", "code", "tax_identifier", "address", "is_active")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "code")
    fields = ("organization", "name", "code", "description")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CostCenter)
class CostCenterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department")
    list_filter = ("department",)
    search_fields = ("code", "name")
    fields = ("department", "code", "name")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(FiscalPeriod)
class FiscalPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "organization",
        "year",
        "period_number",
        "start_date",
        "end_date",
        "is_closed",
    )
    list_filter = ("organization", "year", "is_closed")
    fields = (
        "organization",
        "name",
        "year",
        "period_number",
        "start_date",
        "end_date",
        "is_closed",
    )

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

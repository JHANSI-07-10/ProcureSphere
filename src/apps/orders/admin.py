from django.contrib import admin

from .models import DeliverySchedule, POAmendment, POLine, PurchaseOrder


class POLineInline(admin.TabularInline):
    model = POLine
    extra = 0


class POAmendmentInline(admin.TabularInline):
    model = POAmendment
    extra = 0
    readonly_fields = (
        "amendment_number",
        "reason",
        "created_at",
        "previous_version_snapshot",
    )


class DeliveryScheduleInline(admin.TabularInline):
    model = DeliverySchedule
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "po_number",
        "vendor",
        "version",
        "requisition",
        "cost_center",
        "status",
        "total_amount",
        "created_at",
        "acknowledged_at",
    )
    list_filter = ("status", "version", "cost_center")
    search_fields = ("po_number", "vendor__legal_name")
    inlines = [POLineInline, DeliveryScheduleInline, POAmendmentInline]
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(cost_center__department__organization=request.user.effective_organization)
        return qs


@admin.register(POLine)
class POLineAdmin(admin.ModelAdmin):
    list_display = (
        "po",
        "item_description",
        "quantity",
        "unit_price",
        "line_total",
    )
    search_fields = ("po__po_number", "item_description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(po__cost_center__department__organization=request.user.effective_organization)
        return qs


@admin.register(POAmendment)
class POAmendmentAdmin(admin.ModelAdmin):
    list_display = ("po", "amendment_number", "reason", "created_at")
    search_fields = ("po__po_number", "reason")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(po__cost_center__department__organization=request.user.effective_organization)
        return qs


@admin.register(DeliverySchedule)
class DeliveryScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "po",
        "po_line",
        "expected_delivery_date",
        "quantity_expected",
        "status",
    )
    list_filter = ("status",)
    search_fields = ("po__po_number", "po_line__item_description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(po__cost_center__department__organization=request.user.effective_organization)
        return qs

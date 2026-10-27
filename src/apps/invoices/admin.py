from django.contrib import admin

from apps.core.admin_site import RoleBasedModelAdmin, register_model
from apps.invoices.models import InvoiceLine, MatchException, SupplierInvoice


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 1


class MatchExceptionInline(admin.StackedInline):
    model = MatchException
    extra = 0


class SupplierInvoiceAdmin(RoleBasedModelAdmin):
    list_display = (
        "invoice_number",
        "vendor",
        "po",
        "total_amount",
        "status",
        "invoice_date",
        "due_date",
        "created_at",
    )
    list_filter = ("status", "vendor")
    search_fields = (
        "invoice_number",
        "vendor__legal_name",
        "po__po_number",
    )
    inlines = [InvoiceLineInline, MatchExceptionInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(po__cost_center__department__organization=request.user.effective_organization)
        return qs


class MatchExceptionAdmin(RoleBasedModelAdmin):
    list_display = (
        "invoice",
        "exception_type",
        "variance_amount",
        "status",
        "resolved_by",
        "created_at",
    )
    list_filter = ("status", "exception_type")
    search_fields = ("invoice__invoice_number", "description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(invoice__po__cost_center__department__organization=request.user.effective_organization)
        return qs


register_model(SupplierInvoice, SupplierInvoiceAdmin)
register_model(InvoiceLine)
register_model(MatchException, MatchExceptionAdmin)


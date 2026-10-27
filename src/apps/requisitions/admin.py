from django.contrib import admin

from apps.core.admin_site import RoleBasedModelAdmin, register_model
from apps.requisitions.models import PRAttachment, PRLine, PurchaseRequisition


class PRLineInline(admin.TabularInline):
    model = PRLine
    extra = 0


class PRAttachmentInline(admin.TabularInline):
    model = PRAttachment
    extra = 1


class PurchaseRequisitionAdmin(RoleBasedModelAdmin):
    list_display = (
        "pr_number",
        "title",
        "requester",
        "department",
        "cost_center",
        "total_amount",
        "status",
        "created_at",
    )
    list_filter = ("status", "department", "cost_center")
    search_fields = ("pr_number", "title", "requester__email")
    inlines = [PRLineInline, PRAttachmentInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(department__organization=request.user.effective_organization)
        return qs


class PRLineAdmin(RoleBasedModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(requisition__department__organization=request.user.effective_organization)
        return qs


class PRAttachmentAdmin(RoleBasedModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(requisition__department__organization=request.user.effective_organization)
        return qs


register_model(PurchaseRequisition, PurchaseRequisitionAdmin)
register_model(PRLine, PRLineAdmin)
register_model(PRAttachment, PRAttachmentAdmin)


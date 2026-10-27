from django.contrib import admin
from django.db.models import Q

from apps.accounts.models import User
from apps.organization.models import CostCenter, Department
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "actor",
        "action",
        "target_model",
        "target_object_id",
        "ip_address",
        "request_id",
    )
    list_filter = ("action", "target_model", "timestamp")
    search_fields = (
        "actor__email",
        "target_model",
        "target_object_id",
        "request_id",
    )
    readonly_fields = (
        "id",
        "timestamp",
        "actor",
        "action",
        "target_model",
        "target_object_id",
        "previous_state",
        "new_state",
        "ip_address",
        "request_id",
        "user_agent",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            scoped_org = request.user.effective_organization
            dept_ids = [str(x) for x in Department.objects.filter(organization=scoped_org).values_list("id", flat=True)]
            cc_ids = [str(x) for x in CostCenter.objects.filter(department__organization=scoped_org).values_list("id", flat=True)]
            user_ids = [str(x) for x in User.objects.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org)).values_list("id", flat=True)]

            return qs.filter(
                Q(actor__organization=scoped_org)
                | Q(actor__department__organization=scoped_org)
                | Q(target_model="Organization", target_object_id=str(scoped_org.id))
                | Q(target_model="Department", target_object_id__in=dept_ids)
                | Q(target_model="CostCenter", target_object_id__in=cc_ids)
                | Q(target_model="User", target_object_id__in=user_ids)
            )
        return qs

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


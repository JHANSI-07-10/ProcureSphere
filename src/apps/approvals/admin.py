from django.contrib import admin
from django.db.models import Q

from apps.organization.models import Department
from .models import ApprovalAction, ApprovalDelegate, ApprovalPolicy, ApprovalStep


class ApprovalStepInline(admin.TabularInline):
    model = ApprovalStep
    extra = 0


@admin.register(ApprovalPolicy)
class ApprovalPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "module",
        "department",
        "min_amount",
        "max_amount",
        "is_active",
        "created_at",
    )
    list_filter = ("module", "is_active", "department")
    search_fields = ("name",)
    inlines = [ApprovalStepInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(department__organization=request.user.effective_organization)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            if db_field.name == "department":
                kwargs["queryset"] = Department.objects.filter(organization=org)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ApprovalStep)
class ApprovalStepAdmin(admin.ModelAdmin):
    list_display = (
        "policy",
        "step_number",
        "approver_role",
        "specific_approver",
    )
    list_filter = ("approver_role",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(policy__department__organization=request.user.effective_organization)
        return qs


@admin.register(ApprovalAction)
class ApprovalActionAdmin(admin.ModelAdmin):
    list_display = (
        "target_model_name",
        "target_object_id",
        "actor",
        "action",
        "previous_state",
        "new_state",
        "comments",
        "created_at",
    )
    list_filter = ("action", "target_model_name")
    search_fields = (
        "actor__email",
        "target_model_name",
        "target_object_id",
        "comments",
    )
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            return qs.filter(Q(actor__organization=org) | Q(actor__department__organization=org))
        return qs


@admin.register(ApprovalDelegate)
class ApprovalDelegateAdmin(admin.ModelAdmin):
    list_display = (
        "approver",
        "delegate",
        "start_date",
        "end_date",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("approver__email", "delegate__email")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            return qs.filter(Q(approver__organization=org) | Q(approver__department__organization=org))
        return qs


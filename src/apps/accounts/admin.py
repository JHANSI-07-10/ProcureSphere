from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "created_at")
    search_fields = ("name", "code")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "email",
        "first_name",
        "last_name",
        "role",
        "organization",
        "department",
        "cost_center",
        "vendor",
        "is_staff",
        "is_superuser",
        "is_active",
    )
    list_filter = (
        "role",
        "organization",
        "is_staff",
        "is_superuser",
        "is_active",
        "department",
        "cost_center",
    )
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            return qs.filter(Q(organization=org) | Q(department__organization=org))
        return qs

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.extend(["is_superuser", "user_permissions"])
        return readonly

    def has_module_permission(self, request):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if obj and not request.user.is_superuser:
            scoped_org = getattr(request.user, "effective_organization", None)
            if scoped_org:
                user_org_id = obj.organization_id or (obj.department.organization_id if obj.department else None)
                if user_org_id != scoped_org.id:
                    return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if obj and not request.user.is_superuser:
            scoped_org = getattr(request.user, "effective_organization", None)
            if scoped_org:
                user_org_id = obj.organization_id or (obj.department.organization_id if obj.department else None)
                if user_org_id != scoped_org.id:
                    return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and not request.user.is_superuser:
            scoped_org = getattr(request.user, "effective_organization", None)
            if scoped_org:
                user_org_id = obj.organization_id or (obj.department.organization_id if obj.department else None)
                if user_org_id != scoped_org.id:
                    return False
        return super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            if db_field.name == "organization":
                kwargs["queryset"] = Organization.objects.filter(id=org.id)
            elif db_field.name == "department":
                kwargs["queryset"] = Department.objects.filter(organization=org)
            elif db_field.name == "cost_center":
                kwargs["queryset"] = CostCenter.objects.filter(department__organization=org)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            obj.organization = request.user.effective_organization
            obj.is_superuser = False
        super().save_model(request, obj, form, change)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("first_name", "last_name", "phone_number")},
        ),
        (
            "Organization & Scoping",
            {"fields": ("role", "organization", "department", "cost_center", "vendor")},
        ),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "user_permissions")},
        ),
        (
            "Important dates",
            {"fields": ("last_login", "date_joined")},
        ),
    )

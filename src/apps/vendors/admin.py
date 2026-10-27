from django.contrib import admin
from django.db.models import Q

from apps.accounts.models import Role
from .models import Vendor, VendorCategory, VendorContact, VendorDocument, VendorRiskRecord


class BaseTenantAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_change_permission(request, obj)

    def has_add_permission(self, request):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_delete_permission(request, obj)


@admin.register(VendorCategory)
class VendorCategoryAdmin(BaseTenantAdmin):
    list_display = ("name", "code", "description", "created_at")
    search_fields = ("name", "code")


@admin.register(VendorDocument)
class VendorDocumentAdmin(BaseTenantAdmin):
    list_display = ("title", "vendor", "document_type", "is_verified", "expiry_date", "created_at")
    list_filter = ("document_type", "is_verified")
    search_fields = ("title", "vendor__legal_name", "vendor__vendor_number")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        scoped_org = getattr(request.user, "effective_organization", None)
        if not scoped_org:
            return qs.none()
        return qs.filter(
            Q(vendor__organization=scoped_org)
            | Q(vendor__created_by__organization=scoped_org)
            | Q(vendor__created_by__department__organization=scoped_org)
        )


class VendorDocumentInline(admin.TabularInline):
    model = VendorDocument
    extra = 0
    fields = ("title", "document_type", "file", "is_verified", "expiry_date")


class VendorContactInline(admin.TabularInline):
    model = VendorContact
    extra = 0


class VendorRiskRecordInline(admin.TabularInline):
    model = VendorRiskRecord
    extra = 0
    readonly_fields = ("created_at", "assessed_by")


@admin.register(Vendor)
class VendorAdmin(BaseTenantAdmin):
    list_display = (
        "vendor_number",
        "legal_name",
        "trade_name",
        "organization",
        "tax_identification_number",
        "category",
        "status",
        "email",
        "phone",
        "created_at",
    )
    list_filter = ("status", "category", "organization")
    search_fields = (
        "legal_name",
        "trade_name",
        "vendor_number",
        "tax_identification_number",
        "email",
    )
    inlines = [VendorContactInline, VendorDocumentInline, VendorRiskRecordInline]
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            return qs.filter(
                Q(organization=scoped_org)
                | Q(created_by__organization=scoped_org)
                | Q(created_by__department__organization=scoped_org)
            )
        if request.user.is_superuser:
            return qs
        return qs.none()

    def save_model(self, request, obj, form, change):
        scoped_org = getattr(request.user, "effective_organization", None)
        if not change and scoped_org and not obj.organization:
            obj.organization = scoped_org
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "organization":
            scoped_org = getattr(request.user, "effective_organization", None)
            if scoped_org:
                kwargs["queryset"] = scoped_org.__class__.objects.filter(pk=scoped_org.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(VendorContact)
class VendorContactAdmin(BaseTenantAdmin):
    list_display = ("vendor", "first_name", "last_name", "email", "phone", "is_primary")
    search_fields = ("first_name", "last_name", "email", "vendor__legal_name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            return qs.filter(
                Q(vendor__organization=scoped_org)
                | Q(vendor__created_by__organization=scoped_org)
                | Q(vendor__created_by__department__organization=scoped_org)
            )
        if request.user.is_superuser:
            return qs
        return qs.none()


@admin.register(VendorRiskRecord)
class VendorRiskRecordAdmin(BaseTenantAdmin):
    list_display = ("vendor", "risk_level", "assessed_by", "created_at")
    list_filter = ("risk_level",)
    search_fields = ("vendor__legal_name", "vendor__vendor_number", "assessment_notes")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            return qs.filter(
                Q(vendor__organization=scoped_org)
                | Q(vendor__created_by__organization=scoped_org)
                | Q(vendor__created_by__department__organization=scoped_org)
            )
        if request.user.is_superuser:
            return qs
        return qs.none()



from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.utils.html import format_html

from apps.accounts.models import Role, User
from .models import CostCenter, Department, FiscalPeriod, Organization
from .services import create_organization_with_admin_service


class OrganizationDjangoAdminForm(forms.ModelForm):
    admin_email = forms.EmailField(
        label="Admin Email",
        required=False,
        help_text="Email address for the initial Organization Administrator. Required when creating a new organization.",
    )
    admin_first_name = forms.CharField(
        label="Admin First Name",
        max_length=150,
        required=False,
    )
    admin_last_name = forms.CharField(
        label="Admin Last Name",
        max_length=150,
        required=False,
    )
    admin_password = forms.CharField(
        label="Admin Temporary Password",
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to auto-generate a secure temporary password.",
    )
    send_welcome_email = forms.BooleanField(
        label="Send Welcome Email",
        initial=True,
        required=False,
        help_text="Dispatch welcome email with login details to the Administrator.",
    )

    class Meta:
        model = Organization
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        is_create = not self.instance or not self.instance.pk
        if "admin_user" in self.fields:
            self.fields["admin_user"].required = False
            self.fields["admin_user"].empty_label = "-- Select Existing Admin (Optional) --"
            self.fields["admin_user"].queryset = User.objects.filter(
                Q(role__code=Role.SUPER_ADMIN) | Q(is_superuser=True), is_active=True
            ).order_by("email")
        if is_create:
            self.fields["admin_email"].required = False
        else:
            self.fields["admin_email"].help_text = (
                "Leave blank if you do not wish to re-assign or change the admin user."
            )

    def clean(self):
        cleaned_data = super().clean()
        admin_user = cleaned_data.get("admin_user")
        admin_email = cleaned_data.get("admin_email")
        is_create = not self.instance or not self.instance.pk
        if is_create and not admin_user and not admin_email:
            self.add_error("admin_email", "Please provide an Admin Email or select an Assigned Admin User.")
        return cleaned_data


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    form = OrganizationDjangoAdminForm
    list_display = ("name", "code", "admin_user_display", "tax_identifier", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "admin_user__email")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            return qs.filter(id=scoped_org.id)
        return qs

    def has_add_permission(self, request):
        if getattr(request.user, "effective_organization", None):
            return False
        if not (request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN):
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        if getattr(request.user, "effective_organization", None):
            return False
        if not (request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN):
            return False
        return super().has_delete_permission(request, obj)

    def has_module_permission(self, request):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        scoped_org = getattr(request.user, "effective_organization", None)
        if obj and scoped_org:
            if obj.id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        scoped_org = getattr(request.user, "effective_organization", None)
        if obj and scoped_org:
            if obj.id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_change_permission(request, obj)

    def get_fieldsets(self, request, obj=None):
        if not obj:
            return (
                (
                    "Organization Details",
                    {
                        "fields": ("code", "name", "tax_identifier", "address", "is_active"),
                        "description": "Core legal entity and master data information.",
                    },
                ),
                (
                    "Initial Organization Administrator",
                    {
                        "fields": (
                            "admin_user",
                            "admin_email",
                            "admin_first_name",
                            "admin_last_name",
                            "admin_password",
                            "send_welcome_email",
                        ),
                        "description": (
                            "Specify the Organization Administrator credentials or select an existing administrator. "
                            "This user will receive administrative access to the organization's dashboard."
                        ),
                    },
                ),
            )
        return (
            (
                "Organization Details",
                {
                    "fields": ("code", "name", "tax_identifier", "address", "is_active"),
                },
            ),
            (
                "Administrator Settings",
                {
                    "fields": ("admin_user", "admin_email", "admin_password", "send_welcome_email"),
                    "description": "Current organization administrator or re-provision administrator credentials.",
                },
            ),
        )

    def admin_user_display(self, obj):
        if obj.admin_user:
            return format_html(
                '<a href="/admin/accounts/user/{}/change/">{}</a>',
                obj.admin_user.pk,
                obj.admin_user.email,
            )
        return "—"

    admin_user_display.short_description = "Admin User"

    def save_model(self, request, obj, form, change):
        if not change:
            admin_user_obj = form.cleaned_data.get("admin_user")
            admin_email = (form.cleaned_data.get("admin_email") or "").strip()
            if admin_user_obj and not admin_email:
                admin_email = admin_user_obj.email
            admin_first_name = form.cleaned_data.get("admin_first_name", "")
            admin_last_name = form.cleaned_data.get("admin_last_name", "")
            admin_password = form.cleaned_data.get("admin_password")
            send_email = form.cleaned_data.get("send_welcome_email", True)

            org, admin_user, raw_pwd = create_organization_with_admin_service(
                name=obj.name,
                code=obj.code,
                tax_identifier=obj.tax_identifier,
                address=obj.address,
                is_active=obj.is_active,
                admin_email=admin_email,
                admin_first_name=admin_first_name,
                admin_last_name=admin_last_name,
                admin_password=admin_password,
                send_welcome_email_flag=send_email,
                request=request,
                actor=request.user,
            )
            if not org.created_by_id:
                org.created_by = request.user
                org.save(update_fields=["created_by"])
            obj.created_by = org.created_by
            obj.pk = org.pk
            obj.id = org.id
            obj.admin_user = admin_user
            if admin_user:
                email_status = getattr(org, "_welcome_email_status", None)
                if email_status == "SENT":
                    detail = f" Login email and password were sent to {admin_user.email}."
                    level = messages.SUCCESS
                elif email_status == "FAILED":
                    detail = f" The account was created, but the email could not be sent: {getattr(org, '_welcome_email_error', 'mail server error')}."
                    level = messages.WARNING
                else:
                    detail = ""
                    level = messages.SUCCESS
                self.message_user(
                    request,
                    f"Organization '{org.name}' ({org.code}) and Administrator account '{admin_user.email}' created successfully.{detail}",
                    level=level,
                )
        else:
            typed_email = (form.cleaned_data.get("admin_email") or "").strip()
            admin_password = form.cleaned_data.get("admin_password")
            send_email = form.cleaned_data.get("send_welcome_email", False)
            selected_admin = form.cleaned_data.get("admin_user")
            typed_password = (admin_password or "").strip()
            admin_email = typed_email
            if not admin_email and selected_admin:
                admin_email = selected_admin.email
            elif not admin_email and obj.admin_user:
                admin_email = obj.admin_user.email

            if typed_password:
                send_email = True

            super().save_model(request, obj, form, change)

            if admin_email:
                norm_email = admin_email.strip().lower()
                role, _ = Role.objects.get_or_create(
                    code=Role.SUPER_ADMIN,
                    defaults={"name": "Super Admin"},
                )
                from apps.accounts.password_utils import generate_secure_password

                password_to_send = (admin_password or "").strip()
                if send_email and not password_to_send:
                    password_to_send = generate_secure_password()

                user = User.objects.filter(email__iexact=norm_email).first()
                if not user:
                    user = User.objects.create_user(
                        email=norm_email,
                        password=password_to_send or None,
                        role=role,
                        organization=obj,
                    )
                user.organization = obj
                user.role = role
                user.is_staff = True
                if password_to_send:
                    user.set_password(password_to_send)
                    user.must_change_password = True
                user.save()
                obj.admin_user = user
                obj.save(update_fields=["admin_user"])
                if send_email and password_to_send:
                    from apps.notifications.email_service import send_user_welcome_email

                    status, error = send_user_welcome_email(
                        user=user,
                        temporary_password=password_to_send,
                        request=request,
                    )
                    if status == "SENT":
                        self.message_user(
                            request,
                            f"Login email and password were sent to {user.email}.",
                            level=messages.SUCCESS,
                        )
                    else:
                        self.message_user(
                            request,
                            f"Organization was saved, but the email to {user.email} could not be sent: {error}",
                            level=messages.ERROR,
                        )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "code")
    fields = ("organization", "name", "code", "description")


    def get_queryset(self, request):
        qs = super().get_queryset(request)
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            return qs.filter(organization=scoped_org)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            if db_field.name == "organization":
                kwargs["queryset"] = Organization.objects.filter(id=scoped_org.id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_module_permission(self, request):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        scoped_org = getattr(request.user, "effective_organization", None)
        if obj and scoped_org:
            if obj.organization_id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        scoped_org = getattr(request.user, "effective_organization", None)
        if obj and scoped_org:
            if obj.organization_id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            obj.organization = scoped_org
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

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            return qs.filter(department__organization=scoped_org)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        scoped_org = getattr(request.user, "effective_organization", None)
        if scoped_org:
            if db_field.name == "department":
                kwargs["queryset"] = Department.objects.filter(organization=scoped_org)
            elif db_field.name == "manager":
                kwargs["queryset"] = User.objects.filter(
                    Q(organization=scoped_org) | Q(department__organization=scoped_org)
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_module_permission(self, request):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        scoped_org = getattr(request.user, "effective_organization", None)
        if obj and scoped_org:
            if obj.department.organization_id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        scoped_org = getattr(request.user, "effective_organization", None)
        if obj and scoped_org:
            if obj.department.organization_id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_change_permission(request, obj)


def _organization_section_first(request, app_label=None):
    """Keep departments, cost centers, and fiscal periods off the admin home."""
    app_list = admin.AdminSite.get_app_list(admin.site, request, app_label)
    visible = []
    for app in app_list:
        if app["app_label"] == "organization":
            continue
        visible.append(app)
    visible.sort(key=lambda app: app["name"].lower())
    return visible


def _admin_index(request, extra_context=None):
    extra_context = dict(extra_context or {})
    scoped_org = getattr(request.user, "effective_organization", None) if not request.user.is_superuser else None
    is_global_superadmin = bool(request.user.is_superuser and not scoped_org)

    org_qs = Organization.objects.select_related("admin_user").order_by("name")
    if scoped_org:
        org_qs = org_qs.filter(id=scoped_org.id)

    extra_context["dashboard_organizations"] = org_qs
    extra_context["scoped_org"] = scoped_org
    extra_context["is_global_superadmin"] = is_global_superadmin
    extra_context["can_add_organization"] = is_global_superadmin

    try:
        from decimal import Decimal
        from django.db.models import Sum
        from django.utils import timezone
        from apps.accounts.models import User
        from apps.audit.models import AuditLog
        from apps.budgets.models import Budget
        from apps.invoices.models import MatchException
        from apps.organization.models import CostCenter, Department
        from apps.requisitions.models import PurchaseRequisition

        today = timezone.now().date()
        users_qs = User.objects.all()
        budgets_qs = Budget.objects.all()
        prs_qs = PurchaseRequisition.objects.filter(
            status__in=[
                PurchaseRequisition.STATUS_SUBMITTED,
                PurchaseRequisition.STATUS_MANAGER_REVIEW,
                PurchaseRequisition.STATUS_BUDGET_REVIEW,
            ]
        )
        exceptions_qs = MatchException.objects.filter(status=MatchException.STATUS_OPEN)
        audits_qs = AuditLog.objects.filter(timestamp__date=today)

        if scoped_org:
            users_qs = users_qs.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org))
            budgets_qs = budgets_qs.filter(cost_center__department__organization=scoped_org)
            prs_qs = prs_qs.filter(department__organization=scoped_org)
            exceptions_qs = exceptions_qs.filter(invoice__po__requisition__department__organization=scoped_org)
            audits_qs = audits_qs.filter(
                Q(actor__organization=scoped_org) | Q(actor__department__organization=scoped_org)
            )

        total_users = users_qs.count()
        budget_agg = budgets_qs.aggregate(total=Sum("allocated_amount"))["total"]
        allocated_budget = budget_agg if budget_agg is not None else Decimal("0.00")
        pending_pr_count = prs_qs.count()
        pending_exceptions = exceptions_qs.count()
        audits_today = audits_qs.count()

        extra_context["metrics"] = {
            "total_users": total_users,
            "allocated_budget": allocated_budget,
            "pending_pr_count": pending_pr_count,
            "pending_exceptions": pending_exceptions,
            "audits_today": audits_today,
        }
        extra_context["organizations"] = org_qs
        extra_context["total_organizations"] = 1 if scoped_org else Organization.objects.count()
        extra_context["active_organizations"] = (1 if scoped_org.is_active else 0) if scoped_org else Organization.objects.filter(is_active=True).count()
        extra_context["provisioned_admins"] = (
            Organization.objects.filter(admin_user__isnull=False).count()
            if is_global_superadmin
            else 1
        )
        extra_context["total_system_users"] = total_users
        extra_context["total_departments"] = Department.objects.filter(organization=scoped_org).count() if scoped_org else Department.objects.count()
        extra_context["total_cost_centers"] = CostCenter.objects.filter(department__organization=scoped_org).count() if scoped_org else CostCenter.objects.count()
    except Exception:
        pass

    return admin.AdminSite.index(admin.site, request, extra_context)


admin.site.get_app_list = _organization_section_first
admin.site.index = _admin_index
admin.site.site_header = "ProcureSphere 360 — Platform Administration"
admin.site.site_title = "ProcureSphere 360 Super Admin"
admin.site.index_title = "Super Admin Control Center & Multi-Tenant Manager"



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

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(organization=request.user.effective_organization)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            if db_field.name == "organization":
                kwargs["queryset"] = Organization.objects.filter(id=request.user.effective_organization.id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

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
            if not scoped_org or obj.organization_id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            return False
        if obj and not request.user.is_superuser:
            scoped_org = getattr(request.user, "effective_organization", None)
            if not scoped_org or obj.organization_id != scoped_org.id:
                return False
        if request.user.is_superuser or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN:
            return True
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            obj.organization = request.user.effective_organization
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

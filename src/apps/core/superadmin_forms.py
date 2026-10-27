from django import forms
from django.contrib.auth import password_validation

from apps.accounts.models import Role, User
from apps.accounts.password_utils import generate_secure_password
from apps.approvals.models import ApprovalPolicy
from apps.budgets.models import Budget
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from django.db.models import Q


def _get_form_scoped_org(request):
    if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
        return None
    effective_org = getattr(request.user, "effective_organization", None)
    if effective_org:
        return effective_org
    return None


class UserAdminForm(forms.ModelForm):
    password = forms.CharField(
        label="Password",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter password or generate",
                "id": "id_password",
                "autocomplete": "new-password",
            }
        ),
    )

    class Meta:
        model = User
        fields = ["email", "phone_number", "role", "department", "cost_center", "is_active"]
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "user@example.com"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone number"}),
            "role": forms.Select(attrs={"class": "form-select", "id": "id_role"}),
            "department": forms.Select(attrs={"class": "form-select", "id": "id_department"}),
            "cost_center": forms.Select(attrs={"class": "form-select", "id": "id_cost_center"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        scoped_org = _get_form_scoped_org(request)

        self.fields["email"].required = True
        self.fields["role"].queryset = Role.objects.all().order_by("name")
        self.fields["role"].empty_label = "-- Select Role --"

        dept_qs = Department.objects.select_related("organization").all().order_by("name")
        cc_qs = CostCenter.objects.select_related("department").all().order_by("name")
        if scoped_org:
            dept_qs = dept_qs.filter(organization=scoped_org)
            cc_qs = cc_qs.filter(department__organization=scoped_org)

        self.fields["department"].queryset = dept_qs
        self.fields["department"].empty_label = "-- Select Department (Optional) --"
        self.fields["department"].required = False

        self.fields["cost_center"].queryset = cc_qs
        self.fields["cost_center"].empty_label = "-- Select Cost Center --"
        self.fields["cost_center"].required = False

        # In unbound GET edit mode, filter cost centers to the user's department for clean UI rendering.
        # On bound POST submission, keep all cost centers in queryset so clean() can catch cross-department
        # and duplicate assignments with descriptive domain error messages.
        if not self.is_bound and self.instance and self.instance.pk and self.instance.department_id:
            edit_cc_qs = CostCenter.objects.filter(department_id=self.instance.department_id)
            if scoped_org:
                edit_cc_qs = edit_cc_qs.filter(department__organization=scoped_org)
            self.fields["cost_center"].queryset = edit_cc_qs.order_by("name")

        if not self.instance.pk or getattr(self.instance, "_state", None) and self.instance._state.adding:
            self.initial.setdefault("is_active", True)
            self.fields["password"].help_text = (
                "Must contain at least 12 characters, including uppercase, lowercase, numbers, and special characters."
            )
        else:
            self.fields["password"].help_text = "Leave blank to keep existing password."

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if not email:
            raise forms.ValidationError("Email address is required.")
        qs = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk and not getattr(self.instance, "_state", None).adding:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"User with email '{email}' already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password", "").strip()
        is_creating = not self.instance.pk or (
            getattr(self.instance, "_state", None) and self.instance._state.adding
        )
        if is_creating:
            if not password:
                # If password was omitted or empty, automatically generate a secure random password
                password = generate_secure_password()
                return password

            # Validate password strength with Django's password validators
            user_temp = User(
                email=self.cleaned_data.get("email") or self.data.get("email", ""),
                username=self.cleaned_data.get("email") or self.data.get("email", ""),
            )
            password_validation.validate_password(password, user=user_temp)
            return password
        else:
            # Edit mode: optional
            if password:
                password_validation.validate_password(password, user=self.instance)
            return password

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        department = cleaned_data.get("department")
        cost_center = cleaned_data.get("cost_center")

        is_requester = role and getattr(role, "code", "") == Role.REQUESTER

        if is_requester:
            if not department:
                self.add_error("department", "Department is required for Requester accounts.")
            if not cost_center:
                if department and CostCenter.objects.filter(department=department).exists():
                    self.add_error("cost_center", "Cost Center is mandatory for Requester accounts.")
            elif department and cost_center.department_id != department.id:
                self.add_error(
                    "cost_center",
                    f"Cost Center '{cost_center.code}' does not belong to the selected Department '{department.name}'.",
                )
            elif cost_center:
                # Enforce one designated Requester per Cost Center
                existing_req = User.objects.filter(
                    cost_center=cost_center,
                    role__code=Role.REQUESTER,
                    is_active=True,
                )
                if (
                    self.instance
                    and self.instance.pk
                    and not getattr(self.instance, "_state", None).adding
                ):
                    existing_req = existing_req.exclude(pk=self.instance.pk)
                if existing_req.exists():
                    self.add_error(
                        "cost_center",
                        f"Cost Center '{cost_center.code}' already has a designated Requester ({existing_req.first().email}). Each Cost Center can have only one designated Requester.",
                    )
        else:
            # For non-requester roles, clear cost center to preserve existing role behavior
            cleaned_data["cost_center"] = None

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("email"):
            user.username = self.cleaned_data["email"]
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        elif not user.password:
            temp_pwd = generate_secure_password()
            user.set_password(temp_pwd)
        scoped_org = _get_form_scoped_org(self.request)
        if scoped_org:
            user.organization = scoped_org
        if commit:
            user.save()
        return user


class OrganizationAdminForm(forms.ModelForm):
    admin_email = forms.EmailField(
        label="Admin Email",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "admin@organization.com"}),
        help_text="Primary administrator user email for this organization.",
    )
    admin_first_name = forms.CharField(
        label="Admin First Name",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"}),
    )
    admin_last_name = forms.CharField(
        label="Admin Last Name",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name"}),
    )
    admin_password = forms.CharField(
        label="Temporary Password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Leave blank to auto-generate"}),
        help_text="Leave blank to generate a secure temporary password.",
    )

    class Meta:
        model = Organization
        fields = ["code", "name", "tax_identifier", "address", "is_active", "admin_user"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. HPE-GLOBAL"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Hewlett Packard Enterprise"}),
            "tax_identifier": forms.TextInput(attrs={"class": "form-control", "placeholder": "Tax ID / EIN"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Registered address"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "admin_user": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        is_create = not self.instance or not self.instance.pk
        self.fields["admin_user"].required = False
        self.fields["admin_user"].empty_label = "-- Select Existing Admin (Optional) --"
        self.fields["admin_user"].queryset = User.objects.filter(
            role__code=Role.SUPER_ADMIN, is_active=True
        ).order_by("email")
        if is_create:
            self.fields["admin_email"].required = True
        else:
            self.fields["admin_email"].required = False
            if self.instance and self.instance.admin_user:
                self.fields["admin_email"].initial = self.instance.admin_user.email
                self.fields["admin_first_name"].initial = self.instance.admin_user.first_name
                self.fields["admin_last_name"].initial = self.instance.admin_user.last_name

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        if not code:
            raise forms.ValidationError("Organization code is required.")
        qs = Organization.objects.filter(code=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Organization code '{code}' already exists.")
        return code


class DepartmentAdminForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["organization", "name", "code", "description"]
        widgets = {
            "organization": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Human Resources"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. HR"}),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Department description (optional)"}
            ),
        }

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        scoped_org = _get_form_scoped_org(request)
        if scoped_org:
            self.fields["organization"].queryset = Organization.objects.filter(id=scoped_org.id)
            self.fields["organization"].initial = scoped_org
            self.fields["organization"].empty_label = None
        else:
            self.fields["organization"].queryset = Organization.objects.filter(is_active=True).order_by("name")
            self.fields["organization"].empty_label = "-- Select Organization --"

    def clean_organization(self):
        org = self.cleaned_data.get("organization")
        scoped_org = _get_form_scoped_org(self.request)
        if scoped_org and org and org.id != scoped_org.id:
            raise forms.ValidationError("You cannot assign a department to another organization.")
        return org

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        if not code:
            raise forms.ValidationError("Department code is required.")
        qs = Department.objects.filter(code=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Department code '{code}' already exists. Code must be unique.")
        return code


class CostCenterAdminForm(forms.ModelForm):
    manager = forms.EmailField(
        required=False,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter manager email",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = CostCenter
        fields = ["code", "name", "department"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. CC-IT-101"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. IT Operations"}),
            "department": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        scoped_org = _get_form_scoped_org(request)
        dept_qs = Department.objects.select_related("organization").all().order_by("name")
        if scoped_org:
            dept_qs = dept_qs.filter(organization=scoped_org)
        self.fields["department"].queryset = dept_qs
        self.fields["department"].empty_label = "-- Select Department --"
        if self.instance.pk and self.instance.manager_id and not self.is_bound:
            self.initial["manager"] = self.instance.manager.email

    def clean_department(self):
        dept = self.cleaned_data.get("department")
        scoped_org = _get_form_scoped_org(self.request)
        if scoped_org and dept and dept.organization_id != scoped_org.id:
            raise forms.ValidationError("You cannot assign a cost center to another organization's department.")
        return dept

    def clean_manager(self):
        raw = (self.cleaned_data.get("manager") or "").strip()
        if not raw:
            return None
        user_qs = User.objects.filter(email__iexact=raw, is_active=True)
        scoped_org = _get_form_scoped_org(self.request)
        if scoped_org:
            user_qs = user_qs.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org))
        user = user_qs.first()
        if user is None:
            raise forms.ValidationError("No active user exists with this email in your organization.")
        return user

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.manager = self.cleaned_data.get("manager")
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        if not code:
            raise forms.ValidationError("Cost center code is required.")
        qs = CostCenter.objects.filter(code=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Cost center code '{code}' already exists. Code must be unique.")
        return code


class BudgetAdminForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ["cost_center", "fiscal_period", "allocated_amount", "allow_overspend"]
        widgets = {
            "cost_center": forms.Select(attrs={"class": "form-select"}),
            "fiscal_period": forms.Select(attrs={"class": "form-select"}),
            "allocated_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "allow_overspend": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        scoped_org = _get_form_scoped_org(request)
        cc_qs = CostCenter.objects.select_related("department").all().order_by("name")
        fp_qs = FiscalPeriod.objects.all().order_by("-year", "period_number")
        if scoped_org:
            cc_qs = cc_qs.filter(department__organization=scoped_org)
            fp_qs = fp_qs.filter(organization=scoped_org)
        self.fields["cost_center"].queryset = cc_qs
        self.fields["fiscal_period"].queryset = fp_qs


class PolicyAdminForm(forms.ModelForm):
    class Meta:
        model = ApprovalPolicy
        fields = ["name", "module", "department", "min_amount", "max_amount", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "module": forms.Select(attrs={"class": "form-select"}),
            "department": forms.Select(attrs={"class": "form-select"}),
            "min_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "max_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        scoped_org = _get_form_scoped_org(request)
        dept_qs = Department.objects.all().order_by("name")
        if scoped_org:
            dept_qs = dept_qs.filter(organization=scoped_org)
        self.fields["department"].queryset = dept_qs

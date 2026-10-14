from django import forms

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalPolicy
from apps.budgets.models import Budget
from apps.organization.models import CostCenter, Department, Organization


class UserAdminForm(forms.ModelForm):
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["role"].queryset = Role.objects.all().order_by("name")
        self.fields["role"].empty_label = "-- Select Role --"
        self.fields["department"].queryset = (
            Department.objects.select_related("organization").all().order_by("name")
        )
        self.fields["department"].empty_label = "-- Select Department (Optional) --"
        self.fields["department"].required = False

        self.fields["cost_center"].queryset = (
            CostCenter.objects.select_related("department").all().order_by("name")
        )
        self.fields["cost_center"].empty_label = "-- Select Cost Center --"
        self.fields["cost_center"].required = False

        # In unbound GET edit mode, filter cost centers to the user's department for clean UI rendering.
        # On bound POST submission, keep all cost centers in queryset so clean() can catch cross-department
        # and duplicate assignments with descriptive domain error messages.
        if not self.is_bound and self.instance and self.instance.pk and self.instance.department_id:
            self.fields["cost_center"].queryset = CostCenter.objects.filter(
                department_id=self.instance.department_id
            ).order_by("name")

        if not self.instance.pk or getattr(self.instance, "_state", None) and self.instance._state.adding:
            self.initial.setdefault("is_active", True)

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
        if not user.password:
            user.set_password("Password123!")
        if commit:
            user.save()
        return user


class OrganizationAdminForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["code", "name", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.filter(is_active=True).order_by("name")
        self.fields["organization"].empty_label = "-- Select Organization --"

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
    class Meta:
        model = CostCenter
        fields = ["code", "name", "department", "manager"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. CC-IT-101"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. IT Operations"}),
            "department": forms.Select(attrs={"class": "form-select"}),
            "manager": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = (
            Department.objects.select_related("organization").all().order_by("name")
        )
        self.fields["department"].empty_label = "-- Select Department --"
        self.fields["manager"].queryset = (
            User.objects.filter(is_active=True).order_by("email")
        )
        self.fields["manager"].empty_label = "-- Select Manager (Optional) --"
        self.fields["manager"].required = False

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

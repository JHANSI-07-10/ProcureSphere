from django import forms

from apps.accounts.models import User
from apps.approvals.models import ApprovalPolicy
from apps.budgets.models import Budget
from apps.organization.models import CostCenter, Department, Organization


class UserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["email", "phone_number", "role", "department", "is_active"]
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "role": forms.Select(attrs={"class": "form-select"}),
            "department": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


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

from django import forms

from .models import Vendor, VendorCategory, VendorRiskRecord


class VendorEditForm(forms.Form):
    """
    Procurement Executive: edit vendor master information.
    Allows updating name, email, phone, address, bank and category.
    Tax ID / registration number changes are intentionally excluded (require legal justification).
    """
    legal_name = forms.CharField(max_length=255, label="Legal name")
    trade_name = forms.CharField(max_length=255, required=False, label="Trade / brand name")
    email = forms.EmailField(label="Primary email")
    phone = forms.CharField(max_length=30, required=False, label="Phone")
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Address")
    category = forms.ModelChoiceField(
        queryset=VendorCategory.objects.order_by("name"), label="Category"
    )
    bank_name = forms.CharField(max_length=150, required=False, label="Bank name")
    bank_account_number = forms.CharField(max_length=100, required=False, label="Bank account no.")
    bank_routing_code = forms.CharField(max_length=100, required=False, label="Bank routing / IFSC")


class VendorRegistrationForm(forms.Form):
    legal_name = forms.CharField(max_length=255)
    trade_name = forms.CharField(max_length=255, required=False)
    tax_identification_number = forms.CharField(max_length=100)
    registration_number = forms.CharField(max_length=100, required=False)
    category = forms.ModelChoiceField(queryset=VendorCategory.objects.order_by("name"))
    email = forms.EmailField()
    phone = forms.CharField(max_length=30, required=False)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    bank_name = forms.CharField(max_length=150, required=False)
    bank_account_number = forms.CharField(max_length=100, required=False)
    bank_routing_code = forms.CharField(max_length=100, required=False)

    def clean_tax_identification_number(self):
        tin = self.cleaned_data["tax_identification_number"].strip()
        if Vendor.objects.filter(tax_identification_number=tin).exists():
            raise forms.ValidationError("A vendor with this tax ID already exists.")
        return tin


class RiskAssessmentForm(forms.Form):
    risk_level = forms.ChoiceField(
        choices=VendorRiskRecord.RISK_CHOICES,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    risk_flags = forms.MultipleChoiceField(
        choices=VendorRiskRecord.RISK_FLAG_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control form-control-sm"})
    )


class GovernanceStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Vendor.STATUS_CHOICES)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class VendorProfileUpdateForm(forms.Form):
    """
    Vendor Dashboard self-service company profile update form.
    Validates phone number to strictly enforce country code (+91) followed by 10 digits.
    """
    trade_name = forms.CharField(max_length=255, required=False)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=True)
    phone = forms.CharField(
        max_length=25,
        required=True,
        error_messages={
            "required": "Contact phone number is required.",
        },
    )
    bank_name = forms.CharField(max_length=150, required=False)
    bank_account_number = forms.CharField(max_length=100, required=False)
    bank_routing_code = forms.CharField(max_length=100, required=False)

    def clean_phone(self):
        import re

        raw_phone = self.cleaned_data.get("phone", "").strip()
        clean_phone = re.sub(r"[\s\-\(\)\.]", "", raw_phone)

        # If user submits 12 digits starting with 91 (e.g. 919876543210), normalize to +91XXXXXXXXXX
        if re.match(r"^91\d{10}$", clean_phone):
            clean_phone = f"+{clean_phone}"

        # Strictly enforce +91 followed by exactly 10 digits
        if not re.match(r"^\+91\d{10}$", clean_phone):
            raise forms.ValidationError(
                "Phone number must have country code (+91) followed by exactly 10 digits (e.g., +919876543210)."
            )
        return clean_phone


class VendorContactForm(forms.Form):
    """
    Form for adding and editing authorized vendor contacts.
    Strictly validates primary/secondary contact email and phone format.
    """
    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=25, required=False)
    designation = forms.CharField(max_length=100, required=False)
    is_primary = forms.BooleanField(required=False)

    def clean_phone(self):
        import re

        raw_phone = self.cleaned_data.get("phone", "").strip()
        if not raw_phone:
            return ""
        clean_phone = re.sub(r"[\s\-\(\)\.]", "", raw_phone)
        if re.match(r"^91\d{10}$", clean_phone):
            clean_phone = f"+{clean_phone}"
        if not re.match(r"^\+91\d{10}$", clean_phone):
            raise forms.ValidationError(
                "Phone number must have country code (+91) followed by exactly 10 digits (e.g., +919876543210)."
            )
        return clean_phone


class VendorChangesRequestForm(forms.Form):
    """
    Form for procurement reviewers to request corrections/updates from a vendor.
    """
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Specify changes required..."}),
        required=True,
        error_messages={"required": "Please provide detailed instructions on what changes are required."},
    )



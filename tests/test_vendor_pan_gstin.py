"""
Tests for Vendor PAN and GSTIN fields, validations, data migration backfill, and KYC workflow integration.
Part of Step 6: Add Separate PAN and GSTIN Fields Safely.
"""

import importlib

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.core.validators import (
    extract_pan_from_gstin,
    is_valid_gstin,
    is_valid_pan,
    normalize_tax_identifier,
    validate_gstin,
    validate_pan_gstin_consistency,
    validate_pan_number,
)
from apps.vendors.forms import VendorProfileUpdateForm, VendorRegistrationForm
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import (
    register_vendor_service,
    update_vendor_profile_service,
)

migration_0008 = importlib.import_module(
    "apps.vendors.migrations.0008_vendor_pan_number_vendor_gstin_and_backfill"
)
backfill_pan_and_gstin = migration_0008.backfill_pan_and_gstin

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


# ==============================================================================
# 1. CENTRALIZED VALIDATOR TESTS (PAN, GSTIN, NORMALIZATION, EMBEDDED PAN)
# ==============================================================================


@pytest.mark.parametrize(
    "valid_pan",
    [
        "ABCDE1234F",
        "AAAAA0000A",
        "ZZZZZ9999Z",
        "  abcde1234f  ",  # Normalization test
    ],
)
def test_valid_pan_values(valid_pan):
    clean_pan = normalize_tax_identifier(valid_pan)
    assert is_valid_pan(clean_pan) is True
    # Should not raise
    validate_pan_number(clean_pan)


@pytest.mark.parametrize(
    "invalid_pan",
    [
        "ABCD1234F",      # 9 chars (short)
        "ABCDEF1234F",    # 11 chars (long)
        "1BCDE1234F",     # Starts with digit
        "ABCD11234F",     # 5th char digit instead of letter
        "ABCDE12345",     # Ends with digit
        "ABCDE-1234F",    # Contains hyphen
        "ABCDE1234!",     # Special character
    ],
)
def test_invalid_pan_values(invalid_pan):
    clean_pan = normalize_tax_identifier(invalid_pan)
    assert is_valid_pan(clean_pan) is False
    with pytest.raises(ValidationError):
        validate_pan_number(clean_pan)


@pytest.mark.parametrize(
    "valid_gstin",
    [
        "29ABCDE1234F1Z5",
        "07AAAAA0000A1Z2",
        "27ZZZZZ9999Z9ZA",
        "  29abcde1234f1z5  ",  # Normalization test
    ],
)
def test_valid_gstin_values(valid_gstin):
    clean_gst = normalize_tax_identifier(valid_gstin)
    assert is_valid_gstin(clean_gst) is True
    # Should not raise
    validate_gstin(clean_gst)


@pytest.mark.parametrize(
    "invalid_gstin",
    [
        "29ABCDE1234F1Z",     # 14 chars (short)
        "29ABCDE1234F1Z55",   # 16 chars (long)
        "ABABCDE1234F1Z5",    # First 2 chars not digits
        "29ABCDE123451Z5",    # Embedded PAN invalid (10th char digit)
        "29ABCDE1234F1A5",    # 14th char not 'Z'
        "29ABCDE-1234F1Z",    # Special chars
    ],
)
def test_invalid_gstin_values(invalid_gstin):
    clean_gst = normalize_tax_identifier(invalid_gstin)
    assert is_valid_gstin(clean_gst) is False
    with pytest.raises(ValidationError):
        validate_gstin(clean_gst)


def test_embedded_pan_extraction_and_matching():
    gstin = "29ABCDE1234F1Z5"
    extracted_pan = extract_pan_from_gstin(gstin)
    assert extracted_pan == "ABCDE1234F"

    # Matching PAN and GSTIN
    validate_pan_gstin_consistency("ABCDE1234F", "29ABCDE1234F1Z5")

    # Mismatched PAN and GSTIN
    with pytest.raises(ValidationError) as exc:
        validate_pan_gstin_consistency("XYZAB9999C", "29ABCDE1234F1Z5")
    assert "Embedded PAN" in str(exc.value) or "does not match" in str(exc.value)


# ==============================================================================
# 2. MODEL VALIDATION & PERSISTENCE
# ==============================================================================


def test_vendor_model_clean_and_save_normalization(db):
    cat = VendorCategory.objects.create(name="IT Services", code="IT")
    vendor = Vendor(
        legal_name="Tech Solutions Ltd",
        vendor_number="VND-2026-00099",
        tax_identification_number="29ABCDE1234F1Z5",
        pan_number="  abcde1234f  ",
        gstin="  29abcde1234f1z5  ",
        category=cat,
        email="tech@solutions.test",
        address="10 Silicon Valley, Bangalore",
    )
    # clean() should normalize and validate consistency
    vendor.clean()
    assert vendor.pan_number == "ABCDE1234F"
    assert vendor.gstin == "29ABCDE1234F1Z5"

    vendor.save()
    vendor.refresh_from_db()
    assert vendor.pan_number == "ABCDE1234F"
    assert vendor.gstin == "29ABCDE1234F1Z5"


def test_vendor_model_clean_mismatched_pan_gstin(db):
    cat = VendorCategory.objects.create(name="Consulting", code="CON")
    vendor = Vendor(
        legal_name="Mismatched Tax Corp",
        vendor_number="VND-2026-00100",
        tax_identification_number="TIN-MISMATCH-1",
        pan_number="XYZAB9999C",
        gstin="29ABCDE1234F1Z5",
        category=cat,
        email="mismatch@corp.test",
        address="100 Park Ave",
    )
    with pytest.raises(ValidationError):
        vendor.clean()


def test_vendor_model_optional_pan_and_gstin(db):
    cat = VendorCategory.objects.create(name="General", code="GEN")
    vendor = Vendor.objects.create(
        legal_name="Optional Tax Vendor",
        vendor_number="VND-2026-00101",
        tax_identification_number="LEGACY-TIN-99",
        category=cat,
        email="opt@general.test",
        address="12 Main St",
    )
    vendor.full_clean()  # Must not raise when pan_number and gstin are blank
    assert vendor.pan_number == ""
    assert vendor.gstin == ""
    assert vendor.tax_identification_number == "LEGACY-TIN-99"


# ==============================================================================
# 3. FORMS AND REGISTRATION BEHAVIOR
# ==============================================================================


def test_vendor_registration_form_valid_pan_and_gstin(db):
    cat = VendorCategory.objects.create(name="Logistics", code="LOG")
    data = {
        "legal_name": "Fast Freight Pvt Ltd",
        "tax_identification_number": "TIN-FREIGHT-1",
        "pan_number": "ABCDE1234F",
        "gstin": "29ABCDE1234F1Z5",
        "category": cat.id,
        "email": "info@fastfreight.test",
        "address": "Port Terminal 4",
    }
    form = VendorRegistrationForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["pan_number"] == "ABCDE1234F"
    assert form.cleaned_data["gstin"] == "29ABCDE1234F1Z5"


def test_vendor_registration_form_mismatched_pan_gstin(db):
    cat = VendorCategory.objects.create(name="Logistics", code="LOG2")
    data = {
        "legal_name": "Mismatched Freight",
        "tax_identification_number": "TIN-FREIGHT-2",
        "pan_number": "ZZZZZ1111A",
        "gstin": "29ABCDE1234F1Z5",
        "category": cat.id,
        "email": "mismatch@freight.test",
        "address": "Port Terminal 5",
    }
    form = VendorRegistrationForm(data=data)
    assert not form.is_valid()
    assert "gstin" in form.errors


def test_vendor_profile_update_form_validates_pan_and_gstin():
    data = {
        "trade_name": "Fast Freight Cargo",
        "address": "Updated Address 123",
        "phone": "+919876543210",
        "pan_number": "abcde1234f",
        "gstin": "29abcde1234f1z5",
    }
    form = VendorProfileUpdateForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["pan_number"] == "ABCDE1234F"
    assert form.cleaned_data["gstin"] == "29ABCDE1234F1Z5"


# ==============================================================================
# 4. DATA MIGRATION BACKFILL AND PRESERVATION
# ==============================================================================


class FakeAppConfig:
    def get_model(self, app_label, model_name):
        assert app_label == "vendors"
        assert model_name == "Vendor"
        return Vendor


def test_data_migration_backfills_pan_and_gstin(db):
    cat = VendorCategory.objects.create(name="Industrial", code="IND")

    # Record 1: Recognizable PAN in tax_identification_number
    v_pan = Vendor.objects.create(
        legal_name="PAN Holder Corp",
        vendor_number="VND-2026-P01",
        tax_identification_number="ABCDE1234F",
        category=cat,
        email="pan@holder.test",
        address="PAN Road",
    )

    # Record 2: Recognizable GSTIN in tax_identification_number
    v_gst = Vendor.objects.create(
        legal_name="GSTIN Holder Corp",
        vendor_number="VND-2026-G01",
        tax_identification_number="29ABCDE1234F1Z5",
        category=cat,
        email="gst@holder.test",
        address="GST Road",
    )

    # Record 3: Legacy ambiguous TIN (should be preserved, not altered)
    v_legacy = Vendor.objects.create(
        legal_name="Legacy Unrelated TIN Corp",
        vendor_number="VND-2026-L01",
        tax_identification_number="US-EIN-987654321",
        category=cat,
        email="legacy@unrelated.test",
        address="Legacy Road",
    )

    # Record 4: Already populated PAN/GSTIN (must NOT be overwritten)
    v_existing = Vendor.objects.create(
        legal_name="Pre-Populated Corp",
        vendor_number="VND-2026-E01",
        tax_identification_number="27XYZAB9999C1Z8",
        pan_number="XYZAB9999C",
        gstin="27XYZAB9999C1Z8",
        category=cat,
        email="pre@populated.test",
        address="Pre Road",
    )

    # Execute the backfill logic
    backfill_pan_and_gstin(FakeAppConfig(), None)

    v_pan.refresh_from_db()
    assert v_pan.pan_number == "ABCDE1234F"
    assert v_pan.gstin == ""
    assert v_pan.tax_identification_number == "ABCDE1234F"

    v_gst.refresh_from_db()
    assert v_gst.gstin == "29ABCDE1234F1Z5"
    assert v_gst.pan_number == "ABCDE1234F"  # Embedded PAN extracted
    assert v_gst.tax_identification_number == "29ABCDE1234F1Z5"

    v_legacy.refresh_from_db()
    assert v_legacy.pan_number == ""
    assert v_legacy.gstin == ""
    assert v_legacy.tax_identification_number == "US-EIN-987654321"

    v_existing.refresh_from_db()
    assert v_existing.pan_number == "XYZAB9999C"
    assert v_existing.gstin == "27XYZAB9999C1Z8"


# ==============================================================================
# 5. INTEGRATION: PROFILE UPDATES & KYC REVIEWER VISIBILITY
# ==============================================================================


def test_vendor_profile_update_service_persists_pan_and_gstin(db):
    cat = VendorCategory.objects.create(name="Services", code="SRV")
    user = User.objects.create_user(
        email="vendor.user@profile.test", password="Password123!"
    )
    vendor = Vendor.objects.create(
        legal_name="Services Enterprise",
        vendor_number="VND-2026-S01",
        tax_identification_number="TIN-SERVICES-01",
        category=cat,
        email="vendor.user@profile.test",
        address="Original Address",
    )

    # Update profile with PAN and GSTIN
    update_vendor_profile_service(
        vendor=vendor,
        user=user,
        data={
            "trade_name": "Services Ent",
            "pan_number": "ABCDE1234F",
            "gstin": "29ABCDE1234F1Z5",
        },
    )

    vendor.refresh_from_db()
    assert vendor.pan_number == "ABCDE1234F"
    assert vendor.gstin == "29ABCDE1234F1Z5"
    assert vendor.trade_name == "Services Ent"
    assert vendor.tax_identification_number == "TIN-SERVICES-01"


def test_kyc_reviewer_screens_display_pan_and_gstin(client, db, db_roles):
    pe_role = db_roles[Role.PROC_EXEC]
    vendor_role = db_roles[Role.VENDOR_USER]
    cat = VendorCategory.objects.create(name="Hardware", code="HW")

    vendor = Vendor.objects.create(
        legal_name="Alpha Tech Solutions",
        vendor_number="VND-2026-REV01",
        tax_identification_number="29ABCDE1234F1Z5",
        pan_number="ABCDE1234F",
        gstin="29ABCDE1234F1Z5",
        category=cat,
        email="vendor@alphatech.test",
        address="1 Tech Park",
    )

    pe_user = User.objects.create_user(
        email="exec@procure.test", password="Password123!", role=pe_role
    )
    v_user = User.objects.create_user(
        email="vendor@alphatech.test", password="Password123!", role=vendor_role, vendor=vendor
    )

    # 1. Vendor Profile screen
    client.force_login(v_user)
    resp = client.get(reverse("vendor_profile"))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "ABCDE1234F" in content
    assert "29ABCDE1234F1Z5" in content

    # 2. Procurement Vendor Detail screen
    client.force_login(pe_user)
    resp = client.get(reverse("vendor_detail", args=[vendor.id]))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "ABCDE1234F" in content
    assert "29ABCDE1234F1Z5" in content

    # 3. Documents modal (KYC reviewer)
    resp = client.get(reverse("vendors_documents_modal", args=[vendor.id]))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "ABCDE1234F" in content
    assert "29ABCDE1234F1Z5" in content

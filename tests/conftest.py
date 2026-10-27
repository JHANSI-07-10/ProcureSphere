# ruff: noqa: E402
import sys
from pathlib import Path

# Ensure src/ is on Python path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import django.template.context
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User

# Python 3.14 compatibility patch for Django 4.2 BaseContext.__copy__
_original_context_copy = django.template.context.BaseContext.__copy__


def _fixed_context_copy(self):
    try:
        return _original_context_copy(self)
    except AttributeError:
        duplicate = object.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = [d.copy() if isinstance(d, dict) else d for d in self.dicts]
        return duplicate


django.template.context.BaseContext.__copy__ = _fixed_context_copy


@pytest.fixture(autouse=True, scope="session")
def verify_isolated_test_database():
    """
    Safety gate: Ensure automated tests never connect to the shared or production Supabase database.
    Verifies that SQLite is the active database backend and no remote Supabase host is configured.
    """
    from django.conf import settings

    db_config = settings.DATABASES["default"]
    engine = db_config.get("ENGINE", "")
    host = str(db_config.get("HOST", ""))

    if "sqlite3" not in engine:
        raise RuntimeError(
            f"SAFETY VIOLATION: Automated tests must run on an isolated SQLite database, "
            f"but active ENGINE is '{engine}'."
        )
    if "supabase" in host.lower():
        raise RuntimeError(
            f"SAFETY VIOLATION: Automated tests must never connect to Supabase! Host is '{host}'."
        )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def db_roles(db):
    roles = {}
    for code, name in Role.ROLE_CHOICES:
        role = Role.objects.filter(code=code).first()
        if not role:
            try:
                role = Role.objects.create(code=code, name=name)
            except Exception:
                role = Role.objects.filter(code=code).first()
        roles[code] = role
    return roles


@pytest.fixture
def super_admin_user(db, db_roles):
    return User.objects.create_superuser(
        email="admin@procuresphere.local",
        password="SuperAdminPassword123!",
        first_name="Super",
        last_name="Admin",
        role=db_roles[Role.SUPER_ADMIN],
    )


@pytest.fixture
def requester_user(db, db_roles):
    return User.objects.create_user(
        email="requester@procuresphere.local",
        password="RequesterPassword123!",
        first_name="John",
        last_name="Requester",
        role=db_roles[Role.REQUESTER],
    )


@pytest.fixture
def procurement_world(db, db_roles, settings, tmp_path):
    """
    Procurement Executive scenario data: org/cost center, internal users for every relevant
    role, and vendors in each governance state. Uploaded files go to a temp MEDIA_ROOT.
    """
    from apps.organization.models import CostCenter, Department, Organization
    from apps.vendors.models import Vendor, VendorCategory
    from apps.vendors.services import register_vendor_service

    settings.MEDIA_ROOT = str(tmp_path / "media")

    org = Organization.objects.create(name="HPE Test Org", code="HPE-T")
    dept = Department.objects.create(name="Procurement", code="PROC-T", organization=org)
    cost_center = CostCenter.objects.create(name="IT", code="CC-T-01", department=dept)
    hardware = VendorCategory.objects.create(name="Hardware", code="CAT-HW")
    services_cat = VendorCategory.objects.create(name="Services", code="CAT-SVC")

    def user(email, role_code, **extra):
        return User.objects.create_user(
            email=email, password="Password123!", role=db_roles[role_code], **extra
        )

    def vendor(name, tin, status, category=hardware):
        v = register_vendor_service(
            legal_name=name,
            tax_identification_number=tin,
            category=category,
            email=f"{tin.lower()}@example.com",
            address="1 Test Street",
        )
        update_fields = {"status": status}
        if status == Vendor.STATUS_ACTIVE:
            update_fields["is_event_eligible"] = True
            update_fields["event_eligibility_status"] = Vendor.ELIGIBILITY_ELIGIBLE
        Vendor.objects.filter(pk=v.pk).update(**update_fields)
        v.refresh_from_db()
        return v

    world = {
        "cost_center": cost_center,
        "hardware": hardware,
        "services": services_cat,
        "exec": user("exec@test.local", Role.PROC_EXEC),
        "exec2": user("exec2@test.local", Role.PROC_EXEC),
        "mgr": user("mgr@test.local", Role.PROC_MGR),
        "finance": user("finance@test.local", Role.FINANCE_AP),
        "auditor": user("auditor@test.local", Role.AUDITOR),
        "requester": user("req@test.local", Role.REQUESTER),
        "stores": user("stores@test.local", Role.STORES_RECEIVER),
        "vendor_a": vendor("Alpha Hardware Ltd", "TIN-A", Vendor.STATUS_ACTIVE),
        "vendor_b": vendor("Beta Systems Inc", "TIN-B", Vendor.STATUS_ACTIVE),
        "vendor_c": vendor("Gamma Services LLC", "TIN-C", Vendor.STATUS_ACTIVE, services_cat),
        "vendor_hold": vendor("Held Vendor Co", "TIN-H", Vendor.STATUS_ON_HOLD),
        "vendor_susp": vendor("Suspended Vendor Co", "TIN-S", Vendor.STATUS_SUSPENDED),
        "vendor_draft": vendor("Draft Vendor Co", "TIN-D", Vendor.STATUS_DRAFT),
        "vendor_kyc": vendor("KYC Vendor Co", "TIN-K", Vendor.STATUS_KYC_REVIEW),
    }
    world["user_a"] = user("rep@alpha.test", Role.VENDOR_USER, vendor=world["vendor_a"])
    world["user_b"] = user("rep@beta.test", Role.VENDOR_USER, vendor=world["vendor_b"])
    world["user_c"] = user("rep@gamma.test", Role.VENDOR_USER, vendor=world["vendor_c"])
    return world

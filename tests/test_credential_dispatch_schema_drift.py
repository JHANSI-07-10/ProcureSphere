"""
Production drift: vendors_vendorcredentialdispatch carried an extra NOT NULL column
(``legal_notes``) that no migration in this repository defines, so issuing credentials failed with
IntegrityError. Migration 0006 relaxes such orphan columns without dropping data.
"""

import importlib
from unittest import mock

import pytest
from django.apps import apps as django_apps
from django.core import mail
from django.db import IntegrityError, connection, transaction

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.vendors.models import VendorCredentialDispatch
from apps.vendors.services import issue_vendor_credentials_service

pytestmark = [pytest.mark.django_db, pytest.mark.regression]

import django.db.backends.sqlite3.base as _sqlite_check
import django.db.backends.postgresql.base as _pg_check

def _is_sqlite():
    from django.db import connection
    return "sqlite" in connection.vendor


migration = importlib.import_module(
    "apps.vendors.migrations.0006_relax_orphan_credentialdispatch_columns"
)


@pytest.fixture(autouse=True)
def email_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.BREVO_API_KEY = ""


def _add_drifted_column():
    if _is_sqlite():
        pytest.skip("Schema drift tests require PostgreSQL")
    with connection.cursor() as cur:
        cur.execute(
            "ALTER TABLE vendors_vendorcredentialdispatch ADD COLUMN IF NOT EXISTS legal_notes text NOT NULL DEFAULT ''"
        )
        try:
            cur.execute(
                "ALTER TABLE vendors_vendorcredentialdispatch ALTER COLUMN legal_notes DROP DEFAULT"
            )
        except Exception:
            pass  # column may already exist without default


def _column_nullable(name):
    if _is_sqlite():
        pytest.skip("Schema drift tests require PostgreSQL")
    with connection.cursor() as cur:
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='vendors_vendorcredentialdispatch' AND column_name=%s",
            [name],
        )
        row = cur.fetchone()
    return None if row is None else row[0] == "YES"


def _run_migration():
    with connection.schema_editor(atomic=False) as editor:
        migration.relax_orphan_columns(django_apps, editor)


def test_drift_reproduces_the_production_error(procurement_world):
    _add_drifted_column()
    with pytest.raises(IntegrityError, match="legal_notes"), transaction.atomic():
        issue_vendor_credentials_service(
            vendor=procurement_world["vendor_draft"], issued_by=procurement_world["exec"]
        )


def test_migration_fixes_issue_credentials_and_keeps_column_and_data(procurement_world):
    w = procurement_world
    _add_drifted_column()
    with connection.cursor() as cur:
        # a pre-existing row written while the column was populated must survive untouched
        cur.execute(
            "INSERT INTO vendors_vendorcredentialdispatch "
            "(id, created_at, updated_at, sent_to, status, error, vendor_id, user_id, legal_notes) "
            "SELECT gen_random_uuid(), now(), now(), 'old@x.test', 'SENT', '', %s, u.id, 'kept' FROM accounts_user u LIMIT 1",
            [str(w["vendor_a"].id)],
        )
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # flush deferred FK checks of the raw insert
    _run_migration()
    assert _column_nullable("legal_notes") is True  # constraint relaxed, column retained

    dispatch = issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT and dispatch.sent_at
    assert len(mail.outbox) == 1
    assert User.objects.filter(vendor=w["vendor_draft"], must_change_password=True).exists()
    assert AuditLog.objects.filter(
        target_model="Vendor", target_object_id=str(w["vendor_draft"].id)
    ).exists()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT legal_notes FROM vendors_vendorcredentialdispatch WHERE sent_to='old@x.test'"
        )
        assert cur.fetchone()[0] == "kept"


def test_failed_email_is_recorded_as_failed_after_fix(procurement_world):
    w = procurement_world
    _add_drifted_column()
    _run_migration()
    with mock.patch("apps.vendors.services.send_mail", side_effect=OSError("smtp down")):
        with pytest.raises(Exception):
            issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    dispatch = VendorCredentialDispatch.objects.get(vendor=w["vendor_draft"])
    assert dispatch.status == VendorCredentialDispatch.STATUS_FAILED
    assert "smtp down" in dispatch.error


def test_migration_is_a_noop_on_a_healthy_schema_and_idempotent(procurement_world):
    before = _column_nullable("sent_to")
    _run_migration()
    _run_migration()
    assert _column_nullable("sent_to") == before is False  # model columns are never relaxed
    assert _column_nullable("legal_notes") is None  # nothing was added

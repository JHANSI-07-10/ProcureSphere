"""Deleting a DRAFT vendor that already had portal credentials issued (VendorCredentialDispatch)."""

from unittest import mock

import pytest
from django.contrib.messages import get_messages
from django.db.models import ProtectedError
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.vendors.models import Vendor, VendorCredentialDispatch
from apps.vendors.services import issue_vendor_credentials_service

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture(autouse=True)
def email_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.BREVO_API_KEY = ""


def _issue_twice(w):
    vendor = w["vendor_draft"]
    issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])
    issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])  # resend -> 2 dispatches
    VendorCredentialDispatch.objects.filter(vendor=vendor).update(status="FAILED")
    return vendor, User.objects.get(vendor=vendor)


def test_delete_draft_vendor_with_credential_dispatches(client, procurement_world):
    w = procurement_world
    vendor, portal_user = _issue_twice(w)
    vendor_id = str(vendor.id)
    assert VendorCredentialDispatch.objects.filter(vendor=vendor).count() == 2

    client.force_login(w["exec"])
    resp = client.post(reverse("vendor_delete", args=[vendor.id]))
    assert resp.status_code == 302 and resp.url == reverse("vendor_list")

    assert not Vendor.objects.filter(pk=vendor.pk).exists()
    assert not VendorCredentialDispatch.objects.filter(sent_to=portal_user.email).exists()
    portal_user.refresh_from_db()
    assert portal_user.vendor_id is None
    assert portal_user.is_active is False  # orphaned vendor login can no longer sign in
    entry = AuditLog.objects.get(target_model="Vendor", target_object_id=vendor_id, action="DELETE")
    assert entry.new_state["credential_dispatches_removed"] == 2
    assert entry.new_state["portal_users_deactivated"] == [portal_user.email]


def test_unexpected_protected_relation_shows_friendly_message_and_rolls_back(
    client, procurement_world
):
    w = procurement_world
    vendor, portal_user = _issue_twice(w)
    client.force_login(w["exec"])
    with mock.patch.object(Vendor, "delete", side_effect=ProtectedError("protected", [vendor])):
        resp = client.post(reverse("vendor_delete", args=[vendor.id]))
    assert resp.status_code == 302 and resp.url == reverse("vendor_detail", args=[vendor.id])
    msgs = [str(m) for m in get_messages(resp.wsgi_request)]
    assert any("cannot be deleted because other records still reference it" in m for m in msgs)
    # everything rolled back: vendor, dispatches, active login, no DELETE audit row
    assert Vendor.objects.filter(pk=vendor.pk).exists()
    assert VendorCredentialDispatch.objects.filter(vendor=vendor).count() == 2
    portal_user.refresh_from_db()
    assert portal_user.is_active is True and portal_user.vendor_id == vendor.id
    assert not AuditLog.objects.filter(
        target_model="Vendor", target_object_id=str(vendor.id), action="DELETE"
    ).exists()


def test_active_vendor_still_cannot_be_deleted(client, procurement_world):
    w = procurement_world
    client.force_login(w["exec"])
    client.post(reverse("vendor_delete", args=[w["vendor_a"].id]))
    assert Vendor.objects.filter(pk=w["vendor_a"].pk).exists()
    client.force_login(w["requester"])
    assert client.post(reverse("vendor_delete", args=[w["vendor_draft"].id])).status_code == 403

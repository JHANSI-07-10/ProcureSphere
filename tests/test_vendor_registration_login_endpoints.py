"""
Endpoint audit: vendor registration (staff-driven) and vendor login / password lifecycle.

Covers every route involved: /login/, /logout/, /password-change/, /accounts/change-password/,
/api/v1/auth/login|logout/, /vendors/create|htmx/register|<id>/credentials|edit|delete, the
vendor API, and a sweep of EVERY /vendor/* portal pattern as anonymous / vendor / other vendor /
staff callers (no endpoint may return 5xx or leak across roles or vendors).
"""

import re
import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import get_resolver, reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.sourcing import services as sourcing_svc
from apps.vendors.models import Vendor
from apps.vendors.services import issue_vendor_credentials_service

pytestmark = [pytest.mark.django_db, pytest.mark.regression]

GOOD = "Password123!"
NEW_PW = "Brand#New-Pass-2026x"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.BREVO_API_KEY = ""
    settings.VENDOR_PORTAL_LOGIN_URLS = ["http://testserver/login/"]


@pytest.fixture
def fresh_vendor_login(procurement_world):
    """A draft vendor whose credentials were issued by email (temporary password)."""
    w = procurement_world
    issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
    temp = re.search(r"Temporary password: (\S+)", mail.outbox[-1].body).group(1)
    return w, User.objects.get(vendor=w["vendor_draft"]), temp


def _post_login(client, email, password, **extra):
    return client.post("/login/", {"email": email, "password": password}, **extra)


# ============================================================ LOGIN ==========================


class TestLogin:
    def test_login_page_renders_for_anonymous(self, client):
        assert client.get("/login/").status_code == 200

    def test_vendor_login_lands_on_vendor_dashboard(self, client, procurement_world):
        resp = _post_login(client, procurement_world["user_a"].email, GOOD)
        assert resp.status_code == 302 and resp.url == "/vendor/"
        assert client.get("/vendor/").status_code == 200
        # already-authenticated users are bounced away from the login form
        assert client.get("/login/").status_code == 302
        assert AuditLog.objects.filter(
            actor=procurement_world["user_a"], action=AuditLog.ACTION_LOGIN
        ).exists()

    def test_staff_login_lands_on_home_not_vendor_portal(self, client, procurement_world):
        resp = _post_login(client, procurement_world["exec"].email, GOOD)
        assert resp.status_code == 302 and resp.url == "/"
        assert client.get("/vendor/").status_code in (302, 403)

    @pytest.mark.parametrize(
        "email,password",
        [
            ("", ""),
            ("rep@alpha.test", ""),
            ("", GOOD),
            ("rep@alpha.test", "wrong"),
            ("nobody@x.test", GOOD),
        ],
    )
    def test_bad_credentials_never_log_in_or_500(self, client, procurement_world, email, password):
        resp = _post_login(client, email, password)
        assert resp.status_code == 200  # re-rendered form with an error
        assert "_auth_user_id" not in client.session

    def test_unknown_and_wrong_password_messages_do_not_enumerate_users(
        self, client, procurement_world
    ):
        a = _post_login(client, "rep@alpha.test", "wrong").content.decode()
        b = _post_login(client, "ghost@nowhere.test", "wrong").content.decode()
        assert ("Invalid email or password" in a) == ("Invalid email or password" in b)

    def test_deactivated_vendor_cannot_login(self, client, procurement_world):
        user = procurement_world["user_a"]
        User.objects.filter(pk=user.pk).update(is_active=False)
        resp = _post_login(client, user.email, GOOD)
        assert resp.status_code == 200 and "_auth_user_id" not in client.session

    def test_lockout_is_per_account_and_blocks_even_the_correct_password(
        self, client, procurement_world
    ):
        victim = procurement_world["user_b"].email
        for _ in range(5):
            _post_login(client, victim, "wrong-password")
        resp = _post_login(client, victim, GOOD)  # correct password, but the account is locked
        assert resp.status_code in (403, 429)
        assert "_auth_user_id" not in client.session
        # case variants of the same e-mail share the counter (cannot dodge the lock)
        assert _post_login(client, victim.upper(), GOOD).status_code in (403, 429)
        # a different account on the same IP is NOT locked out
        other = _post_login(client.__class__(), procurement_world["user_a"].email, GOOD)
        assert other.status_code == 302

    @pytest.mark.parametrize(
        "target",
        [
            "https://evil.example/phish",
            "//evil.example",
            "javascript:alert(1)",
            "http://evil.example",
        ],
    )
    def test_next_parameter_cannot_redirect_off_site(self, client, procurement_world, target):
        resp = client.post(
            "/login/?next=" + target, {"email": procurement_world["user_a"].email, "password": GOOD}
        )
        assert resp.status_code == 302
        assert resp.url == "/vendor/"

    def test_next_parameter_is_honoured_for_local_paths(self, client, procurement_world):
        resp = client.post(
            "/login/?next=/vendor/bids/",
            {"email": procurement_world["user_a"].email, "password": GOOD},
        )
        assert resp.status_code == 302 and resp.url == "/vendor/bids/"

    def test_logout_ends_session(self, client, procurement_world):
        client.force_login(procurement_world["user_a"])
        resp = client.post("/logout/")
        assert resp.status_code in (200, 302)
        assert client.get("/vendor/").status_code == 302  # back to login
        assert "_auth_user_id" not in client.session


class TestTemporaryPasswordFlow:
    def test_first_login_forces_password_change_on_every_portal_page(
        self, client, fresh_vendor_login
    ):
        w, user, temp = fresh_vendor_login
        resp = _post_login(client, user.email, temp)
        assert resp.status_code == 302
        assert "change-password" in resp.url
        for url in ("/", "/vendor/", "/vendor/bids/", "/vendor/profile/", "/vendor/sourcing/"):
            r = client.get(url)
            assert r.status_code == 302 and "change-password" in r.url, url
        # still allowed while forced: logout, health, the change-password page itself
        assert client.get("/health").status_code == 200
        assert client.get("/accounts/change-password/").status_code == 200

    @pytest.mark.parametrize("path", ["/accounts/change-password/", "/password-change/"])
    def test_both_change_password_pages_complete_the_flow(self, client, fresh_vendor_login, path):
        w, user, temp = fresh_vendor_login
        _post_login(client, user.email, temp)
        weak = {
            "old_password": temp,
            "current_password": temp,
            "new_password1": "123",
            "new_password2": "123",
        }
        assert client.post(path, weak).status_code == 200
        mism = dict(weak, new_password1=NEW_PW, new_password2=NEW_PW + "x")
        assert client.post(path, mism).status_code == 200
        wrong_cur = {
            "old_password": "nope",
            "current_password": "nope",
            "new_password1": NEW_PW,
            "new_password2": NEW_PW,
        }
        assert client.post(path, wrong_cur).status_code == 200
        user.refresh_from_db()
        assert user.must_change_password is True

        ok = {
            "old_password": temp,
            "current_password": temp,
            "new_password1": NEW_PW,
            "new_password2": NEW_PW,
        }
        assert client.post(path, ok).status_code == 302
        user.refresh_from_db()
        assert user.must_change_password is False
        assert user.check_password(NEW_PW) and not user.check_password(temp)
        assert client.get("/vendor/").status_code == 200  # portal unlocked, session kept
        assert (
            AuditLog.objects.filter(
                actor=user, target_model="User", target_object_id=str(user.id)
            ).count()
            >= 2
        )  # login + password change
        # old temporary password is dead; new one works from a clean client
        c2 = client.__class__()
        assert _post_login(c2, user.email, temp).status_code == 200
        assert _post_login(c2, user.email, NEW_PW).status_code == 302

    def test_change_password_requires_login(self, client):
        for path in ("/accounts/change-password/", "/password-change/"):
            assert client.get(path).status_code == 302
            assert client.post(path, {"new_password1": NEW_PW}).status_code == 302

    def test_resend_credentials_resets_to_a_new_temporary_password(
        self, client, fresh_vendor_login
    ):
        w, user, temp = fresh_vendor_login
        issue_vendor_credentials_service(vendor=w["vendor_draft"], issued_by=w["exec"])
        newtemp = re.search(r"Temporary password: (\S+)", mail.outbox[-1].body).group(1)
        assert newtemp != temp
        assert _post_login(client, user.email, temp).status_code == 200
        assert _post_login(client, user.email, newtemp).status_code == 302


class TestSessionApiAuth:
    def test_api_login_and_logout(self, api_client, procurement_world):
        email = procurement_world["user_a"].email
        bad = api_client.post(
            "/api/v1/auth/login/", {"email": email, "password": "x"}, format="json"
        )
        assert bad.status_code in (400, 401, 403)
        ok = api_client.post(
            "/api/v1/auth/login/", {"email": email, "password": GOOD}, format="json"
        )
        assert ok.status_code == 200
        assert api_client.post("/api/v1/auth/logout/").status_code in (200, 204)

    def test_api_requires_auth(self, api_client):
        assert api_client.get("/api/v1/vendors/").status_code in (401, 403)


# ============================================================ REGISTRATION ===================

REG = {
    "legal_name": "Zenith Components Pvt Ltd",
    "tax_identification_number": "TIN-ZENITH-1",
    "category": None,
    "email": "Zenith@Vendor.test",
    "address": "7 Industrial Estate",
}


class TestRegistration:
    def _payload(self, w, **over):
        data = dict(REG, category=str(w["hardware"].id))
        data.update(over)
        return data

    @pytest.mark.parametrize("url_name", ["vendor_create", "vendors_register_modal"])
    def test_form_access_control(self, client, procurement_world, url_name):
        w = procurement_world
        url = reverse(url_name)
        assert client.get(url).status_code == 302  # anonymous -> login
        for key in ("requester", "stores", "finance", "auditor", "user_a"):
            client.force_login(w[key])
            assert client.get(url).status_code == 403, key
            assert client.post(url, self._payload(w)).status_code == 403, key
        for key in ("exec", "mgr"):
            client.force_login(w[key])
            assert client.get(url).status_code == 200, key

    @pytest.mark.parametrize("url_name", ["vendor_create", "vendors_register_modal"])
    def test_register_creates_draft_vendor(self, client, procurement_world, url_name):
        w = procurement_world
        client.force_login(w["exec"])
        resp = client.post(
            reverse(url_name),
            self._payload(w),
            HTTP_HX_REQUEST="true" if "modal" in url_name else "",
        )
        assert resp.status_code in (200, 302)
        vendor = Vendor.objects.get(tax_identification_number="TIN-ZENITH-1")
        assert vendor.status == Vendor.STATUS_DRAFT
        assert vendor.email == "zenith@vendor.test" or vendor.email.lower() == "zenith@vendor.test"
        assert AuditLog.objects.filter(
            target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_CREATE
        ).exists()

    @pytest.mark.parametrize("url_name", ["vendor_create", "vendors_register_modal"])
    def test_invalid_and_duplicate_input_creates_nothing(self, client, procurement_world, url_name):
        w = procurement_world
        client.force_login(w["exec"])
        before = Vendor.objects.count()
        for bad in (
            {},
            self._payload(w, legal_name=""),
            self._payload(w, email="not-an-email"),
            self._payload(w, tax_identification_number=""),
            self._payload(w, tax_identification_number=w["vendor_a"].tax_identification_number),
            self._payload(w, category=str(uuid.uuid4())),
        ):
            resp = client.post(reverse(url_name), bad)
            assert resp.status_code in (200, 400), bad
        assert Vendor.objects.count() == before

    def test_registration_api(self, api_client, procurement_world):
        w = procurement_world
        payload = {
            "legal_name": "Api Vendor Ltd",
            "tax_identification_number": "TIN-API-1",
            "category": str(w["hardware"].id),
            "email": "api@vendor.test",
            "address": "1 Api Road",
        }
        for key in ("requester", "user_a", "finance"):
            api_client.force_authenticate(w[key])
            assert (
                api_client.post("/api/v1/vendors/", payload, format="json").status_code == 403
            ), key
        api_client.force_authenticate(w["exec"])
        res = api_client.post("/api/v1/vendors/", payload, format="json")
        assert res.status_code == 201, res.content
        assert res.json()["status"] == Vendor.STATUS_DRAFT
        # status can never be set at creation or by PATCH
        res = api_client.post(
            "/api/v1/vendors/",
            dict(
                payload,
                tax_identification_number="TIN-API-2",
                email="api2@vendor.test",
                status="ACTIVE",
            ),
            format="json",
        )
        assert res.status_code in (201, 400)
        if res.status_code == 201:
            assert res.json()["status"] == Vendor.STATUS_DRAFT

    def test_issue_credentials_endpoint(self, client, procurement_world):
        w = procurement_world
        url = reverse("vendor_issue_credentials", args=[w["vendor_draft"].id])
        assert client.post(url).status_code == 302  # anonymous
        for key in ("requester", "stores", "finance", "auditor", "user_a"):
            client.force_login(w[key])
            assert client.post(url).status_code == 403, key
        client.force_login(w["exec"])
        assert client.get(url).status_code == 405
        resp = client.post(url)
        assert resp.status_code == 302
        user = User.objects.get(vendor=w["vendor_draft"])
        assert user.must_change_password is True and user.role_code == "VENDOR_USER"
        # suspended / rejected vendors are refused with a message, not a 500
        for status in (Vendor.STATUS_SUSPENDED, Vendor.STATUS_REJECTED):
            Vendor.objects.filter(pk=w["vendor_draft"].pk).update(status=status)
            assert client.post(url).status_code == 302
        # unknown vendor id
        assert (
            client.post(reverse("vendor_issue_credentials", args=[uuid.uuid4()])).status_code == 404
        )

    def test_vendor_without_email_is_handled(self, client, procurement_world):
        w = procurement_world
        Vendor.objects.filter(pk=w["vendor_draft"].pk).update(email="")
        client.force_login(w["exec"])
        resp = client.post(reverse("vendor_issue_credentials", args=[w["vendor_draft"].id]))
        assert resp.status_code == 302  # error message + redirect, never a 500
        assert not User.objects.filter(vendor=w["vendor_draft"]).exists()
        assert mail.outbox == []


# ============================================================ PORTAL SWEEP ===================


def _portal_patterns():
    from apps.vendors import vendor_dashboard_urls

    return vendor_dashboard_urls.urlpatterns


def _sweep_urls(ids):
    """Concrete URLs for every /vendor/* pattern; unknown params get a random UUID."""
    urls = []
    for pat in _portal_patterns():
        route = str(pat.pattern)
        url = "/vendor/" + re.sub(
            r"<(?:uuid|int|str|slug):(\w+)>",
            lambda m: str(ids.get(m.group(1), uuid.uuid4())),
            route,
        )
        urls.append((pat.name, url))
    return urls


@pytest.fixture
def portal_world(procurement_world):
    w = procurement_world
    now = timezone.now()
    event = sourcing_svc.create_sourcing_event_service(
        title="Sweep RFQ",
        event_type="RFQ",
        bid_start_date=now - timedelta(hours=1),
        bid_end_date=now + timedelta(days=3),
        description="d",
        technical_requirements="t",
        commercial_requirements="c",
        created_by_user=w["exec"],
    )
    sourcing_svc.invite_vendors_to_event_service(
        event=event, vendor_ids=[w["vendor_a"].id], invited_by=w["exec"]
    )
    sourcing_svc.publish_sourcing_event_service(event=event, user=w["exec"])
    bid = sourcing_svc.submit_vendor_bid_service(
        event=event,
        vendor=w["vendor_a"],
        line_items=[{"item_description": "x", "quantity": "1", "quoted_unit_price": "10"}],
        proposal_summary="s",
        technical_proposal="t",
        commercial_proposal="c",
        submitted_by_user=w["user_a"],
    )
    w["ids"] = {"event_id": event.id, "bid_id": bid.id}
    w["event"], w["bid"] = event, bid
    return w


def test_every_vendor_portal_endpoint_is_safe_for_every_caller(client, portal_world):
    w = portal_world
    urls = _sweep_urls(w["ids"])
    assert len(urls) >= 25  # the sweep really enumerates the portal

    # anonymous: redirected to login (never served, never 5xx)
    for name, url in urls:
        r = client.get(url)
        assert r.status_code == 302 and "/login" in r.url, (name, url, r.status_code)

    # owning vendor: nothing may 5xx
    client.force_login(w["user_a"])
    own = {name: client.get(url).status_code for name, url in urls}
    assert all(code < 500 for code in own.values()), own
    assert own["vendor_dashboard"] == 200 and own["vendor_profile"] == 200
    assert own["vendor_sourcing_list"] == 200 and own["vendor_bids_list"] == 200
    assert own["vendor_sourcing_detail"] == 200 and own["vendor_bid_detail"] == 200

    # another vendor: cannot see the first vendor's event-bid / bid (IDOR), nothing 5xx
    client.force_login(w["user_b"])
    other = {name: client.get(url).status_code for name, url in urls}
    assert all(code < 500 for code in other.values()), other
    assert other["vendor_bid_detail"] in (403, 404)
    assert other["vendor_sourcing_detail"] in (403, 404)

    # staff roles must not be served the vendor portal pages; nothing 5xx
    for key in ("exec", "requester", "stores", "finance", "auditor"):
        client.force_login(w[key])
        codes = {name: client.get(url).status_code for name, url in urls}
        assert all(code < 500 for code in codes.values()), (key, codes)
        assert codes["vendor_dashboard"] in (302, 403), key
        assert codes["vendor_bid_detail"] in (302, 403, 404), key


def test_vendor_portal_post_endpoints_reject_cross_vendor_and_staff(client, portal_world):
    w = portal_world
    bid_id = w["ids"]["bid_id"]
    for name in ("vendor_bid_withdraw", "vendor_bid_amend", "vendor_bid_validate"):
        url = reverse(name, args=[bid_id])
        client.force_login(w["user_b"])
        assert client.post(url, {}).status_code in (302, 403, 404), name
        client.force_login(w["requester"])
        assert client.post(url, {}).status_code in (302, 403, 404), name
    w["bid"].refresh_from_db()
    assert w["bid"].status != "WITHDRAWN"


def test_vendor_profile_upload_and_submit_endpoints(client, procurement_world):
    w = procurement_world
    client.force_login(w["user_a"])
    bad = SimpleUploadedFile("evil.exe", b"MZ", content_type="application/x-msdownload")
    resp = client.post(
        reverse("vendor_profile"),
        {"action": "upload_kyc", "document_type": "OTHER", "title": "x", "kyc_file": bad},
    )
    assert resp.status_code < 500
    assert not w["vendor_a"].documents.filter(title="x").exists()
    resp = client.post(reverse("vendor_profile"), {"action": "unknown"})
    assert resp.status_code < 500


# ============================================================ STAFF VENDOR ROUTES ============


def _staff_vendor_urls(vendor_id):
    from apps.vendors import urls as vurls

    out = []
    for pat in vurls.urlpatterns:
        route = str(pat.pattern)
        url = "/" + re.sub(
            r"<uuid:vendor_id>",
            str(vendor_id),
            re.sub(r"<(?:uuid|slug):(\w+)>", str(uuid.uuid4()), route),
        )
        out.append((pat.name, url))
    return out


def test_every_staff_vendor_route_never_5xx_and_is_role_gated(client, procurement_world):
    w = procurement_world
    urls = _staff_vendor_urls(w["vendor_draft"].id)
    assert len(urls) >= 15
    for name, url in urls:
        assert client.get(url).status_code == 302, (name, url)  # anonymous
    for key in ("requester", "stores", "user_a"):
        client.force_login(w[key])
        for name, url in urls:
            code = client.get(url).status_code
            assert code in (302, 403, 404, 405), (key, name, url, code)
    client.force_login(w["exec"])
    for name, url in urls:
        assert client.get(url).status_code < 500, (name, url)
        assert client.post(url, {}).status_code < 500, (name, url)


# ============================================================ RENDER / PRODUCTION SETTINGS ===


@pytest.mark.parametrize("module", ["config.settings.prod", "config.settings.staging"])
def test_prod_and_staging_settings_work_behind_render_proxy(monkeypatch, module):
    """TLS terminates at Render's proxy: without these, prod loops on SSL redirect and HTTPS
    login posts fail the CSRF origin check (verified against a simulated proxied request)."""
    import importlib

    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "procure.onrender.com,.example.com")
    monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "procure-abc.onrender.com")
    mod = importlib.reload(importlib.import_module(module))
    assert mod.DEBUG is False
    assert mod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert mod.SESSION_COOKIE_SECURE and mod.CSRF_COOKIE_SECURE
    assert "https://procure.onrender.com" in mod.CSRF_TRUSTED_ORIGINS
    assert "https://example.com" in mod.CSRF_TRUSTED_ORIGINS
    assert "https://procure-abc.onrender.com" in mod.CSRF_TRUSTED_ORIGINS
    assert not any("*" in o for o in mod.CSRF_TRUSTED_ORIGINS)

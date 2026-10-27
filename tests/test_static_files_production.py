from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings


def test_static_files_production_serving():
    """
    Verify that in production mode (DEBUG=False), WhiteNoise correctly intercepts
    and serves /static/css/style.css and /static/js/app.js with HTTP 200 and
    correct MIME types, preventing browser strict MIME checking errors.
    """
    client = Client()

    # 1. Test CSS delivery under production simulation (DEBUG=False)
    with override_settings(DEBUG=False):
        response_css = client.get("/static/css/style.css")
        assert response_css.status_code == 200, (
            f"Expected HTTP 200 for /static/css/style.css, got {response_css.status_code}"
        )
        content_type_css = response_css.get("Content-Type", "")
        assert "text/css" in content_type_css, (
            f"Expected text/css Content-Type, got '{content_type_css}'"
        )
        css_body = b"".join(response_css.streaming_content) if hasattr(response_css, "streaming_content") else response_css.content
        assert len(css_body) > 0, "CSS body should not be empty"
        assert b"--primary" in css_body or b"body" in css_body or b"{" in css_body

        # 2. Test JavaScript delivery under production simulation (DEBUG=False)
        response_js = client.get("/static/js/app.js")
        assert response_js.status_code == 200, (
            f"Expected HTTP 200 for /static/js/app.js, got {response_js.status_code}"
        )
        content_type_js = response_js.get("Content-Type", "")
        assert "javascript" in content_type_js, (
            f"Expected javascript Content-Type, got '{content_type_js}'"
        )
        js_body = b"".join(response_js.streaming_content) if hasattr(response_js, "streaming_content") else response_js.content
        assert len(js_body) > 0, "JS body should not be empty"


def test_whitenoise_configured_in_middleware():
    """Verify that WhiteNoiseMiddleware is present in MIDDLEWARE right after SecurityMiddleware."""
    middleware = list(settings.MIDDLEWARE)
    assert "whitenoise.middleware.WhiteNoiseMiddleware" in middleware
    sec_idx = middleware.index("django.middleware.security.SecurityMiddleware")
    wn_idx = middleware.index("whitenoise.middleware.WhiteNoiseMiddleware")
    assert wn_idx == sec_idx + 1, "WhiteNoiseMiddleware must be placed immediately after SecurityMiddleware"


def test_static_files_exist_on_filesystem():
    """Verify that style.css and app.js exist at the expected source paths."""
    static_dir = settings.BASE_DIR / "static"
    assert (static_dir / "css" / "style.css").is_file(), "static/css/style.css must exist"
    assert (static_dir / "js" / "app.js").is_file(), "static/js/app.js must exist"

from .base import *  # noqa: F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["staging.procuresphere360.local"])

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

# Behind Render's TLS-terminating proxy: trust X-Forwarded-Proto (else SECURE_SSL_REDIRECT loops
# and HTTPS form posts fail Django's CSRF origin check). Trusted origins default to the allowed
# hosts (plus Render's RENDER_EXTERNAL_HOSTNAME); override with CSRF_TRUSTED_ORIGINS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
_hosts = [h.lstrip(".") for h in ALLOWED_HOSTS if h and h != "*" and "." in h]  # noqa: F405
if env("RENDER_EXTERNAL_HOSTNAME", default=""):
    _hosts.append(env("RENDER_EXTERNAL_HOSTNAME"))
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS", default=[f"https://{h}" for h in dict.fromkeys(_hosts)]
)

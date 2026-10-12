from .base import *  # noqa: F403

DEBUG = True


# Development email backend (log to console)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Restricted CORS configuration (no wildcard *)
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

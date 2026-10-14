from .base import *  # noqa: F403

DEBUG = True


# Development email backend: console by default; set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend in .env to really send via Brevo
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")  # noqa: F405

# Restricted CORS configuration (no wildcard *)
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

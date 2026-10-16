
from .base import *  # noqa: F403, F405

# Development settings
DEBUG = True

# Email configuration
# Defaults to console backend for local development.
# Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# in environment variables to send real emails through Brevo.
EMAIL_BACKEND = env(  # noqa: F405
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

# Restricted CORS configuration
CORS_ALLOW_ALL_ORIGINS = False

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

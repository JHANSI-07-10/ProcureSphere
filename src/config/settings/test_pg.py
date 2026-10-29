"""
PostgreSQL Test Settings for ProcureSphere 360.
Used in CI to run the full test suite against a real PostgreSQL 15 instance,
exercising select_for_update, constraints, and rollback behaviour.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test_pg")

from .base import *  # noqa: F403, F405

DEBUG = False
SECRET_KEY = "test-pg-secret-key-not-for-production-use-procuresphere"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "procuresphere_test_db"),
        "USER": os.environ.get("DB_USER", "procure_user"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "procure_password"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

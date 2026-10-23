"""
Isolated Test Settings for ProcureSphere 360.
Uses a fast, local SQLite database and memory caches.
Completely isolated from remote Supabase / PostgreSQL.
"""
import os
import sys
from pathlib import Path

# Signal to base settings that this is an isolated test environment
# to ensure production database credentials are never loaded from .env
os.environ["PROCURESPHERE_TESTING"] = "1"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

from .base import *  # noqa: F403, F405

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production-use-procuresphere"

# Dedicated local test SQLite database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",  # noqa: F405
    }
}

# In-memory caching and email for test isolation and speed
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Fast password hashing for tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Ensure Celery runs in-memory/eagerly without connecting to Redis
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True


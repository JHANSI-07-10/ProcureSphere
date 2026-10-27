import os
import sys
from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = BASE_DIR / "src"

env = environ.Env(
    DJANGO_DEBUG=(bool, True),
    DJANGO_SECRET_KEY=(str, "dev-secret-key-change-in-production-procuresphere"),
    ALLOWED_HOSTS=(list, ["*"]),
    DB_NAME=(str, "procuresphere_db"),
    DB_USER=(str, "procure_user"),
    DB_PASSWORD=(str, "procure_password"),
    DB_HOST=(str, "127.0.0.1"),
    DB_PORT=(str, "5432"),
    REDIS_URL=(str, "redis://127.0.0.1:6379/0"),
)

# Read .env file if present, unless running in an isolated test environment
# to ensure automated tests never load production database credentials.
env_file = BASE_DIR / ".env"
_is_test_env = (
    os.environ.get("DJANGO_SETTINGS_MODULE") == "config.settings.test"
    or os.environ.get("PROCURESPHERE_TESTING") == "1"
    or "pytest" in sys.modules
    or any("pytest" in arg for arg in sys.argv)
    or any(arg == "test" for arg in sys.argv)
)
if env_file.exists() and not _is_test_env:
    is_docker = os.environ.get("DB_HOST") == "db" or os.path.exists("/.dockerenv")
    env.read_env(str(env_file), overwrite=not is_docker)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Custom User Model
AUTH_USER_MODEL = "accounts.User"

# Application definition
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "axes",
    "corsheaders",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.organization",
    "apps.approvals",
    "apps.vendors",
    "apps.requisitions",
    "apps.sourcing",
    "apps.orders",
    "apps.receipts",
    "apps.invoices",
    "apps.contracts",
    "apps.budgets",
    "apps.scorecards",
    "apps.notifications",
    "apps.reports",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.PasswordChangeRequiredMiddleware",
    "apps.core.middleware.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.notifications.context_processors.notifications_processor",
                "apps.core.context_processors.layout_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database Configuration
DB_ENGINE = env("DB_ENGINE", default="django.db.backends.postgresql")

if DB_ENGINE == "django.db.backends.sqlite3":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
elif env.str("DATABASE_URL", default=""):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": env("DB_HOST"),
            "PORT": env("DB_PORT"),
            "CONN_MAX_AGE": 60,
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}
WHITENOISE_USE_FINDERS = True

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework Settings
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework.authentication.SessionAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
}

# Session & CSRF Security Baseline
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_HTTPONLY = False  # Allows frontend JS/HTMX to attach CSRF headers

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_USE_SESSIONS = False

# OpenAPI / Spectacular Settings
SPECTACULAR_SETTINGS = {
    "TITLE": "ProcureSphere 360 ERP API",
    "DESCRIPTION": "Enterprise Procurement, Vendor & Contract Management ERP API Specifications",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1/",
}

# Celery & Redis Configuration
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0").strip()
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_SCHEDULE = {
    "test-beat-task-every-minute": {
        "task": "apps.notifications.tasks.test_celery_beat_task",
        "schedule": 60.0,  # every 60 seconds
    },
    "sync-sourcing-bid-windows": {
        "task": "apps.sourcing.tasks.sync_sourcing_bid_windows_task",
        "schedule": 60.0,  # every 60 seconds; views/services also sync on access
    },
    "scan-contract-milestones-and-expirations": {
        "task": "contracts.tasks.scan_contract_expirations_and_milestones",
        "schedule": 3600.0,  # every hour
    },
    "scan-contract-expirations-and-milestones": {
        "task": "contracts.tasks.scan_contract_expirations_and_milestones",
        "schedule": 86400.0,  # daily scan for upcoming renewal/milestones
    },
    "scan-overdue-approvals-and-escalate": {
        "task": "notifications.tasks.scan_overdue_approvals_and_escalate",
        "schedule": 3600.0,  # hourly scan for approvals exceeding SLA turnaround
    },
    "scan-vendor-document-expirations": {
        "task": "apps.vendors.tasks.scan_vendor_document_expirations_task",
        "schedule": 86400.0,  # daily scan (runs every 24h)
    },
}

# Login Throttling (django-axes) & Redirects
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.05  # 3 minutes cooloff window
AXES_LOCKOUT_TEMPLATE = "pages/lockout.html"
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_USERNAME_CALLABLE = "apps.accounts.axes_utils.get_axes_username"
LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "/login/"

# Security Baseline
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Email Configuration (loaded from environment variables, never hardcoded)
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="smtp-relay.brevo.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=15)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="ProcureSphere 360 <no-reply@localhost>")

# Application Login URL (used in credential welcome emails)
APP_LOGIN_URL = env("APP_LOGIN_URL", default="http://localhost:8000/login/")

# KYC document types (VendorDocument.document_type codes) that must be present AND verified
# before a vendor can be activated. Empty = at least one verified document is required.
VENDOR_MANDATORY_KYC_DOC_TYPES = env.list("VENDOR_MANDATORY_KYC_DOC_TYPES", default=[])

# Login URLs placed in vendor credential emails (comma separated, one line per environment)
VENDOR_PORTAL_LOGIN_URLS = env.list(
    "VENDOR_PORTAL_LOGIN_URLS", default=["http://localhost:8000/login/"]
)

# ---------------------------------------------------------------------------
# Brevo Transactional Email API (used for vendor credential emails)
# ---------------------------------------------------------------------------
BREVO_API_KEY = env("BREVO_API_KEY", default="")
BREVO_SENDER_EMAIL = env("BREVO_SENDER_EMAIL", default="no-reply@procuresphere.com")
BREVO_SENDER_NAME = env("BREVO_SENDER_NAME", default="ProcureSphere 360")
BREVO_SMTP_LOGIN = env("BREVO_SMTP_LOGIN", default="")
EMAIL_FROM_ADDRESS = env("EMAIL_FROM", default="")
EMAIL_FROM_NAME = env("EMAIL_FROM_NAME", default="")

# An xsmtpsib key is Brevo's SMTP password. Use it when EMAIL_HOST_PASSWORD is empty.
# SMTP login is the Brevo account email (EMAIL_HOST_USER, BREVO_SMTP_LOGIN, or EMAIL_FROM).
if not EMAIL_HOST_PASSWORD and BREVO_API_KEY.startswith("xsmtpsib-"):
    EMAIL_HOST_PASSWORD = BREVO_API_KEY
if not EMAIL_HOST_USER:
    if BREVO_SMTP_LOGIN:
        EMAIL_HOST_USER = BREVO_SMTP_LOGIN
    elif "@" in EMAIL_FROM_ADDRESS:
        EMAIL_HOST_USER = EMAIL_FROM_ADDRESS
    else:
        EMAIL_HOST_USER = BREVO_SENDER_EMAIL
if "localhost" in DEFAULT_FROM_EMAIL:
    if "@" in EMAIL_FROM_ADDRESS:
        sender_name = EMAIL_FROM_NAME or BREVO_SENDER_NAME
        DEFAULT_FROM_EMAIL = f"{sender_name} <{EMAIL_FROM_ADDRESS}>"
    else:
        DEFAULT_FROM_EMAIL = f"{BREVO_SENDER_NAME} <{BREVO_SENDER_EMAIL}>"

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

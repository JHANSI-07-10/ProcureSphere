import logging
import os
from typing import Optional, Tuple

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_login_url(request=None) -> str:
    """
    Constructs the absolute login URL for email templates.
    Uses the current request context if available, or falls back to
    the configured APP_LOGIN_URL from settings.
    """
    if request:
        try:
            return request.build_absolute_uri(reverse("login"))
        except Exception:
            return request.build_absolute_uri("/login/")
    return getattr(settings, "APP_LOGIN_URL", "http://localhost:8000/login/")


def send_user_welcome_email_direct(
    *,
    user,
    temporary_password: str,
    login_url: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Sends the welcome email directly using Django's configured email backend.

    Returns:
        Tuple of (status, error_message)
        where status is 'SENT' or 'FAILED'.
    """
    if not user or not user.email:
        logger.error("Cannot send welcome email: user or user email is missing.")
        return "FAILED", "User email address is missing."

    if not login_url:
        login_url = getattr(settings, "APP_LOGIN_URL", "http://localhost:8000/login/")

    subject = "Welcome to ProcureSphere 360 - Your Account Details"
    context = {
        "user": user,
        "temporary_password": temporary_password,
        "login_url": login_url,
    }

    try:
        html_content = render_to_string("emails/welcome_user_email.html", context)
        text_content = render_to_string("emails/welcome_user_email.txt", context)
    except Exception as template_err:
        logger.error("Failed to render welcome email template for %s: %s", user.email, template_err)
        return "FAILED", f"Email template rendering error: {template_err}"

    brevo_api_key = getattr(settings, "BREVO_API_KEY", "") or ""
    email_backend = getattr(settings, "EMAIL_BACKEND", "")
    smtp_key = brevo_api_key.startswith("xsmtpsib-")

    # An xsmtpsib key is an SMTP password, not an API key. API calls with it return 401.
    if smtp_key and not email_backend.endswith("locmem.EmailBackend"):
        return _send_welcome_via_smtp(
            user=user,
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            password=brevo_api_key,
        )

    # If Brevo API is configured and not in an in-memory test environment, use Brevo HTTP API (avoids blocked SMTP port 587 on Render)
    if brevo_api_key and not email_backend.endswith("locmem.EmailBackend"):
        try:
            import requests

            sender_name = getattr(settings, "BREVO_SENDER_NAME", "ProcureSphere 360")
            sender_email = getattr(settings, "BREVO_SENDER_EMAIL", "") or getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@procuresphere.com")
            if "<" in str(sender_email) and ">" in str(sender_email):
                sender_email = str(sender_email).split("<")[1].split(">")[0].strip()

            payload = {
                "sender": {
                    "name": sender_name,
                    "email": sender_email,
                },
                "to": [{"email": user.email}],
                "subject": subject,
                "htmlContent": html_content,
                "textContent": text_content,
            }

            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers={
                    "api-key": brevo_api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=15,
            )

            if not response.ok:
                err_msg = f"Brevo API error {response.status_code}: {response.text[:300]}"
                logger.error("Failed to send welcome email to %s via Brevo API: %s", user.email, err_msg)
                if response.status_code in (401, 403):
                    smtp_status, smtp_error = _send_welcome_via_smtp(
                        user=user,
                        subject=subject,
                        text_content=text_content,
                        html_content=html_content,
                        password=getattr(settings, "EMAIL_HOST_PASSWORD", "") or brevo_api_key,
                    )
                    if smtp_status == "SENT":
                        return smtp_status, smtp_error
                return "FAILED", err_msg

            # Update audit timestamp on user
            try:
                user.last_welcome_email_sent_at = timezone.now()
                user.save(update_fields=["last_welcome_email_sent_at"])
            except Exception as db_err:
                logger.warning("Could not update last_welcome_email_sent_at for %s: %s", user.email, db_err)

            logger.info("Welcome email successfully sent to %s via Brevo API", user.email)
            return "SENT", None

        except Exception as exc:
            logger.error("Failed to send welcome email to %s via Brevo API: %s", user.email, str(exc))
            return "FAILED", str(exc)

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "ProcureSphere 360 <noreply@procuresphere.com>")

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[user.email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)

        # Update audit timestamp on user
        try:
            user.last_welcome_email_sent_at = timezone.now()
            user.save(update_fields=["last_welcome_email_sent_at"])
        except Exception as db_err:
            logger.warning("Could not update last_welcome_email_sent_at for %s: %s", user.email, db_err)

        logger.info("Welcome email successfully sent to %s", user.email)
        return "SENT", None

    except Exception as exc:
        # Never log or expose the temporary password in exception logs
        logger.error("Failed to send welcome email to %s: %s", user.email, str(exc))
        return "FAILED", str(exc)


def _send_welcome_via_smtp(*, user, subject, text_content, html_content, password):
    """Send the welcome message through Brevo SMTP. The password argument is the SMTP key, never the user's login password."""
    from django.core.mail import get_connection

    env_from = os.environ.get("EMAIL_FROM", "")
    smtp_login = (
        getattr(settings, "EMAIL_HOST_USER", "")
        or getattr(settings, "BREVO_SMTP_LOGIN", "")
        or (env_from if "@" in env_from else "")
        or getattr(settings, "BREVO_SENDER_EMAIL", "")
    )
    if not smtp_login or not password:
        return "FAILED", "Brevo SMTP login is not configured."

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or getattr(settings, "BREVO_SENDER_EMAIL", smtp_login)
    try:
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=getattr(settings, "EMAIL_HOST", "smtp-relay.brevo.com"),
            port=getattr(settings, "EMAIL_PORT", 587),
            username=smtp_login,
            password=password,
            use_tls=getattr(settings, "EMAIL_USE_TLS", True),
            timeout=getattr(settings, "EMAIL_TIMEOUT", 15),
        )
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[user.email],
            connection=connection,
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        try:
            user.last_welcome_email_sent_at = timezone.now()
            user.save(update_fields=["last_welcome_email_sent_at"])
        except Exception as db_err:
            logger.warning("Could not update last_welcome_email_sent_at for %s: %s", user.email, db_err)
        logger.info("Welcome email successfully sent to %s via SMTP", user.email)
        return "SENT", None
    except Exception as exc:
        logger.error("Failed to send welcome email to %s via SMTP: %s", user.email, str(exc))
        text = str(exc)
        if "535" in text or "Authentication failed" in text:
            return (
                "FAILED",
                "Brevo rejected the SMTP login. The key in BREVO_API_KEY was used as the SMTP password. "
                "Set EMAIL_HOST_USER in .env to the Brevo SMTP login shown next to that key "
                "(often an address ending in @smtp-brevo.com), then restart the server.",
            )
        return "FAILED", text


def send_user_welcome_email(
    *,
    user,
    temporary_password: str,
    request=None,
    async_delivery: bool = False,
) -> Tuple[str, Optional[str]]:
    """
    High-level entry point to send or queue a welcome email.

    If async_delivery is True, attempts to enqueue via Celery. If Celery is not
    operational, falls back to direct sending.
    """
    login_url = get_login_url(request)

    if async_delivery:
        try:
            from apps.notifications.tasks import send_user_welcome_email_task
            send_user_welcome_email_task.delay(
                user_id=str(user.id),
                temporary_password=temporary_password,
                login_url=login_url,
            )
            logger.info("Welcome email queued for delivery via Celery for %s", user.email)
            return "QUEUED", None
        except Exception as queue_err:
            logger.warning("Celery enqueue failed for %s, falling back to direct delivery: %s", user.email, queue_err)
            return send_user_welcome_email_direct(
                user=user,
                temporary_password=temporary_password,
                login_url=login_url,
            )

    return send_user_welcome_email_direct(
        user=user,
        temporary_password=temporary_password,
        login_url=login_url,
    )

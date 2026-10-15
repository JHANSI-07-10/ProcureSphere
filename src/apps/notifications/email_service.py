import logging
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

    subject = "Welcome to ProcureSphere 360 – Your Account Details"
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

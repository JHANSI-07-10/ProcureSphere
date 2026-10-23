import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def test_celery_worker_task(message="Hello from Celery Worker!"):
    """A dummy task to verify the celery worker is picking up jobs."""
    logger.info(f"Worker Task Executed at {timezone.now()}: {message}")
    print(f"Worker Task Executed at {timezone.now()}: {message}")
    return f"Success: {message}"


@shared_task
def test_celery_beat_task():
    """A dummy task to verify celery beat is scheduling periodic jobs."""
    logger.info(f"Beat Task Triggered at {timezone.now()}")
    print(f"Beat Task Triggered at {timezone.now()}")
    return "Beat Triggered Successfully"


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_user_welcome_email_task(self, user_id: str, temporary_password: str, login_url: str = None):
    """
    Celery task to asynchronously send the user welcome email.
    """
    from apps.accounts.models import User
    from apps.notifications.email_service import send_user_welcome_email_direct

    try:
        user = User.objects.get(id=user_id)
        status, err = send_user_welcome_email_direct(
            user=user,
            temporary_password=temporary_password,
            login_url=login_url,
        )
        if status == "FAILED":
            logger.warning("Welcome email delivery task failed for user %s: %s", user.email, err)
            raise self.retry(exc=Exception(err))
        return status
    except User.DoesNotExist:
        logger.error("User with id %s does not exist for welcome email task", user_id)
        return "FAILED"
    except Exception as exc:
        logger.error("Unexpected error in send_user_welcome_email_task: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="notifications.tasks.scan_overdue_approvals_and_escalate")
def scan_overdue_approvals_and_escalate():
    """
    Celery Beat periodic task to check for pending approvals exceeding reminder
    and escalation SLA thresholds (48h and 72h).
    """
    from apps.notifications.services import scan_overdue_approvals_and_escalate_service

    result = scan_overdue_approvals_and_escalate_service()
    logger.info("Scan overdue approvals completed: %s", result)
    return result


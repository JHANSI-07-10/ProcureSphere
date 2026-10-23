from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.accounts.models import User

from .models import Notification
from .selectors import get_recent_unread_notifications


def notify_users(*, users, notification_type: str, title: str, message: str, target_url: str = ""):
    """
    Creates one in-app Notification per active recipient. Returns the created notifications.
    """
    notifications = [
        Notification(
            recipient=user,
            notification_type=notification_type,
            title=title[:200],
            message=message,
            target_url=target_url[:255],
        )
        for user in users
        if user.is_active
    ]
    return Notification.objects.bulk_create(notifications)


def notify_vendor_users(*, vendor, notification_type: str, title: str, message: str, target_url=""):
    """Notifies every portal user linked to the given vendor."""
    users = User.objects.filter(vendor=vendor, is_active=True)
    return notify_users(
        users=users,
        notification_type=notification_type,
        title=title,
        message=message,
        target_url=target_url,
    )


def notify_role_users(
    *, role_codes, notification_type: str, title: str, message: str, target_url=""
):
    """Notifies every active user holding one of the given role codes."""
    users = User.objects.filter(role__code__in=role_codes, is_active=True)
    return notify_users(
        users=users,
        notification_type=notification_type,
        title=title,
        message=message,
        target_url=target_url,
    )


def get_top_unread_for_user(*, user, limit: int = 10):
    """Service-layer fetch of the top-N recent unread notifications.

    Read path goes through ``selectors``; this wrapper keeps views thin and
    gives callers a single domain entry-point (enforces authenticated user).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication required to read notifications.")
    return get_recent_unread_notifications(user, limit=limit)


@transaction.atomic
def mark_notification_as_read(*, user, notification_id) -> Notification:
    """Mark one notification read. RBAC: only the recipient may mutate it.

    Raises:
        PermissionDenied: unauthenticated caller.
        Notification.DoesNotExist: id unknown OR owned by another user
            (deliberately indistinguishable to prevent ID enumeration).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication required to update notifications.")
    notification = Notification.objects.select_for_update().get(pk=notification_id, recipient=user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read", "updated_at"])
    return notification


def scan_overdue_approvals_and_escalate_service(
    reminder_hours: int = 48, escalation_hours: int = 72
) -> dict:
    """
    Scans pending approval workflows (Requisitions, Invoices, Exceptions)
    that exceed configured SLA turnaround limits.
    Emits reminders (>= reminder_hours) and escalations (>= escalation_hours)
    to approvers and management, retaining audit evidence.
    """
    from django.utils import timezone

    from apps.accounts.models import Role, User
    from apps.approvals.models import ApprovalAction
    from apps.audit.models import AuditLog
    from apps.invoices.models import MatchException
    from apps.requisitions.models import PurchaseRequisition

    now = timezone.now()
    reminder_cutoff = now - timezone.timedelta(hours=reminder_hours)
    escalation_cutoff = now - timezone.timedelta(hours=escalation_hours)

    reminders_sent = 0
    escalations_sent = 0

    # 1. Check Purchase Requisitions in review
    pending_prs = PurchaseRequisition.objects.filter(
        status__in=[
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
            PurchaseRequisition.STATUS_BUDGET_REVIEW,
        ],
        updated_at__lte=reminder_cutoff,
    ).select_related("department", "requester")

    proc_managers = list(User.objects.filter(role__code=Role.PROC_MGR, is_active=True))

    for pr in pending_prs:
        is_escalation = pr.updated_at <= escalation_cutoff
        today_date = now.date()

        if is_escalation:
            # Send escalation to Procurement Managers & Super Admins
            recipients = proc_managers
            title = f"ESCALATION: PR {pr.pr_number} Approval Overdue (> {escalation_hours}h)"
            message = (
                f"Requisition {pr.pr_number} ({pr.title}, ${pr.total_amount:,.2f}) for Department "
                f"'{pr.department.name if pr.department else 'N/A'}' has been pending approval since "
                f"{pr.updated_at.strftime('%Y-%m-%d %H:%M')}. Management escalation invoked."
            )
            for recipient in recipients:
                already_notified = Notification.objects.filter(
                    recipient=recipient,
                    title=title,
                    created_at__date=today_date,
                ).exists()
                if not already_notified:
                    Notification.objects.create(
                        recipient=recipient,
                        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                        title=title,
                        message=message,
                        target_url=f"/requisitions/{pr.id}/",
                    )
                    escalations_sent += 1
            # Audit the escalation event
            AuditLog.objects.create(
                actor=None,
                action=AuditLog.ACTION_UPDATE,
                target_model="PurchaseRequisition",
                target_object_id=str(pr.id),
                previous_state={"status": pr.status, "escalated": False},
                new_state={"status": pr.status, "escalated": True, "escalation_hours": escalation_hours},
            )
        else:
            # Send reminder to current step approvers
            from apps.approvals.services import evaluate_approval_chain

            chain = evaluate_approval_chain(
                module="PR",
                amount=pr.total_amount,
                department=pr.department,
            )
            approvers = []
            if chain:
                approved_count = ApprovalAction.objects.filter(
                    target_object_id=pr.id,
                    target_model_name="PurchaseRequisition",
                    action=ApprovalAction.ACTION_APPROVE,
                ).count()
                if approved_count < len(chain):
                    step = chain[approved_count]
                    if step["effective_approver"]:
                        approvers = [step["effective_approver"]]
                    else:
                        role_approvers = User.objects.filter(role=step["approver_role"], is_active=True)
                        if step["approver_role"].code == Role.DEPT_APPROVER and pr.department:
                            dept_apps = role_approvers.filter(department=pr.department)
                            approvers = list(dept_apps) if dept_apps.exists() else list(role_approvers)
                        else:
                            approvers = list(role_approvers)

            title = f"Reminder: PR {pr.pr_number} Awaiting Your Approval (> {reminder_hours}h)"
            message = (
                f"Requisition {pr.pr_number} ({pr.title}, ${pr.total_amount:,.2f}) has been pending your "
                f"approval review since {pr.updated_at.strftime('%Y-%m-%d %H:%M')}. Please review promptly."
            )
            for approver in approvers:
                already_notified = Notification.objects.filter(
                    recipient=approver,
                    title=title,
                    created_at__date=today_date,
                ).exists()
                if not already_notified:
                    Notification.objects.create(
                        recipient=approver,
                        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                        title=title,
                        message=message,
                        target_url=f"/requisitions/{pr.id}/",
                    )
                    reminders_sent += 1

    # 2. Check Match Exceptions overdue
    pending_exceptions = MatchException.objects.filter(
        status=MatchException.STATUS_OPEN,
        created_at__lte=reminder_cutoff,
    ).select_related("invoice", "invoice__po")

    finance_users = list(User.objects.filter(role__code=Role.FINANCE_AP, is_active=True))
    for exc in pending_exceptions:
        title = f"Reminder: Unresolved Match Exception for Invoice {exc.invoice.invoice_number}"
        message = (
            f"Match Exception ({exc.get_exception_type_display()}) on Invoice {exc.invoice.invoice_number} "
            f"has been open since {exc.created_at.strftime('%Y-%m-%d %H:%M')}. Finance review required."
        )
        for fin_user in finance_users:
            already_notified = Notification.objects.filter(
                recipient=fin_user,
                title=title,
                created_at__date=now.date(),
            ).exists()
            if not already_notified:
                Notification.objects.create(
                    recipient=fin_user,
                    notification_type=Notification.TYPE_EXCEPTION_RAISED,
                    title=title,
                    message=message,
                    target_url=f"/invoices/{exc.invoice.id}/",
                )
                reminders_sent += 1

    return {
        "reminders_sent": reminders_sent,
        "escalations_sent": escalations_sent,
        "scanned_at": str(now),
    }

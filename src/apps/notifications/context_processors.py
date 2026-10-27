from apps.accounts.models import Role
from apps.notifications.models import Notification
from apps.requisitions.models import PurchaseRequisition


def notifications_processor(request):
    """
    Global context processor supplying unread notifications and pending approval counts to all templates.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "unread_notifications_count": 0,
            "pending_approvals_count": 0,
            "notifications_list": [],
        }

    user = request.user
    role_code = getattr(user, "role_code", None)

    # 1. Calculate unread notifications count for recipient
    unread_count = Notification.objects.filter(recipient=user, is_read=False).count()

    # 2. Calculate pending approvals count for DEPT_APPROVER, PROC_MGR, or SUPER_ADMIN
    pending_count = 0
    statuses = [
        PurchaseRequisition.STATUS_SUBMITTED,
        PurchaseRequisition.STATUS_MANAGER_REVIEW,
        PurchaseRequisition.STATUS_BUDGET_REVIEW,
    ]
    if role_code == Role.DEPT_APPROVER:
        user_dept = getattr(user, "department", None)
        if user_dept:
            pending_count = PurchaseRequisition.objects.filter(
                department=user_dept, status__in=statuses
            ).count()
        else:
            pending_count = 0
    elif role_code in [Role.PROC_MGR, Role.SUPER_ADMIN]:
        pending_count = PurchaseRequisition.objects.filter(status__in=statuses).count()
    elif role_code == Role.LEGAL_MGR:
        from apps.contracts.models import Contract

        pending_count = Contract.objects.filter(status=Contract.STATUS_LEGAL_REVIEW).count()

    recent_notifications = Notification.objects.filter(recipient=user).order_by("-created_at")[:5]

    return {
        "unread_notifications_count": unread_count,
        "pending_approvals_count": pending_count,
        "notifications_list": recent_notifications,
    }

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.requisitions.models import PurchaseRequisition

from .models import ApprovalDelegate
from .services import process_approval_action_service, set_approval_delegate_service


def _get_approval_querysets(user, role_code):
    """Retrieve base pending and history querysets scoped by role and department."""
    pending_statuses = ["SUBMITTED", "MANAGER_REVIEW", "BUDGET_REVIEW"]
    history_statuses = ["APPROVED", "REJECTED", "SOURCING", "PO_ISSUED"]

    if role_code == Role.REQUESTER:
        return (
            PurchaseRequisition.objects.filter(requester=user, status__in=pending_statuses),
            PurchaseRequisition.objects.filter(requester=user, status__in=history_statuses),
        )

    if role_code == Role.DEPT_APPROVER:
        user_dept = getattr(user, "department", None)
        if not user_dept:
            return PurchaseRequisition.objects.none(), PurchaseRequisition.objects.none()
        return (
            PurchaseRequisition.objects.filter(department=user_dept, status__in=pending_statuses),
            PurchaseRequisition.objects.filter(department=user_dept, status__in=history_statuses),
        )

    return (
        PurchaseRequisition.objects.filter(status__in=pending_statuses),
        PurchaseRequisition.objects.filter(status__in=history_statuses),
    )


def _get_base_layout(role_code):
    """Determine layout template based on user role."""
    if role_code in [Role.LEGAL_MGR, "LEGAL_MGR"]:
        return "layouts/legal_base.html"
    if role_code in [Role.DEPT_APPROVER, Role.PROC_MGR]:
        return "layouts/approver_base.html"
    return "layouts/requester_base.html"


def _handle_approval_post(request, role_code):
    """Process approval or rejection POST submission."""
    if role_code == Role.AUDITOR:
        messages.error(
            request,
            "Permission Denied: Compliance Auditors hold strictly read-only permissions and cannot approve or reject requisitions.",
        )
        return redirect("approvals_inbox")

    pr_id = request.POST.get("pr_id")
    action = request.POST.get("action")  # APPROVED or REJECTED
    comments = request.POST.get("comments", "").strip()

    try:
        pr = PurchaseRequisition.objects.get(id=pr_id)
        if action in ["REJECT", "REJECTED"] and not comments:
            comments = "Rejected by Department Approver."
        process_approval_action_service(
            target_object=pr,
            actor=request.user,
            action=action,
            comments=comments,
        )
        messages.success(request, f"Requisition {pr.pr_number} successfully {action.lower()}!")
        referer = request.META.get("HTTP_REFERER")
        if referer:
            return redirect(referer)
        return redirect("approvals_inbox")
    except Exception as e:
        messages.error(request, f"Error processing approval action: {str(e)}")
        return None


@login_required(login_url="/login/")
def approvals_inbox_view(request):
    """
    Unified Approvals Inbox for Department Approvers, Procurement Managers, and Finance.
    """
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )
    if not user.is_superuser and (getattr(user, "is_vendor", False) or role_code in [Role.VENDOR_USER, "VENDOR_USER"]):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("External vendors are not authorized to view the internal approval inbox.")

    if request.method == "POST":
        res = _handle_approval_post(request, role_code)
        if res:
            return res

    tab = request.GET.get("tab", "pending")
    search_q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    pending_prs, history_prs = _get_approval_querysets(user, role_code)

    if search_q:
        q_filter = (
            Q(pr_number__icontains=search_q)
            | Q(title__icontains=search_q)
            | Q(requester__email__icontains=search_q)
            | Q(department__name__icontains=search_q)
        )
        pending_prs = pending_prs.filter(q_filter)
        history_prs = history_prs.filter(q_filter)

    valid_pending_statuses = ["SUBMITTED", "MANAGER_REVIEW", "BUDGET_REVIEW"]
    valid_history_statuses = ["APPROVED", "REJECTED", "SOURCING", "PO_ISSUED"]

    if status_filter:
        if tab == "history":
            if status_filter in valid_history_statuses:
                history_prs = history_prs.filter(status=status_filter)
            else:
                status_filter = ""
        else:
            if status_filter in valid_pending_statuses:
                pending_prs = pending_prs.filter(status=status_filter)
            else:
                status_filter = ""

    pending_prs = pending_prs.order_by("-updated_at")
    history_prs = history_prs.order_by("-updated_at")

    pending_count = pending_prs.count()
    has_budget_overrun = pending_prs.filter(status="BUDGET_REVIEW").exists()

    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    querystring = query_params.urlencode()

    page_number = request.GET.get("page")
    if tab == "history":
        history_paginator = Paginator(history_prs, 10)
        page_obj = history_paginator.get_page(page_number)
        history_display = page_obj
        pending_display = pending_prs
    else:
        pending_paginator = Paginator(pending_prs, 10)
        page_obj = pending_paginator.get_page(page_number)
        pending_display = page_obj
        history_display = history_prs

    base_layout = _get_base_layout(role_code)

    context = {
        "pending_prs": pending_display,
        "history_prs": history_display,
        "page_obj": page_obj,
        "querystring": querystring,
        "pending_count": pending_count,
        "active_tab": tab,
        "role_code": role_code,
        "base_layout": base_layout,
        "search_q": search_q,
        "status_filter": status_filter,
        "has_budget_overrun": has_budget_overrun,
    }
    return render(request, "pages/approvals/inbox.html", context)


# Alias for URL route compatibility
inbox_view = approvals_inbox_view


@login_required(login_url="/login/")
def delegation_view(request):
    """
    Allows approvers to set a temporary delegate for their approval responsibilities.
    """
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else ""
    )

    if request.method == "POST":
        delegate_id = request.POST.get("delegate_id")
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")
        reason = request.POST.get("reason", "").strip()

        try:
            delegate_user = User.objects.get(pk=delegate_id)
            set_approval_delegate_service(
                approver=user,
                delegate=delegate_user,
                start_date=start_date,
                end_date=end_date,
                reason=reason,
            )
            messages.success(request, f"Delegation to {delegate_user.email} set successfully.")
        except User.DoesNotExist:
            messages.error(request, "Selected delegate user not found.")
        except Exception as e:
            messages.error(request, f"Error setting delegation: {str(e)}")
        return redirect("approvals_delegation")

    active_delegates = ApprovalDelegate.objects.filter(
        approver=user, is_active=True, end_date__gte=timezone.now().date()
    ).order_by("-start_date")

    eligible_delegates = User.objects.filter(
        role__code__in=[Role.DEPT_APPROVER, Role.PROC_MGR, Role.PROC_EXEC]
    ).exclude(pk=user.pk).select_related("role")

    base_layout = _get_base_layout(role_code)
    return render(request, "pages/approvals/delegation.html", {
        "active_delegates": active_delegates,
        "eligible_delegates": eligible_delegates,
        "base_layout": base_layout,
        "role_code": role_code,
    })

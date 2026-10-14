from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Role
from apps.approvals.models import ApprovalAction
from apps.approvals.services import process_approval_action_service
from apps.budgets.models import Budget
from apps.budgets.services import validate_budget_availability_service
from apps.core.validators import validate_pr_attachment
from apps.organization.models import CostCenter, Department
from apps.requisitions.models import PRAttachment, PRLine, PurchaseRequisition
from apps.requisitions.services import (
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)


@login_required(login_url="/login/")
def list_view(request):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )
    search_q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if role_code == Role.REQUESTER:
        items = PurchaseRequisition.objects.filter(requester=user).order_by("-created_at")
    elif role_code == Role.DEPT_APPROVER:
        user_department = getattr(user, "department", None)
        if user_department:
            items = PurchaseRequisition.objects.filter(department=user_department).order_by(
                "-created_at"
            )
        else:
            items = PurchaseRequisition.objects.none()
    else:
        items = PurchaseRequisition.objects.all().order_by("-created_at")

    if search_q:
        items = items.filter(
            Q(pr_number__icontains=search_q)
            | Q(title__icontains=search_q)
            | Q(requester__email__icontains=search_q)
        )

    if status_filter:
        if status_filter == "PENDING":
            items = items.filter(
                status__in=[
                    PurchaseRequisition.STATUS_SUBMITTED,
                    PurchaseRequisition.STATUS_MANAGER_REVIEW,
                    PurchaseRequisition.STATUS_BUDGET_REVIEW,
                ]
            )
        elif status_filter == "APPROVED":
            items = items.filter(
                status__in=[
                    PurchaseRequisition.STATUS_APPROVED,
                    PurchaseRequisition.STATUS_SOURCING,
                    PurchaseRequisition.STATUS_PO_ISSUED,
                ]
            )
        else:
            items = items.filter(status=status_filter)

    if role_code == Role.LEGAL_MGR or role_code == "LEGAL_MGR":
        base_layout = "layouts/legal_base.html"
    elif role_code in [Role.DEPT_APPROVER, Role.PROC_MGR]:
        base_layout = "layouts/approver_base.html"
    else:
        base_layout = "layouts/requester_base.html"

    return render(
        request,
        "pages/requisitions/list.html",
        {
            "items": items,
            "role_code": role_code,
            "base_layout": base_layout,
            "search_q": search_q,
            "status_filter": status_filter,
        },
    )


@login_required(login_url="/login/")
def create_view(request):
    user = request.user
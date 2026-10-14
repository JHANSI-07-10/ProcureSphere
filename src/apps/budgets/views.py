from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from apps.accounts.models import Role
from apps.budgets.models import Budget, SpendLedger
from apps.requisitions.models import PurchaseRequisition


def _enforce_internal_budget_access(request):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else None
    )
    if role_code == Role.VENDOR_USER and not user.is_superuser:
        raise PermissionDenied(
            "External vendors are not authorized to view internal budget information."
        )


@login_required(login_url="/login/")
def list_view(request):
    _enforce_internal_budget_access(request)
    budgets = Budget.objects.select_related("cost_center", "fiscal_period").all()
    return render(request, "pages/budgets/list.html", {"budgets": budgets})


@login_required(login_url="/login/")
def ledger_view(request):
    _enforce_internal_budget_access(request)
    ledger = SpendLedger.objects.select_related("budget__cost_center").order_by("-created_at")
    return render(request, "pages/budgets/ledger.html", {"ledger": ledger})


@login_required(login_url="/login/")
def budget_exceptions_view(request):
    _enforce_internal_budget_access(request)
    exceptions = PurchaseRequisition.objects.filter(
        status=PurchaseRequisition.STATUS_BUDGET_REVIEW
    ).order_by("-updated_at")
    return render(request, "pages/budgets/exceptions.html", {"exceptions": exceptions})

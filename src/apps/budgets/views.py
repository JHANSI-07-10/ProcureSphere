from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import render

from apps.accounts.models import Role
from apps.budgets.models import Budget, SpendLedger
from apps.organization.models import CostCenter, Department, FiscalPeriod
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

    selected_dept_id = request.GET.get("dept") or ""
    selected_cc_id = request.GET.get("cost_center") or ""
    selected_period_id = request.GET.get("period") or ""

    departments = Department.objects.all().order_by("name")
    cost_centers = CostCenter.objects.select_related("department").all().order_by("code")
    fiscal_periods = FiscalPeriod.objects.all().order_by("-year", "-period_number")

    all_cost_centers = list(cost_centers)
    if selected_dept_id:
        filtered_cost_centers = [
            cc for cc in all_cost_centers if str(cc.department_id) == str(selected_dept_id)
        ]
    else:
        filtered_cost_centers = all_cost_centers

    budgets = Budget.objects.select_related("cost_center__department", "fiscal_period").all()

    if selected_dept_id:
        budgets = budgets.filter(cost_center__department_id=selected_dept_id)
    if selected_cc_id:
        budgets = budgets.filter(cost_center_id=selected_cc_id)
    if selected_period_id:
        budgets = budgets.filter(fiscal_period_id=selected_period_id)

    totals = budgets.aggregate(
        total_allocated=Sum("allocated_amount"),
        total_reserved=Sum("reserved_amount"),
        total_committed=Sum("committed_amount"),
        total_actual=Sum("actual_amount"),
    )
    total_allocated = totals["total_allocated"] or Decimal("0.00")
    total_reserved = totals["total_reserved"] or Decimal("0.00")
    total_committed = totals["total_committed"] or Decimal("0.00")
    total_actual = totals["total_actual"] or Decimal("0.00")
    total_available = max(
        Decimal("0.00"), total_allocated - (total_reserved + total_committed + total_actual)
    )

    selected_dept_obj = (
        next((d for d in departments if str(d.id) == str(selected_dept_id)), None)
        if selected_dept_id
        else None
    )
    selected_cc_obj = (
        next((c for c in all_cost_centers if str(c.id) == str(selected_cc_id)), None)
        if selected_cc_id
        else None
    )
    selected_period_obj = (
        next((p for p in fiscal_periods if str(p.id) == str(selected_period_id)), None)
        if selected_period_id
        else None
    )

    return render(
        request,
        "pages/budgets/list.html",
        {
            "budgets": budgets,
            "departments": departments,
            "cost_centers": filtered_cost_centers,
            "fiscal_periods": fiscal_periods,
            "selected_dept_id": selected_dept_id,
            "selected_cc_id": selected_cc_id,
            "selected_period_id": selected_period_id,
            "selected_dept_obj": selected_dept_obj,
            "selected_cc_obj": selected_cc_obj,
            "selected_period_obj": selected_period_obj,
            "totals": {
                "allocated": total_allocated,
                "reserved": total_reserved,
                "committed": total_committed,
                "actual": total_actual,
                "available": total_available,
            },
        },
    )


@login_required(login_url="/login/")
def ledger_view(request):
    _enforce_internal_budget_access(request)

    selected_dept_id = request.GET.get("dept") or ""
    selected_cc_id = request.GET.get("cost_center") or ""
    selected_entry_type = request.GET.get("entry_type") or ""

    departments = Department.objects.all().order_by("name")
    cost_centers = CostCenter.objects.select_related("department").all().order_by("code")

    all_cost_centers = list(cost_centers)
    if selected_dept_id:
        filtered_cost_centers = [
            cc for cc in all_cost_centers if str(cc.department_id) == str(selected_dept_id)
        ]
    else:
        filtered_cost_centers = all_cost_centers

    ledger = SpendLedger.objects.select_related("budget__cost_center__department").order_by(
        "-created_at"
    )

    if selected_dept_id:
        ledger = ledger.filter(budget__cost_center__department_id=selected_dept_id)
    if selected_cc_id:
        ledger = ledger.filter(budget__cost_center_id=selected_cc_id)
    if selected_entry_type:
        ledger = ledger.filter(entry_type=selected_entry_type)

    selected_dept_obj = (
        next((d for d in departments if str(d.id) == str(selected_dept_id)), None)
        if selected_dept_id
        else None
    )
    selected_cc_obj = (
        next((c for c in all_cost_centers if str(c.id) == str(selected_cc_id)), None)
        if selected_cc_id
        else None
    )

    return render(
        request,
        "pages/budgets/ledger.html",
        {
            "ledger": ledger,
            "departments": departments,
            "cost_centers": filtered_cost_centers,
            "selected_dept_id": selected_dept_id,
            "selected_cc_id": selected_cc_id,
            "selected_entry_type": selected_entry_type,
            "selected_dept_obj": selected_dept_obj,
            "selected_cc_obj": selected_cc_obj,
            "entry_types": SpendLedger.ENTRY_CHOICES,
        },
    )


@login_required(login_url="/login/")
def budget_exceptions_view(request):
    _enforce_internal_budget_access(request)

    selected_dept_id = request.GET.get("dept") or ""
    selected_cc_id = request.GET.get("cost_center") or ""

    departments = Department.objects.all().order_by("name")
    cost_centers = CostCenter.objects.select_related("department").all().order_by("code")

    all_cost_centers = list(cost_centers)
    if selected_dept_id:
        filtered_cost_centers = [
            cc for cc in all_cost_centers if str(cc.department_id) == str(selected_dept_id)
        ]
    else:
        filtered_cost_centers = all_cost_centers

    exceptions = (
        PurchaseRequisition.objects.filter(status=PurchaseRequisition.STATUS_BUDGET_REVIEW)
        .select_related("department", "cost_center", "requester")
        .order_by("-updated_at")
    )

    if selected_dept_id:
        exceptions = exceptions.filter(department_id=selected_dept_id)
    if selected_cc_id:
        exceptions = exceptions.filter(cost_center_id=selected_cc_id)

    selected_dept_obj = (
        next((d for d in departments if str(d.id) == str(selected_dept_id)), None)
        if selected_dept_id
        else None
    )
    selected_cc_obj = (
        next((c for c in all_cost_centers if str(c.id) == str(selected_cc_id)), None)
        if selected_cc_id
        else None
    )

    return render(
        request,
        "pages/budgets/exceptions.html",
        {
            "exceptions": exceptions,
            "departments": departments,
            "cost_centers": filtered_cost_centers,
            "selected_dept_id": selected_dept_id,
            "selected_cc_id": selected_cc_id,
            "selected_dept_obj": selected_dept_obj,
            "selected_cc_obj": selected_cc_obj,
        },
    )

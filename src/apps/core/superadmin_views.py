import csv

import openpyxl
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalPolicy
from apps.audit.models import AuditLog
from apps.budgets.models import Budget
from apps.core.superadmin_forms import (
    BudgetAdminForm,
    CostCenterAdminForm,
    DepartmentAdminForm,
    OrganizationAdminForm,
    PolicyAdminForm,
    UserAdminForm,
)
from apps.organization.models import CostCenter, Department, Organization
from functools import wraps


def _is_super_admin(user):
    return user.is_authenticated and (
        user.is_superuser
        or getattr(user, "role_code", None) == Role.SUPER_ADMIN
        or (hasattr(user, "role") and user.role and user.role.code == Role.SUPER_ADMIN)
    )


def super_admin_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _is_super_admin(request.user):
            return HttpResponse(
                "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
                status=403,
            )
        return view_func(request, *args, **kwargs)

    return _wrapped


@login_required
@super_admin_required
def user_list_view(request):
    users = User.objects.select_related("role", "department").all().order_by("-date_joined")
    return render(request, "pages/dashboards/superadmin/users.html", {"users": users})


@login_required
@super_admin_required
def user_create_view(request):
    if request.method == "POST":
        form = UserAdminForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.username = form.cleaned_data["email"]
            user.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = UserAdminForm()
    return render(request, "pages/dashboards/superadmin/user_form.html", {"form": form})


@login_required
@super_admin_required
def user_edit_view(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        form = UserAdminForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = UserAdminForm(instance=user)
    return render(request, "pages/dashboards/superadmin/user_form.html", {"form": form})


@login_required
@super_admin_required
def org_list_view(request):
    orgs = Organization.objects.all().order_by("name")
    departments = Department.objects.select_related("organization").all().order_by("name")
    cost_centers = CostCenter.objects.select_related("department", "manager").all().order_by("code")
    selected_dept = request.GET.get("department", "").strip()
    if selected_dept:
        try:
            import uuid
            uuid.UUID(selected_dept)
            cost_centers = cost_centers.filter(department_id=selected_dept)
        except (ValueError, AttributeError):
            selected_dept = ""

    return render(
        request,
        "pages/dashboards/superadmin/organizations.html",
        {
            "orgs": orgs,
            "departments": departments,
            "cost_centers": cost_centers,
            "selected_dept": selected_dept,
        },
    )


@login_required
@super_admin_required
def org_create_view(request):
    if request.method == "POST":
        form = OrganizationAdminForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = OrganizationAdminForm()
    return render(request, "pages/dashboards/superadmin/org_form.html", {"form": form})


@login_required
@super_admin_required
def org_edit_view(request, org_id):
    org = get_object_or_404(Organization, pk=org_id)
    if request.method == "POST":
        form = OrganizationAdminForm(request.POST, instance=org)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = OrganizationAdminForm(instance=org)
    return render(request, "pages/dashboards/superadmin/org_form.html", {"form": form})


@login_required
def dept_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = DepartmentAdminForm(request.POST)
        if form.is_valid():
            dept = form.save(commit=False)
            if hasattr(dept, "created_by_id") and not dept.created_by_id:
                dept.created_by = request.user
            dept.save()

            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=request.user,
                action=AuditLog.ACTION_CREATE,
                target_model="Department",
                target_object_id=str(dept.id),
                previous_state=None,
                new_state={
                    "code": dept.code,
                    "name": dept.name,
                    "organization": dept.organization.code,
                    "description": dept.description,
                },
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = DepartmentAdminForm()
    return render(request, "pages/dashboards/superadmin/dept_form.html", {"form": form, "is_edit": False})


@login_required
def dept_edit_view(request, dept_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    dept = get_object_or_404(Department, pk=dept_id)
    if request.method == "POST":
        old_state = {
            "code": dept.code,
            "name": dept.name,
            "organization": dept.organization.code,
            "description": dept.description,
        }
        form = DepartmentAdminForm(request.POST, instance=dept)
        if form.is_valid():
            dept = form.save()

            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=request.user,
                action=AuditLog.ACTION_UPDATE,
                target_model="Department",
                target_object_id=str(dept.id),
                previous_state=old_state,
                new_state={
                    "code": dept.code,
                    "name": dept.name,
                    "organization": dept.organization.code,
                    "description": dept.description,
                },
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = DepartmentAdminForm(instance=dept)
    return render(request, "pages/dashboards/superadmin/dept_form.html", {"form": form, "is_edit": True, "dept": dept})


@login_required
def cost_center_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = CostCenterAdminForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            from apps.organization.services import create_cost_center_service

            cost_center = create_cost_center_service(
                department=cleaned["department"],
                code=cleaned["code"],
                name=cleaned["name"],
                manager=cleaned.get("manager"),
            )

            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=request.user,
                action=AuditLog.ACTION_CREATE,
                target_model="CostCenter",
                target_object_id=str(cost_center.id),
                previous_state=None,
                new_state={
                    "code": cost_center.code,
                    "name": cost_center.name,
                    "department": cost_center.department.code,
                    "manager": cost_center.manager.email if cost_center.manager else None,
                },
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = CostCenterAdminForm()
    return render(
        request,
        "pages/dashboards/superadmin/cost_center_form.html",
        {"form": form, "is_edit": False},
    )


@login_required
def cost_center_edit_view(request, cc_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    cost_center = get_object_or_404(CostCenter, pk=cc_id)
    if request.method == "POST":
        old_state = {
            "code": cost_center.code,
            "name": cost_center.name,
            "department": cost_center.department.code,
            "manager": cost_center.manager.email if cost_center.manager else None,
        }
        form = CostCenterAdminForm(request.POST, instance=cost_center)
        if form.is_valid():
            cost_center = form.save()

            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=request.user,
                action=AuditLog.ACTION_UPDATE,
                target_model="CostCenter",
                target_object_id=str(cost_center.id),
                previous_state=old_state,
                new_state={
                    "code": cost_center.code,
                    "name": cost_center.name,
                    "department": cost_center.department.code,
                    "manager": cost_center.manager.email if cost_center.manager else None,
                },
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = CostCenterAdminForm(instance=cost_center)
    return render(
        request,
        "pages/dashboards/superadmin/cost_center_form.html",
        {"form": form, "is_edit": True, "cost_center": cost_center},
    )


@login_required
@super_admin_required
def budget_list_view(request):
    budgets = Budget.objects.select_related("cost_center", "fiscal_period").all()
    return render(request, "pages/dashboards/superadmin/budgets.html", {"budgets": budgets})


@login_required
@super_admin_required
def policy_list_view(request):
    policies = ApprovalPolicy.objects.select_related("department").all()
    return render(request, "pages/dashboards/superadmin/policies.html", {"policies": policies})


@login_required
@super_admin_required
def audit_list_view(request):
    logs_list = AuditLog.objects.select_related("actor").all().order_by("-timestamp")
    paginator = Paginator(logs_list, 10)  # Show 10 logs per page

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "pages/dashboards/superadmin/audit.html", {"page_obj": page_obj})


@login_required
@super_admin_required
def budget_create_view(request):
    if request.method == "POST":
        form = BudgetAdminForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = BudgetAdminForm()
    return render(request, "pages/dashboards/superadmin/budget_form.html", {"form": form})


@login_required
@super_admin_required
def budget_edit_view(request, budget_id):
    budget = get_object_or_404(Budget, pk=budget_id)
    if request.method == "POST":
        form = BudgetAdminForm(request.POST, instance=budget)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = BudgetAdminForm(instance=budget)
    return render(request, "pages/dashboards/superadmin/budget_form.html", {"form": form})


@login_required
@super_admin_required
def policy_create_view(request):
    if request.method == "POST":
        form = PolicyAdminForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = PolicyAdminForm()
    return render(request, "pages/dashboards/superadmin/policy_form.html", {"form": form})


@login_required
@super_admin_required
def policy_edit_view(request, policy_id):
    policy = get_object_or_404(ApprovalPolicy, pk=policy_id)
    if request.method == "POST":
        form = PolicyAdminForm(request.POST, instance=policy)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = PolicyAdminForm(instance=policy)
    return render(request, "pages/dashboards/superadmin/policy_form.html", {"form": form})


@login_required
@super_admin_required
def audit_export_view(request):
    format_type = request.GET.get("format", "csv")
    logs = AuditLog.objects.select_related("actor").all().order_by("-timestamp")

    if format_type == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="audit_history.csv"'
        writer = csv.writer(response)
        writer.writerow(["Timestamp", "Actor", "Action", "Target Object", "IP Address", "Comments"])
        for log in logs:
            actor = log.actor.email if log.actor else "System"
            writer.writerow(
                [
                    log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    actor,
                    log.action,
                    log.readable_target,
                    log.ip_address or "-",
                    getattr(log, "comments", "-") or "-",
                ]
            )
        return response

    elif format_type == "xlsx":
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="audit_history.xlsx"'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Audit History"
        ws.append(["Timestamp", "Actor", "Action", "Target Object", "IP Address", "Comments"])
        for log in logs:
            actor = log.actor.email if log.actor else "System"
            ws.append(
                [
                    log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    actor,
                    log.action,
                    log.readable_target,
                    log.ip_address or "-",
                    getattr(log, "comments", "-") or "-",
                ]
            )
        wb.save(response)
        return response

    elif format_type == "pdf":
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            return HttpResponse(
                "PDF export is unavailable: reportlab is not installed.", status=501
            )
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="audit_history.pdf"'
        p = canvas.Canvas(response)
        y = 800
        p.setFont("Helvetica-Bold", 14)
        p.drawString(50, y, "ProcureSphere 360 - Audit History")
        y -= 30
        p.setFont("Helvetica", 10)

        for log in logs[:100]:  # Limit to 100 for simple PDF layout
            if y < 50:
                p.showPage()
                p.setFont("Helvetica", 10)
                y = 800

            actor = log.actor.email if log.actor else "System"
            line = f"{log.timestamp.strftime('%Y-%m-%d %H:%M')} | {actor} | {log.action} | {log.target_model} #{log.target_object_id}"
            p.drawString(50, y, line)
            y -= 15

        p.showPage()
        p.save()
        return response

    return HttpResponse("Invalid format", status=400)

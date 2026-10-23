import csv

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.models import Role, User
from apps.accounts.password_utils import generate_secure_password
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
from apps.notifications.email_service import send_user_welcome_email
from apps.organization.models import CostCenter, Department, Organization


def _is_super_admin(user):
    return user.is_authenticated and (
        user.is_superuser
        or getattr(user, "role_code", None) == Role.SUPER_ADMIN
        or (hasattr(user, "role") and user.role and user.role.code == Role.SUPER_ADMIN)
    )


@login_required
def user_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    users = User.objects.select_related("role", "department", "cost_center").all().order_by("-date_joined")
    return render(request, "pages/dashboards/superadmin/users.html", {"users": users})


@login_required
def user_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = UserAdminForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.username = form.cleaned_data["email"]
                user.must_change_password = True

                raw_password = form.cleaned_data.get("password")
                if not raw_password:
                    raw_password = generate_secure_password()
                user.set_password(raw_password)
                user.save()

                from apps.audit.services import create_audit_log_service

                create_audit_log_service(
                    actor=request.user,
                    action=AuditLog.ACTION_CREATE,
                    target_model="User",
                    target_object_id=str(user.id),
                    previous_state=None,
                    new_state={
                        "email": user.email,
                        "phone_number": user.phone_number,
                        "role": user.role.code if user.role else None,
                        "department": user.department.code if user.department else None,
                        "cost_center": user.cost_center.code if user.cost_center else None,
                        "is_active": user.is_active,
                        "must_change_password": user.must_change_password,
                    },
                    ip_address=request.META.get("REMOTE_ADDR"),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )

            # Trigger welcome email strictly after database commit
            email_status, email_error = send_user_welcome_email(
                user=user,
                temporary_password=raw_password,
                request=request,
            )

            # User-facing notifications per Section 6 specification
            if email_status == "SENT":
                messages.success(request, "User account created successfully.")
                messages.info(request, f"Welcome email sent successfully to {user.email}.")
            elif email_status == "QUEUED":
                messages.success(request, "User created successfully. Welcome email queued for delivery.")
            else:
                messages.warning(
                    request,
                    "User created successfully, but the welcome email could not be delivered. Please retry.",
                )

            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = UserAdminForm()
    return render(request, "pages/dashboards/superadmin/user_form.html", {"form": form, "is_edit": False})


@login_required
def user_edit_view(request, user_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    user = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        old_state = {
            "email": user.email,
            "phone_number": user.phone_number,
            "role": user.role.code if user.role else None,
            "department": user.department.code if user.department else None,
            "cost_center": user.cost_center.code if user.cost_center else None,
            "is_active": user.is_active,
        }
        form = UserAdminForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save(commit=False)
            user.username = form.cleaned_data["email"]
            new_password = form.cleaned_data.get("password")
            if new_password:
                user.set_password(new_password)
            user.save()

            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=request.user,
                action=AuditLog.ACTION_UPDATE,
                target_model="User",
                target_object_id=str(user.id),
                previous_state=old_state,
                new_state={
                    "email": user.email,
                    "phone_number": user.phone_number,
                    "role": user.role.code if user.role else None,
                    "department": user.department.code if user.department else None,
                    "cost_center": user.cost_center.code if user.cost_center else None,
                    "is_active": user.is_active,
                },
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            messages.success(request, f"User '{user.email}' updated successfully.")
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = UserAdminForm(instance=user)
    return render(request, "pages/dashboards/superadmin/user_form.html", {"form": form, "is_edit": True, "user_obj": user})


@login_required
def user_resend_welcome_email_view(request, user_id):
    """
    Super Admin action to safely retry or resend the welcome email with a refreshed
    temporary password.
    """
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        user = get_object_or_404(User, pk=user_id)
        new_temp_pwd = generate_secure_password()
        user.set_password(new_temp_pwd)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])

        from apps.audit.services import create_audit_log_service

        create_audit_log_service(
            actor=request.user,
            action=AuditLog.ACTION_UPDATE,
            target_model="User",
            target_object_id=str(user.id),
            previous_state={"welcome_email_resend": False},
            new_state={"welcome_email_resend": True, "email": user.email},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )

        email_status, email_error = send_user_welcome_email(
            user=user,
            temporary_password=new_temp_pwd,
            request=request,
        )

        if email_status == "SENT":
            messages.success(request, f"Welcome email sent successfully to {user.email}.")
        elif email_status == "QUEUED":
            messages.info(request, "Welcome email queued for delivery.")
        else:
            messages.warning(
                request,
                f"Could not deliver welcome email to {user.email}. Please retry.",
            )

        return HttpResponse("<script>window.location.reload();</script>")
    return HttpResponse(status=405)


@login_required
def cost_center_options_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse("<option value=''>-- Unauthorized --</option>", status=403)
    dept_id = request.GET.get("department", "").strip()
    cost_centers = CostCenter.objects.none()
    if dept_id:
        try:
            import uuid
            uuid.UUID(dept_id)
            cost_centers = CostCenter.objects.filter(department_id=dept_id).order_by("name")
        except (ValueError, AttributeError):
            pass
    return render(
        request,
        "pages/dashboards/superadmin/cost_center_options.html",
        {"cost_centers": cost_centers},
    )


@login_required
def org_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
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
def org_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = OrganizationAdminForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = OrganizationAdminForm()
    return render(request, "pages/dashboards/superadmin/org_form.html", {"form": form, "is_edit": False})


@login_required
def org_edit_view(request, org_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    org = get_object_or_404(Organization, pk=org_id)
    if request.method == "POST":
        form = OrganizationAdminForm(request.POST, instance=org)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = OrganizationAdminForm(instance=org)
    return render(request, "pages/dashboards/superadmin/org_form.html", {"form": form, "is_edit": True, "org": org})


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
def budget_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    budgets = Budget.objects.select_related("cost_center", "fiscal_period").all()
    return render(request, "pages/dashboards/superadmin/budgets.html", {"budgets": budgets})


@login_required
def policy_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    policies = ApprovalPolicy.objects.select_related("department").all()
    return render(request, "pages/dashboards/superadmin/policies.html", {"policies": policies})


@login_required
def audit_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    logs_list = AuditLog.objects.select_related("actor").all().order_by("-timestamp")
    paginator = Paginator(logs_list, 10)  # Show 10 logs per page

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "pages/dashboards/superadmin/audit.html", {"page_obj": page_obj})


@login_required
def budget_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = BudgetAdminForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = BudgetAdminForm()
    return render(request, "pages/dashboards/superadmin/budget_form.html", {"form": form, "is_edit": False})


@login_required
def budget_edit_view(request, budget_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    budget = get_object_or_404(Budget, pk=budget_id)
    if request.method == "POST":
        form = BudgetAdminForm(request.POST, instance=budget)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = BudgetAdminForm(instance=budget)
    return render(request, "pages/dashboards/superadmin/budget_form.html", {"form": form, "is_edit": True, "budget": budget})


@login_required
def policy_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = PolicyAdminForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = PolicyAdminForm()
    return render(request, "pages/dashboards/superadmin/policy_form.html", {"form": form, "is_edit": False})


@login_required
def policy_edit_view(request, policy_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    policy = get_object_or_404(ApprovalPolicy, pk=policy_id)
    if request.method == "POST":
        form = PolicyAdminForm(request.POST, instance=policy)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = PolicyAdminForm(instance=policy)
    return render(request, "pages/dashboards/superadmin/policy_form.html", {"form": form, "is_edit": True, "policy": policy})


@login_required
def audit_export_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
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

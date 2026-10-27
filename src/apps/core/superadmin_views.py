import csv
from functools import wraps

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
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


@login_required
def superadmin_home_view(request):
    """Same Super Admin dashboard as the home page, served at /superadmin/."""
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    from apps.core.views import home_view

    return home_view(request)


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


def get_user_scoped_org(user):
    """
    Returns the organization to which this user is scoped.
    If the user has an effective_organization, they are strictly scoped to that organization.
    Returns None only for global platform superusers without an assigned organization.
    """
    if not user or not user.is_authenticated:
        return None
    effective_org = getattr(user, "effective_organization", None)
    if effective_org:
        return effective_org
    return None


@login_required
def user_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()

    users = User.objects.select_related("role", "department", "cost_center").all()
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        users = users.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org))

    if q:
        users = users.filter(
            Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(department__name__icontains=q)
            | Q(cost_center__code__icontains=q)
            | Q(role__name__icontains=q)
        )
    if role_filter:
        users = users.filter(role__code=role_filter)
    if status_filter == "active":
        users = users.filter(is_active=True)
    elif status_filter == "disabled":
        users = users.filter(is_active=False)

    users = users.order_by("-date_joined")
    roles = Role.objects.all().order_by("name")

    return render(
        request,
        "pages/dashboards/superadmin/users.html",
        {
            "users": users,
            "roles": roles,
            "q": q,
            "role_filter": role_filter,
            "status_filter": status_filter,
            "total_count": users.count(),
        },
    )


@login_required
def user_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = UserAdminForm(request.POST, request=request)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.username = form.cleaned_data["email"]
                user.must_change_password = True
                scoped_org = get_user_scoped_org(request.user)
                if scoped_org:
                    user.organization = scoped_org

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
        form = UserAdminForm(request=request)
    return render(request, "pages/dashboards/superadmin/user_form.html", {"form": form, "is_edit": False})


@login_required
def user_edit_view(request, user_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    user = get_object_or_404(User, pk=user_id)
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        user_org_id = user.organization_id or (user.department.organization_id if user.department else None)
        if user_org_id != scoped_org.id:
            return HttpResponse(
                "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify users from another organization.</div>",
                status=403,
            )

    if request.method == "POST":
        old_state = {
            "email": user.email,
            "phone_number": user.phone_number,
            "role": user.role.code if user.role else None,
            "department": user.department.code if user.department else None,
            "cost_center": user.cost_center.code if user.cost_center else None,
            "is_active": user.is_active,
        }
        form = UserAdminForm(request.POST, instance=user, request=request)
        if form.is_valid():
            user = form.save(commit=False)
            user.username = form.cleaned_data["email"]
            new_password = form.cleaned_data.get("password")
            if new_password and new_password.strip():
                raw_pwd = new_password.strip()
                user.set_password(raw_pwd)
                user.must_change_password = True
                user.save()
                email_status, email_error = send_user_welcome_email(
                    user=user,
                    temporary_password=raw_pwd,
                    request=request,
                )
                if email_status == "SENT":
                    messages.info(request, f"Updated credentials email successfully sent to {user.email}.")
                elif email_status == "QUEUED":
                    messages.info(request, f"Updated credentials email queued for delivery to {user.email}.")
                else:
                    messages.warning(request, f"Credentials updated, but email could not be delivered: {email_error}")
            else:
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
        form = UserAdminForm(instance=user, request=request)
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

    user = get_object_or_404(User, pk=user_id)
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        user_org_id = user.organization_id or (user.department.organization_id if user.department else None)
        if user_org_id != scoped_org.id:
            return HttpResponse(
                "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify users from another organization.</div>",
                status=403,
            )

    if request.method == "POST":
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
            cost_centers = CostCenter.objects.filter(department_id=dept_id)
            scoped_org = get_user_scoped_org(request.user)
            if scoped_org:
                cost_centers = cost_centers.filter(department__organization=scoped_org)
            cost_centers = cost_centers.order_by("name")
        except (ValueError, AttributeError):
            pass
    return render(
        request,
        "pages/dashboards/superadmin/cost_center_options.html",
        {"cost_centers": cost_centers},
    )

def org_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        orgs = Organization.objects.select_related("admin_user").filter(id=scoped_org.id)
        departments = Department.objects.select_related("organization").filter(organization=scoped_org).order_by("name")
        cost_centers = (
            CostCenter.objects.select_related("department", "manager")
            .filter(department__organization=scoped_org)
            .prefetch_related(
                Prefetch(
                    "requester_users",
                    queryset=User.objects.order_by("email"),
                )
            )
            .order_by("code")
        )
    else:
        orgs = Organization.objects.select_related("admin_user").all().order_by("name")
        departments = Department.objects.select_related("organization").all().order_by("name")
        cost_centers = (
            CostCenter.objects.select_related("department", "manager")
            .prefetch_related(
                Prefetch(
                    "requester_users",
                    queryset=User.objects.order_by("email"),
                )
            )
            .order_by("code")
        )
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
            "is_superuser": request.user.is_superuser and not scoped_org,
            "scoped_org": scoped_org,
        },
    )


@login_required
@super_admin_required
def org_create_view(request):
    scoped_org = get_user_scoped_org(request.user)
    if not _is_super_admin(request.user) or scoped_org is not None or not request.user.is_superuser:
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Only platform administrators without an assigned organization can register new legal entities.</div>",
            status=403,
        )

    if request.method == "POST":
        form = OrganizationAdminForm(request.POST)
        if form.is_valid():
            from apps.organization.services import create_organization_with_admin_service

            admin_email = form.cleaned_data.get("admin_email")
            admin_first_name = form.cleaned_data.get("admin_first_name", "")
            admin_last_name = form.cleaned_data.get("admin_last_name", "")
            admin_password = form.cleaned_data.get("admin_password")

            org, admin_user, raw_pwd = create_organization_with_admin_service(
                name=form.cleaned_data["name"],
                code=form.cleaned_data["code"],
                tax_identifier=form.cleaned_data.get("tax_identifier", ""),
                address=form.cleaned_data.get("address", ""),
                is_active=form.cleaned_data.get("is_active", True),
                admin_email=admin_email,
                admin_first_name=admin_first_name,
                admin_last_name=admin_last_name,
                admin_password=admin_password,
                send_welcome_email_flag=True,
                request=request,
                actor=request.user,
            )
            admin_info = f" with Administrator '{admin_user.email}'" if admin_user else ""
            messages.success(request, f"Organization '{org.name}' ({org.code}){admin_info} created successfully.")
            if admin_user:
                email_status = getattr(org, "_welcome_email_status", None)
                if email_status == "SENT":
                    messages.info(request, f"Login email and password successfully sent to {admin_user.email}.")
                elif email_status == "FAILED":
                    messages.warning(
                        request,
                        f"Administrator account created, but email could not be sent: {getattr(org, '_welcome_email_error', 'mail server error')}",
                    )
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = OrganizationAdminForm()
    return render(request, "pages/dashboards/superadmin/org_form.html", {"form": form, "is_edit": False})


@login_required
@super_admin_required
def org_edit_view(request, org_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    org = get_object_or_404(Organization.objects.select_related("admin_user"), pk=org_id)
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org and org.id != scoped_org.id:
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify other organizations.</div>",
            status=403,
        )

    if request.method == "POST":
        form = OrganizationAdminForm(request.POST, instance=org)
        if form.is_valid():
            org = form.save()
            admin_email = form.cleaned_data.get("admin_email")
            admin_password = form.cleaned_data.get("admin_password")
            target_user = None
            password_to_send = None
            if admin_email and (not org.admin_user or org.admin_user.email != admin_email.strip().lower()):
                norm_email = admin_email.strip().lower()
                role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
                user = User.objects.filter(email__iexact=norm_email).first()
                if not user:
                    user = User.objects.create_user(
                        email=norm_email,
                        username=norm_email,
                        first_name=form.cleaned_data.get("admin_first_name", ""),
                        last_name=form.cleaned_data.get("admin_last_name", ""),
                        role=role,
                        organization=org,
                    )
                    user.is_staff = True
                else:
                    user.organization = org
                    user.role = role
                    user.is_staff = True
                if admin_password and admin_password.strip():
                    password_to_send = admin_password.strip()
                    user.set_password(password_to_send)
                    user.must_change_password = True
                user.save()
                org.admin_user = user
                org.save(update_fields=["admin_user"])
                target_user = user
            elif admin_password and admin_password.strip() and org.admin_user:
                password_to_send = admin_password.strip()
                org.admin_user.set_password(password_to_send)
                org.admin_user.must_change_password = True
                org.admin_user.save(update_fields=["password", "must_change_password"])
                target_user = org.admin_user

            if target_user and password_to_send:
                email_status, email_error = send_user_welcome_email(
                    user=target_user,
                    temporary_password=password_to_send,
                    request=request,
                )
                if email_status == "SENT":
                    messages.info(request, f"Login email and password successfully sent to {target_user.email}.")
                elif email_status == "QUEUED":
                    messages.info(request, f"Credentials email queued for delivery to {target_user.email}.")
                else:
                    messages.warning(request, f"Credentials updated, but email could not be delivered: {email_error}")

            messages.success(request, f"Organization '{org.name}' updated successfully.")
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
        form = DepartmentAdminForm(request.POST, request=request)
        if form.is_valid():
            dept = form.save(commit=False)
            scoped_org = get_user_scoped_org(request.user)
            if scoped_org:
                dept.organization = scoped_org
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
        form = DepartmentAdminForm(request=request)
    return render(request, "pages/dashboards/superadmin/dept_form.html", {"form": form, "is_edit": False})


@login_required
def dept_edit_view(request, dept_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    dept = get_object_or_404(Department, pk=dept_id)
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org and dept.organization_id != scoped_org.id:
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify departments of other organizations.</div>",
            status=403,
        )

    if request.method == "POST":
        old_state = {
            "code": dept.code,
            "name": dept.name,
            "organization": dept.organization.code,
            "description": dept.description,
        }
        form = DepartmentAdminForm(request.POST, instance=dept, request=request)
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
        form = DepartmentAdminForm(instance=dept, request=request)
    return render(request, "pages/dashboards/superadmin/dept_form.html", {"form": form, "is_edit": True, "dept": dept})


@login_required
def cost_center_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = CostCenterAdminForm(request.POST, request=request)
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
        form = CostCenterAdminForm(request=request)
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
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org and cost_center.department.organization_id != scoped_org.id:
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify cost centers of other organizations.</div>",
            status=403,
        )

    if request.method == "POST":
        old_state = {
            "code": cost_center.code,
            "name": cost_center.name,
            "department": cost_center.department.code,
            "manager": cost_center.manager.email if cost_center.manager else None,
        }
        form = CostCenterAdminForm(request.POST, instance=cost_center, request=request)
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
        form = CostCenterAdminForm(instance=cost_center, request=request)
    return render(
        request,
        "pages/dashboards/superadmin/cost_center_form.html",
        {"form": form, "is_edit": True, "cost_center": cost_center},
    )


@login_required
@super_admin_required
def budget_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    budgets = Budget.objects.select_related("cost_center", "fiscal_period").all()
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        budgets = budgets.filter(cost_center__department__organization=scoped_org)
    return render(request, "pages/dashboards/superadmin/budgets.html", {"budgets": budgets})


@login_required
@super_admin_required
def policy_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    policies = ApprovalPolicy.objects.select_related("department").all()
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        policies = policies.filter(department__organization=scoped_org)
    return render(request, "pages/dashboards/superadmin/policies.html", {"policies": policies})


def _get_scoped_audit_logs(request):
    logs = AuditLog.objects.select_related("actor").all().order_by("-timestamp")
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org:
        dept_ids = [str(x) for x in Department.objects.filter(organization=scoped_org).values_list("id", flat=True)]
        cc_ids = [str(x) for x in CostCenter.objects.filter(department__organization=scoped_org).values_list("id", flat=True)]
        user_ids = [str(x) for x in User.objects.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org)).values_list("id", flat=True)]

        logs = logs.filter(
            Q(actor__organization=scoped_org)
            | Q(actor__department__organization=scoped_org)
            | Q(target_model="Organization", target_object_id=str(scoped_org.id))
            | Q(target_model="Department", target_object_id__in=dept_ids)
            | Q(target_model="CostCenter", target_object_id__in=cc_ids)
            | Q(target_model="User", target_object_id__in=user_ids)
        )
    return logs


@login_required
@super_admin_required
def audit_list_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    logs_list = _get_scoped_audit_logs(request)
    paginator = Paginator(logs_list, 10)  # Show 10 logs per page

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "pages/dashboards/superadmin/audit.html", {"page_obj": page_obj})


@login_required
@super_admin_required
def budget_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = BudgetAdminForm(request.POST, request=request)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = BudgetAdminForm(request=request)
    return render(request, "pages/dashboards/superadmin/budget_form.html", {"form": form, "is_edit": False})


@login_required
@super_admin_required
def budget_edit_view(request, budget_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    budget = get_object_or_404(Budget, pk=budget_id)
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org and budget.cost_center.department.organization_id != scoped_org.id:
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify budgets of other organizations.</div>",
            status=403,
        )

    if request.method == "POST":
        form = BudgetAdminForm(request.POST, instance=budget, request=request)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = BudgetAdminForm(instance=budget, request=request)
    return render(request, "pages/dashboards/superadmin/budget_form.html", {"form": form, "is_edit": True, "budget": budget})


@login_required
@super_admin_required
def policy_create_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    if request.method == "POST":
        form = PolicyAdminForm(request.POST, request=request)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = PolicyAdminForm(request=request)
    return render(request, "pages/dashboards/superadmin/policy_form.html", {"form": form, "is_edit": False})


@login_required
@super_admin_required
def policy_edit_view(request, policy_id):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )

    policy = get_object_or_404(ApprovalPolicy, pk=policy_id)
    scoped_org = get_user_scoped_org(request.user)
    if scoped_org and policy.department and policy.department.organization_id != scoped_org.id:
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: You cannot modify policies of other organizations.</div>",
            status=403,
        )

    if request.method == "POST":
        form = PolicyAdminForm(request.POST, instance=policy, request=request)
        if form.is_valid():
            form.save()
            return HttpResponse("<script>window.location.reload();</script>")
    else:
        form = PolicyAdminForm(instance=policy, request=request)
    return render(request, "pages/dashboards/superadmin/policy_form.html", {"form": form, "is_edit": True, "policy": policy})


@login_required
@super_admin_required
def audit_export_view(request):
    if not _is_super_admin(request.user):
        return HttpResponse(
            "<div class='alert alert-danger m-3'>Permission Denied: Super Admin access required.</div>",
            status=403,
        )
    format_type = request.GET.get("format", "csv")
    logs = _get_scoped_audit_logs(request)

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

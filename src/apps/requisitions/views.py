import os
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
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
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )
    if role_code not in [Role.REQUESTER, Role.SUPER_ADMIN]:
        messages.error(
            request,
            f"Permission Denied: Only Requesters hold permissions to create purchase requisitions (current role: {role_code}).",
        )
        if role_code == Role.PROC_MGR:
            return redirect("/manager/pipeline/")
        if role_code == Role.AUDITOR:
            return redirect("/audit/dashboard/")
        return redirect("requisitions_list")

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        justification = (request.POST.get("justification") or "").strip()
        department_id = request.POST.get("department")
        cost_center_id = request.POST.get("cost_center")
        requested_delivery_date = request.POST.get("requested_delivery_date")
        attachments = request.FILES.getlist("attachments")
        action_type = request.POST.get("action_type", "save_draft")

        if not (
            title and justification and department_id and cost_center_id and requested_delivery_date
        ):
            messages.error(
                request, "Please fill in all required fields including business justification."
            )
            return redirect("requisition_create")

        if requested_delivery_date:
            try:
                from datetime import datetime

                deliv_date = datetime.strptime(
                    str(requested_delivery_date).strip(), "%Y-%m-%d"
                ).date()
                if deliv_date < timezone.now().date():
                    messages.error(request, "Required delivery date cannot be in the past.")
            except ValueError:
                messages.error(request, "Invalid delivery date format.")
                return redirect("requisition_create")

        if attachments:
            for att in attachments:
                try:
                    validate_pr_attachment(att)
                except ValidationError:
                    messages.error(request, "Invalid file format.")
                    return redirect("requisition_create")

        try:

            department = Department.objects.get(id=department_id)
            cost_center = CostCenter.objects.get(id=cost_center_id)

            item_descriptions = request.POST.getlist("item_description[]")
            quantities = request.POST.getlist("quantity[]")
            unit_prices = request.POST.getlist("estimated_unit_price[]")
            uoms = request.POST.getlist("unit_of_measure[]")

            line_items = []
            for i in range(len(item_descriptions)):
                desc = item_descriptions[i].strip()
                if not desc:
                    continue
                try:
                    qty_num = Decimal(str(quantities[i]))
                    if qty_num <= Decimal("0"):
                        messages.error(
                            request, f"Quantity for line item '{desc}' must be greater than zero."
                        )
                        return redirect("requisition_create")
                    if qty_num % 1 != 0:
                        messages.error(
                            request,
                            f"Quantity for line item '{desc}' must be a whole number (no decimal points allowed).",
                        )
                        return redirect("requisition_create")
                except Exception:
                    messages.error(request, f"Invalid quantity for line item '{desc}'.")
                    return redirect("requisition_create")

                line_items.append(
                    {
                        "item_description": desc,
                        "quantity": quantities[i],
                        "estimated_unit_price": unit_prices[i],
                        "unit_of_measure": uoms[i] if i < len(uoms) else "EA",
                    }
                )

            if not line_items:
                messages.error(request, "Please add at least one line item.")
                return redirect("requisition_create")

            # Prevent duplicate draft saves from repeated clicks or slow network on Render
            from django.core.cache import cache

            lock_key = f"pr_create_lock_{user.id}_{hash((title, department_id, cost_center_id, requested_delivery_date))}"
            if not cache.add(lock_key, True, timeout=15):
                recent_duplicate = (
                    PurchaseRequisition.objects.filter(
                        requester=user,
                        title=title,
                        department=department,
                        cost_center=cost_center,
                        created_at__gte=timezone.now() - timezone.timedelta(seconds=30),
                    )
                    .order_by("-created_at")
                    .first()
                )
                if recent_duplicate:
                    messages.info(
                        request,
                        f"Purchase Requisition {recent_duplicate.pr_number} was already created.",
                    )
                    return redirect("requisition_detail", pk=recent_duplicate.pk)
                else:
                    messages.info(
                        request,
                        "Your requisition request is already being processed. Please wait...",
                    )
                    return redirect("requisitions_list")

            recent_duplicate = (
                PurchaseRequisition.objects.filter(
                    requester=user,
                    title=title,
                    department=department,
                    cost_center=cost_center,
                    created_at__gte=timezone.now() - timezone.timedelta(seconds=15),
                )
                .order_by("-created_at")
                .first()
            )
            if recent_duplicate:
                messages.info(
                    request,
                    f"Purchase Requisition {recent_duplicate.pr_number} was already created.",
                )
                return redirect("requisition_detail", pk=recent_duplicate.pk)

            pr = create_purchase_requisition_service(
                title=title,
                justification=justification,
                requester=user,
                department=department,
                cost_center=cost_center,
                requested_delivery_date=requested_delivery_date,
                line_items=line_items,
                attachments=attachments,
            )

            if action_type == "submit" or "submit" in request.POST:
                submit_purchase_requisition_service(requisition=pr, user=user)
                if pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW:
                    messages.warning(
                        request,
                        f"Budget Alert: Requisition {pr.pr_number} exceeds available budget for Cost Center '{cost_center.code}' and has been routed for Budget Exception Review.",
                    )
                else:
                    messages.success(
                        request,
                        f"Purchase Requisition {pr.pr_number} created and submitted for approval successfully!",
                    )
            else:
                messages.success(
                    request, f"Purchase Requisition {pr.pr_number} created in Draft status."
                )

            return redirect("requisition_detail", pk=pr.pk)
        except Exception as e:
            messages.error(request, f"Error creating requisition: {str(e)}")
            return redirect("requisition_create")

    # GET Request
    if getattr(user, "department", None):
        departments = Department.objects.filter(id=user.department.id)
        cost_centers = CostCenter.objects.filter(department=user.department)
    else:
        departments = Department.objects.all()
        cost_centers = CostCenter.objects.all()

    cost_centers = _attach_available_budgets(cost_centers)

    return render(
        request,
        "pages/requisitions/create.html",
        {
            "departments": departments,
            "cost_centers": cost_centers,
            "today": timezone.now().date().isoformat(),
        },
    )


def _attach_available_budgets(cost_centers):
    from django.utils import timezone

    today = timezone.now().date()

    budgets = Budget.objects.filter(
        cost_center__in=cost_centers,
        fiscal_period__start_date__lte=today,
        fiscal_period__end_date__gte=today,
        fiscal_period__is_closed=False,
    )
    budget_map = {b.cost_center_id: b.available_amount for b in budgets}
    cc_list = []
    for cc in cost_centers:
        cc.available_budget = budget_map.get(cc.id, None)
        cc_list.append(cc)
    return cc_list


@login_required(login_url="/login/")
def edit_view(request, pk):
    pr = get_object_or_404(PurchaseRequisition, pk=pk)
    if pr.requester != request.user and not request.user.is_superuser:
        messages.error(request, "You do not have permission to edit this requisition.")
        return redirect("requisitions_list")

    if pr.status != PurchaseRequisition.STATUS_DRAFT:
        messages.error(request, "Only draft requisitions can be edited.")
        return redirect("requisition_detail", pk=pr.pk)

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        justification = (request.POST.get("justification") or "").strip()
        department_id = request.POST.get("department")
        cost_center_id = request.POST.get("cost_center")
        requested_delivery_date = request.POST.get("requested_delivery_date")
        attachments = request.FILES.getlist("attachments")

        try:
            department = (
                Department.objects.get(id=department_id) if department_id else pr.department
            )
            cost_center = (
                CostCenter.objects.get(id=cost_center_id) if cost_center_id else pr.cost_center
            )

            # Update basic fields
            if title:
                pr.title = title
            if justification:
                pr.justification = justification
            pr.department = department
            pr.cost_center = cost_center
            if requested_delivery_date:
                try:
                    from datetime import datetime

                    deliv_date = datetime.strptime(
                        str(requested_delivery_date).strip(), "%Y-%m-%d"
                    ).date()
                    if deliv_date < timezone.now().date():
                        messages.error(request, "Required delivery date cannot be in the past.")
                        return redirect("requisition_edit", pk=pr.pk)
                except ValueError:
                    messages.error(request, "Invalid delivery date format.")
                    return redirect("requisition_edit", pk=pr.pk)
                pr.requested_delivery_date = requested_delivery_date

            if attachments:
                for att in attachments:
                    try:
                        validate_pr_attachment(att)
                    except ValidationError:
                        messages.error(request, "Invalid file format.")
                        return redirect("requisition_edit", pk=pr.pk)

            # Extract Line Items

            item_descriptions = request.POST.getlist("item_description[]")
            quantities = request.POST.getlist("quantity[]")
            unit_prices = request.POST.getlist("estimated_unit_price[]")
            uoms = request.POST.getlist("unit_of_measure[]")

            with transaction.atomic():
                if item_descriptions:
                    # Delete existing lines and recreate if new line items provided
                    pr.lines.all().delete()

                    total_amount = Decimal("0.00")
                    for i in range(len(item_descriptions)):
                        desc = item_descriptions[i].strip()
                        if not desc:
                            continue

                        try:
                            qty_val = Decimal(str(quantities[i]))
                            if qty_val <= Decimal("0"):
                                messages.error(
                                    request,
                                    f"Quantity for line item '{desc}' must be greater than zero.",
                                )
                                return redirect("requisition_edit", pk=pr.pk)
                            if qty_val % 1 != 0:
                                messages.error(
                                    request,
                                    f"Quantity for line item '{desc}' must be a whole number (no decimal points allowed).",
                                )
                                return redirect("requisition_edit", pk=pr.pk)
                        except Exception:
                            messages.error(request, f"Invalid quantity for line item '{desc}'.")
                            return redirect("requisition_edit", pk=pr.pk)

                        price = Decimal(str(unit_prices[i]))
                        uom = uoms[i] if i < len(uoms) else "EA"

                        PRLine.objects.create(
                            requisition=pr,
                            item_description=desc,
                            quantity=qty_val,
                            unit_of_measure=uom,
                            estimated_unit_price=price,
                        )
                        total_amount += qty_val * price

                    # Validate budget before saving PR total
                    validate_budget_availability_service(
                        cost_center=cost_center, amount=Decimal(str(total_amount))
                    )

                    pr.total_amount = total_amount

                pr.save()

                if attachments:
                    for uploaded_file in attachments:
                        PRAttachment.objects.create(
                            requisition=pr,
                            uploaded_by=request.user,
                            title=uploaded_file.name,
                            file=uploaded_file,
                        )

            messages.success(request, f"Requisition {pr.pr_number} updated successfully.")
            return redirect("requisition_detail", pk=pr.pk)
        except Exception as e:
            messages.error(request, f"Error updating requisition: {str(e)}")

    # GET Request
    if getattr(request.user, "department", None):
        departments = Department.objects.filter(id=request.user.department.id)
        cost_centers = CostCenter.objects.filter(department=request.user.department)
    else:
        departments = Department.objects.all()
        cost_centers = CostCenter.objects.all()

    cost_centers = _attach_available_budgets(cost_centers)

    return render(
        request,
        "pages/requisitions/edit.html",
        {
            "pr": pr,
            "departments": departments,
            "cost_centers": cost_centers,
            "today": timezone.now().date().isoformat(),
        },
    )


@login_required(login_url="/login/")
def detail_view(request, pk):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )
    pr = get_object_or_404(
        PurchaseRequisition.objects.select_related(
            "requester", "department", "cost_center"
        ).prefetch_related("lines", "attachments", "purchase_orders"),
        pk=pk,
    )

    if role_code == Role.VENDOR_USER and not user.is_superuser:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("Vendors are not permitted to view internal Purchase Requisitions.")

    if role_code == Role.REQUESTER and pr.requester != user:
        messages.error(request, "You do not have permission to view this requisition.")
        return redirect("requisitions_list")

    if not user.is_superuser and role_code not in [
        Role.FINANCE_AP,
        Role.AUDITOR,
        Role.PROC_MGR,
        Role.PROC_EXEC,
    ]:
        user_dept = getattr(user, "department", None)
        user_org = getattr(user_dept, "organization", None) if user_dept else None
        pr_org = getattr(getattr(pr, "department", None), "organization", None)
        if user_org and pr_org and user_org.id != pr_org.id:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied(
                "You do not have permission to view requisitions from another organization."
            )

    approval_history = (
        ApprovalAction.objects.filter(
            target_object_id=str(pr.id),
            target_model_name="PurchaseRequisition",
        )
        .select_related("actor")
        .order_by("-created_at")
    )

    from django.utils import timezone

    today = timezone.now().date()
    active_budget = Budget.objects.filter(
        cost_center=pr.cost_center,
        fiscal_period__start_date__lte=today,
        fiscal_period__end_date__gte=today,
        fiscal_period__is_closed=False,
    ).first()
    available_budget = active_budget.available_amount if active_budget else None
    is_budget_exceeded = bool(
        pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW
        or (available_budget is not None and pr.total_amount > available_budget)
    )

    base_layout = (
        "manager/base_manager.html"
        if role_code == Role.PROC_MGR
        else (
            "audit/base_auditor.html"
            if role_code == Role.AUDITOR
            else (
                "layouts/legal_base.html"
                if role_code == Role.LEGAL_MGR
                else (
                    "layouts/approver_base.html"
                    if role_code in [Role.DEPT_APPROVER, Role.PROC_EXEC]
                    else (
                        "layouts/finance_base.html"
                        if role_code == Role.FINANCE_AP
                        else "layouts/requester_base.html"
                    )
                )
            )
        )
    )

    return render(
        request,
        "pages/requisitions/detail.html",
        {
            "pr": pr,
            "role_code": role_code,
            "approval_history": approval_history,
            "available_budget": available_budget,
            "is_budget_exceeded": is_budget_exceeded,
            "base_layout": base_layout,
        },
    )


@login_required(login_url="/login/")
def submit_view(request, pk):
    if request.method != "POST":
        return redirect("requisitions_list")

    pr = get_object_or_404(PurchaseRequisition, pk=pk)

    # Check permissions
    if pr.requester != request.user and not request.user.is_superuser:
        messages.error(request, "You do not have permission to submit this requisition.")
        return redirect("requisition_detail", pk=pr.pk)

    if pr.status != PurchaseRequisition.STATUS_DRAFT:
        messages.error(request, "Only draft requisitions can be submitted.")
        return redirect("requisition_detail", pk=pr.pk)

    try:
        submit_purchase_requisition_service(requisition=pr, user=request.user)
        if pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW:
            messages.warning(
                request,
                f"Budget Alert: Requisition {pr.pr_number} exceeds available budget for Cost Center '{pr.cost_center.code}' and has been routed for Budget Exception Review.",
            )
        else:
            messages.success(
                request, f"Requisition {pr.pr_number} submitted for approval successfully."
            )
    except Exception as e:
        messages.error(request, f"Error submitting requisition: {str(e)}")

    return redirect("requisition_detail", pk=pr.pk)


@login_required(login_url="/login/")
def approve_view(request, pk):
    pr = get_object_or_404(PurchaseRequisition, pk=pk)
    if request.method == "POST":
        comments = request.POST.get("comments", "").strip()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
        )

        if pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW and role_code not in [
            Role.FINANCE_AP,
            Role.SUPER_ADMIN,
        ]:
            messages.error(
                request,
                "Permission Denied: Only Finance/AP can approve a budget overrun exception.",
            )
            return redirect("requisition_detail", pk=pr.pk)
        elif pr.status in [
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
            PurchaseRequisition.STATUS_SUBMITTED,
        ] and role_code not in [Role.DEPT_APPROVER, Role.PROC_MGR, Role.SUPER_ADMIN]:
            messages.error(
                request,
                "Permission Denied: You do not have permission to approve this requisition.",
            )
            return redirect("requisition_detail", pk=pr.pk)

        try:
            process_approval_action_service(
                target_object=pr, actor=request.user, action="APPROVED", comments=comments
            )
            messages.success(request, f"Requisition {pr.pr_number} approved successfully!")
        except Exception as e:
            messages.error(request, f"Approval error: {str(e)}")
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    return redirect("requisition_detail", pk=pr.pk)


@login_required(login_url="/login/")
def reject_view(request, pk):
    pr = get_object_or_404(PurchaseRequisition, pk=pk)
    if request.method == "POST":
        comments = request.POST.get("comments", "").strip() or "Rejected by Approver."
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
        )

        if pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW and role_code not in [
            Role.FINANCE_AP,
            Role.SUPER_ADMIN,
        ]:
            messages.error(
                request, "Permission Denied: Only Finance/AP can reject a budget overrun exception."
            )
            return redirect("requisition_detail", pk=pr.pk)
        elif pr.status in [
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
            PurchaseRequisition.STATUS_SUBMITTED,
        ] and role_code not in [Role.DEPT_APPROVER, Role.PROC_MGR, Role.SUPER_ADMIN]:
            messages.error(
                request, "Permission Denied: You do not have permission to reject this requisition."
            )
            return redirect("requisition_detail", pk=pr.pk)

        try:
            process_approval_action_service(
                target_object=pr, actor=request.user, action="REJECTED", comments=comments
            )
            messages.success(request, f"Requisition {pr.pr_number} rejected.")
        except Exception as e:
            messages.error(request, f"Rejection error: {str(e)}")
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    return redirect("requisition_detail", pk=pr.pk)


@login_required(login_url="/login/")
def cancel_view(request, pk):
    if request.method == "POST":
        pr = get_object_or_404(PurchaseRequisition, pk=pk)

        if pr.requester != request.user and not request.user.is_superuser:
            messages.error(request, "You do not have permission to cancel this requisition.")
            return redirect("requisition_detail", pk=pr.pk)

        allowed_cancel_statuses = [
            PurchaseRequisition.STATUS_DRAFT,
            PurchaseRequisition.STATUS_SUBMITTED,
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
            PurchaseRequisition.STATUS_BUDGET_REVIEW,
        ]

        if pr.status not in allowed_cancel_statuses:
            messages.error(
                request, "This requisition has progressed too far to be cancelled by the requester."
            )
            return redirect("requisition_detail", pk=pr.pk)

        try:
            with transaction.atomic():
                prev_status = pr.status
                pr.status = PurchaseRequisition.STATUS_CANCELLED
                pr.save(update_fields=["status", "updated_at"])

                from apps.audit.models import AuditLog

                AuditLog.objects.create(
                    actor=request.user,
                    action=AuditLog.ACTION_UPDATE,
                    target_model="PurchaseRequisition",
                    target_object_id=str(pr.pk),
                    previous_state={"status": prev_status},
                    new_state={"status": PurchaseRequisition.STATUS_CANCELLED},
                    ip_address=request.META.get("REMOTE_ADDR", ""),
                )

            messages.success(request, f"Requisition {pr.pr_number} has been cancelled.")
        except Exception as e:
            messages.error(request, f"Error cancelling requisition: {str(e)}")

        return redirect("requisition_detail", pk=pr.pk)

    return redirect("requisitions_list")


@login_required(login_url="/login/")
def delete_view(request, pk):
    pr = get_object_or_404(PurchaseRequisition, pk=pk)
    if pr.status == PurchaseRequisition.STATUS_DRAFT and (
        pr.requester == request.user or request.user.is_superuser
    ):
        pr.delete()
        messages.success(request, "Draft requisition deleted successfully.")
        return redirect("requisitions_list")
    messages.error(request, "Cannot delete requisition.")
    return redirect("requisition_detail", pk=pr.pk)


@login_required(login_url="/login/")
def attachment_download_view(request, pk, attachment_id):
    """
    Securely serves / downloads a PR supporting document attachment with RBAC,
    department/organization scoping, and audit logging.
    """
    import mimetypes

    from django.core.exceptions import PermissionDenied

    from apps.audit.models import AuditLog

    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )

    pr = get_object_or_404(PurchaseRequisition, pk=pk)

    # Scoping check
    if role_code == Role.REQUESTER and pr.requester != user:
        raise PermissionDenied(
            "You do not have permission to access attachments for this requisition."
        )

    if not user.is_superuser and role_code not in [
        Role.FINANCE_AP,
        Role.AUDITOR,
        Role.PROC_MGR,
        Role.PROC_EXEC,
        Role.SUPER_ADMIN,
    ]:
        user_dept = getattr(user, "department", None)
        user_org = getattr(user_dept, "organization", None) if user_dept else None
        pr_org = getattr(getattr(pr, "department", None), "organization", None)
        if user_org and pr_org and user_org.id != pr_org.id:
            raise PermissionDenied(
                "You do not have permission to view requisitions from another organization."
            )
        if (
            role_code == Role.DEPT_APPROVER
            and user_dept
            and pr.department
            and user_dept.id != pr.department.id
        ):
            raise PermissionDenied(
                "You do not have permission to access attachments for another department's requisition."
            )

    attachment = get_object_or_404(PRAttachment, pk=attachment_id, requisition=pr)

    file_path = None
    if (
        attachment.file
        and hasattr(attachment.file, "path")
        and os.path.exists(attachment.file.path)
    ):
        file_path = attachment.file.path
    else:
        try:
            from .utils import ensure_attachment_file_exists

            file_path = ensure_attachment_file_exists(attachment)
        except Exception:
            file_path = None

    if not file_path or not os.path.exists(file_path):
        messages.error(request, "Requested supporting document file was not found on storage.")
        return redirect("requisition_detail", pk=pr.pk)

    action = request.GET.get("action", "download")
    as_attachment = action != "view"

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_EXPORT,
        target_model="PRAttachment",
        target_object_id=str(attachment.id),
        new_state={
            "pr_number": pr.pr_number,
            "attachment_title": attachment.title,
            "file_name": os.path.basename(file_path),
            "access_type": action,
        },
    )

    content_type, _ = mimetypes.guess_type(attachment.title or file_path)
    if not content_type:
        content_type = "application/octet-stream"

    filename = attachment.title or os.path.basename(file_path)
    response = FileResponse(
        open(file_path, "rb"),
        as_attachment=as_attachment,
        filename=filename if as_attachment else None,
        content_type=content_type,
    )
    if not as_attachment:
        response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response

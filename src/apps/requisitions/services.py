from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction, ApprovalPolicy
from apps.approvals.services import evaluate_approval_chain, record_approval_action_service
from apps.audit.models import AuditLog
from apps.budgets.services import (
    BudgetOverrunException,
    check_and_reserve_budget_service,
    release_budget_reservation_service,
)
from apps.core.validators import validate_pr_attachment
from apps.organization.models import CostCenter, Department

from .models import PRAttachment, PRLine, PurchaseRequisition


@transaction.atomic
def create_purchase_requisition_service(
    *,
    title: str,
    justification: str,
    requester: User,
    department: Department,
    cost_center: CostCenter,
    requested_delivery_date,
    line_items: list,
    attachments: list = None,
) -> PurchaseRequisition:
    """
    Creates a new PurchaseRequisition in DRAFT status with atomic document number generation.
    """
    if not line_items:
        raise ValidationError("Purchase Requisition must contain at least one line item.")

    if getattr(requester, "cost_center", None) and requester.cost_center != cost_center:
        raise ValidationError(
            f"Requester '{requester.email}' is only authorized to create requisitions for Cost Center '{requester.cost_center.code}'."
        )

    if requested_delivery_date:
        from datetime import date, datetime

        if isinstance(requested_delivery_date, str):
            try:
                deliv_obj = datetime.strptime(
                    str(requested_delivery_date).split("T")[0], "%Y-%m-%d"
                ).date()
            except ValueError:
                deliv_obj = None
        elif isinstance(requested_delivery_date, (datetime, date)):
            deliv_obj = (
                requested_delivery_date
                if isinstance(requested_delivery_date, date)
                else requested_delivery_date.date()
            )
        else:
            deliv_obj = None

        if deliv_obj and deliv_obj < timezone.now().date():
            raise ValidationError("Requested delivery date cannot be in the past.")

    from apps.core.services import generate_document_number

    pr_number = generate_document_number(PurchaseRequisition, "pr_number", "PR")

    pr = PurchaseRequisition.objects.create(
        pr_number=pr_number,
        title=title,
        justification=justification,
        requester=requester,
        department=department,
        cost_center=cost_center,
        status=PurchaseRequisition.STATUS_DRAFT,
        requested_delivery_date=requested_delivery_date,
        total_amount=Decimal("0.00"),
    )

    total = Decimal("0.00")
    for item in line_items:
        qty = Decimal(str(item["quantity"]))
        unit_price = Decimal(str(item["estimated_unit_price"]))
        if qty <= Decimal("0.00"):
            raise ValidationError(
                f"Quantity for line item '{item.get('item_description', '')}' must be greater than zero."
            )
        if qty % 1 != 0:
            raise ValidationError(
                f"Quantity for line item '{item.get('item_description', '')}' must be a whole number (no decimal points allowed)."
            )
        if unit_price < Decimal("0.00"):
            raise ValidationError(
                f"Estimated unit price for line item '{item.get('item_description', '')}' cannot be negative."
            )
        line = PRLine.objects.create(
            requisition=pr,
            item_description=item["item_description"],
            quantity=qty,
            unit_of_measure=item.get("unit_of_measure", "EA"),
            estimated_unit_price=unit_price,
            specifications=item.get("specifications", ""),
        )
        total += line.estimated_total

    pr.total_amount = total

    # We don't block Drafts from being created if over budget.
    # The actual budget reservation and BudgetOverrunException routing happens when the PR is submitted.

    pr.save(update_fields=["total_amount", "updated_at"])

    if attachments:
        for uploaded_file in attachments:
            validate_pr_attachment(uploaded_file)
            PRAttachment.objects.create(
                requisition=pr,
                uploaded_by=requester,
                title=uploaded_file.name,
                file=uploaded_file,
            )

    AuditLog.objects.create(
        actor=requester,
        action=AuditLog.ACTION_CREATE,
        target_model="PurchaseRequisition",
        target_object_id=str(pr.id),
        new_state={
            "pr_number": pr.pr_number,
            "total_amount": str(pr.total_amount),
            "status": pr.status,
        },
    )

    return pr


@transaction.atomic
def submit_purchase_requisition_service(
    *, requisition: PurchaseRequisition, user: User
) -> PurchaseRequisition:
    """
    Submits a PR for approval:
    1. Evaluates server-side approval chain.
    2. Runs pre-approval budget check & locks reservation.
    3. Transitions PR status to SUBMITTED / MANAGER_REVIEW.
    """
    if requisition.status != PurchaseRequisition.STATUS_DRAFT:
        raise ValidationError(
            f"Cannot submit requisition in status '{requisition.status}'. Must be DRAFT."
        )

    if requisition.total_amount <= Decimal("0.00"):
        raise ValidationError("Requisition total amount must be greater than zero.")

    # Evaluate approval policy (PR-08 fail-closed on missing policy)
    chain = evaluate_approval_chain(
        module=ApprovalPolicy.MODULE_PR,
        amount=requisition.total_amount,
        department=requisition.department,
    )
    if not chain:
        dept_name = requisition.department.name if requisition.department else "Global"
        raise ValidationError(
            f"No active approval policy found matching requisition amount ${requisition.total_amount:,.2f} "
            f"and department '{dept_name}'. Submission rejected under fail-closed routing rule."
        )

    try:
        # Execute budget reservation check
        check_and_reserve_budget_service(requisition=requisition, requested_by_user=user)
        requisition.status = PurchaseRequisition.STATUS_MANAGER_REVIEW
    except BudgetOverrunException:
        # Route to budget exception review
        requisition.status = PurchaseRequisition.STATUS_BUDGET_REVIEW

    previous_status = PurchaseRequisition.STATUS_DRAFT
    requisition.save(update_fields=["status", "updated_at"])

    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=user,
        action=ApprovalAction.ACTION_SUBMIT,
        previous_state=previous_status,
        new_state=requisition.status,
        comments="PR submitted for approval workflow.",
    )

    # Notify Department Approvers via Notification model
    from apps.accounts.models import Role, User
    from apps.notifications.models import Notification

    approvers = User.objects.filter(role__code=Role.DEPT_APPROVER)
    if requisition.department:
        dept_approvers = approvers.filter(department=requisition.department)
        if dept_approvers.exists():
            approvers = dept_approvers

    if requisition.status == PurchaseRequisition.STATUS_BUDGET_REVIEW:
        finance_users = User.objects.filter(role__code=Role.FINANCE_AP)
        for fin_user in finance_users:
            Notification.objects.create(
                recipient=fin_user,
                notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                title=f"Budget Alert: PR {requisition.pr_number} Exceeds Budget",
                message=f"Requisition {requisition.pr_number} (${requisition.total_amount:,.2f}) exceeds available budget for Cost Center {requisition.cost_center.code} and requires budget exception approval.",
                target_url=f"/requisitions/{requisition.id}/",
            )
        for approver in approvers:
            Notification.objects.create(
                recipient=approver,
                notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                title=f"Budget Alert: PR {requisition.pr_number} Exceeds Budget",
                message=f"Requisition {requisition.pr_number} (${requisition.total_amount:,.2f}) exceeds available budget for Cost Center {requisition.cost_center.code} and is queued for budget exception review.",
                target_url=f"/requisitions/{requisition.id}/",
            )
    else:
        for approver in approvers:
            Notification.objects.create(
                recipient=approver,
                notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                title=f"New PR Approval Request: {requisition.pr_number}",
                message=f"Requisition {requisition.pr_number} ({requisition.title}) amounting to ${requisition.total_amount:,.2f} requires your department approval review.",
                target_url=f"/requisitions/{requisition.id}/",
            )

    return requisition


@transaction.atomic
def approve_purchase_requisition_service(
    *, requisition: PurchaseRequisition, approver: User, comments: str = ""
) -> PurchaseRequisition:
    """
    Approves a PR through its multi-step approval policy chain.
    Advances the step sequence or completes with APPROVED on final step (SMK-PR-050).
    Enforces that the approver is authorized for the current step (SMK-AP-005).
    """
    allowed_statuses = [
        PurchaseRequisition.STATUS_SUBMITTED,
        PurchaseRequisition.STATUS_MANAGER_REVIEW,
        PurchaseRequisition.STATUS_BUDGET_REVIEW,
    ]
    if requisition.status not in allowed_statuses:
        raise ValidationError(f"Cannot approve requisition in status '{requisition.status}'.")

    if requisition.requester_id == approver.id and not approver.is_superuser:
        raise ValidationError(
            "Self-approval is strictly prohibited: Requesters cannot approve their own Purchase Requisitions."
        )

    from apps.accounts.models import Role, User
    from apps.notifications.models import Notification

    # Evaluate approval chain for this requisition
    chain = evaluate_approval_chain(
        module=ApprovalPolicy.MODULE_PR,
        amount=requisition.total_amount,
        department=requisition.department,
    )

    # Determine completed approval steps
    existing_approvals = ApprovalAction.objects.filter(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        action=ApprovalAction.ACTION_APPROVE,
    ).order_by("created_at")
    current_step_index = existing_approvals.count()

    current_policy_step = None
    if chain:
        if current_step_index >= len(chain):
            raise ValidationError("All approval steps for this requisition have already been completed.")

        step_info = chain[current_step_index]
        current_policy_step = step_info["policy_step"]
        required_role = step_info["approver_role"]
        specific_approver = step_info["specific_approver"]
        effective_approver = step_info["effective_approver"]

        # Validate approver authorization for this step (SMK-AP-005)
        is_authorized = False
        if approver.is_superuser:
            is_authorized = True
        elif specific_approver:
            if approver == effective_approver or approver == specific_approver:
                is_authorized = True
        else:
            approver_role_code = getattr(approver.role, "code", None)
            if (getattr(approver, "role_id", None) == required_role.id) or (approver_role_code == required_role.code):
                if required_role.code == Role.DEPT_APPROVER and requisition.department and approver.department:
                    if approver.department == requisition.department:
                        is_authorized = True
                    else:
                        raise ValidationError("Department Approver can only approve requisitions for their own department.")
                else:
                    is_authorized = True

        if not is_authorized:
            raise ValidationError(
                f"User '{approver.email}' is not authorized to approve step {step_info['step_number']}. "
                f"Required role: '{required_role.name}'."
            )

    previous_status = requisition.status

    if requisition.status == PurchaseRequisition.STATUS_BUDGET_REVIEW:
        # Finance is approving an overrun explicitly, force the reservation
        check_and_reserve_budget_service(
            requisition=requisition, requested_by_user=approver, force_overrun=True
        )

    # Determine next status based on multi-step chain (SMK-PR-050)
    is_final_step = True
    if chain and (current_step_index + 1 < len(chain)):
        is_final_step = False

    if is_final_step:
        requisition.status = PurchaseRequisition.STATUS_APPROVED
    else:
        requisition.status = PurchaseRequisition.STATUS_MANAGER_REVIEW

    requisition.save(update_fields=["status", "updated_at"])

    step_label = f"Step {chain[current_step_index]['step_number']}" if chain else "Step 1"
    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_APPROVE,
        previous_state=previous_status,
        new_state=requisition.status,
        policy_step=current_policy_step,
        comments=comments or f"{step_label} approved.",
    )

    if requisition.status == PurchaseRequisition.STATUS_APPROVED:
        try:
            proc_execs = User.objects.filter(role__code=Role.PROC_EXEC, is_active=True)
            for pe in proc_execs:
                Notification.objects.create(
                    recipient=pe,
                    notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                    title="Approved PR Ready for Sourcing",
                    message=f"Requisition {requisition.pr_number} has been approved by {approver.email} and is ready on your Sourcing Intake.",
                    target_url=f"/requisitions/{requisition.id}/",
                )
        except Exception:
            pass
    elif chain and not is_final_step:
        try:
            next_step = chain[current_step_index + 1]
            next_role = next_step["approver_role"]
            if next_step["effective_approver"]:
                target_users = [next_step["effective_approver"]]
            else:
                target_users = list(User.objects.filter(role=next_role, is_active=True))
                if next_role.code == Role.DEPT_APPROVER and requisition.department:
                    dept_users = [u for u in target_users if u.department == requisition.department]
                    if dept_users:
                        target_users = dept_users

            for u in target_users:
                Notification.objects.create(
                    recipient=u,
                    notification_type=Notification.TYPE_APPROVAL_REQUIRED,
                    title=f"Approval Required: PR {requisition.pr_number} (Step {next_step['step_number']})",
                    message=f"Requisition {requisition.pr_number} requires your approval for Step {next_step['step_number']} ({next_role.name}).",
                    target_url=f"/requisitions/{requisition.id}/",
                )
        except Exception:
            pass

    return requisition


@transaction.atomic
def reject_purchase_requisition_service(
    *, requisition: PurchaseRequisition, approver: User, comments: str
) -> PurchaseRequisition:
    allowed_statuses = [
        PurchaseRequisition.STATUS_SUBMITTED,
        PurchaseRequisition.STATUS_MANAGER_REVIEW,
        PurchaseRequisition.STATUS_BUDGET_REVIEW,
    ]
    if requisition.status not in allowed_statuses:
        raise ValidationError(f"Cannot reject requisition in status '{requisition.status}'.")

    if not comments or not comments.strip():
        raise ValidationError("Rejection justification comments are mandatory.")

    previous_status = requisition.status
    requisition.status = PurchaseRequisition.STATUS_REJECTED
    requisition.save(update_fields=["status", "updated_at"])

    # Release any active budget reservations for this requisition
    release_budget_reservation_service(
        requisition=requisition, user=approver, reason=f"PR Rejected: {comments}"
    )

    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_REJECT,
        previous_state=previous_status,
        new_state=requisition.status,
        comments=comments,
    )

    return requisition



@transaction.atomic
def approve_purchase_requisition_service(
    *, requisition: PurchaseRequisition, approver: User, comments: str = ""
) -> PurchaseRequisition:
    """
    Approves a PR and transitions state to APPROVED.
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

    previous_status = requisition.status

    if requisition.status == PurchaseRequisition.STATUS_BUDGET_REVIEW:
        # Finance is approving an overrun explicitly, force the reservation
        check_and_reserve_budget_service(
            requisition=requisition, requested_by_user=approver, force_overrun=True
        )
    elif requisition.status == PurchaseRequisition.STATUS_MANAGER_REVIEW:
        # Manager is approving. If it wasn't already reserved, reserve it now.
        # Wait, if it didn't overrun, it was reserved during submission. But we should ideally just leave it reserved.
        # However, to be safe, we just update status.
        pass

    requisition.status = PurchaseRequisition.STATUS_APPROVED
    requisition.save(update_fields=["status", "updated_at"])

    record_approval_action_service(
        target_object_id=requisition.id,
        target_model_name="PurchaseRequisition",
        actor=approver,
        action=ApprovalAction.ACTION_APPROVE,
        previous_state=previous_status,
        new_state=requisition.status,
        comments=comments or "Requisition approved.",
    )

    try:
        from apps.accounts.models import Role, User
        from apps.notifications.services import create_notification_service

        proc_execs = User.objects.filter(role__code=Role.PROC_EXEC, is_active=True)
        for pe in proc_execs:
            create_notification_service(
                recipient=pe,
                title="Approved PR Ready for Sourcing",
                message=f"Requisition {requisition.pr_number} has been approved by {approver.email} and is ready on your Sourcing Intake.",
                notification_type="IN_APP",
                action_url=f"/requisitions/{requisition.id}/",
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

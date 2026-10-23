from django.db import models

from apps.core.models import TimeStampedModel


class Notification(TimeStampedModel):
    TYPE_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TYPE_BID_DEADLINE = "BID_DEADLINE"
    TYPE_EXCEPTION_RAISED = "EXCEPTION_RAISED"
    TYPE_CONTRACT_EXPIRATION = "CONTRACT_EXPIRATION"
    TYPE_CONTRACT_MILESTONE = "CONTRACT_MILESTONE"
    TYPE_CONTRACT_OBLIGATION = "CONTRACT_OBLIGATION"
    TYPE_PO_ACKNOWLEDGED = "PO_ACKNOWLEDGED"
    TYPE_RFQ_INVITATION = "RFQ_INVITATION"
    TYPE_CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    TYPE_BID_SUBMITTED = "BID_SUBMITTED"
    TYPE_BID_AMENDED = "BID_AMENDED"
    TYPE_PO_ISSUED = "PO_ISSUED"
    TYPE_PO_AMENDMENT = "PO_AMENDMENT"
    TYPE_KYC_REQUEST = "KYC_REQUEST"
    TYPE_GOODS_RECEIVED = "GOODS_RECEIVED"

    TYPE_CHOICES = [
        (TYPE_APPROVAL_REQUIRED, "Approval Required"),
        (TYPE_BID_DEADLINE, "Bid Window Closing"),
        (TYPE_EXCEPTION_RAISED, "3-Way Match Exception"),
        (TYPE_CONTRACT_EXPIRATION, "Contract Expiration Warning"),
        (TYPE_CONTRACT_MILESTONE, "Contract Milestone Due Warning"),
        (TYPE_CONTRACT_OBLIGATION, "Contract Obligation Due Warning"),
        (TYPE_PO_ACKNOWLEDGED, "PO Acknowledged"),
        (TYPE_RFQ_INVITATION, "New RFQ/RFP Invitation"),
        (TYPE_CLARIFICATION_RESPONSE, "Clarification Response"),
        (TYPE_BID_SUBMITTED, "Bid Submission Confirmation"),
        (TYPE_BID_AMENDED, "Bid Amendment Confirmation"),
        (TYPE_PO_ISSUED, "Purchase Order Issued"),
        (TYPE_PO_AMENDMENT, "PO Amendment Notification"),
        (TYPE_KYC_REQUEST, "KYC / Compliance Notification"),
        (TYPE_GOODS_RECEIVED, "Goods Receipt Recorded"),
    ]

    recipient = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(max_length=50, choices=TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    target_url = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Fast lookup for "my unread notifications, newest first"
            # (dashboard feed + polling). Covers recipient + is_read filter.
            models.Index(
                fields=["recipient", "is_read", "-created_at"],
                name="notif_recip_unread_idx",
            ),
            models.Index(fields=["recipient", "-created_at"], name="notif_recip_created_idx"),
        ]

    @property
    def action_url(self) -> str:
        """Alias for ``target_url`` (optional link to the PR/PO/Event).

        The column is named ``target_url`` for backwards compatibility;
        templates and API consumers may use either name.
        """
        return self.target_url or ""

    @action_url.setter
    def action_url(self, value: str) -> None:
        self.target_url = value or ""

    def __str__(self):
        return f"Notification to {self.recipient.email}: {self.title} [{'READ' if self.is_read else 'UNREAD'}]"

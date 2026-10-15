from rest_framework import serializers

from .models import (
    AwardDecision,
    BidAttachment,
    BidEvaluation,
    BidInvite,
    BidLine,
    Clarification,
    NegotiationNote,
    SourcingEvent,
    VendorBid,
)


class SourcingEventSerializer(serializers.ModelSerializer):
    invite_count = serializers.SerializerMethodField()
    submitted_bid_count = serializers.SerializerMethodField()
    bid_count = serializers.SerializerMethodField()
    award_readiness = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = SourcingEvent
        fields = [
            "id",
            "event_number",
            "title",
            "event_type",
            "status",
            "status_display",
            "requisition",
            "bid_start_date",
            "bid_end_date",
            "is_sealed",
            "description",
            "technical_requirements",
            "commercial_requirements",
            "required_documents",
            "technical_weight",
            "commercial_weight",
            "invite_count",
            "submitted_bid_count",
            "bid_count",
            "award_readiness",
            "created_at",
            "updated_at",
        ]
        # Status only changes through explicit action endpoints (publish/close/...).
        read_only_fields = ["id", "event_number", "status", "is_sealed", "created_at", "updated_at"]

    def get_invite_count(self, obj) -> int:
        count = getattr(obj, "invite_count", None)
        return count if count is not None else obj.invitations.count()

    def get_submitted_bid_count(self, obj) -> int:
        count = getattr(obj, "submitted_bid_count", None)
        if count is not None:
            return count
        return obj.bids.filter(
            status__in=[VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED]
        ).count()

    def get_bid_count(self, obj) -> int:
        return obj.bids.count()

    def get_award_readiness(self, obj) -> str:
        if obj.status == SourcingEvent.STATUS_BID_WINDOW:
            return "BIDDING_OPEN"
        if obj.status == SourcingEvent.STATUS_TECHNICAL_REVIEW:
            return "TECHNICAL_REVIEW"
        if obj.status == SourcingEvent.STATUS_AWARD_APPROVAL:
            return "READY_FOR_AWARD"
        return obj.status


class BidInviteSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)
    vendor_number = serializers.CharField(source="vendor.vendor_number", read_only=True)
    vendor_status = serializers.CharField(source="vendor.status", read_only=True)

    class Meta:
        model = BidInvite
        fields = [
            "id",
            "event",
            "vendor",
            "vendor_name",
            "vendor_number",
            "vendor_status",
            "is_responded",
            "created_at",
        ]


class BidLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BidLine
        fields = ["id", "item_description", "quantity", "quoted_unit_price", "quoted_total_price"]


class BidAttachmentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = BidAttachment
        fields = ["id", "title", "document_type", "file_size", "created_at", "download_url"]

    def get_download_url(self, obj) -> str:
        request = self.context.get("request")
        if request and request.user.is_vendor:
            return f"/vendor/documents/bid/{obj.id}/download/"
        return f"/sourcing-events/{obj.bid.event_id}/attachments/{obj.id}/download/"


COMMERCIAL_FIELDS = ("total_bid_amount", "commercial_proposal", "lines")


class VendorBidSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)
    event_number = serializers.CharField(source="event.event_number", read_only=True)
    lines = BidLineSerializer(many=True, read_only=True)

    class Meta:
        model = VendorBid
        fields = [
            "id",
            "bid_number",
            "event",
            "event_number",
            "vendor",
            "vendor_name",
            "version",
            "status",
            "proposal_summary",
            "technical_proposal",
            "commercial_proposal",
            "total_bid_amount",
            "lines",
            "submitted_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        is_vendor = bool(request and request.user.is_vendor)
        # Commercial envelope stays sealed for evaluators until COMMERCIAL_REVIEW.
        if not is_vendor and not instance.event.commercial_visible_to_evaluators:
            for field in COMMERCIAL_FIELDS:
                data.pop(field, None)
        return data


class BidEvaluationSerializer(serializers.ModelSerializer):
    evaluator_email = serializers.CharField(source="evaluator.email", read_only=True)
    bid_number = serializers.CharField(source="bid.bid_number", read_only=True)

    class Meta:
        model = BidEvaluation
        fields = [
            "id",
            "event",
            "bid",
            "bid_number",
            "evaluator_email",
            "technical_score",
            "commercial_score",
            "weighted_total_score",
            "technical_evaluated_at",
            "commercial_evaluated_at",
            "comments",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not instance.event.commercial_visible_to_evaluators:
            for field in ("commercial_score", "weighted_total_score", "commercial_evaluated_at"):
                data.pop(field, None)
        return data


class ClarificationSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)
    event_number = serializers.CharField(source="event.event_number", read_only=True)

    class Meta:
        model = Clarification
        fields = [
            "id",
            "event",
            "event_number",
            "vendor",
            "vendor_name",
            "question",
            "answer",
            "status",
            "answered_at",
            "created_at",
        ]
        read_only_fields = fields


class NegotiationNoteSerializer(serializers.ModelSerializer):
    author_email = serializers.CharField(source="author.email", read_only=True)

    class Meta:
        model = NegotiationNote
        fields = ["id", "event", "bid", "author_email", "note", "created_at"]
        read_only_fields = fields


class AwardDecisionSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="winning_bid.vendor.legal_name", read_only=True)

    class Meta:
        model = AwardDecision
        fields = [
            "id",
            "event",
            "winning_bid",
            "vendor_name",
            "award_reason",
            "status",
            "recommended_by",
            "approved_by",
            "decided_at",
            "decision_comments",
            "created_at",
        ]
        read_only_fields = fields


# ----- action payloads -------------------------------------------------------------------


class InvitePayload(serializers.Serializer):
    vendor_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class ReasonPayload(serializers.Serializer):
    reason = serializers.CharField()


class AmendBidPayload(serializers.Serializer):
    amendment_reason = serializers.CharField()
    line_items = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    proposal_summary = serializers.CharField(required=False, allow_blank=True, default="")
    technical_proposal = serializers.CharField(required=False, allow_blank=True, default="")
    commercial_proposal = serializers.CharField(required=False, allow_blank=True, default="")


class CommentsPayload(serializers.Serializer):
    comments = serializers.CharField(required=False, allow_blank=True, default="")


class EvaluationPayload(serializers.Serializer):
    bid_id = serializers.UUIDField()
    score = serializers.DecimalField(max_digits=5, decimal_places=2)
    comments = serializers.CharField(required=False, allow_blank=True, default="")


class RecommendAwardPayload(serializers.Serializer):
    bid_id = serializers.UUIDField()
    award_reason = serializers.CharField()


class NegotiationPayload(serializers.Serializer):
    bid_id = serializers.UUIDField()
    note = serializers.CharField()


class GeneratePOPayload(serializers.Serializer):
    cost_center_id = serializers.UUIDField(required=False, allow_null=True)


class AnswerPayload(serializers.Serializer):
    answer = serializers.CharField()

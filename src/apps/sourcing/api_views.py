from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import services
from .models import BidEvaluation, Clarification, SourcingEvent, VendorBid
from .permissions import (
    AwardApprovalPermission,
    BidPermission,
    SourcingEventPermission,
    can_manage_events,
    can_read_bids,
)
from .selectors import (
    get_eligible_vendors_for_event,
    get_sealed_vendor_bids,
    get_sourcing_events_for_user,
    search_sourcing_events,
)
from .serializers import (
    AnswerPayload,
    AwardDecisionSerializer,
    BidEvaluationSerializer,
    BidInviteSerializer,
    ClarificationSerializer,
    CommentsPayload,
    EvaluationPayload,
    GeneratePOPayload,
    InvitePayload,
    NegotiationNoteSerializer,
    NegotiationPayload,
    ReasonPayload,
    RecommendAwardPayload,
    SourcingEventSerializer,
    VendorBidSerializer,
)


def _require_manage(request):
    if not can_manage_events(request.user):
        raise PermissionDenied("Only procurement users can perform this sourcing action.")


class SourcingEventViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    RFQ/RFP events. Vendor users only see published events they are invited to.
    Status changes happen exclusively through the POST action endpoints.
    """

    serializer_class = SourcingEventSerializer
    permission_classes = [SourcingEventPermission]
    search_fields = []  # search handled by search_sourcing_events (?q=)
    ordering_fields = ["created_at", "bid_end_date", "event_number"]

    def get_queryset(self):
        qs = get_sourcing_events_for_user(self.request.user)
        return search_sourcing_events(qs, self.request.query_params)

    def get_object(self):
        event = super().get_object()
        services.sync_event_window_status(event=event)
        return event

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = services.create_sourcing_event_service(
            title=data.get("title", ""),
            event_type=data.get("event_type", ""),
            bid_start_date=data.get("bid_start_date"),
            bid_end_date=data.get("bid_end_date"),
            description=data.get("description", ""),
            technical_requirements=data.get("technical_requirements", ""),
            commercial_requirements=data.get("commercial_requirements", ""),
            required_documents=data.get("required_documents", ""),
            requisition=data.get("requisition"),
            technical_weight=data.get("technical_weight", 50),
            commercial_weight=data.get("commercial_weight", 50),
            created_by_user=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.instance = services.update_draft_sourcing_event_service(
            event=serializer.instance, user=self.request.user, data=serializer.validated_data
        )

    def _ok(self, event):
        event.refresh_from_db()
        return Response(SourcingEventSerializer(event).data)

    # ----- lifecycle actions -----------------------------------------------------------

    @extend_schema(request=None, responses=SourcingEventSerializer)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        _require_manage(request)
        event = self.get_object()
        services.publish_sourcing_event_service(event=event, user=request.user)
        return self._ok(event)

    @extend_schema(request=None, responses=SourcingEventSerializer)
    @action(detail=True, methods=["post"], url_path="close-bidding")
    def close_bidding(self, request, pk=None):
        _require_manage(request)
        event = self.get_object()
        if event.status != SourcingEvent.STATUS_BID_WINDOW:
            return Response(
                {
                    "error": {
                        "code": "INVALID_TRANSITION",
                        "message": f"Cannot close bidding on event in status '{event.status}'. Must be in BID_WINDOW.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        services.close_bid_window_service(event=event, user=request.user)
        return self._ok(event)

    @extend_schema(request=None, responses=SourcingEventSerializer)
    @action(detail=True, methods=["post"], url_path="start-commercial-review")
    def start_commercial_review(self, request, pk=None):
        _require_manage(request)
        event = self.get_object()
        services.advance_to_commercial_review_service(event=event, user=request.user)
        return self._ok(event)

    @extend_schema(request=ReasonPayload, responses=SourcingEventSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        _require_manage(request)
        payload = ReasonPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        event = self.get_object()
        services.cancel_sourcing_event_service(
            event=event, user=request.user, reason=payload.validated_data["reason"]
        )
        return self._ok(event)

    # ----- invitations -----------------------------------------------------------------

    @extend_schema(request=InvitePayload, responses=BidInviteSerializer(many=True))
    @action(detail=True, methods=["get", "post"])
    def invitations(self, request, pk=None):
        event = self.get_object()
        if request.method == "GET":
            qs = event.invitations.select_related("vendor")
            if request.user.is_vendor:
                qs = qs.filter(vendor_id=request.user.vendor_id)
            return Response(BidInviteSerializer(qs, many=True).data)
        _require_manage(request)
        payload = InvitePayload(data=request.data)
        payload.is_valid(raise_exception=True)
        invites = services.invite_vendors_to_event_service(
            event=event,
            vendor_ids=payload.validated_data["vendor_ids"],
            invited_by=request.user,
        )
        return Response(
            BidInviteSerializer(invites, many=True).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path="eligible-vendors")
    def eligible_vendors(self, request, pk=None):
        _require_manage(request)
        event = self.get_object()
        vendors = get_eligible_vendors_for_event(
            event,
            category_id=request.query_params.get("category"),
            q=request.query_params.get("q", ""),
        )
        page = self.paginate_queryset(vendors)
        rows = [
            {
                "id": str(v.id),
                "vendor_number": v.vendor_number,
                "legal_name": v.legal_name,
                "category": v.category.name,
                "status": v.status,
            }
            for v in (page if page is not None else vendors)
        ]
        return self.get_paginated_response(rows) if page is not None else Response(rows)

    # ----- bids & evaluation -----------------------------------------------------------

    @extend_schema(responses=VendorBidSerializer(many=True))
    @action(detail=True, methods=["get"])
    def bids(self, request, pk=None):
        event = self.get_object()
        if not request.user.is_vendor and not can_read_bids(request.user):
            raise PermissionDenied("Your role cannot view vendor bids.")
        bids = get_sealed_vendor_bids(event, request.user)
        return Response(VendorBidSerializer(bids, many=True, context={"request": request}).data)

    @extend_schema(request=EvaluationPayload, responses=BidEvaluationSerializer)
    @action(detail=True, methods=["post"], url_path="evaluate/(?P<stage>technical|commercial)")
    def evaluate(self, request, pk=None, stage=None):
        _require_manage(request)
        payload = EvaluationPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        event = self.get_object()
        bid = get_object_or_404(VendorBid, pk=payload.validated_data["bid_id"], event=event)
        fn = (
            services.record_technical_evaluation_service
            if stage == "technical"
            else services.record_commercial_evaluation_service
        )
        evaluation = fn(
            event=event,
            bid=bid,
            evaluator=request.user,
            score=payload.validated_data["score"],
            comments=payload.validated_data["comments"],
        )
        return Response(BidEvaluationSerializer(evaluation).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=NegotiationPayload, responses=NegotiationNoteSerializer)
    @action(detail=True, methods=["get", "post"], url_path="negotiation-notes")
    def negotiation_notes(self, request, pk=None):
        if not can_manage_events(request.user) and not can_read_bids(request.user):
            raise PermissionDenied("Your role cannot view negotiation notes.")
        event = self.get_object()
        if request.method == "GET":
            notes = event.negotiation_notes.select_related("author")
            return Response(NegotiationNoteSerializer(notes, many=True).data)
        _require_manage(request)
        payload = NegotiationPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        bid = get_object_or_404(VendorBid, pk=payload.validated_data["bid_id"], event=event)
        note = services.add_negotiation_note_service(
            event=event, bid=bid, author=request.user, note=payload.validated_data["note"]
        )
        return Response(NegotiationNoteSerializer(note).data, status=status.HTTP_201_CREATED)

    # ----- award -----------------------------------------------------------------------

    @extend_schema(request=RecommendAwardPayload, responses=AwardDecisionSerializer)
    @action(detail=True, methods=["post"], url_path="recommend-award")
    def recommend_award(self, request, pk=None):
        _require_manage(request)
        payload = RecommendAwardPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        event = self.get_object()
        bid = get_object_or_404(VendorBid, pk=payload.validated_data["bid_id"], event=event)
        decision = services.recommend_award_service(
            event=event,
            winning_bid=bid,
            recommended_by=request.user,
            award_reason=payload.validated_data["award_reason"],
        )
        return Response(AwardDecisionSerializer(decision).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CommentsPayload, responses=AwardDecisionSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path="approve-award",
        permission_classes=[AwardApprovalPermission],
    )
    def approve_award(self, request, pk=None):
        payload = CommentsPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        event = self.get_object()
        decision = services.approve_award_service(
            event=event, approver=request.user, comments=payload.validated_data["comments"]
        )
        return Response(AwardDecisionSerializer(decision).data)

    @extend_schema(request=ReasonPayload, responses=AwardDecisionSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path="reject-award",
        permission_classes=[AwardApprovalPermission],
    )
    def reject_award(self, request, pk=None):
        payload = ReasonPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        event = self.get_object()
        decision = services.reject_award_service(
            event=event, approver=request.user, comments=payload.validated_data["reason"]
        )
        return Response(AwardDecisionSerializer(decision).data)

    @extend_schema(request=GeneratePOPayload, responses=None)
    @action(detail=True, methods=["post"], url_path="generate-po")
    def generate_po(self, request, pk=None):
        from apps.organization.models import CostCenter

        _require_manage(request)
        payload = GeneratePOPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        event = self.get_object()
        cc_id = payload.validated_data.get("cost_center_id")
        cost_center = get_object_or_404(CostCenter, pk=cc_id) if cc_id else None
        po = services.generate_po_from_award_service(
            event=event, user=request.user, cost_center=cost_center
        )
        return Response(
            {"id": str(po.id), "po_number": po.po_number, "status": po.status},
            status=status.HTTP_201_CREATED,
        )


class VendorBidViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Bids: vendors see only their own; internal bid readers see submitted bids only after the
    bid window closes, with the commercial envelope sealed until COMMERCIAL_REVIEW.
    """

    serializer_class = VendorBidSerializer
    permission_classes = [BidPermission]

    def get_queryset(self):
        user = self.request.user
        qs = (
            VendorBid.objects.select_related("event", "vendor")
            .prefetch_related("lines")
            .order_by("-created_at")
        )
        if user.is_vendor:
            return qs.filter(vendor_id=user.vendor_id) if user.vendor_id else qs.none()
        if not can_read_bids(user):
            return qs.none()
        return qs.exclude(
            event__status__in=[
                SourcingEvent.STATUS_DRAFT,
                SourcingEvent.STATUS_PUBLISHED,
                SourcingEvent.STATUS_BID_WINDOW,
                SourcingEvent.STATUS_CANCELLED,
            ]
        ).exclude(status=VendorBid.STATUS_DRAFT)

    @extend_schema(request=ReasonPayload, responses=VendorBidSerializer)
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        if not request.user.is_vendor:
            raise PermissionDenied("Only the bidding vendor can withdraw its bid.")
        payload = ReasonPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        bid = self.get_object()  # already scoped to the vendor's own bids
        bid = services.withdraw_vendor_bid_service(
            bid=bid, user=request.user, reason=payload.validated_data["reason"]
        )
        return Response(VendorBidSerializer(bid, context={"request": request}).data)


class ClarificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ClarificationSerializer
    permission_classes = [SourcingEventPermission]

    def get_queryset(self):
        user = self.request.user
        qs = Clarification.objects.select_related("event", "vendor").order_by("-created_at")
        if user.is_vendor:
            return qs.filter(vendor_id=user.vendor_id) if user.vendor_id else qs.none()
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        status_filter = self.request.query_params.get("status")
        if status_filter in ("PENDING", "ANSWERED"):
            qs = qs.filter(status=status_filter)
        return qs

    @extend_schema(request=AnswerPayload, responses=ClarificationSerializer)
    @action(detail=True, methods=["post"])
    def answer(self, request, pk=None):
        _require_manage(request)
        payload = AnswerPayload(data=request.data)
        payload.is_valid(raise_exception=True)
        clarification = services.answer_clarification_service(
            clarification=self.get_object(),
            user=request.user,
            answer=payload.validated_data["answer"],
        )
        return Response(ClarificationSerializer(clarification).data)


class BidEvaluationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BidEvaluationSerializer
    permission_classes = [BidPermission]

    def get_queryset(self):
        user = self.request.user
        if user.is_vendor or not can_read_bids(user):
            return BidEvaluation.objects.none()
        qs = BidEvaluation.objects.select_related("event", "bid", "evaluator").order_by(
            "-created_at"
        )
        event_id = self.request.query_params.get("event")
        return qs.filter(event_id=event_id) if event_id else qs

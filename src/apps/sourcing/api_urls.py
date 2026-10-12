from django.db import models
from django.shortcuts import get_object_or_404
from django.urls import path
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.accounts.permissions import IsProcurementManager
from apps.audit.models import AuditLog

from .api_views import (
    BidEvaluationViewSet,
    ClarificationViewSet,
    SourcingEventViewSet,
    VendorBidViewSet,
)
from .models import SourcingEvent, VendorBid
from .services import evaluate_and_award_sourcing_event_service


def get_user_org(user):
    if user and user.is_authenticated and not user.is_superuser:
        if getattr(user, "department_id", None) and getattr(
            user.department, "organization_id", None
        ):
            return user.department.organization
    return None


class PendingAwardsAPIView(APIView):
    """
    GET /api/v1/sourcing-events/awards/pending/
    Returns sourcing events currently awaiting Manager Award Decision with evaluated vendor bids.
    """

    permission_classes = [IsAuthenticated, IsProcurementManager]

    def get(self, request):
        org = get_user_org(request.user)
        events = (
            SourcingEvent.objects.filter(
                status__in=[
                    SourcingEvent.STATUS_AWARD_APPROVAL,
                    SourcingEvent.STATUS_COMMERCIAL_REVIEW,
                    SourcingEvent.STATUS_TECHNICAL_REVIEW,
                ]
            )
            .select_related("requisition", "requisition__department", "requisition__cost_center")
            .prefetch_related(
                "bids",
                "bids__vendor",
                "evaluations",
                "evaluations__evaluator",
            )
            .order_by("-updated_at")
        )
        if org:
            events = events.filter(
                models.Q(requisition__department__organization=org)
                | (
                    models.Q(requisition__isnull=True)
                    & models.Q(created_by__department__organization=org)
                )
            )

        results = []
        for event in events:
            evaluated_bids = []
            evaluations_list = list(event.evaluations.all())
            for bid in event.bids.all():
                if bid.status not in [VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED]:
                    continue
                eval_obj = next((e for e in evaluations_list if e.bid_id == bid.id), None)
                evaluated_bids.append(
                    {
                        "bid_id": str(bid.id),
                        "bid_number": bid.bid_number,
                        "vendor_id": str(bid.vendor.id),
                        "vendor_name": bid.vendor.legal_name,
                        "total_bid_amount": float(bid.total_bid_amount),
                        "version": bid.version,
                        "submitted_at": bid.submitted_at.isoformat() if bid.submitted_at else None,
                        "technical_score": float(eval_obj.technical_score) if eval_obj else 0.0,
                        "commercial_score": float(eval_obj.commercial_score) if eval_obj else 0.0,
                        "weighted_total_score": (
                            float(eval_obj.weighted_total_score) if eval_obj else 0.0
                        ),
                        "evaluation_comments": eval_obj.comments if eval_obj else "",
                    }
                )

            evaluated_bids.sort(key=lambda b: (-b["weighted_total_score"], b["total_bid_amount"]))

            results.append(
                {
                    "id": str(event.id),
                    "event_number": event.event_number,
                    "title": event.title,
                    "event_type": event.event_type,
                    "status": event.status,
                    "description": event.description or "",
                    "requisition_id": str(event.requisition.id) if event.requisition else None,
                    "requisition_number": (
                        event.requisition.pr_number if event.requisition else None
                    ),
                    "requisition_title": (event.requisition.title if event.requisition else None),
                    "department": (
                        event.requisition.department.name
                        if event.requisition and event.requisition.department
                        else None
                    ),
                    "cost_center": (
                        event.requisition.cost_center.code
                        if event.requisition and event.requisition.cost_center
                        else None
                    ),
                    "bid_start_date": (
                        event.bid_start_date.isoformat() if event.bid_start_date else None
                    ),
                    "bid_end_date": (
                        event.bid_end_date.isoformat() if event.bid_end_date else None
                    ),
                    "bids_count": event.bids.count(),
                    "evaluations_count": event.evaluations.count(),
                    "evaluated_bids": evaluated_bids,
                    "recommended_bid": evaluated_bids[0] if evaluated_bids else None,
                }
            )

        return Response(results, status=status.HTTP_200_OK)


class AwardApproveAPIView(APIView):
    """
    POST /api/v1/sourcing-events/events/<uuid:pk>/award-approve/
    Processes Manager Award Decision (APPROVE, REJECT, or RETURN) with audit ledger logging.
    """

    permission_classes = [IsAuthenticated, IsProcurementManager]

    def post(self, request, pk):
        event = get_object_or_404(SourcingEvent, pk=pk)

        org = get_user_org(request.user)
        event_org = (
            event.requisition.department.organization
            if event.requisition and event.requisition.department
            else (
                event.created_by.department.organization
                if event.created_by and event.created_by.department
                else None
            )
        )
        if org and event_org and event_org.id != org.id:
            return Response(
                {
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": "Permission denied: Sourcing event belongs to another organization.",
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if event.status == SourcingEvent.STATUS_AWARDED:
            return Response(
                {
                    "error": {
                        "code": "INVALID_TRANSITION",
                        "message": f"Sourcing Event {event.event_number} is already awarded. Duplicate award approval is prevented.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if event.status not in [
            SourcingEvent.STATUS_AWARD_APPROVAL,
            SourcingEvent.STATUS_COMMERCIAL_REVIEW,
            SourcingEvent.STATUS_TECHNICAL_REVIEW,
        ]:
            return Response(
                {
                    "error": {
                        "code": "INVALID_TRANSITION",
                        "message": f"Sourcing Event in status '{event.status}' cannot be awarded, rejected, or returned.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = str(request.data.get("action", "APPROVE")).upper()
        comments = str(request.data.get("comments", request.data.get("award_reason", ""))).strip()

        if action == "APPROVE":
            winning_bid_id = request.data.get("winning_bid_id")
            if winning_bid_id:
                winning_bid = event.bids.filter(
                    id=winning_bid_id,
                    status__in=[VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED],
                ).first()
            else:
                top_eval = event.evaluations.order_by("-weighted_total_score").first()
                if top_eval and top_eval.bid.status in [
                    VendorBid.STATUS_SUBMITTED,
                    VendorBid.STATUS_AMENDED,
                ]:
                    winning_bid = top_eval.bid
                else:
                    winning_bid = (
                        event.bids.filter(
                            status__in=[VendorBid.STATUS_SUBMITTED, VendorBid.STATUS_AMENDED]
                        )
                        .order_by("total_bid_amount")
                        .first()
                    )

            if not winning_bid:
                return Response(
                    {
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": "A valid submitted winning bid must be selected for award approval.",
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                decision = evaluate_and_award_sourcing_event_service(
                    event=event,
                    winning_bid=winning_bid,
                    award_reason=comments or "Award approved by Procurement Manager.",
                    approved_by_user=request.user,
                )
                return Response(
                    {
                        "message": f"Sourcing Event {event.event_number} successfully awarded to {winning_bid.vendor.legal_name}.",
                        "event_id": str(event.id),
                        "status": event.status,
                        "winning_bid_id": str(winning_bid.id),
                        "winning_vendor": winning_bid.vendor.legal_name,
                        "award_decision_id": str(decision.id),
                    },
                    status=status.HTTP_200_OK,
                )
            except Exception as e:
                return Response(
                    {"error": {"code": "VALIDATION_ERROR", "message": str(e)}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        elif action in ["REJECT", "RETURN"]:
            if not comments:
                return Response(
                    {
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": "Rejection/return justification comments are mandatory.",
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            previous_status = event.status
            new_status = (
                SourcingEvent.STATUS_COMMERCIAL_REVIEW
                if action == "RETURN"
                else SourcingEvent.STATUS_CANCELLED
            )
            event.status = new_status
            event.save(update_fields=["status", "updated_at"])

            AuditLog.objects.create(
                actor=request.user,
                action=AuditLog.ACTION_REJECT if action == "REJECT" else AuditLog.ACTION_UPDATE,
                target_model="SourcingEvent",
                target_object_id=str(event.id),
                previous_state={"status": previous_status},
                new_state={"status": event.status, "action": action, "comments": comments},
            )

            return Response(
                {
                    "message": f"Sourcing Event {event.event_number} status updated to {new_status}.",
                    "event_id": str(event.id),
                    "status": event.status,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": f"Invalid action '{action}'. Supported actions: APPROVE, REJECT, RETURN.",
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


router = DefaultRouter()
router.register(r"events", SourcingEventViewSet, basename="sourcing-event")
router.register(r"bids", VendorBidViewSet, basename="vendor-bid")
router.register(r"clarifications", ClarificationViewSet, basename="sourcing-clarification")
router.register(r"evaluations", BidEvaluationViewSet, basename="bid-evaluation")

urlpatterns = [
    path("awards/pending/", PendingAwardsAPIView.as_view(), name="pending-awards-api"),
    path(
        "events/<uuid:pk>/award-approve/", AwardApproveAPIView.as_view(), name="award-approve-api"
    ),
] + router.urls

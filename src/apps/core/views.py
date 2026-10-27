from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Avg, Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from redis import Redis
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.orders.models import PurchaseOrder
from apps.requisitions.models import PurchaseRequisition
from apps.scorecards.models import VendorScorecard
from apps.sourcing.models import Clarification, SourcingEvent
from apps.vendors.models import Vendor


def health_check_view(request):
    """
    Health check endpoint returning DB and Redis status.
    """
    health_status = {
        "status": "healthy",
        "service": "ProcureSphere 360",
        "database": "unknown",
        "redis": "unknown",
    }

    # Check Database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            health_status["database"] = "ok"
    except Exception as e:
        health_status["database"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    # Check Redis connection
    try:
        redis_client = Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        if redis_client.ping():
            health_status["redis"] = "ok"
    except Exception as e:
        health_status["redis"] = f"offline (local mode): {str(e)}"

    http_status = 200 if health_status["status"] == "healthy" else 503
    return JsonResponse(health_status, status=http_status)


class HealthAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return health_check_view(request)


@login_required(login_url="/login/")
def home_view(request):  # noqa: C901
    """
    Role-tailored Dashboard page view with live aggregated ERP metrics.
    Dispatches to custom workspace per user role (Requester, Approver, Procurement, Finance, Vendor, Legal, Auditor, Admin).
    """
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )

    # 1. REQUESTER ROLE DASHBOARD
    if role_code == Role.REQUESTER:
        my_prs = PurchaseRequisition.objects.filter(requester=user)
        context = {
            "metrics": {
                "total_my_prs": my_prs.count(),
                "pending_prs": my_prs.filter(
                    status__in=["SUBMITTED", "MANAGER_REVIEW", "BUDGET_REVIEW"]
                ).count(),
                "approved_prs": my_prs.filter(status="APPROVED").count(),
            },
            "my_recent_prs": my_prs.order_by("-created_at")[:10],
        }
        return render(request, "pages/dashboards/requester_dashboard.html", context)

    elif role_code == Role.PROC_MGR:
        from apps.core.manager_views import manager_dashboard_view

        return manager_dashboard_view(request)

    # 4. STORES / RECEIVER DASHBOARD
    elif role_code == Role.STORES_RECEIVER:
        from apps.receipts.views import get_stores_dashboard_data

        data = get_stores_dashboard_data(user)
        return render(
            request,
            "pages/dashboards/stores_dashboard.html",
            {
                "metrics": data["metrics"],
                "recent_grns": data["recent_grns"],
                "pending_deliveries": data.get("pending_deliveries", []),
                "pending_inspections": data.get("pending_inspections", []),
                "pending_handoffs": data.get("pending_handoffs", []),
                "pending_rejections": data.get("pending_rejections", []),
            },
        )

    # 5. VENDOR PORTAL USER DASHBOARD
    elif role_code == Role.VENDOR_USER:
        from apps.vendors.vendor_dashboard_views import vendor_dashboard_overview_view

        return vendor_dashboard_overview_view(request)

    # 6. FINANCE / AP SPECIALIST DASHBOARD
    elif role_code == Role.FINANCE_AP:
        from apps.budgets.models import SpendLedger
        from apps.invoices.models import MatchException, SupplierInvoice

        open_match_exceptions = MatchException.objects.filter(
            status=MatchException.STATUS_OPEN
        ).order_by("-created_at")
        open_budget_exceptions = PurchaseRequisition.objects.filter(
            status=PurchaseRequisition.STATUS_BUDGET_REVIEW
        ).order_by("-updated_at")
        ready_for_payment = SupplierInvoice.objects.filter(
            status=SupplierInvoice.STATUS_READY_FOR_PAYMENT
        ).count()
        committed_spend = (
            SpendLedger.objects.filter(entry_type=SpendLedger.ENTRY_COMMITMENT).aggregate(
                total=Sum("amount")
            )["total"]
            or 0
        )
        actual_spend = (
            SpendLedger.objects.filter(entry_type=SpendLedger.ENTRY_ACTUAL).aggregate(
                total=Sum("amount")
            )["total"]
            or 0
        )
        context = {
            "metrics": {
                "pending_match_exceptions": open_match_exceptions.count(),
                "pending_budget_exceptions": open_budget_exceptions.count(),
                "ready_for_payment": ready_for_payment,
                "committed_spend": float(committed_spend),
                "actual_spend": float(actual_spend),
            },
            "open_match_exceptions": open_match_exceptions[:10],
            "open_budget_exceptions": open_budget_exceptions[:10],
        }
        return render(request, "pages/dashboards/finance_dashboard.html", context)

    # 7. PROCUREMENT EXECUTIVE DASHBOARD
    elif role_code == Role.PROC_EXEC:
        from apps.notifications.models import Notification
        from apps.sourcing.selectors import get_sourcing_dashboard_metrics
        from apps.sourcing.services import sync_all_event_windows
        from apps.vendors.filters import annotate_governance
        from apps.vendors.selectors import get_governance_metrics

        sync_all_event_windows()
        now = timezone.now()
        closing_soon = (
            SourcingEvent.objects.filter(
                status=SourcingEvent.STATUS_BID_WINDOW,
                bid_end_date__gt=now,
                bid_end_date__lte=now + timezone.timedelta(days=3),
            )
            .annotate(invite_count=Count("invitations"))
            .order_by("bid_end_date")[:8]
        )
        action_queue = SourcingEvent.objects.filter(
            status__in=[
                SourcingEvent.STATUS_DRAFT,
                SourcingEvent.STATUS_TECHNICAL_REVIEW,
                SourcingEvent.STATUS_COMMERCIAL_REVIEW,
                SourcingEvent.STATUS_AWARD_APPROVAL,
            ]
        ).order_by("-updated_at")[:10]
        attention_vendors = (
            annotate_governance(Vendor.objects.select_related("category"))
            .filter(
                Q(status=Vendor.STATUS_ON_HOLD)
                | Q(latest_risk_level="HIGH")
                | Q(latest_composite_score__lt=70)
            )
            .order_by("-updated_at")[:8]
        )
        awarded_without_po = (
            SourcingEvent.objects.filter(status=SourcingEvent.STATUS_AWARDED)
            .exclude(
                purchase_orders__status__in=[
                    PurchaseOrder.STATUS_DRAFT,
                    PurchaseOrder.STATUS_APPROVAL,
                    PurchaseOrder.STATUS_ISSUED,
                    PurchaseOrder.STATUS_ACKNOWLEDGED,
                    PurchaseOrder.STATUS_PARTIAL_RECEIPT,
                    PurchaseOrder.STATUS_COMPLETED,
                ]
            )
            .order_by("-updated_at")[:8]
        )
        avg_scorecard = VendorScorecard.objects.aggregate(avg=Avg("composite_score"))["avg"]
        ready_prs_count = PurchaseRequisition.objects.filter(
            status=PurchaseRequisition.STATUS_APPROVED
        ).count()
        context = {
            "sourcing": get_sourcing_dashboard_metrics(),
            "governance": get_governance_metrics(),
            "ready_prs_count": ready_prs_count,
            "open_pos": PurchaseOrder.objects.filter(
                status__in=[
                    PurchaseOrder.STATUS_ISSUED,
                    PurchaseOrder.STATUS_ACKNOWLEDGED,
                    PurchaseOrder.STATUS_PARTIAL_RECEIPT,
                ]
            ).count(),
            "pending_clarifications": Clarification.objects.filter(status="PENDING")
            .exclude(event__status__in=["AWARDED", "CANCELLED"])
            .count(),
            "avg_scorecard": round(float(avg_scorecard), 1) if avg_scorecard is not None else None,
            "unread_notifications": Notification.objects.filter(
                recipient=user, is_read=False
            ).count(),
            "closing_soon": closing_soon,
            "action_queue": action_queue,
            "attention_vendors": attention_vendors,
            "awarded_without_po": awarded_without_po,
        }
        return render(request, "pages/dashboards/procurement_dashboard.html", context)

    # 8. LEGAL / CONTRACT MANAGER DASHBOARD
    elif role_code == Role.LEGAL_MGR:
        from apps.contracts.views import dashboard_view as legal_dashboard_view

        return legal_dashboard_view(request)

    # 9. DEPARTMENT APPROVER DASHBOARD
    elif role_code == Role.DEPT_APPROVER:
        user_department = getattr(user, "department", None)
        if user_department:
            dept_prs = PurchaseRequisition.objects.filter(department=user_department)
            pending_prs_qs = (
                dept_prs.filter(status__in=["SUBMITTED", "MANAGER_REVIEW", "BUDGET_REVIEW"])
                .order_by("-updated_at")
                .prefetch_related("attachments")
            )

            from apps.budgets.models import Budget
            from apps.organization.models import CostCenter, FiscalPeriod

            # Basic available budget calculation for dashboard
            now = timezone.now()
            # Find current fiscal period
            current_period = FiscalPeriod.objects.filter(
                start_date__lte=now, end_date__gte=now, is_closed=False
            ).first()

            available_budget = 0
            if current_period:
                # Aggregate available budget across all cost centers in this department
                dept_ccs = CostCenter.objects.filter(department=user_department)
                budgets = Budget.objects.filter(
                    cost_center__in=dept_ccs, fiscal_period=current_period
                )
                available_budget = sum([b.available_amount for b in budgets])

            context = {
                "user_department": user_department,
                "metrics": {
                    "total_prs_count": dept_prs.count(),
                    "pending_count": pending_prs_qs.count(),
                    "approved_count": dept_prs.filter(status="APPROVED").count(),
                    "available_budget": float(available_budget),
                },
                "pending_prs": pending_prs_qs[:10],
            }
        else:
            context = {
                "user_department": None,
                "metrics": {
                    "total_prs_count": 0,
                    "pending_count": 0,
                    "approved_count": 0,
                    "available_budget": 0.0,
                },
                "pending_prs": [],
            }
        return render(request, "pages/dashboards/approver_dashboard.html", context)

    # 10. SUPER ADMIN DASHBOARD
    elif role_code == Role.SUPER_ADMIN:
        return render(request, "pages/dashboards/superadmin_dashboard.html")

    # 11. COMPLIANCE AUDITOR DASHBOARD
    elif role_code == Role.AUDITOR:
        from apps.audit.views import auditor_dashboard_view

        return auditor_dashboard_view(request)

    # 12. VENDOR PORTAL DASHBOARD
    elif role_code == Role.VENDOR_USER:
        from apps.vendors.vendor_dashboard_views import vendor_dashboard_overview_view

        return vendor_dashboard_overview_view(request)

    # Fallback for all other unknown roles
    return render(request, "pages/dashboards/requester_dashboard.html")


def _is_api_request(request):
    accept = request.headers.get("Accept", "").lower()
    return request.path.startswith("/api/") or "application/json" in accept or "json" in accept


def custom_404_view(request, exception=None):
    if _is_api_request(request):
        return JsonResponse(
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "The requested resource was not found.",
                    "details": {"path": request.path},
                }
            },
            status=404,
        )
    return render(request, "404.html", status=404)


def custom_500_view(request):
    if _is_api_request(request):
        return JsonResponse(
            {
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected server error occurred.",
                    "details": {},
                }
            },
            status=500,
        )
    return render(request, "500.html", status=500)


def custom_403_view(request, exception=None):
    if _is_api_request(request):
        return JsonResponse(
            {
                "error": {
                    "code": "PERMISSION_DENIED",
                    "message": "You do not have permission to access this resource.",
                    "details": {"detail": str(exception) if exception else "Permission denied."},
                }
            },
            status=403,
        )
    return render(request, "403.html", status=403)

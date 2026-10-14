from django.urls import path
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role

from .models import ExportJob
from .services import (
    generate_export_job_service,
    get_audit_log_report,
    get_contract_expiry_report,
    get_contract_obligation_report,
    get_invoice_exception_aging_report,
    get_po_status_report,
    get_pr_aging_report,
    get_receipt_delivery_performance_summary,
    get_receipt_rejection_report,
    get_sourcing_cycle_time_report,
    get_spend_analytics_report,
    get_supplier_performance_report,
)

VALID_REPORT_TYPES = [
    "pr_aging",
    "spend_analytics",
    "sourcing_cycle_time",
    "po_status",
    "receipt_rejection",
    "invoice_exception_aging",
    "contract_expiry",
    "supplier_scorecard",
    "supplier_performance",
    "audit_log",
]


class IsNotVendorUser(permissions.BasePermission):
    """
    Prevents external vendor users from accessing internal ERP reports and dashboards.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return not (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        )


class CanCreateExportJob(permissions.BasePermission):
    """
    Restricts export job creation to authorized internal staff (Procurement, Finance, Legal, Auditor, Admin).
    Requesters and Vendors cannot generate bulk exports.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        role_code = getattr(request.user, "role_code", None)
        if role_code in [Role.REQUESTER, Role.VENDOR_USER]:
            return False
        return True


class CanViewSpendAnalytics(permissions.BasePermission):
    """
    Restricts organisation-wide spend analytics to executive, finance, and auditing roles.
    Requesters and external vendors are strictly prohibited (403).
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        role_code = getattr(request.user, "role_code", None)
        if role_code in [Role.REQUESTER, Role.VENDOR_USER]:
            return False
        return True


class ExportJobSerializer(serializers.ModelSerializer):
    requested_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ExportJob
        fields = "__all__"
        read_only_fields = ["requested_by", "status", "result_file", "error_message"]

    def validate_report_type(self, value):
        val = str(value).strip().lower()
        if val not in VALID_REPORT_TYPES:
            raise serializers.ValidationError(
                f"Invalid report_type '{value}'. Supported types: {', '.join(VALID_REPORT_TYPES)}"
            )
        return val


class ExportJobViewSet(viewsets.ModelViewSet):
    queryset = ExportJob.objects.select_related("requested_by").order_by("-created_at")
    serializer_class = ExportJobSerializer
    permission_classes = [IsAuthenticated, CanCreateExportJob]

    def perform_create(self, serializer):
        job = serializer.save(requested_by=self.request.user)
        generate_export_job_service(job.id)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def dashboard_summary_view(request):
    """
    Executes executive procurement dashboard overview combining spend, PRs, POs, Invoices, Contracts.
    """
    pr_data = get_pr_aging_report()
    spend_data = get_spend_analytics_report()
    po_data = get_po_status_report()
    inv_data = get_invoice_exception_aging_report()
    contract_data = get_contract_expiry_report(user=request.user)
    scorecard_data = get_supplier_performance_report()

    summary = {
        "total_prs": len(pr_data),
        "total_pos": len(po_data),
        "total_spend": sum(
            float(item.get("actual_spend") or item.get("actual") or 0.0) for item in spend_data
        ),
        "pending_exceptions": len([item for item in inv_data if item["status"] == "OPEN"]),
        "expiring_contracts": len(
            [item for item in contract_data if 0 <= item["days_to_expiry"] <= 60]
        ),
        "vendor_count": len(scorecard_data),
    }
    return Response(summary, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def pr_aging_report_view(request):
    return Response(get_pr_aging_report(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, CanViewSpendAnalytics])
def spend_analytics_report_view(request):
    return Response(get_spend_analytics_report(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def sourcing_cycle_report_view(request):
    return Response(get_sourcing_cycle_time_report(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def po_status_report_view(request):
    return Response(get_po_status_report(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def receipt_rejection_report_view(request):
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    vendor = request.GET.get("vendor")
    po_number = request.GET.get("po_number")
    status_param = request.GET.get("status")
    return Response(
        get_receipt_rejection_report(
            start_date=start_date,
            end_date=end_date,
            vendor=vendor,
            po_number=po_number,
            inspection_status=status_param,
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def receipt_delivery_summary_view(request):
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    vendor = request.GET.get("vendor")
    return Response(
        get_receipt_delivery_performance_summary(
            start_date=start_date,
            end_date=end_date,
            vendor=vendor,
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def invoice_exception_report_view(request):
    return Response(get_invoice_exception_aging_report(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def contract_expiry_report_view(request):
    status_param = request.GET.get("status")
    days_param = request.GET.get("days")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    return Response(
        get_contract_expiry_report(
            status=status_param,
            days=days_param,
            start_date=start_date,
            end_date=end_date,
            user=request.user,
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def contract_obligation_report_view(request):
    due_status = request.GET.get("due_status")
    responsible_party = request.GET.get("responsible_party")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    return Response(
        get_contract_obligation_report(
            due_status=due_status,
            responsible_party=responsible_party,
            start_date=start_date,
            end_date=end_date,
            user=request.user,
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def supplier_performance_report_view(request):
    return Response(get_supplier_performance_report(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsNotVendorUser])
def audit_log_report_view(request):
    return Response(get_audit_log_report(), status=status.HTTP_200_OK)


router = DefaultRouter()
router.register(r"exports", ExportJobViewSet, basename="export-job")

urlpatterns = router.urls + [
    path("dashboard-summary/", dashboard_summary_view, name="dashboard-summary"),
    path("pr-aging/", pr_aging_report_view, name="report-pr-aging"),
    path("spend-analytics/", spend_analytics_report_view, name="report-spend-analytics"),
    path("spend/", spend_analytics_report_view, name="report-spend-alias"),
    path("sourcing-cycle/", sourcing_cycle_report_view, name="report-sourcing-cycle"),
    path("po-status/", po_status_report_view, name="report-po-status"),
    path("receipt-rejection/", receipt_rejection_report_view, name="report-receipt-rejection"),
    path(
        "receipt-delivery-summary/",
        receipt_delivery_summary_view,
        name="report-receipt-delivery-summary",
    ),
    path("invoice-exceptions/", invoice_exception_report_view, name="report-invoice-exceptions"),
    path("contract-expiry/", contract_expiry_report_view, name="report-contract-expiry"),
    path(
        "contract-obligation/", contract_obligation_report_view, name="report-contract-obligation"
    ),
    path(
        "supplier-performance/",
        supplier_performance_report_view,
        name="report-supplier-performance",
    ),
    path("audit-logs/", audit_log_report_view, name="report-audit-logs"),
]

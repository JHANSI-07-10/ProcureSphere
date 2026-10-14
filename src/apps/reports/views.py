from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.reports.models import ExportJob
from apps.reports.services import generate_export_job_service


@login_required(login_url="/login/")
def dashboard_view(request):
    jobs = ExportJob.objects.filter(requested_by=request.user).order_by("-created_at")[:20]

    reports = [
        {
            "id": "spend_analytics",
            "name": "Spend Analytics",
            "desc": "Spend by cost center and period",
        },
        {
            "id": "pr_aging",
            "name": "PR Approval Aging",
            "desc": "Requisition cycle times and pending approver bottlenecks",
        },
        {
            "id": "sourcing_cycle",
            "name": "Sourcing Cycle Time",
            "desc": "RFQ/RFP duration and supplier participation",
        },
        {
            "id": "po_status",
            "name": "PO Status & Lifecycle Register",
            "desc": "Purchase order status, amendments, and delivery schedule progression",
        },
        {
            "id": "receipt_rejection",
            "name": "Receipt, Rejection & Delivery Performance",
            "desc": "Goods receipt intake, quality rejection rates, and vendor on-time delivery metrics",
        },
        {
            "id": "invoice_exception_aging",
            "name": "Match Exception Aging",
            "desc": "Aging report of open 3-way match exceptions",
        },
        {
            "id": "contract_expiry",
            "name": "Contract Expiry Pipeline",
            "desc": "Upcoming contract expirations within 30/60/90 days",
        },
        {
            "id": "contract_obligation",
            "name": "Contract Obligation Register",
            "desc": "Track upcoming, overdue, and fulfilled legal obligations",
        },
        {
            "id": "supplier_scorecard",
            "name": "Supplier Scorecard Export",
            "desc": "Comprehensive weighted scorecards for vendor review",
        },
        {
            "id": "audit_log",
            "name": "Audit Trail Register",
            "desc": "Chronological system change trail and security events",
        },
    ]

    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else ""
    )
    role_code = str(role_code).upper()

    base_layout = "layouts/finance_base.html"
    if role_code in ["AUDITOR", "COMPLIANCE_AUDITOR", "AUDIT"]:
        base_layout = "audit/base_auditor.html"
    elif role_code in ["PROCUREMENT_MANAGER", "PROC_MGR", "MANAGER", "LEGAL_MGR"]:
        base_layout = "manager/base_manager.html"
    elif role_code in ["STORES_RECEIVER", "STORES"]:
        base_layout = "layouts/stores_base.html"

    return render(
        request,
        "pages/reports/dashboard.html",
        {
            "jobs": jobs,
            "reports": reports,
            "base_layout": base_layout,
            "role_code": role_code,
        },
    )


# Aliases so both URL names work
reports_hub_view = dashboard_view


@login_required(login_url="/login/")
def generate_view(request):
    if request.method == "POST":
        report_type = request.POST.get("report_type")
        format_type = request.POST.get("format_type", "CSV")

        job = ExportJob.objects.create(
            report_type=report_type, export_format=format_type, requested_by=request.user
        )

        try:
            generate_export_job_service(job.id)
            messages.success(request, f"{report_type} report successfully generated!")
        except Exception as e:
            messages.error(request, f"Error generating report: {str(e)}")

    return redirect("reports_hub")

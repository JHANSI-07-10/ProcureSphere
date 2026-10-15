import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render

from apps.audit.models import AuditLog
from apps.reports.models import ExportJob
from apps.reports.services import (
    generate_export_job_service,
    get_audit_log_report,
    get_po_status_report,
    get_pr_aging_report,
    get_sourcing_cycle_time_report,
)

PROC_REPORT_ROLES = {"PROC_EXEC", "PROCUREMENT_EXECUTIVE", "PROC_MGR", "SUPER_ADMIN"}


def _get_role_code(user):
    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else ""
    )
    return str(role_code or "").upper()


def _resolve_base_layout(role_code):
    """
    Resolve role-specific base layout so all workspaces retain their designated sidebar.
    Procurement executive uses base.html (which embeds proc_exec_sidebar.html).
    All other roles use their respective layout with dedicated sidebar.
    """
    if role_code in ("PROC_EXEC", "PROCUREMENT_EXECUTIVE"):
        return "base.html"
    if role_code in ("FINANCE_AP", "FINANCE"):
        return "layouts/finance_base.html"
    if role_code in ("SUPER_ADMIN", "ADMIN"):
        return "layouts/superadmin_base.html"
    if role_code in ("AUDITOR", "COMPLIANCE_AUDITOR", "AUDIT"):
        return "audit/base_auditor.html"
    if role_code in ("PROCUREMENT_MANAGER", "PROC_MGR", "MANAGER"):
        return "manager/base_manager.html"
    if role_code in ("LEGAL_MGR", "LEGAL"):
        return "layouts/legal_base.html"
    if role_code in ("STORES_RECEIVER", "STORES"):
        return "layouts/stores_base.html"
    if role_code in ("DEPT_APPROVER", "APPROVER"):
        return "layouts/approver_base.html"
    if role_code in ("REQUESTER",):
        return "layouts/requester_base.html"
    if role_code in ("VENDOR_USER", "VENDOR"):
        return "layouts/vendor_base.html"
    return "layouts/finance_base.html"


def _require_proc_report_access(request):
    role_code = _get_role_code(request.user)
    if role_code not in PROC_REPORT_ROLES:
        raise PermissionDenied("You do not have access to procurement reports.")
    return role_code


def _csv_response(filename, fieldnames, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fieldnames})
    return response


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

    role_code = _get_role_code(request.user)
    base_layout = _resolve_base_layout(role_code)

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


@login_required(login_url="/login/")
def proc_exec_pr_aging_view(request):
    """PR Aging — approval aging & bottlenecks for the Procurement Executive workspace."""
    role_code = _require_proc_report_access(request)
    base_layout = _resolve_base_layout(role_code)

    rows = get_pr_aging_report()

    q = (request.GET.get("q") or "").strip().lower()
    status_filter = (request.GET.get("status") or "").strip().upper()
    if q:
        rows = [
            r
            for r in rows
            if q in str(r.get("pr_number", "")).lower()
            or q in str(r.get("title", "")).lower()
            or q in str(r.get("department", "")).lower()
        ]
    if status_filter:
        rows = [r for r in rows if str(r.get("status", "")).upper() == status_filter]

    under_3 = sum(1 for r in rows if (r.get("age_days") or 0) < 3)
    btw_3_7 = sum(1 for r in rows if 3 <= (r.get("age_days") or 0) <= 7)
    over_7 = sum(1 for r in rows if (r.get("age_days") or 0) > 7)

    if request.GET.get("export") == "csv":
        fieldnames = [
            "pr_number",
            "title",
            "department",
            "cost_center",
            "status",
            "total_amount",
            "created_at",
            "age_days",
        ]
        return _csv_response("pr_aging_report.csv", fieldnames, rows)

    statuses = sorted({str(r.get("status", "")) for r in rows if r.get("status")})
    return render(
        request,
        "pages/reports/proc_exec_pr_aging.html",
        {
            "rows": rows,
            "total": len(rows),
            "under_3": under_3,
            "btw_3_7": btw_3_7,
            "over_7": over_7,
            "statuses": statuses,
            "q": request.GET.get("q", ""),
            "status_filter": request.GET.get("status", ""),
            "base_layout": base_layout,
            "role_code": role_code,
        },
    )


@login_required(login_url="/login/")
def proc_exec_sourcing_cycle_view(request):
    """Sourcing Cycle — RFQ/RFP duration and supplier participation."""
    role_code = _require_proc_report_access(request)
    base_layout = _resolve_base_layout(role_code)

    rows = get_sourcing_cycle_time_report()

    q = (request.GET.get("q") or "").strip().lower()
    status_filter = (request.GET.get("status") or "").strip().upper()
    if q:
        rows = [
            r
            for r in rows
            if q in str(r.get("event_number", "")).lower()
            or q in str(r.get("title", "")).lower()
        ]
    if status_filter:
        rows = [r for r in rows if str(r.get("status", "")).upper() == status_filter]

    from datetime import datetime

    cycles = []
    for r in rows:
        try:
            start = datetime.strptime(str(r.get("start_date", "")), "%Y-%m-%d").date()
            end = datetime.strptime(str(r.get("end_date", "")), "%Y-%m-%d").date()
            cycles.append((end - start).days)
        except (ValueError, TypeError):
            continue
    avg_cycle_days = round(sum(cycles) / len(cycles), 1) if cycles else 0
    total_invited = sum(int(r.get("invited_vendors") or 0) for r in rows)
    total_bids = sum(int(r.get("submitted_bids") or 0) for r in rows)
    bid_rate = round((total_bids / total_invited * 100) if total_invited else 0, 1)

    if request.GET.get("export") == "csv":
        fieldnames = [
            "event_number",
            "title",
            "event_type",
            "status",
            "invited_vendors",
            "submitted_bids",
            "start_date",
            "end_date",
        ]
        return _csv_response("sourcing_cycle_report.csv", fieldnames, rows)

    statuses = sorted({str(r.get("status", "")) for r in rows if r.get("status")})
    return render(
        request,
        "pages/reports/proc_exec_sourcing_cycle.html",
        {
            "rows": rows,
            "total": len(rows),
            "avg_cycle_days": avg_cycle_days,
            "total_invited": total_invited,
            "total_bids": total_bids,
            "bid_rate": bid_rate,
            "statuses": statuses,
            "q": request.GET.get("q", ""),
            "status_filter": request.GET.get("status", ""),
            "base_layout": base_layout,
            "role_code": role_code,
        },
    )


@login_required(login_url="/login/")
def proc_exec_po_status_view(request):
    """PO Status — open / partial / closed purchase order status."""
    role_code = _require_proc_report_access(request)
    base_layout = _resolve_base_layout(role_code)

    rows = get_po_status_report()

    q = (request.GET.get("q") or "").strip().lower()
    status_filter = (request.GET.get("status") or "").strip().upper()
    if q:
        rows = [
            r
            for r in rows
            if q in str(r.get("po_number", "")).lower()
            or q in str(r.get("vendor", "")).lower()
        ]
    if status_filter:
        rows = [r for r in rows if str(r.get("status", "")).upper() == status_filter]

    groups = {}
    for r in rows:
        key = str(r.get("status", "UNKNOWN")).upper()
        groups[key] = groups.get(key, 0) + 1

    if request.GET.get("export") == "csv":
        fieldnames = [
            "po_number",
            "version",
            "vendor",
            "cost_center",
            "status",
            "total_amount",
            "acknowledged_at",
        ]
        return _csv_response("po_status_report.csv", fieldnames, rows)

    statuses = sorted({str(r.get("status", "")) for r in rows if r.get("status")})
    return render(
        request,
        "pages/reports/proc_exec_po_status.html",
        {
            "rows": rows,
            "total": len(rows),
            "groups": groups,
            "statuses": statuses,
            "q": request.GET.get("q", ""),
            "status_filter": request.GET.get("status", ""),
            "base_layout": base_layout,
            "role_code": role_code,
        },
    )


PROCUREMENT_AUDIT_MODELS = {
    "PurchaseRequisition",
    "PRLine",
    "SourcingEvent",
    "BidInvite",
    "VendorBid",
    "BidLine",
    "BidEvaluation",
    "Clarification",
    "AwardDecision",
    "PurchaseOrder",
    "POLine",
    "POAmendment",
    "Vendor",
    "VendorContact",
    "VendorDocument",
    "VendorRiskRecord",
}


@login_required(login_url="/login/")
def proc_exec_audit_report_view(request):
    """Immutable Audit Report — append-only procurement audit trail."""
    role_code = _require_proc_report_access(request)
    base_layout = _resolve_base_layout(role_code)

    logs_qs = (
        AuditLog.objects.select_related("actor")
        .filter(target_model__in=sorted(PROCUREMENT_AUDIT_MODELS))
        .order_by("-timestamp")[:200]
    )

    q = (request.GET.get("q") or "").strip().lower()
    action_filter = (request.GET.get("action") or "").strip().upper()

    rows = []
    for log in logs_qs:
        actor_email = log.actor.email if log.actor else "System"
        row = {
            "audit_id": str(log.id),
            "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "actor": actor_email,
            "action": log.action,
            "target_model": log.target_model,
            "target_object_id": log.target_object_id,
        }
        rows.append(row)

    if q:
        rows = [
            r
            for r in rows
            if q in r["target_model"].lower()
            or q in r["target_object_id"].lower()
            or q in r["actor"].lower()
            or q in r["audit_id"].lower()
        ]
    if action_filter:
        rows = [r for r in rows if r["action"].upper() == action_filter]

    if request.GET.get("export") == "csv":
        # Export reuses the audited report service shape (direct download for speed).
        _ = get_audit_log_report()
        fieldnames = [
            "audit_id",
            "timestamp",
            "actor",
            "action",
            "target_model",
            "target_object_id",
        ]
        return _csv_response("immutable_audit_report.csv", fieldnames, rows)

    actions = sorted({r["action"] for r in rows})
    return render(
        request,
        "pages/reports/proc_exec_audit_report.html",
        {
            "rows": rows,
            "total": len(rows),
            "actions": actions,
            "q": request.GET.get("q", ""),
            "action_filter": request.GET.get("action", ""),
            "base_layout": base_layout,
            "role_code": role_code,
        },
    )

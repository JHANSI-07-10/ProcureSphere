from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render

from apps.accounts.models import Role
from apps.audit.selectors import (
    get_approval_history_audit,
    get_audit_logs,
    get_audit_metrics,
    get_auditor_dashboard_data,
    get_contract_changes_audit,
    get_invoice_exceptions_audit,
    get_po_changes_audit,
    get_security_events_audit,
    get_sourcing_activity_audit,
    get_vendor_compliance_audit,
)
from apps.audit.services import get_transaction_lifecycle_service


def _enforce_auditor_access(request):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )
    if not (
        user.is_superuser
        or role_code
        in [Role.AUDITOR, Role.SUPER_ADMIN, Role.FINANCE_AP, "AUDITOR", "SUPER_ADMIN", "FINANCE_AP"]
    ):
        raise PermissionDenied("Access restricted to Compliance Auditor role.")


@login_required
def auditor_dashboard_view(request):
    """
    Main Executive Compliance Auditor & Traceability Hub Dashboard.
    Supplies core executive KPIs, activity trend charts, action distribution,
    open anomalies, and recent audit activity feed.
    """
    _enforce_auditor_access(request)
    period_days = int(request.GET.get("period_days", 30))
    dashboard_data = get_auditor_dashboard_data(period_days=period_days)
    vendor_compliance = get_vendor_compliance_audit(limit=10)
    approval_history = get_approval_history_audit(limit=10)
    po_changes = get_po_changes_audit(limit=10)
    invoice_exceptions = get_invoice_exceptions_audit(limit=10)
    contract_changes = get_contract_changes_audit(limit=10)
    security_events = get_security_events_audit(limit=10)
    sourcing_activity = get_sourcing_activity_audit(limit=10)

    context = {
        "metrics": {
            "total_audit_logs": dashboard_data["metrics"]["total_logs_all_time"],
            "total_approvals": dashboard_data["metrics"]["critical_events"]["approvals"],
            "total_rejections": dashboard_data["metrics"]["critical_events"]["rejections"],
            "total_logins": dashboard_data["metrics"]["critical_events"]["logins"],
            "total_exports": dashboard_data["metrics"]["critical_events"]["exports"],
            "open_exceptions": dashboard_data["domain_counts"]["open_exceptions_count"],
            "suspended_vendors": dashboard_data["domain_counts"]["suspended_vendors_count"],
            "overdue_obligations": dashboard_data["domain_counts"]["overdue_obligations_count"],
            "compliance_rate": vendor_compliance["summary"]["compliance_rate"],
        },
        "audit_data": dashboard_data,
        "recent_logs": dashboard_data["recent_logs"],
        "recent_approvals": dashboard_data["recent_approvals"],
        "open_exceptions": dashboard_data["open_exceptions"],
        "suspended_vendors": dashboard_data["suspended_vendors"],
        "overdue_obligations": dashboard_data["overdue_obligations"],
        "active_contract_alerts": dashboard_data["active_contract_alerts"],
        "vendor_compliance": vendor_compliance,
        "approval_history": approval_history,
        "po_changes": po_changes,
        "invoice_exceptions": invoice_exceptions,
        "contract_changes": contract_changes,
        "security_events": security_events,
        "sourcing_activity": sourcing_activity,
        "period_days": period_days,
    }
    return render(request, "pages/dashboards/auditor_dashboard.html", context)


@login_required
def audit_log_view(request):
    """
    Dedicated Append-Only Immutable Audit Log Register Page.
    """
    _enforce_auditor_access(request)
    action_filter = request.GET.get("action", "").strip()
    search_query = request.GET.get("q", "").strip() or request.GET.get("search", "").strip()
    model_filter = (
        request.GET.get("model", "").strip() or request.GET.get("target_model", "").strip()
    )
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_audit_logs(
        limit=limit,
        offset=offset,
        action=action_filter if action_filter else None,
        search_term=search_query if search_query else None,
        target_model=model_filter if model_filter else None,
    )
    total_count = data.get("total_count", 0)
    total_pages = max(1, (total_count + limit - 1) // limit)

    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if getattr(request.user, "role", None) else None
    )
    base_layout = "layouts/requester_base.html"
    if role_code == "DEPT_APPROVER":
        base_layout = "layouts/approver_base.html"
    elif role_code == "FINANCE_AP":
        base_layout = "layouts/finance_base.html"
    elif role_code in ["PROC_MGR", "PROC_EXEC"]:
        base_layout = "layouts/procurement_base.html"
    elif role_code in ["AUDITOR", "SUPER_ADMIN"]:
        base_layout = "layouts/auditor_base.html"
    elif role_code in ["STORES_RECEIVER", "STORES"]:
        base_layout = "layouts/stores_base.html"

    context = {
        "logs": data.get("logs", []),
        "audit_logs": data.get("logs", []),
        "action": action_filter,
        "action_filter": action_filter,
        "search": search_query,
        "search_query": search_query,
        "model": model_filter,
        "model_filter": model_filter,
        "page": page,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "base_layout": base_layout,
    }
    return render(request, "audit/logs.html", context)


@login_required
def audit_approvals_view(request):
    """
    Approval Governance History View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    action_filter = request.GET.get("action", "").strip()
    target_model = (
        request.GET.get("model", "").strip() or request.GET.get("target_model", "").strip()
    )
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_approval_history_audit(
        target_model=target_model if target_model else None,
        action=action_filter if action_filter else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "approvals": data["results"],
        "search_query": search_query,
        "action_filter": action_filter,
        "target_model": target_model,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/approvals.html", context)


@login_required
def audit_po_changes_view(request):
    """
    Purchase Order Changes & Amendments View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_po_changes_audit(
        search=search_query if search_query else None,
        status=status_filter if status_filter else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "pos": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/po_changes.html", context)


@login_required
def audit_vendors_view(request):
    """
    Vendor Compliance & KYC Audit View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    kyc_status = request.GET.get("kyc_status", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_vendor_compliance_audit(
        status=status_filter if status_filter else None,
        kyc_status=kyc_status if kyc_status else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "vendors": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "kyc_status": kyc_status,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/vendors.html", context)


@login_required
def audit_invoice_exceptions_view(request):
    """
    3-Way Match Invoice Exceptions View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    exception_type = request.GET.get("exception_type", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_invoice_exceptions_audit(
        status=status_filter if status_filter else None,
        exception_type=exception_type if exception_type else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "exceptions": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "exception_type": exception_type,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/invoice_exceptions.html", context)


@login_required
def audit_contract_changes_view(request):
    """
    Contract Compliance & Milestone Governance View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_contract_changes_audit(
        search=search_query if search_query else None,
        status=status_filter if status_filter else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "contracts": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/contract_changes.html", context)


@login_required
def audit_security_events_view(request):
    """
    Security & Authentication Events View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    action_filter = request.GET.get("action", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_security_events_audit(
        search=search_query if search_query else None,
        action=action_filter if action_filter else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "security_events": data["results"],
        "search_query": search_query,
        "action_filter": action_filter,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/security_events.html", context)


@login_required
def audit_sourcing_activity_view(request):
    """
    Sourcing & Competitive Bids Activity View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    event_type = request.GET.get("event_type", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    data = get_sourcing_activity_audit(
        status=status_filter if status_filter else None,
        event_type=event_type if event_type else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "events": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "event_type": event_type,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/sourcing_activity.html", context)


@login_required
def audit_lifecycle_view(request):
    """
    Source-to-Pay Transaction Lifecycle Traceability Explorer.
    """
    _enforce_auditor_access(request)
    identifier = request.GET.get("identifier", "").strip() or request.GET.get("q", "").strip()
    entity_type = request.GET.get("entity_type", "AUTO").strip()

    lifecycle = None
    if identifier:
        lifecycle = get_transaction_lifecycle_service(
            entity_type=entity_type,
            entity_identifier=identifier,
        )

    context = {
        "identifier": identifier,
        "entity_type": entity_type,
        "lifecycle": lifecycle,
    }
    return render(request, "audit/lifecycle_viewer.html", context)


@login_required
def audit_metrics_api_view(request):
    """
    JSON API for auditor statistical metrics.
    """
    _enforce_auditor_access(request)
    try:
        period_days = int(request.GET.get("period_days", 30))
    except (ValueError, TypeError):
        period_days = 30
    data = get_audit_metrics(period_days=period_days)
    return JsonResponse(data)

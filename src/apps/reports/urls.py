from django.urls import path

from .views import (
    generate_view,
    proc_exec_audit_report_view,
    proc_exec_po_status_view,
    proc_exec_pr_aging_view,
    proc_exec_sourcing_cycle_view,
    reports_hub_view,
)

urlpatterns = [
    path("", reports_hub_view, name="reports_dashboard"),
    path("hub/", reports_hub_view, name="reports_hub"),
    path("reports/", reports_hub_view, name="reports_hub_alias"),
    path("generate/", generate_view, name="generate_report"),
    path("reports/generate/", generate_view, name="generate_report_alias"),
    path("pr-aging/", proc_exec_pr_aging_view, name="proc_exec_pr_aging"),
    path("sourcing-cycle/", proc_exec_sourcing_cycle_view, name="proc_exec_sourcing_cycle"),
    path("po-status/", proc_exec_po_status_view, name="proc_exec_po_status"),
    path("audit-trail/", proc_exec_audit_report_view, name="proc_exec_audit_report"),
]

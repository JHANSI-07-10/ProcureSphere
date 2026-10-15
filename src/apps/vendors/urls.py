from django.urls import path

from . import views

urlpatterns = [
    path("vendors/", views.vendor_list_view, name="vendor_list"),
    path("vendors/governance/", views.vendor_governance_view, name="vendor_governance"),
    path("vendors/onboarding/", views.vendor_onboarding_view, name="vendor_onboarding"),
    path("vendors/create/", views.vendor_create_view, name="vendor_create"),
    path("vendors/htmx/register/", views.vendor_register_modal_view, name="vendors_register_modal"),
    path(
        "vendors/<uuid:vendor_id>/credentials/",
        views.vendor_issue_credentials_view,
        name="vendor_issue_credentials",
    ),
    path("vendors/<uuid:vendor_id>/", views.vendor_detail_view, name="vendor_detail"),
    path(
        "vendors/<uuid:vendor_id>/status/",
        views.vendor_status_change_view,
        name="vendor_status_change",
    ),
    path(
        "vendors/<uuid:vendor_id>/risk/", views.vendor_risk_assess_view, name="vendor_risk_assess"
    ),
    path(
        "vendors/<uuid:vendor_id>/kyc/<slug:kyc_action>/",
        views.vendor_kyc_action_view,
        name="vendor_kyc_action",
    ),
    path(
        "vendors/<uuid:vendor_id>/documents/<uuid:doc_id>/download/",
        views.vendor_document_download_view,
        name="vendor_document_download",
    ),
    # Procurement Executive Dashboard — Pillar 1 HTMX fragments
    path(
        "vendors/htmx/qualification/",
        views.vendor_qualification_tab_view,
        name="vendors_qualification_tab",
    ),
    path(
        "vendors/<uuid:vendor_id>/htmx/documents/",
        views.vendor_documents_modal_view,
        name="vendors_documents_modal",
    ),
    path(
        "vendors/<uuid:vendor_id>/htmx/risk/",
        views.vendor_risk_modal_view,
        name="vendors_risk_modal",
    ),
    path(
        "vendors/<uuid:vendor_id>/htmx/kyc/<slug:kyc_action>/",
        views.vendor_kyc_htmx_view,
        name="vendors_kyc_htmx",
    ),
    # Edit vendor master data (HTMX modal)
    path(
        "vendors/<uuid:vendor_id>/edit/",
        views.vendor_edit_view,
        name="vendor_edit",
    ),
    # Delete vendor (POST-only, DRAFT/REJECTED only)
    path(
        "vendors/<uuid:vendor_id>/delete/",
        views.vendor_delete_view,
        name="vendor_delete",
    ),
]

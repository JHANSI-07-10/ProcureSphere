from django.urls import path

from . import views

urlpatterns = [
    path("contracts/", views.list_view, name="contracts_list"),
    path("contracts/dashboard/", views.dashboard_view, name="contracts_dashboard"),
    path("contracts/create/", views.create_view, name="contract_create"),
    path("contracts/<uuid:contract_id>/", views.detail_view, name="contract_detail"),
    path(
        "contracts/<uuid:contract_id>/submit-legal/",
        views.submit_legal_view,
        name="contract_submit_legal",
    ),
    path(
        "contracts/<uuid:contract_id>/legal-approve/",
        views.legal_approve_view,
        name="contract_legal_approve",
    ),
    path(
        "contracts/<uuid:contract_id>/legal-reject/",
        views.legal_reject_view,
        name="contract_legal_reject",
    ),
    path(
        "contracts/<uuid:contract_id>/business-approve/",
        views.business_approve_view,
        name="contract_business_approve",
    ),
    path("contracts/<uuid:contract_id>/amend/", views.amend_view, name="contract_amend"),
    path(
        "contracts/<uuid:contract_id>/milestone/add/",
        views.milestone_create_view,
        name="contract_milestone_create",
    ),
    path(
        "contracts/<uuid:contract_id>/milestone/<str:milestone_id>/complete/",
        views.milestone_toggle_view,
        name="contract_milestone_complete",
    ),
    path(
        "contracts/<uuid:contract_id>/obligation/add/",
        views.obligation_create_view,
        name="contract_obligation_create",
    ),
    path(
        "contracts/<uuid:contract_id>/obligation/<str:obligation_id>/fulfill/",
        views.obligation_toggle_view,
        name="contract_obligation_fulfill",
    ),
    path(
        "contracts/<uuid:contract_id>/document/upload/",
        views.document_upload_view,
        name="contract_document_upload",
    ),
    path(
        "contracts/<uuid:contract_id>/document/<uuid:document_id>/download/",
        views.document_download_view,
        name="contract_document_download",
    ),
    path("contracts/<uuid:contract_id>/renew/", views.renew_view, name="contract_renew"),
    path(
        "contracts/<uuid:contract_id>/terminate/", views.terminate_view, name="contract_terminate"
    ),
    path(
        "contracts/credentials/<uuid:dispatch_id>/validate/",
        views.legal_validate_vendor_credentials_view,
        name="legal_validate_credentials",
    ),
    path(
        "contracts/credentials/<uuid:dispatch_id>/reject/",
        views.legal_reject_vendor_credentials_view,
        name="legal_reject_credentials",
    ),
    path(
        "contracts/documents/<uuid:document_id>/decision/",
        views.legal_document_decision_view,
        name="legal_document_decision",
    ),
]

from django.urls import path

from . import vendor_dashboard_views as views

urlpatterns = [
    # Dashboard Overview
    path("", views.vendor_dashboard_overview_view, name="vendor_dashboard"),
    path("dashboard/", views.vendor_dashboard_overview_view, name="vendor_dashboard_home"),
    # Company Profile
    path("profile/", views.vendor_profile_view, name="vendor_profile"),
    # Sourcing (RFQ / RFP)
    path("sourcing/", views.vendor_sourcing_list_view, name="vendor_sourcing_list"),
    path(
        "sourcing/<uuid:event_id>/",
        views.vendor_sourcing_detail_view,
        name="vendor_sourcing_detail",
    ),
    path(
        "sourcing/<uuid:event_id>/participate/",
        views.vendor_sourcing_participate_view,
        name="vendor_sourcing_participate",
    ),
    # My Bids & Preparation
    path("bids/", views.vendor_bids_list_view, name="vendor_bids_list"),
    path("bids/create/<uuid:event_id>/", views.vendor_bid_create_view, name="vendor_bid_create"),
    path("bids/<uuid:bid_id>/", views.vendor_bid_detail_view, name="vendor_bid_detail"),
    path(
        "bids/<uuid:bid_id>/validate/", views.vendor_bid_validate_view, name="vendor_bid_validate"
    ),
    path("bids/<uuid:bid_id>/amend/", views.vendor_bid_amend_view, name="vendor_bid_amend"),
    path(
        "bids/<uuid:bid_id>/withdraw/",
        views.vendor_bid_withdraw_view,
        name="vendor_bid_withdraw",
    ),
    # Clarifications
    path("clarifications/", views.vendor_clarifications_view, name="vendor_clarifications"),
    # Purchase Orders & Acknowledgement
    path(
        "purchase-orders/",
        views.vendor_purchase_orders_list_view,
        name="vendor_purchase_orders_list",
    ),
    path(
        "purchase-orders/<uuid:po_id>/",
        views.vendor_purchase_order_detail_view,
        name="vendor_purchase_order_detail",
    ),
    path(
        "purchase-orders/<uuid:po_id>/acknowledge/",
        views.vendor_purchase_order_acknowledge_view,
        name="vendor_purchase_order_acknowledge",
    ),
    # Supplier Performance
    path("performance/", views.vendor_performance_view, name="vendor_performance"),
    # Invoices & Payments
    path("invoices/", views.vendor_invoices_view, name="vendor_invoices"),
    path(
        "purchase-orders/<uuid:po_id>/submit-invoice/",
        views.vendor_invoice_submit_view,
        name="vendor_invoice_submit",
    ),
    path("contracts/", views.vendor_contracts_view, name="vendor_contracts"),
    # Documents & Secure Download
    path("documents/", views.vendor_documents_view, name="vendor_documents"),
    path(
        "documents/download/<str:doc_type>/<uuid:doc_id>/",
        views.vendor_document_download_view,
        name="vendor_document_download",
    ),
    # Notifications
    path("notifications/", views.vendor_notifications_view, name="vendor_notifications"),
    path(
        "notifications/<uuid:notif_id>/read/",
        views.vendor_notification_mark_read_view,
        name="vendor_notification_mark_read",
    ),
    path(
        "notifications/mark-all-read/",
        views.vendor_notifications_mark_all_read_view,
        name="vendor_notifications_mark_all_read",
    ),
    # Reports & History
    path("reports/", views.vendor_reports_history_view, name="vendor_reports_history"),
    # Account & Security
    path("account/", views.vendor_account_security_view, name="vendor_account_security"),
]

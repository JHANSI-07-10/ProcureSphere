from django.urls import path

from .views import (
    receipt_create_view,
    receipt_dashboard_view,
    receipt_delivery_performance_report_view,
    receipt_detail_view,
    receipt_inspect_view,
    receipt_inspection_queue_view,
    receipt_line_correct_view,
    receipt_rejections_queue_view,
    receipt_stock_handoff_queue_view,
    receipt_stock_handoff_view,
    receipts_list_view,
    record_vendor_return_view,
)

urlpatterns = [
    path("receipts/", receipts_list_view, name="receipts_list"),
    path("receipts/dashboard/", receipt_dashboard_view, name="receipt_dashboard"),
    path("dashboard/", receipt_dashboard_view, name="receipts_dashboard"),
    path(
        "receipts/reports/delivery-performance/",
        receipt_delivery_performance_report_view,
        name="receipt_delivery_performance_report",
    ),
    path(
        "reports/delivery-performance/",
        receipt_delivery_performance_report_view,
        name="receipts_delivery_performance_report",
    ),
    path(
        "receipts/inspections/",
        receipt_inspection_queue_view,
        name="receipts_inspection_queue",
    ),
    path(
        "receipts/handoffs/",
        receipt_stock_handoff_queue_view,
        name="receipts_stock_handoff_queue",
    ),
    path(
        "receipts/rejections/",
        receipt_rejections_queue_view,
        name="receipts_rejections_queue",
    ),
    path(
        "receipts/returns/",
        receipt_rejections_queue_view,
        name="receipts_returns_queue",
    ),
    path("receipts/create/<uuid:po_id>/", receipt_create_view, name="receipt_create"),
    path("create/<uuid:po_id>/", receipt_create_view, name="receipt_create_alt"),
    path("receipts/<uuid:grn_id>/", receipt_detail_view, name="receipt_detail"),
    path("receipts/<uuid:grn_id>/inspect/", receipt_inspect_view, name="receipt_inspect"),
    path(
        "receipts/<uuid:grn_id>/handoff/",
        receipt_stock_handoff_view,
        name="receipt_stock_handoff",
    ),
    path(
        "receipts/rejections/<uuid:rejection_id>/return/",
        record_vendor_return_view,
        name="record_vendor_return",
    ),
    path(
        "receipts/lines/<uuid:line_id>/correct/",
        receipt_line_correct_view,
        name="receipt_line_correct",
    ),
]

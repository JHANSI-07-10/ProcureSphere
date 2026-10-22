from django.urls import path

from . import views

urlpatterns = [
    path("purchase-orders/", views.list_view, name="orders_list"),
    path(
        "purchase-orders/htmx/awards-pending/",
        views.awards_pending_po_tab_view,
        name="orders_awards_pending_tab",
    ),
    path(
        "purchase-orders/htmx/generate-from-award/<uuid:event_id>/",
        views.po_generate_from_award_htmx_view,
        name="orders_generate_from_award",
    ),
    path("purchase-orders/<uuid:pk>/", views.detail_view, name="order_detail"),
    path(
        "purchase-orders/<uuid:pk>/htmx/amend/",
        views.po_amend_modal_view,
        name="orders_amend_modal",
    ),
    path(
        "purchase-orders/<uuid:pk>/htmx/amendments/",
        views.po_amendments_partial_view,
        name="orders_amendments_partial",
    ),
    path(
        "purchase-orders/<uuid:pk>/approve/",
        views.po_approve_action_view,
        name="order_approve",
    ),
    path(
        "purchase-orders/<uuid:pk>/submit-approval/",
        views.po_submit_approval_action_view,
        name="order_submit_approval",
    ),
    path("purchase-orders/create/", views.po_create_view, name="orders_create"),
    path("orders/create/", views.po_create_view, name="orders_create_alias"),
    path("orders/", views.list_view, name="orders_list_alias"),
    path("orders/<uuid:pk>/", views.detail_view, name="order_detail_alias"),
]

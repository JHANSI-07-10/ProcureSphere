from django.urls import path

from . import views

urlpatterns = [
    path("requisitions/", views.list_view, name="requisitions_list"),
    path("requisitions/create/", views.create_view, name="requisition_create"),
    path("requisitions/<uuid:pk>/", views.detail_view, name="requisition_detail"),
    path("requisitions/<uuid:pk>/edit/", views.edit_view, name="requisition_edit"),
    path("requisitions/<uuid:pk>/submit/", views.submit_view, name="requisition_submit"),
    path("requisitions/<uuid:pk>/cancel/", views.cancel_view, name="requisition_cancel"),
    path("requisitions/<uuid:pk>/approve/", views.approve_view, name="requisition_approve"),
    path("requisitions/<uuid:pk>/reject/", views.reject_view, name="requisition_reject"),
    path(
        "requisitions/<uuid:pk>/attachments/<uuid:attachment_id>/",
        views.attachment_download_view,
        name="requisition_attachment_download",
    ),
    # Alt names from dev
    path("requisitions/create/", views.create_view, name="requisition_create_alt"),
    path("requisitions/<uuid:pk>/", views.detail_view, name="requisition_detail_alt"),
    path("requisitions/<uuid:pk>/edit/", views.edit_view, name="requisition_edit_alt"),
    path("requisitions/<uuid:pk>/submit/", views.submit_view, name="requisition_submit_alt"),
    path("requisitions/<uuid:pk>/cancel/", views.cancel_view, name="requisition_cancel_alt"),
    path("requisitions/<uuid:pk>/approve/", views.approve_view, name="requisition_approve_alt"),
    path("requisitions/<uuid:pk>/reject/", views.reject_view, name="requisition_reject_alt"),
    path(
        "requisitions/<uuid:pk>/attachments/<uuid:attachment_id>/download/",
        views.attachment_download_view,
        name="requisition_attachment_download_alt",
    ),
    # Direct alias fallbacks
    path("", views.list_view, name="requisitions_list_root"),
    path("create/", views.create_view, name="requisition_create_alias"),
    path("<uuid:pk>/", views.detail_view, name="requisition_detail_alias"),
    path("<uuid:pk>/edit/", views.edit_view, name="requisition_edit_alias"),
    path("<uuid:pk>/submit/", views.submit_view, name="requisition_submit_alias"),
    path("<uuid:pk>/cancel/", views.cancel_view, name="requisition_cancel_alias"),
    path("<uuid:pk>/approve/", views.approve_view, name="requisition_approve_alias"),
    path("<uuid:pk>/reject/", views.reject_view, name="requisition_reject_alias"),
    path(
        "<uuid:pk>/attachments/<uuid:attachment_id>/",
        views.attachment_download_view,
        name="requisition_attachment_download_alias",
    ),
]

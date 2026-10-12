from django.urls import path

from .views import approvals_inbox_view

urlpatterns = [
    path("approvals/inbox/", approvals_inbox_view, name="approvals_inbox"),
    path("approvals/", approvals_inbox_view, name="approvals_inbox_root"),
    path("inbox/", approvals_inbox_view, name="approvals_inbox_alias"),
]

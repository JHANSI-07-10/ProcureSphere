from django.contrib import admin
from django.db.models import Q

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "notification_type", "title", "is_read", "created_at")
    list_filter = ("notification_type", "is_read")
    search_fields = ("recipient__email", "title", "message")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            org = request.user.effective_organization
            return qs.filter(Q(recipient__organization=org) | Q(recipient__department__organization=org))
        return qs


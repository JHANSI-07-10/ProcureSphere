from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from .models import Notification

HIGH_PRIORITY_TYPES = [
    Notification.TYPE_APPROVAL_REQUIRED,
    Notification.TYPE_EXCEPTION_RAISED,
    Notification.TYPE_BID_DEADLINE,
    Notification.TYPE_KYC_REQUEST,
    Notification.TYPE_CONTRACT_EXPIRATION,
    Notification.TYPE_CONTRACT_MILESTONE,
    Notification.TYPE_CONTRACT_OBLIGATION,
]


class NotificationSerializer(serializers.ModelSerializer):
    recipient = serializers.PrimaryKeyRelatedField(
        queryset=Notification._meta.get_field("recipient").remote_field.model.objects.all(),
        required=False,
        allow_null=True,
    )
    recipient_email = serializers.CharField(source="recipient.email", read_only=True)
    notification_type_display = serializers.CharField(
        source="get_notification_type_display", read_only=True
    )
    is_high_priority = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "recipient",
            "recipient_email",
            "notification_type",
            "notification_type_display",
            "title",
            "message",
            "is_read",
            "target_url",
            "is_high_priority",
            "created_at",
            "updated_at",
        ]

    def to_internal_value(self, data):
        data_copy = data.copy() if hasattr(data, "copy") else dict(data)
        if "type" in data_copy and "notification_type" not in data_copy:
            data_copy["notification_type"] = data_copy["type"]
        if "recipient" in data_copy and str(data_copy["recipient"]).lower() == "self":
            request = self.context.get("request")
            if request and request.user:
                data_copy["recipient"] = request.user.pk
        return super().to_internal_value(data_copy)

    def get_is_high_priority(self, obj):
        return obj.notification_type in HIGH_PRIORITY_TYPES


class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.select_related("recipient").all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["title", "message"]
    ordering_fields = ["created_at", "is_read", "notification_type"]

    def perform_create(self, serializer):
        recipient = serializer.validated_data.get("recipient")
        if recipient and recipient != self.request.user:
            raise PermissionDenied(
                "Permission Denied: Users cannot push notifications to other recipients."
            )
        serializer.save(recipient=recipient or self.request.user)

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset.filter(recipient=user).order_by("-created_at")

        # Filter by read status
        is_read_param = self.request.query_params.get("is_read", "").strip().lower()
        if is_read_param in ["true", "1", "yes"]:
            qs = qs.filter(is_read=True)
        elif is_read_param in ["false", "0", "no"]:
            qs = qs.filter(is_read=False)

        # Filter by notification type
        type_param = self.request.query_params.get("type") or self.request.query_params.get(
            "notification_type"
        )
        if type_param:
            qs = qs.filter(notification_type=type_param.strip().upper())

        # Filter by priority
        priority_param = self.request.query_params.get("priority", "").strip().upper()
        if priority_param == "HIGH":
            qs = qs.filter(notification_type__in=HIGH_PRIORITY_TYPES)
        elif priority_param == "NORMAL":
            qs = qs.exclude(notification_type__in=HIGH_PRIORITY_TYPES)

        # Filter by search query
        search_query = self.request.query_params.get("search", "").strip()
        if search_query:
            qs = qs.filter(Q(title__icontains=search_query) | Q(message__icontains=search_query))

        # Sort
        sort_by = self.request.query_params.get("sort_by", "").strip()
        if sort_by in ["created_at", "-created_at", "is_read", "-is_read"]:
            qs = qs.order_by(sort_by)

        return qs

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        GET /api/v1/notifications/summary/
        Returns live KPI counters for the user's notification inbox.
        """
        user = request.user
        base_qs = Notification.objects.filter(recipient=user)

        total_count = base_qs.count()
        unread_count = base_qs.filter(is_read=False).count()
        read_count = total_count - unread_count
        high_priority_count = base_qs.filter(
            notification_type__in=HIGH_PRIORITY_TYPES, is_read=False
        ).count()
        actionable_count = base_qs.exclude(target_url="").count()

        type_counts = dict(base_qs.values_list("notification_type").annotate(c=Count("id")))

        return Response(
            {
                "total_count": total_count,
                "unread_count": unread_count,
                "read_count": read_count,
                "high_priority_count": high_priority_count,
                "actionable_count": actionable_count,
                "type_distribution": type_counts,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        """
        POST /api/v1/notifications/<uuid:pk>/mark-read/
        Marks a specific notification as read.
        """
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read", "updated_at"])
        return Response(
            {
                "message": "Notification marked as read.",
                "id": str(notification.id),
                "is_read": True,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="mark-unread")
    def mark_unread(self, request, pk=None):
        """
        POST /api/v1/notifications/<uuid:pk>/mark-unread/
        Marks a specific notification as unread.
        """
        notification = self.get_object()
        notification.is_read = False
        notification.save(update_fields=["is_read", "updated_at"])
        return Response(
            {
                "message": "Notification marked as unread.",
                "id": str(notification.id),
                "is_read": False,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        """
        POST /api/v1/notifications/mark-all-read/
        Marks all notifications for the authenticated user as read.
        """
        updated_count = Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, updated_at=timezone.now()
        )
        return Response(
            {"message": f"Successfully marked {updated_count} notification(s) as read."},
            status=status.HTTP_200_OK,
        )


router = DefaultRouter()
router.register(r"", NotificationViewSet, basename="notification")

urlpatterns = router.urls

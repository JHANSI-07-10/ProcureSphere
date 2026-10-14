import django_filters
from django.apps import apps
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.accounts.permissions import IsAuditorReadOnly, IsNotVendorUser

from .models import ApprovalAction, ApprovalPolicy
from .services import process_approval_action_service, record_approval_action_service


class CanManageApprovalPolicy(BasePermission):
    """
    Super Admin and Procurement Manager can manage approval policies.
    Internal staff can view them.
    External vendor users and requesters/auditors cannot mutate policies.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        ):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.role_code in [
            Role.SUPER_ADMIN,
            Role.PROC_MGR,
        ]


class ApprovalPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalPolicy
        fields = "__all__"


class ApprovalActionSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True)

    class Meta:
        model = ApprovalAction
        fields = "__all__"


class ApprovalActionFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(field_name="action", lookup_expr="iexact")
    action = django_filters.CharFilter(lookup_expr="iexact")
    target_model = django_filters.CharFilter(
        field_name="target_model_name", lookup_expr="icontains"
    )

    class Meta:
        model = ApprovalAction
        fields = ["status", "action", "target_model_name"]


class ApprovalPolicyViewSet(viewsets.ModelViewSet):
    queryset = ApprovalPolicy.objects.all().order_by("name")
    serializer_class = ApprovalPolicySerializer
    permission_classes = [IsAuthenticated, CanManageApprovalPolicy]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "module"]
    ordering_fields = ["name", "min_amount", "created_at"]


class ApprovalActionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ApprovalAction.objects.select_related("actor", "policy_step").order_by("-created_at")
    serializer_class = ApprovalActionSerializer
    permission_classes = [IsAuthenticated, IsNotVendorUser, IsAuditorReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = ApprovalActionFilter
    search_fields = ["target_model_name", "comments", "actor__email"]
    ordering_fields = ["created_at", "action"]

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        action_obj = self.get_object()
        comment = request.data.get("comment", "")
        target_obj = None
        for model in apps.get_models():
            if model.__name__.lower() == action_obj.target_model_name.lower():
                target_obj = model.objects.filter(id=action_obj.target_object_id).first()
                break

        if target_obj:
            new_action = process_approval_action_service(
                target_object=target_obj,
                actor=request.user,
                action=ApprovalAction.ACTION_APPROVE,
                comments=comment,
            )
        else:
            new_action = record_approval_action_service(
                target_object_id=action_obj.target_object_id,
                target_model_name=action_obj.target_model_name,
                actor=request.user,
                action=ApprovalAction.ACTION_APPROVE,
                previous_state=action_obj.new_state,
                new_state="APPROVED",
                comments=comment,
            )

        if isinstance(new_action, ApprovalAction):
            res_data = ApprovalActionSerializer(new_action).data
        else:
            res_data = {
                "id": str(getattr(new_action, "id", "")),
                "status": "APPROVED",
                "comments": comment,
            }

        return Response(res_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        action_obj = self.get_object()
        reason = request.data.get("reason", request.data.get("comment", ""))
        target_obj = None
        for model in apps.get_models():
            if model.__name__.lower() == action_obj.target_model_name.lower():
                target_obj = model.objects.filter(id=action_obj.target_object_id).first()
                break

        if target_obj:
            new_action = process_approval_action_service(
                target_object=target_obj,
                actor=request.user,
                action=ApprovalAction.ACTION_REJECT,
                comments=reason,
            )
        else:
            new_action = record_approval_action_service(
                target_object_id=action_obj.target_object_id,
                target_model_name=action_obj.target_model_name,
                actor=request.user,
                action=ApprovalAction.ACTION_REJECT,
                previous_state=action_obj.new_state,
                new_state="REJECTED",
                comments=reason,
            )

        if isinstance(new_action, ApprovalAction):
            res_data = ApprovalActionSerializer(new_action).data
        else:
            res_data = {
                "id": str(getattr(new_action, "id", "")),
                "status": "REJECTED",
                "comments": reason,
            }

        return Response(res_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="comment")
    def comment(self, request, pk=None):
        action_obj = self.get_object()
        comment_text = request.data.get("comment", "")
        if comment_text:
            if action_obj.comments:
                action_obj.comments = f"{action_obj.comments}\n{comment_text}"
            else:
                action_obj.comments = comment_text
            action_obj.save()
        return Response(ApprovalActionSerializer(action_obj).data, status=status.HTTP_200_OK)


router = DefaultRouter()
router.register(r"policies", ApprovalPolicyViewSet, basename="approval-policy")
router.register(r"actions", ApprovalActionViewSet, basename="approval-action")

urlpatterns = router.urls

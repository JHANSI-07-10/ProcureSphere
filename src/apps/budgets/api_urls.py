from decimal import Decimal

from rest_framework import serializers, viewsets
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.accounts.permissions import IsNotVendorUser

from .models import Budget, SpendLedger


class CanManageBudget(BasePermission):
    """
    Budget Register Access:
    - Vendor users: BLOCKED completely (read & write -> 403).
    - Internal users: Can view budgets (GET, HEAD, OPTIONS).
    - Requesters & Auditors: Strictly read-only (mutations -> 403).
    - Finance / AP & Super Admin: Full management (create, update, delete).
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Vendor users have zero access to budget register
        if (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        ):
            return False

        if request.method in SAFE_METHODS:
            return True

        if getattr(request.user, "role_code", None) in [Role.AUDITOR, Role.REQUESTER]:
            return False

        return request.user.is_superuser or request.user.role_code in [
            Role.SUPER_ADMIN,
            Role.FINANCE_AP,
        ]


class BudgetSerializer(serializers.ModelSerializer):
    available_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    reserved_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    committed_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    actual_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cost_center_code = serializers.CharField(source="cost_center.code", read_only=True)

    class Meta:
        model = Budget
        fields = "__all__"

    def validate_allocated_amount(self, value):
        if value is not None and value < Decimal("0.00"):
            raise serializers.ValidationError("Allocated amount must be non-negative.")
        return value


class SpendLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpendLedger
        fields = "__all__"


class BudgetViewSet(viewsets.ModelViewSet):
    queryset = Budget.objects.select_related("cost_center", "fiscal_period").all()
    serializer_class = BudgetSerializer
    permission_classes = [IsAuthenticated, CanManageBudget]


class SpendLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SpendLedger.objects.select_related("budget").order_by("-created_at")
    serializer_class = SpendLedgerSerializer
    permission_classes = [IsAuthenticated, IsNotVendorUser]


router = DefaultRouter()
router.register(r"ledger", SpendLedgerViewSet, basename="spend-ledger")
router.register(r"", BudgetViewSet, basename="budget")

urlpatterns = router.urls

from rest_framework import serializers, viewsets
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role

from .models import CostCenter, Department, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = "__all__"


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = "__all__"


class CostCenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostCenter
        fields = "__all__"


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        ):
            return False
        if (
            request.user.is_superuser
            or getattr(request.user, "role_code", None) == Role.SUPER_ADMIN
        ):
            return True
        return request.method in SAFE_METHODS


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAdminOrReadOnly]


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminOrReadOnly]


class CostCenterViewSet(viewsets.ModelViewSet):
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer
    permission_classes = [IsAdminOrReadOnly]


router = DefaultRouter()
router.register(r"entities", OrganizationViewSet, basename="organization")
router.register(r"departments", DepartmentViewSet, basename="department")
router.register(r"cost-centers", CostCenterViewSet, basename="cost-center")

urlpatterns = router.urls

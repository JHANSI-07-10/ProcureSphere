from decimal import Decimal

from django.db.models import Avg, Count, Q
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.vendors.models import Vendor

from .models import VendorScorecard
from .services import calculate_vendor_scorecard_service


class CanManageScorecards(permissions.BasePermission):
    """
    Scorecard creation and calculations are restricted to Procurement Executives, Managers, and Super Admins.
    Vendors, Requesters, and Auditors cannot score suppliers or create scorecards.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.is_superuser:
            return True
        role = getattr(request.user, "role_code", None)
        return role in [Role.PROC_EXEC, Role.PROC_MGR, Role.SUPER_ADMIN]


class VendorScorecardSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)
    vendor_number = serializers.CharField(source="vendor.vendor_number", read_only=True)
    vendor_category = serializers.CharField(source="vendor.category.name", read_only=True)
    vendor_category_code = serializers.CharField(source="vendor.category.code", read_only=True)
    vendor_status = serializers.CharField(source="vendor.status", read_only=True)
    evaluated_by_email = serializers.CharField(source="evaluated_by.email", read_only=True)
    performance_status = serializers.SerializerMethodField()

    class Meta:
        model = VendorScorecard
        fields = [
            "id",
            "vendor",
            "vendor_name",
            "vendor_number",
            "vendor_category",
            "vendor_category_code",
            "vendor_status",
            "evaluation_period",
            "delivery_score",
            "quality_score",
            "price_score",
            "compliance_score",
            "composite_score",
            "performance_status",
            "evaluator_comments",
            "evaluated_by",
            "evaluated_by_email",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        score_fields = [
            "delivery_score",
            "quality_score",
            "price_score",
            "compliance_score",
            "responsiveness_score",
            "sla_score",
            "composite_score",
        ]
        for field in score_fields:
            val = attrs.get(field)
            if val is not None:
                if val < Decimal("0.00") or val > Decimal("100.00"):
                    raise serializers.ValidationError(
                        {field: f"{field} must be between 0.00 and 100.00."}
                    )
        return attrs

    def get_performance_status(self, obj):
        score = float(obj.composite_score or 0)
        if score >= 85.0:
            return "EXCELLENT"
        elif score >= 70.0:
            return "SATISFACTORY"
        return "ATTENTION_REQUIRED"


class VendorScorecardViewSet(viewsets.ModelViewSet):
    queryset = VendorScorecard.objects.select_related(
        "vendor", "vendor__category", "evaluated_by"
    ).order_by("-created_at")
    serializer_class = VendorScorecardSerializer
    permission_classes = [IsAuthenticated, CanManageScorecards]
    search_fields = ["vendor__legal_name", "vendor__vendor_number", "evaluation_period"]
    ordering_fields = ["composite_score", "delivery_score", "quality_score", "created_at"]

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset

        # Vendor user scoping - vendors only see their own scorecards
        if (
            getattr(user, "is_vendor", False)
            or getattr(user, "role_code", None) == Role.VENDOR_USER
        ):
            if getattr(user, "vendor_id", None):
                return qs.filter(vendor_id=user.vendor_id)
            return qs.none()

        vendor_id = self.request.query_params.get("vendor_id")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)

        period = self.request.query_params.get("period")
        if period:
            qs = qs.filter(evaluation_period=period)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(vendor__legal_name__icontains=search)
                | Q(vendor__vendor_number__icontains=search)
                | Q(evaluation_period__icontains=search)
            )

        min_score = self.request.query_params.get("min_score")
        if min_score:
            try:
                qs = qs.filter(composite_score__gte=Decimal(str(min_score)))
            except Exception:
                pass

        max_score = self.request.query_params.get("max_score")
        if max_score:
            try:
                qs = qs.filter(composite_score__lte=Decimal(str(max_score)))
            except Exception:
                pass

        sort_by = self.request.query_params.get("sort_by", "").strip()
        allowed_sorts = [
            "created_at",
            "-created_at",
            "composite_score",
            "-composite_score",
            "quality_score",
            "-quality_score",
            "delivery_score",
            "-delivery_score",
            "price_score",
            "-price_score",
            "compliance_score",
            "-compliance_score",
            "vendor__legal_name",
            "-vendor__legal_name",
        ]
        if sort_by in allowed_sorts:
            qs = qs.order_by(sort_by)

        return qs

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        GET /api/v1/scorecards/summary/
        Returns live KPI aggregations for supplier performance scorecards.
        """
        qs = self.get_queryset()

        total_evaluated = qs.count()
        distinct_vendors = qs.values("vendor_id").distinct().count()

        avg_data = qs.aggregate(
            avg_composite=Avg("composite_score"),
            avg_quality=Avg("quality_score"),
            avg_delivery=Avg("delivery_score"),
            avg_price=Avg("price_score"),
            avg_compliance=Avg("compliance_score"),
        )

        top_performing = qs.filter(composite_score__gte=Decimal("85.00")).count()
        satisfactory = qs.filter(
            composite_score__gte=Decimal("70.00"), composite_score__lt=Decimal("85.00")
        ).count()
        attention_required = qs.filter(composite_score__lt=Decimal("70.00")).count()

        period_distribution = dict(
            qs.values_list("evaluation_period")
            .annotate(count=Count("id"))
            .order_by("-evaluation_period")[:6]
        )

        return Response(
            {
                "total_scorecards": total_evaluated,
                "distinct_vendors_evaluated": distinct_vendors,
                "average_composite_score": round(float(avg_data["avg_composite"] or 0), 1),
                "average_quality_score": round(float(avg_data["avg_quality"] or 0), 1),
                "average_delivery_score": round(float(avg_data["avg_delivery"] or 0), 1),
                "average_price_score": round(float(avg_data["avg_price"] or 0), 1),
                "average_compliance_score": round(float(avg_data["avg_compliance"] or 0), 1),
                "top_performing_count": top_performing,
                "satisfactory_count": satisfactory,
                "attention_required_count": attention_required,
                "period_distribution": period_distribution,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="calculate")
    def calculate(self, request):
        vendor_id = request.data.get("vendor_id")
        period = request.data.get("period", "Q1-2026")
        comments = request.data.get("comments", "")

        if not vendor_id:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "vendor_id is required.",
                        "details": {"vendor_id": ["This field is required."]},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            vendor = Vendor.objects.get(pk=vendor_id)
        except (Vendor.DoesNotExist, Exception):
            return Response(
                {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "Vendor not found.",
                        "details": {"vendor_id": [f"Vendor {vendor_id} does not exist."]},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        scorecard = calculate_vendor_scorecard_service(
            vendor=vendor,
            evaluation_period=period,
            evaluated_by_user=request.user,
            comments=comments,
        )
        return Response(VendorScorecardSerializer(scorecard).data, status=status.HTTP_201_CREATED)


router = DefaultRouter()
router.register(r"", VendorScorecardViewSet, basename="scorecard")

urlpatterns = router.urls

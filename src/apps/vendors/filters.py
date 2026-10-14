import django_filters
from django.db.models import OuterRef, Q, Subquery

from apps.scorecards.models import VendorScorecard

from .models import Vendor, VendorCategory, VendorRiskRecord

HOLD_SUSPENSION_CHOICES = [
    ("ON_HOLD", "On hold"),
    ("SUSPENDED", "Suspended / blacklisted"),
    ("ANY", "On hold or suspended"),
    ("NONE", "Not restricted"),
]

PERFORMANCE_CHOICES = [
    ("GOOD", "Good (>= 85)"),
    ("WATCH", "Watch (70 - 84.99)"),
    ("ISSUE", "Performance issue (< 70)"),
    ("NO_DATA", "No performance data"),
]

# Composite score below which a vendor is flagged as having performance issues
# (matches the existing scorecard colour bands used across the UI).
PERFORMANCE_ISSUE_THRESHOLD = 70
PERFORMANCE_GOOD_THRESHOLD = 85

APPROVAL_STAGE_STATUSES = [
    Vendor.STATUS_DRAFT,
    Vendor.STATUS_SUBMITTED,
    Vendor.STATUS_KYC_REVIEW,
    Vendor.STATUS_CHANGES_REQUIRED,
    Vendor.STATUS_APPROVED,
    Vendor.STATUS_REJECTED,
]
OPERATIONAL_STATUSES = [Vendor.STATUS_ACTIVE, Vendor.STATUS_ON_HOLD, Vendor.STATUS_SUSPENDED]


def annotate_governance(queryset):
    """Adds latest risk level and latest composite score as annotations (no N+1)."""
    latest_risk = VendorRiskRecord.objects.filter(vendor=OuterRef("pk")).order_by("-created_at")
    latest_score = VendorScorecard.objects.filter(vendor=OuterRef("pk")).order_by("-created_at")
    return queryset.annotate(
        latest_risk_level=Subquery(latest_risk.values("risk_level")[:1]),
        latest_composite_score=Subquery(latest_score.values("composite_score")[:1]),
    )


class VendorFilter(django_filters.FilterSet):
    """
    Server-side vendor search/filter shared by the governance page and the vendor API.
    Expects a queryset annotated with annotate_governance().
    """

    q = django_filters.CharFilter(method="filter_search", label="Search")
    category = django_filters.ModelChoiceFilter(queryset=VendorCategory.objects.order_by("name"))
    status = django_filters.ChoiceFilter(choices=Vendor.STATUS_CHOICES)
    approval_status = django_filters.ChoiceFilter(
        choices=[c for c in Vendor.STATUS_CHOICES if c[0] in APPROVAL_STAGE_STATUSES],
        field_name="status",
        label="Approval status",
    )
    operational_status = django_filters.ChoiceFilter(
        choices=[c for c in Vendor.STATUS_CHOICES if c[0] in OPERATIONAL_STATUSES],
        field_name="status",
        label="Operational status",
    )
    risk_level = django_filters.ChoiceFilter(
        choices=VendorRiskRecord.RISK_CHOICES + [("NONE", "Not assessed")],
        method="filter_risk",
        label="Risk level",
    )
    restriction = django_filters.ChoiceFilter(
        choices=HOLD_SUSPENSION_CHOICES, method="filter_restriction", label="Hold / suspension"
    )
    performance = django_filters.ChoiceFilter(
        choices=PERFORMANCE_CHOICES, method="filter_performance", label="Performance"
    )

    class Meta:
        model = Vendor
        fields = []

    def filter_search(self, queryset, name, value):
        value = value.strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(vendor_number__icontains=value)
            | Q(legal_name__icontains=value)
            | Q(trade_name__icontains=value)
            | Q(tax_identification_number__icontains=value)
            | Q(category__name__icontains=value)
            | Q(category__code__icontains=value)
        )

    def filter_risk(self, queryset, name, value):
        if value == "NONE":
            return queryset.filter(latest_risk_level__isnull=True)
        return queryset.filter(latest_risk_level=value)

    def filter_restriction(self, queryset, name, value):
        if value == "ANY":
            return queryset.filter(status__in=[Vendor.STATUS_ON_HOLD, Vendor.STATUS_SUSPENDED])
        if value == "NONE":
            return queryset.exclude(status__in=[Vendor.STATUS_ON_HOLD, Vendor.STATUS_SUSPENDED])
        return queryset.filter(status=value)

    def filter_performance(self, queryset, name, value):
        if value == "NO_DATA":
            return queryset.filter(latest_composite_score__isnull=True)
        if value == "GOOD":
            return queryset.filter(latest_composite_score__gte=PERFORMANCE_GOOD_THRESHOLD)
        if value == "WATCH":
            return queryset.filter(
                latest_composite_score__gte=PERFORMANCE_ISSUE_THRESHOLD,
                latest_composite_score__lt=PERFORMANCE_GOOD_THRESHOLD,
            )
        return queryset.filter(latest_composite_score__lt=PERFORMANCE_ISSUE_THRESHOLD)

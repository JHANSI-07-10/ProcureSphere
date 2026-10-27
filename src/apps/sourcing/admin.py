from django.contrib import admin

from .models import (
    AwardDecision,
    BidAttachment,
    BidEvaluation,
    BidInvite,
    BidLine,
    BidVersion,
    Clarification,
    SourcingEvent,
    VendorBid,
)


class BidInviteInline(admin.TabularInline):
    model = BidInvite
    extra = 0


@admin.register(SourcingEvent)
class SourcingEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_number",
        "title",
        "event_type",
        "requisition",
        "status",
        "bid_start_date",
        "bid_end_date",
        "is_sealed",
    )
    list_filter = ("event_type", "status", "is_sealed")
    search_fields = ("event_number", "title", "description")
    inlines = [BidInviteInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(requisition__department__organization=request.user.effective_organization)
        return qs


class BidLineInline(admin.TabularInline):
    model = BidLine
    extra = 0


class BidVersionInline(admin.TabularInline):
    model = BidVersion
    extra = 0
    readonly_fields = (
        "version_number",
        "total_bid_amount",
        "amendment_reason",
        "created_at",
    )


class BidAttachmentInline(admin.TabularInline):
    model = BidAttachment
    extra = 0


@admin.register(VendorBid)
class VendorBidAdmin(admin.ModelAdmin):
    list_display = (
        "bid_number",
        "event",
        "vendor",
        "version",
        "status",
        "total_bid_amount",
        "submitted_at",
        "created_at",
    )
    list_filter = ("status", "version", "event")
    search_fields = (
        "bid_number",
        "event__event_number",
        "vendor__legal_name",
    )
    inlines = [BidLineInline, BidVersionInline, BidAttachmentInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(event__requisition__department__organization=request.user.effective_organization)
        return qs


@admin.register(Clarification)
class ClarificationAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "vendor",
        "status",
        "created_at",
        "answered_at",
    )
    list_filter = ("status",)
    search_fields = (
        "question",
        "answer",
        "event__event_number",
        "vendor__legal_name",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(event__requisition__department__organization=request.user.effective_organization)
        return qs


@admin.register(BidEvaluation)
class BidEvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "bid",
        "evaluator",
        "weighted_total_score",
        "created_at",
    )
    search_fields = ("event__event_number", "bid__bid_number")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(event__requisition__department__organization=request.user.effective_organization)
        return qs


@admin.register(AwardDecision)
class AwardDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "winning_bid",
        "approved_by",
        "created_at",
    )
    search_fields = (
        "event__event_number",
        "winning_bid__bid_number",
        "award_reason",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(event__requisition__department__organization=request.user.effective_organization)
        return qs

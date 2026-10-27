from django.contrib import admin

from apps.core.admin_site import RoleBasedModelAdmin, register_model
from apps.receipts.models import GoodsReceipt, InspectionRecord, ReceiptLine, RejectionRecord


class ReceiptLineInline(admin.TabularInline):
    model = ReceiptLine
    extra = 0


class GoodsReceiptAdmin(RoleBasedModelAdmin):
    list_display = ("grn_number", "po", "received_by", "received_date", "delivery_note_number")
    search_fields = ("grn_number", "po__po_number", "delivery_note_number")
    inlines = [ReceiptLineInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(po__cost_center__department__organization=request.user.effective_organization)
        return qs


class InspectionRecordAdmin(RoleBasedModelAdmin):
    list_display = ("receipt_line", "inspected_by", "passed", "created_at")
    list_filter = ("passed",)
    search_fields = ("inspection_notes", "inspected_by__email")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(receipt_line__goods_receipt__po__cost_center__department__organization=request.user.effective_organization)
        return qs


class RejectionRecordAdmin(RoleBasedModelAdmin):
    list_display = ("receipt_line", "rejected_quantity", "returned_to_vendor", "created_at")
    list_filter = ("returned_to_vendor",)
    search_fields = ("rejection_reason",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "effective_organization", None):
            return qs.filter(receipt_line__goods_receipt__po__cost_center__department__organization=request.user.effective_organization)
        return qs


register_model(GoodsReceipt, GoodsReceiptAdmin)
register_model(ReceiptLine)
register_model(InspectionRecord, InspectionRecordAdmin)
register_model(RejectionRecord, RejectionRecordAdmin)

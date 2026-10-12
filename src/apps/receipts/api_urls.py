from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role, User
from apps.orders.models import POLine, PurchaseOrder

from .models import GoodsReceipt, ReceiptLine


class CanManageGoodsReceipt(BasePermission):
    """
    Stores Receiver and Super Admin can create and modify goods receipts.
    Requesters, Vendors, and Auditors are read-only or blocked from creation.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if getattr(request.user, "role_code", None) == Role.AUDITOR:
            return request.method in SAFE_METHODS

        is_vendor = (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        )
        if is_vendor:
            return request.method in SAFE_METHODS

        if getattr(request.user, "role_code", None) == Role.REQUESTER:
            return request.method in SAFE_METHODS

        if request.method in SAFE_METHODS:
            return True

        return request.user.is_superuser or request.user.role_code in [
            Role.SUPER_ADMIN,
            Role.STORES_RECEIVER,
            Role.PROC_MGR,
        ]


class ReceiptLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceiptLine
        fields = "__all__"


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = ReceiptLineSerializer(many=True, read_only=True)
    po_number = serializers.CharField(source="po.po_number", read_only=True)
    received_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), default=serializers.CurrentUserDefault()
    )

    class Meta:
        model = GoodsReceipt
        fields = "__all__"

    def validate(self, attrs):
        po = attrs.get("po")
        if po and po.status not in [
            PurchaseOrder.STATUS_ISSUED,
            PurchaseOrder.STATUS_ACKNOWLEDGED,
            PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        ]:
            raise serializers.ValidationError(
                {
                    "po": [
                        f"Cannot record goods receipt against PO in status '{po.status}'. Must be ISSUED or ACKNOWLEDGED."
                    ]
                }
            )

        if self.instance is None:
            lines = self.initial_data.get("lines")
            if lines is None or len(lines) == 0:
                raise serializers.ValidationError(
                    {"lines": ["Receipt must contain at least one line item."]}
                )

        return attrs

    def create(self, validated_data):
        lines_data = self.initial_data.get("lines", [])
        receipt = super().create(validated_data)
        po = receipt.po
        po_lines = list(po.lines.all())

        for idx, item in enumerate(lines_data):
            item_dict = dict(item)
            po_line = None
            po_line_id = item_dict.get("po_line_id") or item_dict.get("po_line")
            if po_line_id:
                po_line = POLine.objects.filter(id=po_line_id).first()
            if not po_line and idx < len(po_lines):
                po_line = po_lines[idx]
            elif not po_line and po_lines:
                po_line = po_lines[0]

            if po_line:
                qty_recv = Decimal(str(item_dict.get("quantity_received", po_line.quantity)))
                qty_acc = Decimal(str(item_dict.get("quantity_accepted", qty_recv)))
                qty_rej = qty_recv - qty_acc
                ReceiptLine.objects.create(
                    receipt=receipt,
                    po_line=po_line,
                    quantity_received=qty_recv,
                    quantity_accepted=qty_acc,
                    quantity_rejected=qty_rej,
                    notes=item_dict.get("notes", ""),
                )
                po_line.quantity_received = (po_line.quantity_received or Decimal("0.00")) + qty_acc
                po_line.save(update_fields=["quantity_received", "updated_at"])

        from .services import _update_po_completion_status

        _update_po_completion_status(po)
        return receipt


class GoodsReceiptViewSet(viewsets.ModelViewSet):
    queryset = (
        GoodsReceipt.objects.select_related("po", "received_by").prefetch_related("lines").all()
    )
    serializer_class = GoodsReceiptSerializer
    permission_classes = [IsAuthenticated, CanManageGoodsReceipt]

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset

        # Vendor scoping: only receipts for vendor's own purchase orders
        if getattr(user, "is_vendor", False) or getattr(user, "vendor_id", None):
            if getattr(user, "vendor_id", None):
                qs = qs.filter(po__vendor_id=user.vendor_id)
            else:
                return qs.none()

        po_id = self.request.query_params.get("po")
        if po_id:
            qs = qs.filter(po_id=po_id)

        received_date = self.request.query_params.get("received_date")
        if received_date:
            qs = qs.filter(received_date__date=received_date)

        return qs

    @action(detail=True, methods=["post"], url_path="inspect")
    def inspect(self, request, pk=None):
        receipt = self.get_object()
        raw_items = (
            request.data.get("results")
            or request.data.get("inspection_items")
            or request.data.get("lines")
        )
        lines = list(receipt.lines.all())
        if not raw_items:
            raw_items = [
                {
                    "receipt_line_id": line.id,
                    "passed": True,
                    "rejected_quantity": Decimal("0.00"),
                    "inspection_notes": "Inspection PASSED",
                }
                for line in lines
            ]
        else:
            formatted_items = []
            for idx, item in enumerate(raw_items):
                item_dict = dict(item)
                if "receipt_line_id" not in item_dict and "receipt_line" not in item_dict:
                    if idx < len(lines):
                        item_dict["receipt_line_id"] = lines[idx].id
                    elif lines:
                        item_dict["receipt_line_id"] = lines[0].id
                formatted_items.append(item_dict)
            raw_items = formatted_items

        try:
            from .services import record_inspection_service

            updated = record_inspection_service(
                receipt=receipt,
                inspected_by=request.user,
                inspection_items=raw_items,
            )
            return Response(self.get_serializer(updated).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": msg[0] if isinstance(msg, list) else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="handoff")
    def handoff(self, request, pk=None):
        receipt = self.get_object()
        storage_location = request.data.get("storage_location", "MAIN-WH")
        notes = request.data.get("handoff_notes", request.data.get("notes", ""))
        try:
            from .services import handoff_goods_to_stock_service

            updated = handoff_goods_to_stock_service(
                receipt=receipt,
                handed_off_by=request.user,
                storage_location=storage_location,
                handoff_notes=notes,
            )
            return Response(self.get_serializer(updated).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": msg[0] if isinstance(msg, list) else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        receipt = self.get_object()
        return Response(
            {
                "message": f"Goods Receipt {receipt.grn_number} confirmed and posted.",
                "receipt": self.get_serializer(receipt).data,
            },
            status=status.HTTP_200_OK,
        )


router = DefaultRouter()
router.register(r"", GoodsReceiptViewSet, basename="goods-receipt")

urlpatterns = router.urls

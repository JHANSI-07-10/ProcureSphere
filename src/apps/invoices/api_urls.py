from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.orders.models import PurchaseOrder
from apps.vendors.models import Vendor

from .models import InvoiceLine, MatchException, SupplierInvoice
from .services import (
    create_supplier_invoice_service,
    mark_invoice_paid_service,
    resolve_match_exception_service,
    run_3_way_match_service,
)


class CanManageSupplierInvoice(BasePermission):
    """
    Enforces RBAC on supplier invoices:
    - Safe methods (GET, HEAD, OPTIONS): Authenticated users (vendor-scoped).
    - Auditors: Strictly read-only (SAFE_METHODS).
    - Vendors: Cannot pay invoices or resolve exceptions.
    - Payment: Restricted to FINANCE_AP and SUPER_ADMIN.
    - Write/Create: FINANCE_AP, PROC_MGR, PROC_EXEC, SUPER_ADMIN.
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

        if view.action in ["pay_invoice", "pay", "resolve_exception", "resolve"]:
            if is_vendor or getattr(request.user, "role_code", None) == Role.AUDITOR:
                return False
            user_role = getattr(request.user, "role_code", None) or (
                request.user.role.code if getattr(request.user, "role", None) else None
            )
            return request.user.is_superuser or user_role in [
                Role.SUPER_ADMIN,
                Role.FINANCE_AP,
            ]

        if is_vendor:
            return request.method in SAFE_METHODS

        if request.method in SAFE_METHODS:
            return True

        return request.user.is_superuser or request.user.role_code in [
            Role.SUPER_ADMIN,
            Role.FINANCE_AP,
            Role.PROC_MGR,
            Role.PROC_EXEC,
        ]


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = "__all__"

    def validate_quantity(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError(
                "Total Quantity Billed must be greater than zero. The count cannot be less than zero or negative."
            )
        return value

    def validate_unit_price(self, value):
        if value < Decimal("0.00"):
            raise serializers.ValidationError(
                "Unit price cannot be less than zero or negative."
            )
        return value


class MatchExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchException
        fields = "__all__"


class SupplierInvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    exceptions = MatchExceptionSerializer(many=True, read_only=True)
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)

    class Meta:
        model = SupplierInvoice
        fields = "__all__"
        read_only_fields = ["status", "created_at", "updated_at"]

    def validate(self, attrs):
        vendor = attrs.get("vendor", getattr(self.instance, "vendor", None))
        invoice_number = attrs.get("invoice_number", getattr(self.instance, "invoice_number", None))
        if vendor and invoice_number:
            qs = SupplierInvoice.objects.filter(vendor=vendor, invoice_number=invoice_number)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {
                        "invoice_number": [
                            f"Duplicate invoice detected: Invoice '{invoice_number}' already exists for vendor '{vendor.legal_name}'."
                        ]
                    }
                )

        po = attrs.get("po", getattr(self.instance, "po", None))
        if po and vendor and po.vendor_id != vendor.id:
            raise serializers.ValidationError(
                {
                    "po": [
                        f"Purchase Order {po.po_number} does not belong to vendor '{vendor.legal_name}'."
                    ]
                }
            )

        subtotal = attrs.get("subtotal")
        total_amount = attrs.get("total_amount")
        if subtotal is not None and subtotal < Decimal("0.00"):
            raise serializers.ValidationError({"subtotal": ["Subtotal cannot be negative."]})
        if total_amount is not None and total_amount < Decimal("0.00"):
            raise serializers.ValidationError(
                {"total_amount": ["Total amount cannot be negative."]}
            )
        if subtotal is not None and total_amount is not None and total_amount < subtotal:
            raise serializers.ValidationError(
                {"total_amount": ["Total amount must be greater than or equal to subtotal."]}
            )

        invoice_date = attrs.get("invoice_date", getattr(self.instance, "invoice_date", None))
        due_date = attrs.get("due_date", getattr(self.instance, "due_date", None))
        if invoice_date and due_date and due_date < invoice_date:
            raise serializers.ValidationError(
                {"due_date": ["Due date cannot be before invoice date."]}
            )

        return attrs


class SupplierInvoiceViewSet(viewsets.ModelViewSet):
    queryset = (
        SupplierInvoice.objects.select_related("vendor", "po")
        .prefetch_related("lines", "exceptions")
        .all()
    )
    serializer_class = SupplierInvoiceSerializer
    permission_classes = [IsAuthenticated, CanManageSupplierInvoice]

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset
        if getattr(user, "is_vendor", False) or getattr(user, "vendor_id", None):
            if getattr(user, "vendor_id", None):
                qs = qs.filter(vendor_id=user.vendor_id)
            else:
                return qs.none()

        status_param = self.request.query_params.get("status", "").strip().upper()
        if status_param:
            valid_statuses = [choice[0] for choice in SupplierInvoice.STATUS_CHOICES]
            if status_param not in valid_statuses:
                raise serializers.ValidationError(
                    {
                        "status": [
                            f"Invalid status '{status_param}'. Valid choices are: {', '.join(valid_statuses)}"
                        ]
                    }
                )
            qs = qs.filter(status=status_param)

        po_param = self.request.query_params.get("po")
        if po_param:
            qs = qs.filter(po_id=po_param)

        return qs

    @action(detail=False, methods=["post"], url_path="create-invoice")
    def create_invoice(self, request):
        vendor_id = request.data.get("vendor_id")
        po_id = request.data.get("po_id")
        invoice_number = request.data.get("invoice_number")
        invoice_date = request.data.get("invoice_date")
        due_date = request.data.get("due_date")
        line_items = request.data.get("line_items", [])
        notes = request.data.get("notes", "")

        if not vendor_id or not po_id or not invoice_number:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "vendor_id, po_id, and invoice_number are required.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            vendor = Vendor.objects.get(pk=vendor_id)
        except (Vendor.DoesNotExist, ValueError):
            return Response(
                {"error": {"code": "NOT_FOUND", "message": f"Vendor '{vendor_id}' not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            po = PurchaseOrder.objects.get(pk=po_id)
        except (PurchaseOrder.DoesNotExist, ValueError):
            return Response(
                {"error": {"code": "NOT_FOUND", "message": f"Purchase Order '{po_id}' not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            invoice = create_supplier_invoice_service(
                vendor=vendor,
                po=po,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                due_date=due_date,
                line_items=line_items,
                notes=notes,
                created_by_user=request.user,
            )
            return Response(SupplierInvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)
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

    @action(detail=True, methods=["post"], url_path="run-match")
    def run_match(self, request, pk=None):
        invoice = self.get_object()
        try:
            updated_invoice, exceptions = run_3_way_match_service(
                invoice=invoice, user=request.user
            )
            return Response(
                {
                    "invoice": SupplierInvoiceSerializer(updated_invoice).data,
                    "exceptions_count": len(exceptions),
                },
                status=status.HTTP_200_OK,
            )
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

    @action(detail=True, methods=["post"], url_path="pay")
    def pay_invoice(self, request, pk=None):
        invoice = self.get_object()
        try:
            paid_invoice = mark_invoice_paid_service(invoice=invoice, user=request.user)
            return Response(SupplierInvoiceSerializer(paid_invoice).data, status=status.HTTP_200_OK)
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


class MatchExceptionViewSet(viewsets.ModelViewSet):
    queryset = MatchException.objects.select_related("invoice", "resolved_by").all()
    serializer_class = MatchExceptionSerializer
    permission_classes = [IsAuthenticated, CanManageSupplierInvoice]

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve_exception(self, request, pk=None):
        exception = self.get_object()
        notes = request.data.get("resolution_notes", "Resolved by Finance")
        action_type = request.data.get("action", "RESOLVE")
        try:
            resolved = resolve_match_exception_service(
                match_exception=exception,
                resolved_by_user=request.user,
                resolution_notes=notes,
                action=action_type,
            )
            return Response(MatchExceptionSerializer(resolved).data, status=status.HTTP_200_OK)
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


router = DefaultRouter()
router.register(r"exceptions", MatchExceptionViewSet, basename="match-exception")
router.register(r"", SupplierInvoiceViewSet, basename="invoice")

urlpatterns = router.urls

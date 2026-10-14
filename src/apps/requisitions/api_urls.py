from decimal import Decimal

from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.audit.permissions import AuditorReadOnlyPermission

from .models import PRAttachment, PRLine, PurchaseRequisition


class PRLineSerializer(serializers.ModelSerializer):
    estimated_unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, write_only=True
    )

    class Meta:
        model = PRLine
        fields = [
            "id",
            "requisition",
            "item_description",
            "quantity",
            "unit_of_measure",
            "estimated_unit_price",
            "unit_price",
            "estimated_total",
            "specifications",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "requisition", "estimated_total", "created_at", "updated_at"]

    def validate(self, attrs):
        if "unit_price" in attrs and "estimated_unit_price" not in attrs:
            attrs["estimated_unit_price"] = attrs.pop("unit_price")
        elif "unit_price" in attrs:
            attrs.pop("unit_price")
        if "estimated_unit_price" not in attrs:
            raise serializers.ValidationError({"estimated_unit_price": "This field is required."})
        return attrs


class PRAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PRAttachment
        fields = "__all__"


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    lines = PRLineSerializer(many=True, required=False)
    attachments = PRAttachmentSerializer(many=True, read_only=True)
    requester_email = serializers.CharField(source="requester.email", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = PurchaseRequisition
        fields = "__all__"
        read_only_fields = [
            "id",
            "pr_number",
            "status",
            "total_amount",
            "requester",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        lines_data = self.initial_data.get("line_items", self.initial_data.get("lines", []))
        validated_data.pop("lines", None)

        if not validated_data.get("pr_number"):
            from django.utils import timezone

            count = PurchaseRequisition.objects.count() + 1
            validated_data["pr_number"] = f"PR-{timezone.now().strftime('%Y')}-{count:05d}"

        pr = super().create(validated_data)

        total = Decimal("0.00")
        for item in lines_data:
            qty = Decimal(str(item.get("quantity", 1)))
            price = Decimal(
                str(
                    item.get("estimated_unit_price")
                    if item.get("estimated_unit_price") is not None
                    else item.get("unit_price", 0)
                )
            )
            line_tot = qty * price
            total += line_tot
            PRLine.objects.create(
                requisition=pr,
                item_description=item.get("item_description", "Line Item"),
                quantity=qty,
                unit_of_measure=item.get("unit_of_measure", "EA"),
                estimated_unit_price=price,
                estimated_total=line_tot,
                specifications=item.get("specifications", ""),
            )

        if total > Decimal("0.00"):
            pr.total_amount = total
            pr.save(update_fields=["total_amount"])

        return pr

    def update(self, instance, validated_data):
        lines_data = self.initial_data.get("line_items", self.initial_data.get("lines", None))
        validated_data.pop("lines", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if lines_data is not None:
            instance.lines.all().delete()
            total = Decimal("0.00")
            for item in lines_data:
                qty = Decimal(str(item.get("quantity", 1)))
                price = Decimal(
                    str(
                        item.get("estimated_unit_price")
                        if item.get("estimated_unit_price") is not None
                        else item.get("unit_price", 0)
                    )
                )
                line_tot = qty * price
                total += line_tot
                PRLine.objects.create(
                    requisition=instance,
                    item_description=item.get("item_description", "Line Item"),
                    quantity=qty,
                    unit_of_measure=item.get("unit_of_measure", "EA"),
                    estimated_unit_price=price,
                    estimated_total=line_tot,
                    specifications=item.get("specifications", ""),
                )
            if total > Decimal("0.00"):
                instance.total_amount = total
                instance.save(update_fields=["total_amount"])

        return instance


class CanCreateRequisitionPermission(permissions.BasePermission):
    """
    Requisition RBAC:
    - Vendors are completely blocked from internal requisitions (403).
    - Writes (create, update, delete) are strictly limited to Requesters and Super Admins.
    - Auditors, approvers, vendors receive 403 before payload validation.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if hasattr(request.user, "role") and request.user.role else None
        )

        if getattr(request.user, "is_vendor", False) or role_code == Role.VENDOR_USER:
            return False

        if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            if request.user.is_superuser:
                return True
            action = getattr(view, "action", None)
            if action in ["approve", "reject"]:
                return role_code in [
                    Role.DEPT_APPROVER,
                    Role.PROC_MGR,
                    Role.FINANCE_AP,
                    Role.SUPER_ADMIN,
                ]
            if action in ["submit", "cancel"]:
                return role_code in [Role.REQUESTER, Role.PROC_MGR, Role.SUPER_ADMIN]
            return role_code in [Role.REQUESTER, Role.SUPER_ADMIN]

        return True


class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
    queryset = (
        PurchaseRequisition.objects.select_related("requester", "department", "cost_center")
        .prefetch_related("lines", "attachments")
        .all()
    )
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [
        IsAuthenticated,
        AuditorReadOnlyPermission,
        CanCreateRequisitionPermission,
    ]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return self.queryset.none()

        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )

        # Vendors have zero visibility into internal purchase requisitions
        if getattr(user, "is_vendor", False) or role_code == Role.VENDOR_USER:
            return self.queryset.none()

        if user.is_superuser or role_code in [
            Role.SUPER_ADMIN,
            Role.PROC_EXEC,
            Role.PROC_MGR,
            Role.AUDITOR,
        ]:
            qs = self.queryset
        elif role_code == Role.DEPT_APPROVER and user.department_id:
            qs = self.queryset.filter(department_id=user.department_id)
        else:
            # Requesters are strictly scoped to their own requisitions (prevents IDOR)
            qs = self.queryset.filter(requester=user)

        # Filter by status
        status_param = self.request.query_params.get("status", "").strip().upper()
        if status_param:
            valid_statuses = [c[0] for c in PurchaseRequisition.STATUS_CHOICES]
            if status_param not in valid_statuses:
                raise serializers.ValidationError(
                    {
                        "status": f"Invalid status '{status_param}'. Valid choices: {', '.join(valid_statuses)}"
                    }
                )
            qs = qs.filter(status=status_param)

        return qs

    def perform_create(self, serializer):
        user = self.request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if not user.is_superuser and role_code not in [Role.REQUESTER, Role.SUPER_ADMIN]:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Only users with the Requester role can raise Purchase Requisitions."
            )
        serializer.save(requester=user)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        pr = self.get_object()
        from apps.requisitions.services import submit_purchase_requisition_service

        updated_pr = submit_purchase_requisition_service(requisition=pr, user=request.user)
        return Response(self.get_serializer(updated_pr).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        pr = self.get_object()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if pr.requester_id == user.id and not user.is_superuser:
            raise permissions.exceptions.PermissionDenied(
                "Self-approval is strictly prohibited: Requesters cannot approve their own Purchase Requisitions."
            )
        if not user.is_superuser and role_code not in [
            Role.DEPT_APPROVER,
            Role.PROC_MGR,
            Role.FINANCE_AP,
            Role.SUPER_ADMIN,
        ]:
            raise permissions.exceptions.PermissionDenied(
                "You do not have permission to approve Purchase Requisitions."
            )
        from apps.requisitions.services import approve_purchase_requisition_service

        comments = request.data.get("comments", "")
        updated_pr = approve_purchase_requisition_service(
            requisition=pr, approver=user, comments=comments
        )
        return Response(self.get_serializer(updated_pr).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        pr = self.get_object()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if not user.is_superuser and role_code not in [
            Role.DEPT_APPROVER,
            Role.PROC_MGR,
            Role.FINANCE_AP,
            Role.SUPER_ADMIN,
        ]:
            raise permissions.exceptions.PermissionDenied(
                "You do not have permission to reject Purchase Requisitions."
            )
        comments = (request.data.get("comments") or "").strip()
        if not comments:
            raise serializers.ValidationError(
                {"comments": "Rejection comments/justification are mandatory."}
            )
        from apps.requisitions.services import reject_purchase_requisition_service

        updated_pr = reject_purchase_requisition_service(
            requisition=pr, approver=user, comments=comments
        )
        return Response(self.get_serializer(updated_pr).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        pr = self.get_object()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if pr.requester_id != user.id and not user.is_superuser and role_code != Role.PROC_MGR:
            raise permissions.exceptions.PermissionDenied(
                "Only the original requester or a procurement manager can cancel this requisition."
            )
        if pr.status in [
            PurchaseRequisition.STATUS_PO_ISSUED,
            PurchaseRequisition.STATUS_CANCELLED,
            PurchaseRequisition.STATUS_SOURCING,
        ]:
            raise serializers.ValidationError(
                {"status": f"Cannot cancel requisition in '{pr.status}' status."}
            )
        from apps.requisitions.services import release_budget_reservation_service

        pr.status = PurchaseRequisition.STATUS_CANCELLED
        pr.save(update_fields=["status", "updated_at"])
        release_budget_reservation_service(requisition=pr, user=user, reason="PR cancelled by user")
        return Response(self.get_serializer(pr).data, status=status.HTTP_200_OK)


router = DefaultRouter()
router.register(r"", PurchaseRequisitionViewSet, basename="requisition")

urlpatterns = router.urls

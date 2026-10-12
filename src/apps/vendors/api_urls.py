from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q, Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.accounts.permissions import IsProcurementManager
from apps.audit.models import AuditLog
from apps.orders.models import PurchaseOrder
from apps.vendors.filters import VendorFilter, annotate_governance
from apps.vendors.models import (
    Vendor,
    VendorCategory,
    VendorContact,
    VendorDocument,
    VendorRiskRecord,
)
from apps.vendors.permissions import (
    GovernanceOperatePermission,
    VendorAccessPermission,
)
from apps.vendors.selectors import get_vendor_change_history
from apps.vendors.services import (
    allowed_governance_transitions,
    record_vendor_risk_assessment_service,
    set_vendor_status_governance_service,
)


class VendorCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorCategory
        fields = ["id", "name", "code", "description"]


class VendorContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorContact
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "phone",
            "designation",
            "is_primary",
            "created_at",
        ]


class VendorDocumentSerializer(serializers.ModelSerializer):
    document_type_display = serializers.CharField(
        source="get_document_type_display", read_only=True
    )
    verified_by_email = serializers.CharField(source="verified_by.email", read_only=True)
    is_expired = serializers.SerializerMethodField()
    is_expiring_soon = serializers.SerializerMethodField()

    class Meta:
        model = VendorDocument
        fields = [
            "id",
            "vendor",
            "document_type",
            "document_type_display",
            "title",
            "expiry_date",
            "is_verified",
            "verified_by",
            "verified_by_email",
            "is_expired",
            "is_expiring_soon",
            "created_at",
            "updated_at",
        ]

    def get_is_expired(self, obj):
        if not obj.expiry_date:
            return False
        return obj.expiry_date < date.today()

    def get_is_expiring_soon(self, obj):
        if not obj.expiry_date:
            return False
        today = date.today()
        return today <= obj.expiry_date <= (today + timedelta(days=30))


class VendorRiskRecordSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)
    vendor_number = serializers.CharField(source="vendor.vendor_number", read_only=True)
    assessed_by_email = serializers.CharField(source="assessed_by.email", read_only=True)

    class Meta:
        model = VendorRiskRecord
        fields = [
            "id",
            "vendor",
            "vendor_name",
            "vendor_number",
            "risk_level",
            "risk_flags",
            "assessment_notes",
            "assessed_by",
            "assessed_by_email",
            "created_at",
            "updated_at",
        ]


class VendorSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_code = serializers.CharField(source="category.code", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    documents = VendorDocumentSerializer(many=True, read_only=True)
    contacts = VendorContactSerializer(many=True, read_only=True)
    risk_records = VendorRiskRecordSerializer(many=True, read_only=True)

    current_risk_level = serializers.SerializerMethodField()
    risk_assessment_notes = serializers.SerializerMethodField()
    last_risk_assessment_date = serializers.SerializerMethodField()
    last_assessed_by_email = serializers.SerializerMethodField()

    active_pos_count = serializers.SerializerMethodField()
    active_pos_total_amount = serializers.SerializerMethodField()
    total_pos_count = serializers.SerializerMethodField()
    total_po_spend = serializers.SerializerMethodField()
    documents_summary = serializers.SerializerMethodField()
    contacts_count = serializers.SerializerMethodField()

    class Meta:
        model = Vendor
        fields = [
            "id",
            "legal_name",
            "trade_name",
            "vendor_number",
            "tax_identification_number",
            "registration_number",
            "category",
            "category_name",
            "category_code",
            "status",
            "status_display",
            "status_notes",
            "email",
            "phone",
            "address",
            "bank_name",
            "bank_account_number",
            "bank_routing_code",
            "current_risk_level",
            "risk_assessment_notes",
            "last_risk_assessment_date",
            "last_assessed_by_email",
            "active_pos_count",
            "active_pos_total_amount",
            "total_pos_count",
            "total_po_spend",
            "documents_summary",
            "contacts_count",
            "documents",
            "contacts",
            "risk_records",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "vendor_number"]

    def get_current_risk_level(self, obj):
        records = getattr(obj, "_prefetched_risk_records", None)
        if records is not None:
            latest = records[0] if records else None
        else:
            latest = obj.risk_records.order_by("-created_at").first()
        return latest.risk_level if latest else VendorRiskRecord.RISK_LEVEL_LOW

    def get_risk_assessment_notes(self, obj):
        records = getattr(obj, "_prefetched_risk_records", None)
        if records is not None:
            latest = records[0] if records else None
        else:
            latest = obj.risk_records.order_by("-created_at").first()
        return latest.assessment_notes if latest else ""

    def get_last_risk_assessment_date(self, obj):
        records = getattr(obj, "_prefetched_risk_records", None)
        if records is not None:
            latest = records[0] if records else None
        else:
            latest = obj.risk_records.order_by("-created_at").first()
        return latest.created_at.isoformat() if latest else None

    def get_last_assessed_by_email(self, obj):
        records = getattr(obj, "_prefetched_risk_records", None)
        if records is not None:
            latest = records[0] if records else None
        else:
            latest = obj.risk_records.order_by("-created_at").first()
        return latest.assessed_by.email if latest and latest.assessed_by else None

    def get_active_pos_count(self, obj):
        return obj.purchase_orders.filter(
            status__in=[
                PurchaseOrder.STATUS_ISSUED,
                PurchaseOrder.STATUS_ACKNOWLEDGED,
                PurchaseOrder.STATUS_PARTIAL_RECEIPT,
            ]
        ).count()

    def get_active_pos_total_amount(self, obj):
        val = obj.purchase_orders.filter(
            status__in=[
                PurchaseOrder.STATUS_ISSUED,
                PurchaseOrder.STATUS_ACKNOWLEDGED,
                PurchaseOrder.STATUS_PARTIAL_RECEIPT,
            ]
        ).aggregate(total=Sum("total_amount"))["total"]
        return float(val or Decimal("0.00"))

    def get_total_pos_count(self, obj):
        return obj.purchase_orders.count()

    def get_total_po_spend(self, obj):
        val = obj.purchase_orders.exclude(status=PurchaseOrder.STATUS_CANCELLED).aggregate(
            total=Sum("total_amount")
        )["total"]
        return float(val or Decimal("0.00"))

    def get_contacts_count(self, obj):
        return obj.contacts.count()

    def get_documents_summary(self, obj):
        docs = list(obj.documents.all())
        today = date.today()
        thirty_days = today + timedelta(days=30)

        total_docs = len(docs)
        verified_docs = sum(1 for d in docs if d.is_verified)
        expired_docs = sum(1 for d in docs if d.expiry_date and d.expiry_date < today)
        expiring_soon_docs = sum(
            1 for d in docs if d.expiry_date and today <= d.expiry_date <= thirty_days
        )

        doc_types = {d.document_type for d in docs}
        required_types = {
            VendorDocument.DOC_TYPE_CERT,
            VendorDocument.DOC_TYPE_TAX,
            VendorDocument.DOC_TYPE_BANK,
        }
        missing_required = list(required_types - doc_types)

        return {
            "total_documents": total_docs,
            "verified_documents": verified_docs,
            "expired_documents": expired_docs,
            "expiring_soon_documents": expiring_soon_docs,
            "missing_required_docs": missing_required,
            "is_fully_compliant": (
                total_docs >= 3
                and verified_docs >= 3
                and expired_docs == 0
                and len(missing_required) == 0
            ),
        }

    def create(self, validated_data):
        if not validated_data.get("vendor_number"):
            from django.utils import timezone

            count = Vendor.objects.count() + 1
            year = timezone.now().strftime("%Y")
            validated_data["vendor_number"] = f"VND-{year}-{count:05d}"
        return super().create(validated_data)


class VendorViewSet(viewsets.ModelViewSet):
    queryset = (
        Vendor.objects.select_related("category")
        .prefetch_related(
            "documents",
            "documents__verified_by",
            "contacts",
            "risk_records",
            "risk_records__assessed_by",
            "purchase_orders",
        )
        .order_by("-created_at")
    )
    serializer_class = VendorSerializer
    permission_classes = [IsAuthenticated, VendorAccessPermission]
    search_fields = [
        "legal_name",
        "trade_name",
        "vendor_number",
        "tax_identification_number",
        "email",
    ]
    ordering_fields = ["created_at", "legal_name", "vendor_number", "status"]

    def get_queryset(self):
        user = self.request.user
        qs = annotate_governance(
            Vendor.objects.select_related("category").prefetch_related(
                "documents",
                "documents__verified_by",
                "contacts",
                "risk_records",
                "risk_records__assessed_by",
                "purchase_orders",
            )
        )

        # Vendor user scoping
        if getattr(user, "is_vendor", False) or getattr(user, "vendor_id", None):
            if getattr(user, "vendor_id", None):
                qs = qs.filter(id=user.vendor_id)
            elif getattr(user, "vendor", None):
                qs = qs.filter(id=user.vendor.id)
            else:
                return qs.none()

        filterset = VendorFilter(self.request.query_params, queryset=qs)
        if not filterset.is_valid():
            raise serializers.ValidationError(filterset.errors)
        qs = filterset.qs

        # Search query
        search_query = self.request.query_params.get("search", "").strip()
        if search_query:
            qs = qs.filter(
                Q(legal_name__icontains=search_query)
                | Q(trade_name__icontains=search_query)
                | Q(vendor_number__icontains=search_query)
                | Q(tax_identification_number__icontains=search_query)
                | Q(email__icontains=search_query)
            )

        # Compliance Status filter
        compliance_param = self.request.query_params.get("compliance_status", "").strip().lower()
        today = date.today()
        thirty_days = today + timedelta(days=30)
        if compliance_param == "expiring_soon":
            qs = qs.filter(
                documents__expiry_date__gte=today,
                documents__expiry_date__lte=thirty_days,
            ).distinct()
        elif compliance_param == "expired_docs":
            qs = qs.filter(documents__expiry_date__lt=today).distinct()
        elif compliance_param == "missing_docs":
            qs = qs.annotate(doc_count=Count("documents")).filter(doc_count__lt=3)

        # Sorting
        sort_by = self.request.query_params.get("sort_by", "").strip()
        if sort_by:
            allowed_sorts = [
                "created_at",
                "-created_at",
                "legal_name",
                "-legal_name",
                "vendor_number",
                "-vendor_number",
                "status",
                "-status",
            ]
            if sort_by in allowed_sorts:
                qs = qs.order_by(sort_by)
        else:
            qs = qs.order_by("-created_at")

        return qs

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="risk",
        permission_classes=[IsAuthenticated, GovernanceOperatePermission],
    )
    def risk(self, request, pk=None):
        vendor = self.get_object()
        if request.method == "GET":
            records = vendor.risk_records.select_related("assessed_by").order_by("-created_at")
            return Response(
                VendorRiskRecordSerializer(records, many=True).data, status=status.HTTP_200_OK
            )

        risk_level = str(request.data.get("risk_level", "")).upper()
        risk_flags = request.data.get("risk_flags", [])
        notes = str(request.data.get("notes", request.data.get("assessment_notes", ""))).strip()
        record = record_vendor_risk_assessment_service(
            vendor=vendor,
            assessor=request.user,
            risk_level=risk_level,
            risk_flags=risk_flags,
            notes=notes,
        )
        return Response(VendorRiskRecordSerializer(record).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="set-status",
        permission_classes=[IsAuthenticated, GovernanceOperatePermission],
    )
    def set_status(self, request, pk=None):
        vendor = self.get_object()
        new_status = str(request.data.get("status", "")).upper()
        notes = str(request.data.get("notes", request.data.get("reason", ""))).strip()
        try:
            vendor = set_vendor_status_governance_service(
                vendor=vendor,
                actor=request.user,
                new_status=new_status,
                notes=notes,
            )
        except DjangoValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return Response(
                {"error": {"code": "INVALID", "message": msg, "details": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(vendor).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="allowed-transitions")
    def allowed_transitions(self, request, pk=None):
        vendor = self.get_object()
        allowed = allowed_governance_transitions(vendor, request.user)
        return Response({"allowed": allowed}, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["get"],
        url_path="history",
        permission_classes=[IsAuthenticated, GovernanceOperatePermission],
    )
    def history(self, request, pk=None):
        vendor = self.get_object()
        entries = get_vendor_change_history(vendor)
        data = [
            {
                "id": str(entry.id),
                "action": entry.action,
                "object": entry.target_model,
                "target_object_id": entry.target_object_id,
                "actor_email": entry.actor.email if entry.actor else None,
                "timestamp": entry.timestamp.isoformat(),
                "previous_state": entry.previous_state,
                "new_state": entry.new_state,
            }
            for entry in entries
        ]
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="documents")
    def documents(self, request, pk=None):
        vendor = self.get_object()
        docs = vendor.documents.all().order_by("-created_at")
        return Response(VendorDocumentSerializer(docs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path=r"documents/(?P<doc_id>[^/.]+)/download")
    def download_document(self, request, pk=None, doc_id=None):
        vendor = self.get_object()
        doc = get_object_or_404(VendorDocument, id=doc_id, vendor=vendor)
        if not doc.file:
            return Response({"detail": "File not found."}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(
            doc.file.open("rb"), as_attachment=True, filename=doc.title or "document"
        )

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        GET /api/v1/vendors/summary/
        Returns comprehensive KPI metrics for the Vendor Risk & Governance Desk.
        """
        qs = self.get_queryset()

        total_vendors = qs.count()
        status_counts = dict(qs.values_list("status").annotate(count=Count("id")))
        active_vendors = status_counts.get(Vendor.STATUS_ACTIVE, 0)
        on_hold_vendors = status_counts.get(Vendor.STATUS_ON_HOLD, 0)
        suspended_vendors = status_counts.get(Vendor.STATUS_SUSPENDED, 0)
        kyc_review_vendors = status_counts.get(Vendor.STATUS_KYC_REVIEW, 0) + status_counts.get(
            Vendor.STATUS_SUBMITTED, 0
        )
        draft_vendors = status_counts.get(Vendor.STATUS_DRAFT, 0)
        rejected_vendors = status_counts.get(Vendor.STATUS_REJECTED, 0)

        high_risk_count = (
            qs.filter(risk_records__risk_level=VendorRiskRecord.RISK_LEVEL_HIGH).distinct().count()
        )
        medium_risk_count = (
            qs.filter(risk_records__risk_level=VendorRiskRecord.RISK_LEVEL_MEDIUM)
            .exclude(risk_records__risk_level=VendorRiskRecord.RISK_LEVEL_HIGH)
            .distinct()
            .count()
        )
        low_risk_count = max(0, total_vendors - high_risk_count - medium_risk_count)

        today = date.today()
        thirty_days = today + timedelta(days=30)
        expiring_docs_count = (
            qs.filter(
                Q(documents__expiry_date__lt=today)
                | Q(documents__expiry_date__gte=today, documents__expiry_date__lte=thirty_days)
            )
            .distinct()
            .count()
        )
        missing_docs_count = (
            qs.annotate(doc_count=Count("documents")).filter(doc_count__lt=3).count()
        )

        return Response(
            {
                "total_vendors": total_vendors,
                "active_vendors": active_vendors,
                "high_risk_vendors": high_risk_count,
                "medium_risk_vendors": medium_risk_count,
                "low_risk_vendors": low_risk_count,
                "on_hold_vendors": on_hold_vendors,
                "suspended_vendors": suspended_vendors,
                "kyc_reviews_count": kyc_review_vendors,
                "draft_vendors": draft_vendors,
                "rejected_vendors": rejected_vendors,
                "expiring_docs_vendors": expiring_docs_count,
                "missing_docs_vendors": missing_docs_count,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="dossier")
    def dossier(self, request, pk=None):
        """
        GET /api/v1/vendors/<uuid:pk>/dossier/
        Returns complete supplier compliance dossier for governance audits.
        """
        vendor = self.get_object()
        serializer = self.get_serializer(vendor)

        pos = vendor.purchase_orders.order_by("-created_at")[:10]
        recent_pos_data = [
            {
                "id": str(po.id),
                "po_number": po.po_number,
                "version": po.version,
                "status": po.status,
                "status_display": po.get_status_display(),
                "total_amount": float(po.total_amount),
                "cost_center_code": po.cost_center.code if po.cost_center else None,
                "created_at": po.created_at.isoformat(),
            }
            for po in pos
        ]

        data = serializer.data
        data["recent_purchase_orders"] = recent_pos_data
        return Response(data, status=status.HTTP_200_OK)


class VendorCategoryViewSet(viewsets.ModelViewSet):
    queryset = VendorCategory.objects.order_by("name")
    serializer_class = VendorCategorySerializer
    permission_classes = [IsAuthenticated, VendorAccessPermission]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]


class VendorRiskRecordViewSet(viewsets.ModelViewSet):
    queryset = VendorRiskRecord.objects.select_related("vendor", "assessed_by").order_by(
        "-created_at"
    )
    serializer_class = VendorRiskRecordSerializer
    permission_classes = [IsAuthenticated, VendorAccessPermission]

    def get_queryset(self):
        qs = self.queryset
        risk_level = self.request.query_params.get("risk_level")
        vendor_id = self.request.query_params.get("vendor_id")
        if risk_level:
            qs = qs.filter(risk_level=risk_level.upper())
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        return qs


class VendorAssessRiskAPIView(APIView):
    """
    POST /api/v1/vendors/<uuid:pk>/assess-risk/
    Records a new VendorRiskRecord and optionally updates vendor status (ACTIVE, ON_HOLD, SUSPENDED).
    """

    permission_classes = [IsAuthenticated, IsProcurementManager]

    def post(self, request, pk):
        vendor = get_object_or_404(Vendor, pk=pk)

        risk_level = str(request.data.get("risk_level", VendorRiskRecord.RISK_LEVEL_LOW)).upper()
        assessment_notes = str(request.data.get("assessment_notes", "")).strip()
        governance_status = request.data.get("governance_status")

        valid_levels = [
            VendorRiskRecord.RISK_LEVEL_LOW,
            VendorRiskRecord.RISK_LEVEL_MEDIUM,
            VendorRiskRecord.RISK_LEVEL_HIGH,
        ]
        if risk_level not in valid_levels:
            return Response(
                {"detail": f"Invalid risk level '{risk_level}'. Allowed: {valid_levels}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not assessment_notes:
            return Response(
                {
                    "detail": "Assessment notes / justification is required when recording vendor risk."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        risk_record = VendorRiskRecord.objects.create(
            vendor=vendor,
            risk_level=risk_level,
            assessment_notes=assessment_notes,
            assessed_by=request.user,
        )

        # Append-only AuditLog
        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.ACTION_CREATE,
            target_model="VendorRiskRecord",
            target_object_id=str(risk_record.id),
            new_state={
                "vendor": vendor.legal_name,
                "risk_level": risk_level,
                "notes": assessment_notes,
            },
        )

        if governance_status:
            gov_status_upper = str(governance_status).upper()
            if gov_status_upper in [
                Vendor.STATUS_ACTIVE,
                Vendor.STATUS_ON_HOLD,
                Vendor.STATUS_SUSPENDED,
                Vendor.STATUS_REJECTED,
            ]:
                set_vendor_status_governance_service(
                    vendor=vendor,
                    actor=request.user,
                    new_status=gov_status_upper,
                    notes=assessment_notes,
                )

        return Response(
            {
                "message": f"Risk assessment recorded for {vendor.legal_name}: {risk_level} Risk.",
                "risk_record_id": str(risk_record.id),
                "vendor_id": str(vendor.id),
                "risk_level": risk_level,
                "vendor_status": vendor.status,
            },
            status=status.HTTP_200_OK,
        )


class VendorGovernanceStatusAPIView(APIView):
    """
    POST /api/v1/vendors/<uuid:pk>/governance-status/
    Manager governance action to set vendor status to ACTIVE, ON_HOLD, SUSPENDED, or REJECTED.
    """

    permission_classes = [IsAuthenticated, IsProcurementManager]

    def post(self, request, pk):
        vendor = get_object_or_404(Vendor, pk=pk)

        new_status = str(request.data.get("status", "")).upper()
        notes = str(request.data.get("notes", request.data.get("reason", ""))).strip()

        if not notes:
            return Response(
                {"detail": "Governance notes are mandatory when modifying vendor status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            set_vendor_status_governance_service(
                vendor=vendor, actor=request.user, new_status=new_status, notes=notes
            )
            return Response(
                {
                    "message": f"Vendor {vendor.legal_name} status updated to {new_status}.",
                    "vendor_id": str(vendor.id),
                    "status": vendor.status,
                    "status_notes": vendor.status_notes,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


router = DefaultRouter()
router.register(r"categories", VendorCategoryViewSet, basename="vendor-category")
router.register(r"risk-records", VendorRiskRecordViewSet, basename="vendor-risk-record")
router.register(r"", VendorViewSet, basename="vendor")

urlpatterns = [
    path(
        "<uuid:pk>/assess-risk/", VendorAssessRiskAPIView.as_view(), name="vendor-assess-risk-api"
    ),
    path(
        "<uuid:pk>/governance-status/",
        VendorGovernanceStatusAPIView.as_view(),
        name="vendor-governance-status-api",
    ),
] + router.urls

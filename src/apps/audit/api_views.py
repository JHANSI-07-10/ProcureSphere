from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.permissions import AuditorReadOnlyPermission, IsAuditorOrSuperAdmin
from apps.audit.selectors import (
    get_approval_history_audit,
    get_audit_logs,
    get_audit_metrics,
    get_auditor_dashboard_data,
    get_contract_changes_audit,
    get_entity_audit_trail,
    get_invoice_exceptions_audit,
    get_po_changes_audit,
    get_security_events_audit,
    get_sourcing_activity_audit,
    get_vendor_compliance_audit,
)
from apps.audit.serializers import (
    ApprovalHistoryQuerySerializer,
    AuditFilterParamsSerializer,
    AuditLogDetailSerializer,
    AuditLogListSerializer,
    AuditorExportRequestSerializer,
    ContractChangesQuerySerializer,
    EntityAuditTrailQuerySerializer,
    InvoiceExceptionsQuerySerializer,
    POChangesQuerySerializer,
    SecurityEventsQuerySerializer,
    SourcingActivityQuerySerializer,
    TransactionLifecycleQuerySerializer,
    VendorComplianceQuerySerializer,
)
from apps.audit.services import export_auditor_data_service, get_transaction_lifecycle_service


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API ViewSet providing access to immutable AuditLog records,
    statistical metrics, full-chain Source-to-Pay transaction lifecycle graphs,
    entity audit trails, domain audit logs, and export services.
    """

    queryset = AuditLog.objects.select_related("actor").all()
    serializer_class = AuditLogListSerializer
    permission_classes = [IsAuthenticated, IsAuditorOrSuperAdmin, AuditorReadOnlyPermission]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AuditLogDetailSerializer
        return AuditLogListSerializer

    def list(self, request, *args, **kwargs):
        filter_serializer = AuditFilterParamsSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        data = filter_serializer.validated_data

        result = get_audit_logs(
            actor_id=str(data.get("actor_id")) if data.get("actor_id") else None,
            action=data.get("action"),
            target_model=data.get("target_model"),
            target_object_id=data.get("target_object_id"),
            search_term=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            request_id=data.get("request_id"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )

        serializer = AuditLogListSerializer(result["logs"], many=True)
        return Response(
            {
                "total_count": result["total_count"],
                "limit": result["limit"],
                "offset": result["offset"],
                "results": serializer.data,
            }
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "period_days", int, description="Time window in days for metrics (default: 30)"
            ),
        ],
        description="Get statistical aggregations, action breakdown, and daily activity trends.",
    )
    @action(detail=False, methods=["get"], url_path="metrics")
    def metrics(self, request):
        try:
            period_days = int(request.query_params.get("period_days", 30))
        except (ValueError, TypeError):
            period_days = 30

        metrics_data = get_audit_metrics(period_days=period_days)
        return Response(metrics_data, status=status.HTTP_200_OK)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "identifier",
                str,
                required=True,
                description="Document/Entity number or UUID (e.g. PR-2026-0001, PO-2026-0001, INV-2026-0001)",
            ),
            OpenApiParameter(
                "entity_type",
                str,
                description="Optional entity domain code (PR, PO, SOURCING, INVOICE, CONTRACT, VENDOR, RECEIPT)",
            ),
        ],
        description="Retrieve complete end-to-end Source-to-Pay transaction lifecycle graph and linked audit trail.",
    )
    @action(detail=False, methods=["get"], url_path="lifecycle")
    def lifecycle(self, request):
        query_serializer = TransactionLifecycleQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        identifier = query_serializer.validated_data["identifier"]
        entity_type = query_serializer.validated_data.get("entity_type", "AUTO")

        lifecycle_data = get_transaction_lifecycle_service(
            entity_type=entity_type,
            entity_identifier=identifier,
        )

        if not lifecycle_data.get("is_found"):
            return Response(
                {
                    "error": {
                        "code": "ENTITY_NOT_FOUND",
                        "message": f"No entity matching identifier '{identifier}' was found.",
                    },
                    "lifecycle": lifecycle_data,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(lifecycle_data, status=status.HTTP_200_OK)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "target_model",
                str,
                required=True,
                description="Name of the model (e.g. PurchaseRequisition, PurchaseOrder)",
            ),
            OpenApiParameter(
                "target_object_id",
                str,
                required=True,
                description="UUID or ID of the target object",
            ),
        ],
        description="Retrieve all chronological audit log events and approval actions for a specific target entity.",
    )
    @action(detail=False, methods=["get"], url_path="entity-trail")
    def entity_trail(self, request):
        query_serializer = EntityAuditTrailQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        model_name = query_serializer.validated_data["target_model"]
        obj_id = query_serializer.validated_data["target_object_id"]

        trail_data = get_entity_audit_trail(
            target_model=model_name,
            target_object_id=obj_id,
        )

        logs_serializer = AuditLogDetailSerializer(trail_data["logs"], many=True)
        return Response(
            {
                "target_model": trail_data["target_model"],
                "target_object_id": trail_data["target_object_id"],
                "logs_count": trail_data["logs_count"],
                "approvals_count": trail_data["approvals_count"],
                "logs": logs_serializer.data,
                "approvals": [
                    {
                        "id": str(a.id),
                        "actor": a.actor.email if a.actor else "System",
                        "action": a.action,
                        "comments": a.comments,
                        "previous_state": a.previous_state,
                        "new_state": a.new_state,
                        "timestamp": a.created_at.isoformat(),
                    }
                    for a in trail_data["approvals"]
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="dashboard-data")
    def dashboard_data(self, request):
        try:
            period_days = int(request.query_params.get("period_days", 30))
        except (ValueError, TypeError):
            period_days = 30

        data = get_auditor_dashboard_data(period_days=period_days)
        data["recent_logs"] = AuditLogListSerializer(data["recent_logs"], many=True).data
        data["recent_approvals"] = [
            {
                "id": str(a.id),
                "actor": a.actor.email if a.actor else "System",
                "action": a.action,
                "comments": a.comments,
                "target_model": a.target_model_name,
                "target_id": str(a.target_object_id),
                "timestamp": a.created_at.isoformat(),
            }
            for a in data["recent_approvals"]
        ]
        data["open_exceptions"] = [
            {
                "id": str(e.id),
                "invoice_number": e.invoice.invoice_number,
                "vendor": e.invoice.vendor.legal_name,
                "type": e.get_exception_type_display(),
                "variance_amount": float(e.variance_amount),
                "created_at": e.created_at.isoformat(),
            }
            for e in data["open_exceptions"]
        ]
        data["suspended_vendors"] = [
            {
                "id": str(v.id),
                "vendor_number": v.vendor_number,
                "legal_name": v.legal_name,
                "status": v.get_status_display(),
                "category": v.category.name if v.category else None,
            }
            for v in data["suspended_vendors"]
        ]
        data["overdue_obligations"] = [
            {
                "id": str(o.id),
                "contract_number": o.contract.contract_number,
                "title": o.title,
                "responsible_party": o.responsible_party,
                "due_date": str(o.due_date),
            }
            for o in data["overdue_obligations"]
        ]
        data["active_contract_alerts"] = [
            {
                "id": str(a.id),
                "contract_number": a.contract.contract_number,
                "alert_type": a.get_alert_type_display(),
                "message": a.message,
                "triggered_at": a.triggered_at.isoformat(),
            }
            for a in data["active_contract_alerts"]
        ]

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="vendor-compliance")
    def vendor_compliance(self, request):
        serializer = VendorComplianceQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_vendor_compliance_audit(
            status=data.get("status"),
            kyc_status=data.get("kyc_status"),
            search=data.get("search"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="approval-history")
    def approval_history(self, request):
        serializer = ApprovalHistoryQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_approval_history_audit(
            target_model=data.get("target_model"),
            action=data.get("action"),
            actor_id=str(data.get("actor_id")) if data.get("actor_id") else None,
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="sourcing-activity")
    def sourcing_activity(self, request):
        serializer = SourcingActivityQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_sourcing_activity_audit(
            status=data.get("status"),
            event_type=data.get("event_type"),
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="po-changes")
    def po_changes(self, request):
        serializer = POChangesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_po_changes_audit(
            search=data.get("search"),
            status=data.get("status"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="invoice-exceptions")
    def invoice_exceptions(self, request):
        serializer = InvoiceExceptionsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_invoice_exceptions_audit(
            status=data.get("status"),
            exception_type=data.get("exception_type"),
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="contract-changes")
    def contract_changes(self, request):
        serializer = ContractChangesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_contract_changes_audit(
            search=data.get("search"),
            status=data.get("status"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="security-events")
    def security_events(self, request):
        serializer = SecurityEventsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = get_security_events_audit(
            actor_id=str(data.get("actor_id")) if data.get("actor_id") else None,
            action=data.get("action"),
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get", "post"], url_path="export")
    def export_data(self, request):
        if request.method == "POST":
            serializer = AuditorExportRequestSerializer(data=request.data)
        else:
            serializer = AuditorExportRequestSerializer(data=request.query_params)

        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        export_res = export_auditor_data_service(
            export_type=data["export_type"],
            export_format=data["export_format"],
            filters=data.get("filters", {}),
            actor=request.user,
            request=request,
        )

        response = HttpResponse(
            export_res["content"],
            content_type=export_res["content_type"],
        )
        response["Content-Disposition"] = f'attachment; filename="{export_res["filename"]}"'
        return response


# --- Direct Standalone Functional API Views matching project URL standards ---


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_dashboard_summary_api_view(request):
    """
    Returns executive auditor dashboard KPIs, compliance alerts, and activity distribution.
    """
    try:
        period_days = int(request.query_params.get("period_days", 30))
        if period_days <= 0:
            period_days = 30
    except (ValueError, TypeError):
        period_days = 30
    data = get_auditor_dashboard_data(period_days=period_days)
    data["recent_logs"] = AuditLogListSerializer(data["recent_logs"], many=True).data
    data["recent_approvals"] = [
        {
            "id": str(a.id),
            "actor": a.actor.email if a.actor else "System",
            "action": a.action,
            "comments": a.comments,
            "target_model": a.target_model_name,
            "target_id": str(a.target_object_id),
            "timestamp": a.created_at.isoformat(),
        }
        for a in data["recent_approvals"]
    ]
    data["open_exceptions"] = [
        {
            "id": str(e.id),
            "invoice_number": e.invoice.invoice_number,
            "vendor": e.invoice.vendor.legal_name,
            "type": e.get_exception_type_display(),
            "variance_amount": float(e.variance_amount),
            "created_at": e.created_at.isoformat(),
        }
        for e in data["open_exceptions"]
    ]
    data["suspended_vendors"] = [
        {
            "id": str(v.id),
            "vendor_number": v.vendor_number,
            "legal_name": v.legal_name,
            "status": v.get_status_display(),
            "category": v.category.name if v.category else None,
        }
        for v in data["suspended_vendors"]
    ]
    data["overdue_obligations"] = [
        {
            "id": str(o.id),
            "contract_number": o.contract.contract_number,
            "title": o.title,
            "responsible_party": o.responsible_party,
            "due_date": str(o.due_date),
        }
        for o in data["overdue_obligations"]
    ]
    data["active_contract_alerts"] = [
        {
            "id": str(a.id),
            "contract_number": a.contract.contract_number,
            "alert_type": a.get_alert_type_display(),
            "message": a.message,
            "triggered_at": a.triggered_at.isoformat(),
        }
        for a in data["active_contract_alerts"]
    ]
    return Response(data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_vendor_compliance_api_view(request):
    serializer = VendorComplianceQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return Response(
        get_vendor_compliance_audit(**serializer.validated_data), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_approval_history_api_view(request):
    serializer = ApprovalHistoryQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if "actor_id" in data:
        data["actor_id"] = str(data["actor_id"])
    return Response(get_approval_history_audit(**data), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_sourcing_activity_api_view(request):
    serializer = SourcingActivityQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return Response(
        get_sourcing_activity_audit(**serializer.validated_data), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_po_changes_api_view(request):
    serializer = POChangesQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return Response(get_po_changes_audit(**serializer.validated_data), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_invoice_exceptions_api_view(request):
    serializer = InvoiceExceptionsQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return Response(
        get_invoice_exceptions_audit(**serializer.validated_data), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_contract_changes_api_view(request):
    serializer = ContractChangesQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return Response(
        get_contract_changes_audit(**serializer.validated_data), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_security_events_api_view(request):
    serializer = SecurityEventsQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if "actor_id" in data:
        data["actor_id"] = str(data["actor_id"])
    return Response(get_security_events_audit(**data), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_lifecycle_api_view(request):
    serializer = TransactionLifecycleQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    lifecycle_data = get_transaction_lifecycle_service(
        entity_type=data.get("entity_type", "AUTO"),
        entity_identifier=data["identifier"],
    )
    if not lifecycle_data.get("is_found"):
        return Response(
            {
                "error": {
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No entity matching identifier '{data['identifier']}' was found.",
                },
                "lifecycle": lifecycle_data,
            },
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(lifecycle_data, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_export_api_view(request):
    if request.method == "POST":
        serializer = AuditorExportRequestSerializer(data=request.data)
    else:
        serializer = AuditorExportRequestSerializer(data=request.query_params)

    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    export_res = export_auditor_data_service(
        export_type=data["export_type"],
        export_format=data["export_format"],
        filters=data.get("filters", {}),
        actor=request.user,
        request=request,
    )

    response = HttpResponse(
        export_res["content"],
        content_type=export_res["content_type"],
    )
    response["Content-Disposition"] = f'attachment; filename="{export_res["filename"]}"'
    return response

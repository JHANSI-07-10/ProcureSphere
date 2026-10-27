import os

from django.core.exceptions import ValidationError
from django.http import FileResponse
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditLog

from .models import Contract, ContractDocument, ContractMilestone, ContractObligation
from .permissions import (
    CanBusinessApproveContract,
    CanManageContract,
    CanRenewOrTerminateContract,
    CanViewContract,
    IsLegalManager,
    IsNotAuditor,
)
from .selectors import get_contracts_qs
from .serializers import (
    ContractDocumentSerializer,
    ContractMilestoneSerializer,
    ContractObligationSerializer,
    ContractSerializer,
    ContractVersionSerializer,
)
from .services import (
    add_contract_milestone_service,
    add_contract_obligation_service,
    approve_business_service,
    approve_legal_review_service,
    complete_contract_milestone_service,
    create_contract_service,
    create_contract_version_service,
    fulfill_contract_obligation_service,
    reject_legal_review_service,
    renew_contract_service,
    submit_for_legal_review_service,
    terminate_contract_service,
    upload_contract_document_service,
)


class ContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractSerializer
    permission_classes = [IsAuthenticated, CanManageContract, CanViewContract]

    def get_queryset(self):
        status_param = self.request.query_params.get("status", "").strip().upper()
        if status_param:
            valid_statuses = [choice[0] for choice in Contract.STATUS_CHOICES]
            if status_param not in valid_statuses:
                raise serializers.ValidationError(
                    {
                        "status": [
                            f"Invalid status '{status_param}'. Valid choices are: {', '.join(valid_statuses)}"
                        ]
                    }
                )
        search_param = self.request.query_params.get("search", "").strip()
        return get_contracts_qs(
            user=self.request.user, status=status_param or None, search=search_param or None
        )

    def perform_create(self, serializer):
        role_code = getattr(self.request.user, "role_code", None) or (
            self.request.user.role.code if getattr(self.request.user, "role", None) else None
        )
        if role_code == "AUDITOR":
            raise PermissionDenied(
                "Permission Denied: Compliance Auditors hold strictly read-only permissions and cannot draft contracts."
            )
        data = serializer.validated_data
        contract = create_contract_service(
            title=data["title"],
            vendor=data["vendor"],
            contract_value=data.get("contract_value", 0),
            start_date=data["start_date"],
            end_date=data["end_date"],
            contract_owner=self.request.user,
            renewal_notice_days=data.get("renewal_notice_days", 30),
            sourcing_event=data.get("sourcing_event"),
            po=data.get("po"),
        )
        serializer.instance = contract

    @action(detail=True, methods=["post"], url_path="submit-legal")
    def submit_legal(self, request, pk=None):
        contract = self.get_object()
        notes = request.data.get("notes", "")
        try:
            contract = submit_for_legal_review_service(
                contract=contract, user=request.user, notes=notes
            )
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_TRANSITION", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="legal-approve",
        permission_classes=[IsAuthenticated, IsLegalManager],
    )
    def legal_approve(self, request, pk=None):
        contract = self.get_object()
        notes = request.data.get("notes", "")
        try:
            contract = approve_legal_review_service(
                contract=contract, user=request.user, notes=notes
            )
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_TRANSITION", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="legal-reject",
        permission_classes=[IsAuthenticated, IsLegalManager],
    )
    def legal_reject(self, request, pk=None):
        contract = self.get_object()
        reason = request.data.get("reason", "")
        if not reason:
            return Response(
                {"error": {"code": "MISSING_PARAM", "message": "Rejection reason required"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            contract = reject_legal_review_service(
                contract=contract, user=request.user, reason=reason
            )
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_TRANSITION", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="business-approve",
        permission_classes=[IsAuthenticated, CanBusinessApproveContract],
    )
    def business_approve(self, request, pk=None):
        contract = self.get_object()
        notes = request.data.get("notes", "")
        try:
            contract = approve_business_service(contract=contract, user=request.user, notes=notes)
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_TRANSITION", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="amend",
        permission_classes=[IsAuthenticated, CanViewContract, IsNotAuditor, CanManageContract],
    )
    def amend(self, request, pk=None):
        contract = self.get_object()
        summary = request.data.get("amendment_summary")
        value = request.data.get("contract_value")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")

        if not summary or not value or not start_date or not end_date:
            return Response(
                {
                    "error": {
                        "code": "MISSING_PARAM",
                        "message": "All amendment fields are required",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            version = create_contract_version_service(
                contract=contract,
                user=request.user,
                amendment_summary=summary,
                contract_value=value,
                start_date=start_date,
                end_date=end_date,
            )
            return Response(ContractVersionSerializer(version).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_AMENDMENT", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get"], url_path="versions")
    def versions(self, request, pk=None):
        contract = self.get_object()
        version_records = contract.versions.all().order_by("-version_number")
        return Response(
            ContractVersionSerializer(version_records, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="add-milestone")
    def add_milestone(self, request, pk=None):
        contract = self.get_object()
        title = request.data.get("title")
        due_date = request.data.get("due_date")
        amount = request.data.get("amount", 0)

        if not title or not due_date:
            return Response(
                {
                    "error": {
                        "code": "MISSING_PARAM",
                        "message": "Both title and due_date are required",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            milestone = add_contract_milestone_service(
                contract=contract, title=title, due_date=due_date, amount=amount, user=request.user
            )
            return Response(
                ContractMilestoneSerializer(milestone).data, status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_MILESTONE", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="complete-milestone/(?P<milestone_id>[^/.]+)")
    def complete_milestone(self, request, pk=None, milestone_id=None):
        contract = self.get_object()
        try:
            milestone = ContractMilestone.objects.get(id=milestone_id, contract=contract)
            milestone = complete_contract_milestone_service(milestone=milestone, user=request.user)
            return Response(ContractMilestoneSerializer(milestone).data, status=status.HTTP_200_OK)
        except ContractMilestone.DoesNotExist:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Milestone record not found"}},
                status=status.HTTP_404_NOT_FOUND,
            )

    @action(detail=True, methods=["post"], url_path="add-obligation")
    def add_obligation(self, request, pk=None):
        contract = self.get_object()
        title = request.data.get("title")
        responsible_party = request.data.get("responsible_party", "VENDOR")
        due_date = request.data.get("due_date")

        if not title or not due_date:
            return Response(
                {
                    "error": {
                        "code": "MISSING_PARAM",
                        "message": "Both title and due_date are required",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            obligation = add_contract_obligation_service(
                contract=contract,
                title=title,
                responsible_party=responsible_party,
                due_date=due_date,
                user=request.user,
            )
            return Response(
                ContractObligationSerializer(obligation).data, status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_OBLIGATION", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="fulfill-obligation/(?P<obligation_id>[^/.]+)")
    def fulfill_obligation(self, request, pk=None, obligation_id=None):
        contract = self.get_object()
        try:
            obligation = ContractObligation.objects.get(id=obligation_id, contract=contract)
            obligation = fulfill_contract_obligation_service(
                obligation=obligation, user=request.user
            )
            return Response(
                ContractObligationSerializer(obligation).data, status=status.HTTP_200_OK
            )
        except ContractObligation.DoesNotExist:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Obligation record not found"}},
                status=status.HTTP_404_NOT_FOUND,
            )

    @action(detail=True, methods=["get"], url_path="milestones")
    def milestones(self, request, pk=None):
        contract = self.get_object()
        milestone_records = contract.milestones.all().order_by("due_date")
        return Response(
            ContractMilestoneSerializer(milestone_records, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="obligations")
    def obligations(self, request, pk=None):
        contract = self.get_object()
        obligation_records = contract.obligations.all().order_by("due_date")
        return Response(
            ContractObligationSerializer(obligation_records, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="renew",
        permission_classes=[IsAuthenticated, CanRenewOrTerminateContract],
    )
    def renew(self, request, pk=None):
        contract = self.get_object()
        new_end_date = request.data.get("new_end_date")
        new_value = request.data.get("new_value")
        notes = request.data.get("notes", "")
        if not new_end_date:
            return Response(
                {"error": {"code": "MISSING_PARAM", "message": "new_end_date is required"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            contract = renew_contract_service(
                contract=contract,
                user=request.user,
                new_end_date=new_end_date,
                new_value=new_value,
                notes=notes,
            )
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_RENEWAL", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="terminate",
        permission_classes=[IsAuthenticated, CanRenewOrTerminateContract],
    )
    def terminate(self, request, pk=None):
        contract = self.get_object()
        reason = request.data.get("reason", "")
        if not reason:
            return Response(
                {"error": {"code": "MISSING_PARAM", "message": "Termination reason is required"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            contract = terminate_contract_service(
                contract=contract, user=request.user, reason=reason
            )
            return Response(ContractSerializer(contract).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_TERMINATION", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="upload-document")
    def upload_document(self, request, pk=None):
        contract = self.get_object()
        title = request.data.get("title")
        file_obj = request.FILES.get("file") or request.data.get("file")

        if not title or not file_obj:
            return Response(
                {"error": {"code": "MISSING_PARAM", "message": "Both title and file are required"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            document = upload_contract_document_service(
                contract=contract,
                user=request.user,
                title=title,
                file=file_obj,
            )
            return Response(
                ContractDocumentSerializer(document).data, status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            return Response(
                {"error": {"code": "INVALID_DOCUMENT", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get"], url_path="documents")
    def documents(self, request, pk=None):
        contract = self.get_object()
        documents = contract.documents.all().order_by("-created_at")
        return Response(
            ContractDocumentSerializer(documents, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="documents/(?P<document_id>[^/.]+)/download")
    def download_document(self, request, pk=None, document_id=None):
        contract = self.get_object()
        try:
            document = ContractDocument.objects.get(id=document_id, contract=contract)
        except (ContractDocument.DoesNotExist, ValueError):
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Contract document not found"}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if (
            not document.file
            or not hasattr(document.file, "path")
            or not os.path.exists(document.file.path)
        ):
            return Response(
                {
                    "error": {
                        "code": "FILE_NOT_FOUND",
                        "message": "Document file not found on storage",
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.ACTION_EXPORT,
            target_model="ContractDocument",
            target_object_id=str(document.id),
            new_state={
                "document_title": document.title,
                "file_name": os.path.basename(document.file.name),
            },
        )

        return FileResponse(
            open(document.file.path, "rb"),
            as_attachment=True,
            filename=os.path.basename(document.file.name),
        )

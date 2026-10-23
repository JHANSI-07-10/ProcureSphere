from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction, ApprovalPolicy
from apps.approvals.services import add_approval_step_service, create_approval_policy_service
from apps.audit.models import AuditLog
from apps.budgets.services import allocate_budget_service
from apps.invoices.models import MatchException, SupplierInvoice
from apps.invoices.services import create_supplier_invoice_service, run_3_way_match_service
from apps.orders.models import PurchaseOrder
from apps.orders.services import generate_purchase_order_service
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.services import create_goods_receipt_service
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import (
    approve_purchase_requisition_service,
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)
from apps.sourcing.models import SourcingEvent
from apps.sourcing.services import create_sourcing_event_service
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import register_vendor_service, upload_vendor_document_service


@pytest.fixture
def base_org_and_users(db_roles):
    today = timezone.now().date()
    org = Organization.objects.create(name="Enterprise Test Corp", code="ETC-01")
    dept_it = Department.objects.create(organization=org, name="IT Systems", code="DEPT-IT")
    dept_hr = Department.objects.create(organization=org, name="HR Dept", code="DEPT-HR")

    cost_center = CostCenter.objects.create(
        department=dept_it, code="CC-IT-200", name="Core Infrastructure"
    )

    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=today.year,
        period_number=1,
        name=f"FY{today.year}-Q1",
        start_date=today - timedelta(days=15),
        end_date=today + timedelta(days=75),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("500000.00"),
    )

    requester = User.objects.create_user(
        email="requester_ws2@etc.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        department=dept_it,
        cost_center=cost_center,
    )
    dept_approver = User.objects.create_user(
        email="dept_approver_ws2@etc.com",
        password="Password123!",
        role=db_roles[Role.DEPT_APPROVER],
        department=dept_it,
    )
    finance_approver = User.objects.create_user(
        email="finance_approver_ws2@etc.com",
        password="Password123!",
        role=db_roles[Role.FINANCE_AP],
        department=dept_it,
    )
    proc_exec = User.objects.create_user(
        email="proc_exec_ws2@etc.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )
    stores_receiver = User.objects.create_user(
        email="stores_ws2@etc.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )
    other_user = User.objects.create_user(
        email="hr_user_ws2@etc.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        department=dept_hr,
    )

    cat, _ = VendorCategory.objects.get_or_create(code="HW", defaults={"name": "Hardware"})

    vendor = register_vendor_service(
        legal_name="Apex Server Solutions Inc",
        trade_name="Apex Server",
        tax_identification_number="27ABCDE1234F1Z5",
        category=cat,
        email="sales@apexservers.com",
        phone="+1-555-0199",
        address="100 Technology Parkway",
        bank_name="Global Tech Bank",
        bank_account_number="98765432101",
        bank_routing_code="GTB0001",
        created_by_user=proc_exec,
    )
    vendor.status = Vendor.STATUS_ACTIVE
    vendor.save(update_fields=["status"])

    return {
        "org": org,
        "dept_it": dept_it,
        "dept_hr": dept_hr,
        "cost_center": cost_center,
        "fiscal_period": fiscal_period,
        "requester": requester,
        "dept_approver": dept_approver,
        "finance_approver": finance_approver,
        "proc_exec": proc_exec,
        "stores_receiver": stores_receiver,
        "other_user": other_user,
        "vendor": vendor,
    }


@pytest.mark.django_db
def test_inv_cr_016_cumulative_quantity_match_exception(base_org_and_users):
    """
    INV-CR-016: When a second valid invoice is submitted against a PO with cumulative
    quantity exceeding accepted quantity (e.g. 4 + 10 vs 10 received),
    run_3_way_match_service must flag TYPE_QUANTITY_VARIANCE.
    """
    ctx = base_org_and_users
    today = timezone.now().date()

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-TEST-001",
        vendor=ctx["vendor"],
        cost_center=ctx["cost_center"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("10000.00"),
        created_by=ctx["proc_exec"],
    )
    po.lines.create(
        item_description="Storage Array Units",
        quantity=Decimal("10"),
        unit_price=Decimal("1000.00"),
        line_total=Decimal("10000.00"),
    )

    # Stores receives 10 units
    create_goods_receipt_service(
        po=po,
        received_by=ctx["stores_receiver"],
        receipt_items=[
            {
                "po_line": po.lines.first(),
                "quantity_received": Decimal("10"),
                "quantity_accepted": Decimal("10"),
            }
        ],
    )

    po_line = po.lines.first()

    # First invoice: 4 units ($4,000)
    inv1 = create_supplier_invoice_service(
        vendor=ctx["vendor"],
        po=po,
        invoice_number="INV-APEX-001",
        invoice_date=today,
        due_date=today + timedelta(days=30),
        line_items=[
            {
                "po_line": po_line,
                "item_description": "Storage Array Units",
                "quantity": Decimal("4"),
                "unit_price": Decimal("1000.00"),
            }
        ],
        created_by_user=ctx["proc_exec"],
    )
    inv1, exc1 = run_3_way_match_service(invoice=inv1)
    assert inv1.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT
    assert len(exc1) == 0

    # Second invoice: 10 units ($10,000) -> cumulative 14 units > 10 units received!
    inv2 = create_supplier_invoice_service(
        vendor=ctx["vendor"],
        po=po,
        invoice_number="INV-APEX-002",
        invoice_date=today,
        due_date=today + timedelta(days=30),
        line_items=[
            {
                "po_line": po_line,
                "item_description": "Storage Array Units",
                "quantity": Decimal("10"),
                "unit_price": Decimal("1000.00"),
            }
        ],
        created_by_user=ctx["proc_exec"],
    )
    inv2, exc2 = run_3_way_match_service(invoice=inv2)
    assert inv2.status == SupplierInvoice.STATUS_EXCEPTION
    assert len(exc2) > 0

    # Confirm quantity variance exception is recorded
    var_exc = MatchException.objects.filter(
        invoice=inv2,
        exception_type=MatchException.TYPE_QUANTITY_VARIANCE,
    ).first()
    assert var_exc is not None
    assert var_exc.status == MatchException.STATUS_OPEN


@pytest.mark.django_db
def test_pr_08_fail_closed_on_missing_policy(base_org_and_users):
    """
    PR-08: When submitting a PR, if no matching policy exists, fail-closed
    by raising ValidationError rather than silently advancing to MANAGER_REVIEW.
    """
    ctx = base_org_and_users
    today = timezone.now().date()

    # Ensure no approval policies exist
    ApprovalPolicy.objects.all().delete()

    pr = create_purchase_requisition_service(
        title="Unrouted Hardware PR",
        justification="Needs approval policy",
        requester=ctx["requester"],
        department=ctx["dept_it"],
        cost_center=ctx["cost_center"],
        requested_delivery_date=today + timedelta(days=10),
        line_items=[
            {
                "item_description": "Network Blade",
                "quantity": Decimal("2"),
                "estimated_unit_price": Decimal("5000.00"),
            }
        ],
    )

    with pytest.raises(ValidationError) as exc:
        submit_purchase_requisition_service(requisition=pr, user=ctx["requester"])
    assert "No active approval policy found" in str(exc.value)
    assert "fail-closed" in str(exc.value)

    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_DRAFT


@pytest.mark.django_db
def test_smk_pr_050_multi_step_approval_and_smk_ap_005_role_validation(base_org_and_users, db_roles):
    """
    SMK-PR-050: Multi-step approval chain: Step 1 approval keeps PR in MANAGER_REVIEW;
    final step transitions to APPROVED.
    SMK-AP-005: Approver with wrong role cannot approve a step.
    """
    ctx = base_org_and_users
    today = timezone.now().date()

    policy = create_approval_policy_service(
        name="Multi-Tier Approval Policy",
        module=ApprovalPolicy.MODULE_PR,
        min_amount=Decimal("0.00"),
        department=ctx["dept_it"],
    )
    add_approval_step_service(
        policy=policy,
        step_number=1,
        approver_role=db_roles[Role.DEPT_APPROVER],
        specific_approver=ctx["dept_approver"],
        description="Department Manager Approval",
    )
    add_approval_step_service(
        policy=policy,
        step_number=2,
        approver_role=db_roles[Role.FINANCE_AP],
        specific_approver=ctx["finance_approver"],
        description="Finance Budget Approval",
    )

    pr = create_purchase_requisition_service(
        title="Tiered Review Infrastructure",
        justification="Multi-step approval verification",
        requester=ctx["requester"],
        department=ctx["dept_it"],
        cost_center=ctx["cost_center"],
        requested_delivery_date=today + timedelta(days=14),
        line_items=[
            {
                "item_description": "Edge Router",
                "quantity": Decimal("1"),
                "estimated_unit_price": Decimal("15000.00"),
            }
        ],
    )
    submit_purchase_requisition_service(requisition=pr, user=ctx["requester"])
    assert pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW

    # SMK-AP-005: Finance approver tries to approve Step 1 (requires DEPT_APPROVER) -> rejected!
    with pytest.raises(ValidationError) as exc:
        approve_purchase_requisition_service(
            requisition=pr, approver=ctx["finance_approver"], comments="Unauthorized Step 1"
        )
    assert "not authorized to approve step 1" in str(exc.value)

    # Step 1: Authorized Dept Approver approves
    pr = approve_purchase_requisition_service(
        requisition=pr, approver=ctx["dept_approver"], comments="Step 1 Dept Approved"
    )
    # SMK-PR-050: Must NOT be marked APPROVED yet! Must advance and stay in MANAGER_REVIEW
    assert pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW

    # Step 2: Authorized Finance Approver approves
    pr = approve_purchase_requisition_service(
        requisition=pr, approver=ctx["finance_approver"], comments="Step 2 Finance Approved"
    )
    # Final step: Marked APPROVED!
    assert pr.status == PurchaseRequisition.STATUS_APPROVED

    # Verify ApprovalActions
    actions = ApprovalAction.objects.filter(target_object_id=pr.id).order_by("created_at")
    assert actions.count() == 3  # Submit, Step 1 Approve, Step 2 Approve


@pytest.mark.django_db
def test_pr_02_soft_delete_preserves_audit_history(base_org_and_users):
    """
    PR-02: Hard delete of PRs is blocked. pr.delete() and DELETE API endpoint
    perform soft cancel to CANCELLED and preserve records and AuditLog.
    """
    ctx = base_org_and_users
    today = timezone.now().date()

    pr = create_purchase_requisition_service(
        title="PR for Deletion Test",
        justification="Testing soft-delete safety",
        requester=ctx["requester"],
        department=ctx["dept_it"],
        cost_center=ctx["cost_center"],
        requested_delivery_date=today + timedelta(days=10),
        line_items=[
            {
                "item_description": "Test Item",
                "quantity": Decimal("1"),
                "estimated_unit_price": Decimal("100.00"),
            }
        ],
    )
    pr_id = pr.id

    # 1. Model delete() invocation
    pr.delete()
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_CANCELLED
    assert PurchaseRequisition.objects.filter(id=pr_id).exists()

    # 2. REST API DELETE endpoint invocation
    client = APIClient()
    client.force_authenticate(user=ctx["requester"])
    pr2 = create_purchase_requisition_service(
        title="PR for API Deletion Test",
        justification="Testing API soft-delete safety",
        requester=ctx["requester"],
        department=ctx["dept_it"],
        cost_center=ctx["cost_center"],
        requested_delivery_date=today + timedelta(days=10),
        line_items=[
            {
                "item_description": "API Item",
                "quantity": Decimal("1"),
                "estimated_unit_price": Decimal("200.00"),
            }
        ],
    )
    resp = client.delete(f"/api/v1/requisitions/{pr2.id}/")
    assert resp.status_code == 204

    pr2.refresh_from_db()
    assert pr2.status == PurchaseRequisition.STATUS_CANCELLED

    # Check AuditLog records ACTION_DELETE
    audit = AuditLog.objects.filter(
        target_model="PurchaseRequisition",
        target_object_id=str(pr2.id),
        action=AuditLog.ACTION_DELETE,
    ).first()
    assert audit is not None
    assert audit.new_state.get("status") == PurchaseRequisition.STATUS_CANCELLED


@pytest.mark.django_db
def test_vmr_cr_010_safe_document_numbering_after_deletions(base_org_and_users):
    """
    VMR-CR-010: Document number generation must not crash with duplicate key
    after records have been deleted.
    """
    ctx = base_org_and_users

    cat = VendorCategory.objects.first()

    # Create 3 vendors
    v1 = register_vendor_service(
        legal_name="Vendor One Alpha",
        tax_identification_number="27AAAAA1111A1Z1",
        category=cat,
        email="v1@alpha.com",
        address="Address 1",
        created_by_user=ctx["proc_exec"],
    )
    v2 = register_vendor_service(
        legal_name="Vendor Two Beta",
        tax_identification_number="27BBBBB2222B1Z2",
        category=cat,
        email="v2@beta.com",
        address="Address 2",
        created_by_user=ctx["proc_exec"],
    )
    v3 = register_vendor_service(
        legal_name="Vendor Three Gamma",
        tax_identification_number="27CCCCC3333C1Z3",
        category=cat,
        email="v3@gamma.com",
        address="Address 3",
        created_by_user=ctx["proc_exec"],
    )

    # Delete Vendor 2
    v2.delete()

    # Create 4th vendor -> Previously caused duplicate key crash (HTTP 500)
    v4 = register_vendor_service(
        legal_name="Vendor Four Delta",
        tax_identification_number="27DDDDD4444D1Z4",
        category=cat,
        email="v4@delta.com",
        address="Address 4",
        created_by_user=ctx["proc_exec"],
    )
    assert v4.vendor_number != v3.vendor_number
    assert v4.vendor_number != v1.vendor_number
    assert Vendor.objects.filter(vendor_number=v4.vendor_number).count() == 1


@pytest.mark.django_db
def test_src_09_sourcing_event_requires_approved_pr(base_org_and_users):
    """
    SRC-09: Sourcing event creation or update must reject a linked PR
    if the PR is not in APPROVED status.
    """
    ctx = base_org_and_users
    today = timezone.now().date()

    pr = create_purchase_requisition_service(
        title="Draft PR Not Approved",
        justification="Should be rejected by sourcing",
        requester=ctx["requester"],
        department=ctx["dept_it"],
        cost_center=ctx["cost_center"],
        requested_delivery_date=today + timedelta(days=10),
        line_items=[
            {
                "item_description": "Compute Blades",
                "quantity": Decimal("1"),
                "estimated_unit_price": Decimal("5000.00"),
            }
        ],
    )
    assert pr.status == PurchaseRequisition.STATUS_DRAFT

    # Sourcing event creation with DRAFT PR must raise ValidationError
    start_time = timezone.now() + timedelta(days=1)
    end_time = timezone.now() + timedelta(days=7)

    with pytest.raises(ValidationError) as exc:
        create_sourcing_event_service(
            title="Compute Sourcing RFQ",
            event_type=SourcingEvent.TYPE_RFQ,
            bid_start_date=start_time,
            bid_end_date=end_time,
            description="Procurement of compute blades",
            requisition=pr,
            created_by_user=ctx["proc_exec"],
        )
    assert "Only APPROVED requisitions may be sourced" in str(exc.value)


@pytest.mark.django_db
def test_ven_07_mime_validation_on_upload(base_org_and_users):
    """
    VEN-07: Uploading a vendor document with mismatched MIME type raises ValidationError.
    """
    ctx = base_org_and_users

    # Spoofed file: .pdf extension with HTML/executable mime type
    fake_file = SimpleUploadedFile(
        name="contract.pdf",
        content=b"%PDF-1.4 dummy valid pdf content header",
        content_type="text/html",  # Mismatched MIME
    )

    with pytest.raises(ValidationError) as exc:
        upload_vendor_document_service(
            vendor=ctx["vendor"],
            user=ctx["proc_exec"],
            file=fake_file,
            document_type="OTHER",
            title="Spoofed MIME Document",
        )
    assert "Invalid MIME type" in str(exc.value)

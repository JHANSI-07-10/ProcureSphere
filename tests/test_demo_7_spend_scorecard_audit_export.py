from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.services import allocate_budget_service, check_and_reserve_budget_service
from apps.orders.services import acknowledge_purchase_order_service, generate_purchase_order_service
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.services import create_goods_receipt_service
from apps.reports.models import ExportJob
from apps.reports.services import (
    generate_export_job_service,
    get_spend_analytics_report,
    get_supplier_performance_report,
)
from apps.requisitions.services import create_purchase_requisition_service
from apps.scorecards.services import calculate_vendor_scorecard_service
from apps.vendors.models import VendorCategory
from apps.vendors.services import register_vendor_service


@pytest.mark.django_db
def test_demo_7_spend_scorecard_and_audited_report_export():
    """
    Day-90 Acceptance Demonstration #7:
    Show supplier performance and spend dashboards and export an audit report tracing complete transaction lifecycle.
    """
    admin_role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Admin"})
    proc_mgr_role, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Manager"})
    stores_role, _ = Role.objects.get_or_create(
        code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"}
    )

    admin_user = User.objects.create_user(
        email="admin.d7@hpe.com", password="password123", role=admin_role
    )
    proc_mgr = User.objects.create_user(
        email="manager.d7@hpe.com", password="password123", role=proc_mgr_role
    )
    stores_user = User.objects.create_user(
        email="stores.d7@hpe.com", password="password123", role=stores_role
    )

    org = Organization.objects.create(name="HPE Global Enterprise", code="HPE-GLOB")
    dept = Department.objects.create(organization=org, name="IT Infrastructure", code="IT-INFRA")
    cost_center = CostCenter.objects.create(department=dept, code="CC-IT-001", name="Cloud Tech")
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q1",
        start_date=timezone.now().date() - timezone.timedelta(days=10),
        end_date=timezone.now().date() + timezone.timedelta(days=90),
    )
    allocate_budget_service(
        cost_center=cost_center, fiscal_period=fiscal_period, allocated_amount=Decimal("100000.00")
    )

    category = VendorCategory.objects.create(name="Cloud Hardware D7", code="CAT-CLOUD-D7")
    vendor = register_vendor_service(
        legal_name="OmniCloud Networks Inc",
        tax_identification_number="TAX-OMNI-777",
        category=category,
        email="accounts@omnicloud.com",
        address="300 Cloud Way",
    )

    pr = create_purchase_requisition_service(
        title="High Performance Switches",
        justification="Core network switch upgrade for Cloud Tech cost center.",
        requester=proc_mgr,
        department=dept,
        cost_center=cost_center,
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=30),
        line_items=[
            {
                "item_description": "Network Switch 48-Port",
                "quantity": 5,
                "estimated_unit_price": 2000.00,
            }
        ],
    )
    check_and_reserve_budget_service(requisition=pr, requested_by_user=proc_mgr)

    po = generate_purchase_order_service(
        vendor=vendor,
        cost_center=cost_center,
        requisition=pr,
        line_items=[
            {"item_description": "Network Switch 48-Port", "quantity": 5, "unit_price": 2000.00}
        ],
        created_by_user=proc_mgr,
    )
    acknowledge_purchase_order_service(po=po, vendor_user=proc_mgr)

    create_goods_receipt_service(
        po=po,
        received_by=stores_user,
        receipt_items=[
            {"po_line": po.lines.first(), "quantity_received": 5, "quantity_accepted": 4}
        ],
    )

    # 1. Automated Vendor Scorecard calculation
    scorecard = calculate_vendor_scorecard_service(
        vendor=vendor,
        evaluation_period="Q1-2026",
        evaluated_by_user=proc_mgr,
        comments="80% acceptance rate on 48-Port Network Switches delivery.",
    )

    assert scorecard.vendor == vendor
    assert scorecard.composite_score is not None
    assert scorecard.quality_score == Decimal("80.00")  # 4 accepted out of 5 received

    # 2. Spend analytics report & Supplier performance report
    from apps.invoices.models import SupplierInvoice

    SupplierInvoice.objects.create(
        vendor=vendor,
        po=po,
        invoice_number="INV-OMNI-001",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("10000.00"),
        total_amount=Decimal("10000.00"),
        status=SupplierInvoice.STATUS_RECEIVED,
    )

    spend_report = get_spend_analytics_report()
    assert len(spend_report) >= 1

    scorecard_report = get_supplier_performance_report()
    assert len(scorecard_report) >= 1
    assert scorecard_report[0]["vendor"] == "OmniCloud Networks Inc"

    # 3. Create & Execute Audited Export Job
    export_job = ExportJob.objects.create(
        report_type="supplier_performance",
        export_format=ExportJob.FORMAT_CSV,
        status=ExportJob.STATUS_PENDING,
        requested_by=admin_user,
    )

    processed_job = generate_export_job_service(export_job.id)

    assert processed_job.status == ExportJob.STATUS_COMPLETED
    assert processed_job.result_file is not None
    assert processed_job.result_file.name != ""

    # 4. Verify Immutable Audit Log recorded for export action
    export_audit = AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ExportJob",
        target_object_id=str(export_job.id),
    ).first()

    assert export_audit is not None
    assert export_audit.actor == admin_user
    assert export_audit.new_state["report_type"] == "supplier_performance"

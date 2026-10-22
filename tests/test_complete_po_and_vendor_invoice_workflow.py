from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.models import SpendLedger
from apps.budgets.services import allocate_budget_service
from apps.invoices.models import MatchException, MatchTolerancePolicy, SupplierInvoice
from apps.invoices.services import mark_invoice_paid_service, resolve_match_exception_service
from apps.orders.models import PurchaseOrder
from apps.orders.services import (
    acknowledge_purchase_order_service,
    approve_purchase_order_service,
    generate_purchase_order_service,
    submit_purchase_order_for_approval_service,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.services import create_goods_receipt_service
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def workflow_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Enterprise Workflow Org", code="HPE-PO-ORG")
    dept = Department.objects.create(organization=org, name="Infrastructure", code="DEPT-INFRA")
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-INFRA-77", name="Core Tech Infrastructure"
    )
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-PO-Q1",
        start_date=timezone.now().date() - timezone.timedelta(days=15),
        end_date=timezone.now().date() + timezone.timedelta(days=75),
    )
    budget = allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("200000.00"),
    )

    category = VendorCategory.objects.create(name="Enterprise Hardware", code="CAT-ENT-HW")
    vendor = Vendor.objects.create(
        legal_name="Apex Global Networks Ltd",
        tax_identification_number="TAX-APEX-999",
        category=category,
        email="billing@apexnetworks.com",
        status=Vendor.STATUS_ACTIVE,
    )

    proc_exec_role = Role.objects.get(code=Role.PROC_EXEC)
    proc_mgr_role = Role.objects.get(code=Role.PROC_MGR)
    stores_role = Role.objects.get(code=Role.STORES_RECEIVER)
    finance_role = Role.objects.get(code=Role.FINANCE_AP)
    vendor_role = Role.objects.get(code=Role.VENDOR_USER)

    proc_exec = User.objects.create_user(
        email="proc_exec.po@hpe.com", password="Password123!", role=proc_exec_role
    )
    proc_mgr = User.objects.create_user(
        email="proc_mgr.po@hpe.com", password="Password123!", role=proc_mgr_role
    )
    stores_user = User.objects.create_user(
        email="stores.po@hpe.com", password="Password123!", role=stores_role, department=dept
    )
    finance_user = User.objects.create_user(
        email="finance.po@hpe.com", password="Password123!", role=finance_role
    )
    vendor_user = User.objects.create_user(
        email="portal@apexnetworks.com",
        password="Password123!",
        role=vendor_role,
        vendor=vendor,
    )

    # Set 2% price and 0% qty tolerance
    policy = MatchTolerancePolicy.get_current()
    policy.price_tolerance_pct = Decimal("0.02")
    policy.qty_tolerance_pct = Decimal("0.00")
    policy.save()

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "budget": budget,
        "vendor": vendor,
        "proc_exec": proc_exec,
        "proc_mgr": proc_mgr,
        "stores_user": stores_user,
        "finance_user": finance_user,
        "vendor_user": vendor_user,
    }


@pytest.mark.django_db
def test_complete_po_workflow_from_draft_to_payment(workflow_setup):
    """
    Validates Stage 3 through Stage 11 end-to-end:
    1. PO Creation in DRAFT -> submit for approval -> APPROVAL -> approve -> ISSUED
    2. Vendor acknowledgement
    3. Goods receipt & inspection by Stores
    4. Vendor invoice submission via Vendor Portal view
    5. Duplicate invoice number detection
    6. 3-way match execution & tolerance exception handling
    7. Exception resolution by Finance
    8. Payment recording and SpendLedger commitment -> actual conversion
    """
    data = workflow_setup
    vendor = data["vendor"]
    cost_center = data["cost_center"]
    proc_exec = data["proc_exec"]
    proc_mgr = data["proc_mgr"]
    stores_user = data["stores_user"]
    finance_user = data["finance_user"]
    vendor_user = data["vendor_user"]

    # --- Stage 3 & 4: PO Creation, Approval and Issuance ---
    po = generate_purchase_order_service(
        vendor=vendor,
        cost_center=cost_center,
        line_items=[
            {
                "item_description": "Network Core Router 10GbE",
                "quantity": 10,
                "unit_price": 5000.00,
                "unit_of_measure": "EA",
            }
        ],
        created_by_user=proc_exec,
        initial_status=PurchaseOrder.STATUS_DRAFT,
    )
    assert po.status == PurchaseOrder.STATUS_DRAFT
    assert po.total_amount == Decimal("55000.00")  # 50,000 + 10% tax

    # Submit for approval
    po = submit_purchase_order_for_approval_service(po=po, actor=proc_exec)
    assert po.status == PurchaseOrder.STATUS_APPROVAL

    # Approve by Procurement Manager -> transitions to ISSUED and commits budget
    po = approve_purchase_order_service(
        po=po, approver=proc_mgr, comments="Approved for Core Tech upgrade."
    )
    assert po.status == PurchaseOrder.STATUS_ISSUED

    data["budget"].refresh_from_db()
    assert data["budget"].committed_amount == Decimal("55000.00")

    # --- Stage 5: Vendor PO Acknowledgement ---
    po = acknowledge_purchase_order_service(
        po=po,
        vendor_user=vendor_user,
        acknowledgement_notes="Confirmed shipment delivery within 14 business days.",
    )
    assert po.status == PurchaseOrder.STATUS_ACKNOWLEDGED
    assert po.acknowledged_at is not None

    # --- Stage 6: Stores Goods Receipt & Inspection ---
    grn = create_goods_receipt_service(
        po=po,
        received_by=stores_user,
        receipt_items=[
            {
                "po_line": po.lines.first(),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            }
        ],
        delivery_note_number="DN-APEX-8821",
    )
    assert grn.grn_number.startswith("GRN-")
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED

    # --- Stage 7: Vendor Invoice Submission via Vendor Portal ---
    client = Client()
    client.force_login(vendor_user)

    # Test GET on vendor invoice submit page
    resp_get = client.get(f"/vendor/purchase-orders/{po.id}/submit-invoice/")
    assert resp_get.status_code == 200

    # Test POST to submit invoice with price variance exceeding tolerance (5300 > 5000 * 1.02)
    resp_post = client.post(
        f"/vendor/purchase-orders/{po.id}/submit-invoice/",
        data={
            "po_id": str(po.id),
            "invoice_number": "INV-APEX-2026-001",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + timezone.timedelta(days=30)).isoformat(),
            f"qty_{po.lines.first().id}": "10.00",
            f"price_{po.lines.first().id}": "5300.00",  # 6% variance (tolerance is 2%)
            "notes": "Original billing with surcharge.",
        },
    )
    assert resp_post.status_code == 302

    invoice = SupplierInvoice.objects.get(vendor=vendor, invoice_number="INV-APEX-2026-001")
    assert invoice.po == po
    assert invoice.total_amount == Decimal("58300.00")  # 53,000 + 10% tax

    # --- Stage 8 & 9: 3-Way Match resulted in Match Exception ---
    assert invoice.status == SupplierInvoice.STATUS_EXCEPTION
    exception = invoice.exceptions.filter(status=MatchException.STATUS_OPEN).first()
    assert exception is not None
    assert exception.exception_type == MatchException.TYPE_PRICE_VARIANCE

    # --- Test Duplicate Invoice Detection ---
    with pytest.raises(ValidationError):
        from apps.invoices.services import create_supplier_invoice_service

        create_supplier_invoice_service(
            vendor=vendor,
            po=po,
            invoice_number="INV-APEX-2026-001",  # Duplicate number
            invoice_date=timezone.now().date(),
            due_date=timezone.now().date(),
            line_items=[
                {"po_line": po.lines.first(), "quantity": 10, "unit_price": Decimal("5000.00")}
            ],
            created_by_user=vendor_user,
        )

    # --- Stage 10: Exception Resolution by Finance ---
    resolve_match_exception_service(
        match_exception=exception,
        resolved_by_user=finance_user,
        resolution_notes="Approved variance due to expedited freight agreement.",
        action="RESOLVE",
    )
    invoice.refresh_from_db()
    assert invoice.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT

    # --- Stage 11: Ready for Payment & Payment Recording ---
    paid_invoice = mark_invoice_paid_service(invoice=invoice, user=finance_user)
    assert paid_invoice.status == SupplierInvoice.STATUS_PAID

    data["budget"].refresh_from_db()
    assert data["budget"].actual_amount == Decimal("58300.00")

    # Verify SpendLedger recorded ACTUAL spend entry
    ledger_entry = SpendLedger.objects.filter(
        budget=data["budget"], entry_type=SpendLedger.ENTRY_ACTUAL
    ).first()
    assert ledger_entry is not None
    assert ledger_entry.amount == Decimal("58300.00")

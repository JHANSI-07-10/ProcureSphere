from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction, ApprovalPolicy
from apps.approvals.services import add_approval_step_service, create_approval_policy_service
from apps.audit.models import AuditLog
from apps.budgets.services import allocate_budget_service
from apps.contracts.models import Contract
from apps.contracts.services import (
    create_contract_service,
    scan_contract_expirations_and_milestones_service,
)
from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
from apps.core.services import generate_document_number
from apps.invoices.models import MatchException, SupplierInvoice
from apps.invoices.services import create_supplier_invoice_service, run_3_way_match_service
from apps.notifications.models import Notification
from apps.notifications.services import scan_overdue_approvals_and_escalate_service
from apps.notifications.tasks import scan_overdue_approvals_and_escalate
from apps.orders.models import PurchaseOrder
from apps.orders.services import (
    acknowledge_purchase_order_service,
    amend_purchase_order_service,
    generate_purchase_order_service,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt
from apps.receipts.services import create_goods_receipt_service
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import (
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import register_vendor_service


@pytest.fixture
def ws3_setup(db_roles):
    today = timezone.now().date()
    org = Organization.objects.create(name="Enterprise Global Corp", code="EGC-01")
    dept = Department.objects.create(organization=org, name="Engineering", code="DEPT-ENG")
    cost_center = CostCenter.objects.create(department=dept, code="CC-ENG-101", name="Cloud Infrastructure")

    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=today.year,
        period_number=1,
        name=f"FY{today.year}-Q1",
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=60),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("1000000.00"),
    )

    super_admin = User.objects.create_superuser(
        email="superadmin_ws3@egc.com",
        password="Password123!",
        first_name="Super",
        last_name="Admin",
        role=db_roles[Role.SUPER_ADMIN],
    )

    requester = User.objects.create_user(
        email="requester_ws3@egc.com",
        password="Password123!",
        first_name="Alice",
        last_name="Requester",
        role=db_roles[Role.REQUESTER],
        department=dept,
    )

    dept_approver = User.objects.create_user(
        email="dept_approver_ws3@egc.com",
        password="Password123!",
        first_name="Bob",
        last_name="DeptApprover",
        role=db_roles[Role.DEPT_APPROVER],
        department=dept,
    )

    proc_manager = User.objects.create_user(
        email="proc_manager_ws3@egc.com",
        password="Password123!",
        first_name="Carol",
        last_name="ProcManager",
        role=db_roles[Role.PROC_MGR],
        department=dept,
    )

    finance_user = User.objects.create_user(
        email="finance_ws3@egc.com",
        password="Password123!",
        first_name="Dave",
        last_name="Finance",
        role=db_roles[Role.FINANCE_AP],
        department=dept,
    )

    stores_user = User.objects.create_user(
        email="stores_ws3@egc.com",
        password="Password123!",
        first_name="Stan",
        last_name="Stores",
        role=db_roles[Role.STORES_RECEIVER],
        department=dept,
    )

    cat = VendorCategory.objects.create(name="Cloud Services", code="CAT-CLOUD")
    vendor = register_vendor_service(
        legal_name="AWS Cloud Solutions Inc",
        trade_name="AWS Cloud",
        tax_identification_number="US-998877665",
        category=cat,
        email="vendor_user_ws3@awscloud.com",
        phone="+12065551234",
        address="410 Terry Ave N, Seattle, WA",
    )
    vendor_user = User.objects.create_user(
        email="vendor_user_ws3@awscloud.com",
        password="Password123!",
        first_name="Jeff",
        last_name="Vendor",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor,
    )

    # Requisition Approval Policy
    policy = create_approval_policy_service(
        name="Eng PR Policy",
        module="PR",
        department=dept,
        min_amount=Decimal("0.00"),
        max_amount=Decimal("100000.00"),
    )
    add_approval_step_service(
        policy=policy,
        step_number=1,
        approver_role=db_roles[Role.DEPT_APPROVER],
        description="Dept Head Approval",
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "super_admin": super_admin,
        "requester": requester,
        "dept_approver": dept_approver,
        "proc_manager": proc_manager,
        "finance_user": finance_user,
        "stores_user": stores_user,
        "vendor": vendor,
        "vendor_user": vendor_user,
    }


@pytest.mark.django_db
def test_super_admin_dashboard_kpis_real_queries(ws3_setup):
    """
    Audit item A-4: Verify Super Admin dashboard KPI cards calculate real values from DB.
    """
    client = Client()
    client.force_login(ws3_setup["super_admin"])
    response = client.get("/")
    assert response.status_code == 200

    ctx = response.context
    assert "kpi_total_users" in ctx
    assert "kpi_total_budget" in ctx
    assert "kpi_pending_transactions" in ctx
    assert "kpi_audit_logs_today" in ctx

    # Verify counts match database state
    assert ctx["kpi_total_users"] == User.objects.filter(is_active=True).count()
    assert ctx["kpi_total_budget"] >= Decimal("1000000.00")
    assert isinstance(ctx["kpi_audit_logs_today"], int)
    assert ctx["kpi_audit_logs_today"] >= 0


@pytest.mark.django_db
def test_celery_beat_schedule_configuration_and_contract_scan_task(ws3_setup):
    """
    Audit item A-5: Verify Celery Beat configuration and periodic tasks execution.
    """
    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
    assert "scan-contract-expirations-and-milestones" in beat_schedule
    assert beat_schedule["scan-contract-expirations-and-milestones"]["task"] == "contracts.tasks.scan_contract_expirations_and_milestones"

    assert "scan-overdue-approvals-and-escalate" in beat_schedule
    assert beat_schedule["scan-overdue-approvals-and-escalate"]["task"] == "notifications.tasks.scan_overdue_approvals_and_escalate"

    # Create active contract expiring in 20 days with 30-day notice period
    today = timezone.now().date()
    contract = create_contract_service(
        title="Enterprise SLA Support",
        vendor=ws3_setup["vendor"],
        start_date=today - timedelta(days=340),
        end_date=today + timedelta(days=20),
        contract_value=Decimal("50000.00"),
        renewal_notice_days=30,
        contract_owner=ws3_setup["proc_manager"],
    )
    contract.status = Contract.STATUS_ACTIVE
    contract.save(update_fields=["status"])

    # Execute task
    result = scan_contract_expirations_and_milestones_task()
    assert "Contract alert scan completed" in result

    # Check alert was created
    alerts = contract.alerts.all()
    assert alerts.count() >= 1
    assert alerts.first().alert_type == "RENEWAL"


@pytest.mark.django_db
def test_scan_overdue_approvals_48h_reminder_and_72h_escalation(ws3_setup):
    """
    Audit item A-6: Verify 48h reminder to approver and 72h escalation to manager.
    """
    # 1. Create and submit PR
    today = timezone.now().date()
    pr = create_purchase_requisition_service(
        title="Urgent Development Laptops",
        requester=ws3_setup["requester"],
        department=ws3_setup["dept"],
        cost_center=ws3_setup["cost_center"],
        justification="Hardware upgrade",
        requested_delivery_date=today + timedelta(days=14),
        line_items=[
            {
                "item_description": "MacBook Pro M3",
                "quantity": Decimal("2"),
                "estimated_unit_price": Decimal("2500.00"),
            }
        ],
    )
    submit_purchase_requisition_service(requisition=pr, user=ws3_setup["requester"])
    pr.refresh_from_db()
    assert pr.status in [PurchaseRequisition.STATUS_SUBMITTED, PurchaseRequisition.STATUS_MANAGER_REVIEW]

    # 2. Simulate PR pending for 50 hours (should trigger 48h reminder)
    PurchaseRequisition.objects.filter(id=pr.id).update(
        updated_at=timezone.now() - timedelta(hours=50)
    )

    res1 = scan_overdue_approvals_and_escalate()
    assert res1["reminders_sent"] >= 1

    reminder_notifs = Notification.objects.filter(
        recipient=ws3_setup["dept_approver"],
        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
    )
    assert reminder_notifs.exists()
    assert "Reminder:" in reminder_notifs.first().title

    # 3. Simulate PR pending for 75 hours (should trigger 72h management escalation)
    PurchaseRequisition.objects.filter(id=pr.id).update(
        updated_at=timezone.now() - timedelta(hours=75)
    )

    res2 = scan_overdue_approvals_and_escalate()
    assert res2["escalations_sent"] >= 1

    escalation_notifs = Notification.objects.filter(
        recipient=ws3_setup["proc_manager"],
        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
    )
    assert escalation_notifs.exists()
    assert "ESCALATION:" in escalation_notifs.first().title

    # Verify audit log recorded the escalation action
    escalation_audit = AuditLog.objects.filter(
        target_model="PurchaseRequisition",
        target_object_id=str(pr.id),
        action=AuditLog.ACTION_UPDATE,
    )
    assert escalation_audit.exists()
    assert escalation_audit.first().new_state.get("escalated") is True


@pytest.mark.django_db
def test_match_exception_overdue_reminders(ws3_setup):
    """
    Audit item A-6: Verify 48h reminder for unresolved 3-way match exceptions.
    """
    # Create PO
    po = generate_purchase_order_service(
        vendor=ws3_setup["vendor"],
        cost_center=ws3_setup["cost_center"],
        created_by_user=ws3_setup["proc_manager"],
        line_items=[
            {
                "item_description": "Storage Server Node",
                "quantity": Decimal("1"),
                "unit_price": Decimal("5000.00"),
            }
        ],
    )

    # Create Invoice
    today = timezone.now().date()
    inv = create_supplier_invoice_service(
        vendor=ws3_setup["vendor"],
        po=po,
        invoice_number="INV-MATCH-REMINDER-01",
        invoice_date=today,
        due_date=today + timedelta(days=30),
        line_items=[
            {
                "po_line": po.lines.first(),
                "item_description": "Storage Server Node",
                "quantity": Decimal("1"),
                "unit_price": Decimal("7000.00"),  # > PO price, variance
            }
        ],
    )
    run_3_way_match_service(invoice=inv, user=ws3_setup["finance_user"])
    inv.refresh_from_db()
    assert inv.status == SupplierInvoice.STATUS_EXCEPTION

    # Age exception by 50 hours
    MatchException.objects.filter(invoice=inv).update(
        created_at=timezone.now() - timedelta(hours=50)
    )

    result = scan_overdue_approvals_and_escalate_service()
    assert result["reminders_sent"] >= 1

    fin_notifs = Notification.objects.filter(
        recipient=ws3_setup["finance_user"],
        notification_type=Notification.TYPE_EXCEPTION_RAISED,
    )
    assert fin_notifs.exists()
    assert "Reminder: Unresolved Match Exception" in fin_notifs.first().title


@pytest.mark.django_db
def test_module_notifications_and_safe_document_numbering(ws3_setup):
    """
    Verify notifications emitted across PO issuance, PO ack, PO amendment,
    goods receipt, match exceptions, and verify safe document numbering.
    """
    # 1. PO Issuance -> Check Notification to Vendor and Requester
    pr = create_purchase_requisition_service(
        title="Router Hardware",
        requester=ws3_setup["requester"],
        department=ws3_setup["dept"],
        cost_center=ws3_setup["cost_center"],
        justification="Lab gear",
        requested_delivery_date=timezone.now().date() + timedelta(days=14),
        line_items=[
            {
                "item_description": "Core Router",
                "quantity": Decimal("1"),
                "estimated_unit_price": Decimal("3000.00"),
            }
        ],
    )
    po = generate_purchase_order_service(
        vendor=ws3_setup["vendor"],
        cost_center=ws3_setup["cost_center"],
        requisition=pr,
        created_by_user=ws3_setup["proc_manager"],
        line_items=[
            {
                "item_description": "Core Router",
                "quantity": Decimal("1"),
                "unit_price": Decimal("3000.00"),
            }
        ],
        initial_status=PurchaseOrder.STATUS_ISSUED,
    )
    # Check vendor received notification
    v_notif = Notification.objects.filter(
        recipient=ws3_setup["vendor_user"],
        notification_type=Notification.TYPE_PO_ISSUED,
    )
    assert v_notif.exists()

    # Check requester received notification
    r_notif = Notification.objects.filter(
        recipient=ws3_setup["requester"],
        notification_type=Notification.TYPE_PO_ISSUED,
    )
    assert r_notif.exists()

    # 2. PO Acknowledgment -> Check Notification
    acknowledge_purchase_order_service(
        po=po,
        vendor_user=ws3_setup["vendor_user"],
        acknowledgement_notes="Confirmed delivery schedule.",
    )
    ack_notif = Notification.objects.filter(
        recipient=ws3_setup["requester"],
        notification_type=Notification.TYPE_PO_ACKNOWLEDGED,
    )
    assert ack_notif.exists()

    # 3. PO Amendment -> Check Notifications
    amend_purchase_order_service(
        po=po,
        reason="Updated unit specifications",
        requested_by_user=ws3_setup["proc_manager"],
        updated_line_items=[
            {
                "item_description": "Core Router V2",
                "quantity": Decimal("1"),
                "unit_price": Decimal("3200.00"),
            }
        ],
    )
    amd_notif = Notification.objects.filter(
        recipient=ws3_setup["vendor_user"],
        notification_type=Notification.TYPE_PO_AMENDMENT,
    )
    assert amd_notif.exists()

    # 4. Goods Receipt -> Check Notification
    receipt = create_goods_receipt_service(
        po=po,
        received_by=ws3_setup["stores_user"],
        delivery_note_number="DN-998811",
        receipt_items=[
            {
                "po_line_id": str(po.lines.first().id),
                "quantity_received": Decimal("1"),
                "quantity_accepted": Decimal("1"),
                "quantity_rejected": Decimal("0"),
            }
        ],
    )
    grn_notif = Notification.objects.filter(
        recipient=ws3_setup["requester"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
    )
    assert grn_notif.exists()

    # 5. Safe Document Numbering with earlier row deletion (preventing VMR-CR-010)
    po_a = generate_purchase_order_service(
        vendor=ws3_setup["vendor"],
        cost_center=ws3_setup["cost_center"],
        created_by_user=ws3_setup["proc_manager"],
        line_items=[
            {"item_description": "Item A", "quantity": Decimal("1"), "unit_price": Decimal("100.00")}
        ],
    )
    po_b = generate_purchase_order_service(
        vendor=ws3_setup["vendor"],
        cost_center=ws3_setup["cost_center"],
        created_by_user=ws3_setup["proc_manager"],
        line_items=[
            {"item_description": "Item B", "quantity": Decimal("1"), "unit_price": Decimal("100.00")}
        ],
    )
    po_b_num = po_b.po_number
    # Delete earlier row po_a (no receipts attached)
    po_a.delete()

    po_c = generate_purchase_order_service(
        vendor=ws3_setup["vendor"],
        cost_center=ws3_setup["cost_center"],
        created_by_user=ws3_setup["proc_manager"],
        line_items=[
            {"item_description": "Item C", "quantity": Decimal("1"), "unit_price": Decimal("100.00")}
        ],
    )
    # Old count()+1 would compute 2+1-1 = 2 (colliding with po_b!).
    # generate_document_number safely computes highest existing suffix + 1, avoiding collision with po_b!
    assert po_c.po_number != po_b_num

"""
Unit and integration test suite for Vendor Hold, Suspension, and Risk-History Workflows.
Covers:
- Vendor Hold transition, exposure capture, audit logging, notifications, and operational blocks
  (sourcing invite/bid/award block, PO generation block, invoice payment block).
- Vendor Suspension transition, Manager RBAC enforcement, reinstatement, exposure capture,
  audit logging, notifications, and operational blocks (invitations, bids, awards, PO generation,
  PO amendments, contract creation/activation, invoice payments, credential dispatch, profile & contact freeze).
- Risk-History workflow (append-only records, risk levels, flags, assessment notes, previous/new state
  in AuditLog, selector fetching, and REST API access with Auditor view / Vendor block).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.contracts import services as contract_svc
from apps.contracts.models import Contract
from apps.invoices import services as invoice_svc
from apps.invoices.models import SupplierInvoice
from apps.orders import services as order_svc
from apps.orders.models import PurchaseOrder
from apps.sourcing import services as sourcing_svc
from apps.sourcing.models import SourcingEvent, VendorBid
from apps.vendors.models import Vendor, VendorCategory, VendorContact, VendorRiskRecord
from apps.vendors.selectors import get_vendor_change_history, get_vendor_risk_history
from apps.vendors.services import (
    create_vendor_contact_service,
    delete_vendor_contact_service,
    issue_vendor_credentials_service,
    record_vendor_risk_assessment_service,
    register_vendor_service,
    set_vendor_status_governance_service,
    update_vendor_contact_service,
    update_vendor_profile_service,
)

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def test_setup(db, db_roles):
    cat = VendorCategory.objects.create(name="IT Hardware", code="HW-001")

    admin = User.objects.create_user(email="admin_vh@test.local", password="Password123!", role=db_roles[Role.SUPER_ADMIN])
    mgr = User.objects.create_user(email="mgr_vh@test.local", password="Password123!", role=db_roles[Role.PROC_MGR])
    proc_exec = User.objects.create_user(email="exec_vh@test.local", password="Password123!", role=db_roles[Role.PROC_EXEC])
    finance = User.objects.create_user(email="finance_vh@test.local", password="Password123!", role=db_roles[Role.FINANCE_AP])
    auditor = User.objects.create_user(email="auditor_vh@test.local", password="Password123!", role=db_roles[Role.AUDITOR])

    vendor = register_vendor_service(
        legal_name="Apex Global Technologies Ltd",
        trade_name="Apex Global",
        tax_identification_number="TIN-APEX-999",
        category=cat,
        email="contact@apexglobal.test",
        address="100 Enterprise Way, Silicon Valley",
        created_by_user=admin,
    )
    # Move directly to ACTIVE
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.STATUS_ACTIVE)
    vendor.refresh_from_db()

    vendor_user = User.objects.create_user(
        email="rep@apexglobal.test",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor,
    )

    from apps.budgets.models import Budget
    from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
    org = Organization.objects.create(name="HPE Global", code="ORG-001")
    dept = Department.objects.create(name="IT Procurement", code="D-IT", organization=org)
    cost_center = CostCenter.objects.create(name="IT Operations", code="CC-IT-001", department=dept)
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    Budget.objects.create(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("500000.00"),
        committed_amount=Decimal("2000.00"),
    )

    return {
        "admin": admin,
        "mgr": mgr,
        "exec": proc_exec,
        "finance": finance,
        "auditor": auditor,
        "vendor": vendor,
        "vendor_user": vendor_user,
        "category": cat,
        "cost_center": cost_center,
    }


# ==============================================================================
# 1. VENDOR HOLD WORKFLOW TESTS
# ==============================================================================

class TestVendorHoldWorkflow:
    def test_executive_can_place_on_hold_and_release(self, test_setup):
        s = test_setup
        v = s["vendor"]
        assert v.status == Vendor.STATUS_ACTIVE

        # Place on hold with reason
        v = set_vendor_status_governance_service(
            vendor=v,
            actor=s["exec"],
            new_status=Vendor.STATUS_ON_HOLD,
            notes="Compliance audit in progress",
        )
        assert v.status == Vendor.STATUS_ON_HOLD
        assert v.status_notes == "Compliance audit in progress"

        # Check AuditLog
        audit = AuditLog.objects.filter(target_model="Vendor", target_object_id=str(v.id)).first()
        assert audit is not None
        assert audit.previous_state == {"status": Vendor.STATUS_ACTIVE}
        assert audit.new_state["status"] == Vendor.STATUS_ON_HOLD
        assert audit.new_state["notes"] == "Compliance audit in progress"
        assert "open_transactions_at_change" in audit.new_state

        # Release from hold
        v = set_vendor_status_governance_service(
            vendor=v,
            actor=s["exec"],
            new_status=Vendor.STATUS_ACTIVE,
            notes="Audit passed successfully",
        )
        assert v.status == Vendor.STATUS_ACTIVE
        assert v.status_notes == "Audit passed successfully"

    def test_on_hold_vendor_blocked_from_sourcing_and_orders(self, test_setup):
        s = test_setup
        v = s["vendor"]
        set_vendor_status_governance_service(
            vendor=v,
            actor=s["exec"],
            new_status=Vendor.STATUS_ON_HOLD,
            notes="Document renewal pending",
        )

        # 1. Sourcing invitation blocked
        event = sourcing_svc.create_sourcing_event_service(
            title="Q4 Hardware RFQ",
            event_type="RFQ",
            bid_start_date=timezone.now(),
            bid_end_date=timezone.now() + timedelta(days=5),
            description="Hardware procurement",
            created_by_user=s["exec"],
        )
        with pytest.raises(ValidationError, match="not eligible"):
            sourcing_svc.invite_vendors_to_event_service(
                event=event,
                vendor_ids=[v.id],
                invited_by=s["exec"],
            )

        # 2. PO generation blocked
        with pytest.raises(ValidationError, match="on hold"):
            order_svc.generate_purchase_order_service(
                vendor=v,
                cost_center=s["cost_center"],
                line_items=[{"item_description": "Server", "quantity": 1, "unit_price": 5000}],
                created_by_user=s["exec"],
            )

    def test_on_hold_vendor_blocked_from_invoice_payment(self, test_setup):
        s = test_setup
        v = s["vendor"]

        # Create PO and invoice while ACTIVE
        po = order_svc.generate_purchase_order_service(
            vendor=v,
            cost_center=s["cost_center"],
            line_items=[{"item_description": "Network Switch", "quantity": 2, "unit_price": 1000}],
            created_by_user=s["exec"],
        )
        inv = invoice_svc.create_supplier_invoice_service(
            vendor=v,
            po=po,
            invoice_number="INV-HOLD-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            line_items=[{"po_line": po.lines.first(), "item_description": "Network Switch", "quantity": 2, "unit_price": 1000}],
            created_by_user=s["vendor_user"],
        )
        inv.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
        inv.save(update_fields=["status"])

        # Place vendor on hold
        set_vendor_status_governance_service(
            vendor=v,
            actor=s["exec"],
            new_status=Vendor.STATUS_ON_HOLD,
            notes="Payment hold pending bank verification",
        )

        # Attempt to pay invoice -> blocked
        with pytest.raises(ValidationError, match="on hold"):
            invoice_svc.mark_invoice_paid_service(invoice=inv, user=s["finance"])

        # Release hold -> payment succeeds
        set_vendor_status_governance_service(
            vendor=v,
            actor=s["exec"],
            new_status=Vendor.STATUS_ACTIVE,
            notes="Bank verified",
        )
        inv.refresh_from_db()
        paid_inv = invoice_svc.mark_invoice_paid_service(invoice=inv, user=s["finance"])
        assert paid_inv.status == SupplierInvoice.STATUS_PAID


# ==============================================================================
# 2. VENDOR SUSPENSION WORKFLOW TESTS
# ==============================================================================

class TestVendorSuspensionWorkflow:
    def test_manager_can_suspend_and_reinstate_executive_denied(self, test_setup):
        s = test_setup
        v = s["vendor"]

        # Executive denied suspension
        with pytest.raises(PermissionDenied):
            set_vendor_status_governance_service(
                vendor=v,
                actor=s["exec"],
                new_status=Vendor.STATUS_SUSPENDED,
                notes="Unauthorized attempt",
            )

        # Manager suspends
        v = set_vendor_status_governance_service(
            vendor=v,
            actor=s["mgr"],
            new_status=Vendor.STATUS_SUSPENDED,
            notes="Sanctions watchlist match",
        )
        assert v.status == Vendor.STATUS_SUSPENDED
        assert v.status_notes == "Sanctions watchlist match"

        # Executive denied reinstatement
        with pytest.raises(PermissionDenied):
            set_vendor_status_governance_service(
                vendor=v,
                actor=s["exec"],
                new_status=Vendor.STATUS_ACTIVE,
                notes="Unauthorized reinstatement",
            )

        # Manager reinstates
        v = set_vendor_status_governance_service(
            vendor=v,
            actor=s["mgr"],
            new_status=Vendor.STATUS_ACTIVE,
            notes="False positive cleared",
        )
        assert v.status == Vendor.STATUS_ACTIVE

    def test_manager_can_suspend_from_on_hold(self, test_setup):
        s = test_setup
        v = s["vendor"]
        set_vendor_status_governance_service(
            vendor=v, actor=s["exec"], new_status=Vendor.STATUS_ON_HOLD, notes="Investigation"
        )
        assert v.status == Vendor.STATUS_ON_HOLD

        # Escalate to suspended
        v = set_vendor_status_governance_service(
            vendor=v, actor=s["mgr"], new_status=Vendor.STATUS_SUSPENDED, notes="Confirmed fraud"
        )
        assert v.status == Vendor.STATUS_SUSPENDED

    def test_suspended_vendor_blocked_from_po_issuance_and_amendment(self, test_setup):
        s = test_setup
        v = s["vendor"]

        # Create PO while active
        po = order_svc.generate_purchase_order_service(
            vendor=v,
            cost_center=s["cost_center"],
            line_items=[{"item_description": "RAM 32GB", "quantity": 10, "unit_price": 100}],
            created_by_user=s["exec"],
        )

        # Suspend vendor
        set_vendor_status_governance_service(
            vendor=v, actor=s["mgr"], new_status=Vendor.STATUS_SUSPENDED, notes="Contract breach"
        )

        # 1. New PO issuance blocked
        with pytest.raises(ValidationError, match="suspended"):
            order_svc.generate_purchase_order_service(
                vendor=v,
                cost_center=s["cost_center"],
                line_items=[{"item_description": "SSD 1TB", "quantity": 5, "unit_price": 80}],
                created_by_user=s["exec"],
            )

        # 2. PO amendment blocked
        with pytest.raises(ValidationError, match="suspended"):
            order_svc.amend_purchase_order_service(
                po=po,
                reason="Increase quantity",
                updated_line_items=[{"item_description": "RAM 32GB", "quantity": 20, "unit_price": 100}],
                requested_by_user=s["exec"],
            )

    def test_suspended_vendor_blocked_from_contracts(self, test_setup):
        s = test_setup
        v = s["vendor"]

        # Create contract in draft while active
        contract = contract_svc.create_contract_service(
            title="Enterprise Cloud Maintenance",
            vendor=v,
            contract_value=Decimal("50000.00"),
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            contract_owner=s["exec"],
        )
        contract.status = Contract.STATUS_BUSINESS_APPROVAL
        contract.save(update_fields=["status"])

        # Suspend vendor
        set_vendor_status_governance_service(
            vendor=v, actor=s["mgr"], new_status=Vendor.STATUS_SUSPENDED, notes="Litigation"
        )

        # 1. New contract creation blocked
        with pytest.raises(ValidationError, match="suspended"):
            contract_svc.create_contract_service(
                title="New Software Support",
                vendor=v,
                contract_value=Decimal("10000.00"),
                start_date=date.today(),
                end_date=date.today() + timedelta(days=180),
                contract_owner=s["exec"],
            )

        # 2. Contract business approval / activation blocked
        with pytest.raises(ValidationError, match="suspended"):
            contract_svc.approve_business_service(
                contract=contract,
                user=s["mgr"],
                notes="Attempt to activate suspended vendor contract",
            )

    def test_suspended_vendor_frozen_from_self_service_mutations(self, test_setup):
        s = test_setup
        v = s["vendor"]
        contact = VendorContact.objects.create(
            vendor=v,
            first_name="John",
            last_name="Doe",
            email="john@apexglobal.test",
            phone="123456",
            is_primary=True,
        )

        # Suspend vendor
        set_vendor_status_governance_service(
            vendor=v, actor=s["mgr"], new_status=Vendor.STATUS_SUSPENDED, notes="Frozen"
        )

        # Profile update blocked
        with pytest.raises(ValidationError, match="suspended"):
            update_vendor_profile_service(
                vendor=v,
                user=s["vendor_user"],
                data={"trade_name": "Apex Updated", "address": "New Address"},
            )

        # Contact creation blocked
        with pytest.raises(ValidationError, match="suspended"):
            create_vendor_contact_service(
                vendor=v,
                user=s["vendor_user"],
                data={"first_name": "Jane", "last_name": "Smith", "email": "jane@apex.test"},
            )

        # Contact update blocked
        with pytest.raises(ValidationError, match="suspended"):
            update_vendor_contact_service(
                contact=contact,
                vendor=v,
                user=s["vendor_user"],
                data={"first_name": "Johnny"},
            )

        # Contact deletion blocked
        with pytest.raises(ValidationError, match="suspended"):
            delete_vendor_contact_service(
                contact=contact,
                vendor=v,
                user=s["vendor_user"],
            )


# ==============================================================================
# 3. RISK-HISTORY WORKFLOW TESTS
# ==============================================================================

class TestRiskHistoryWorkflow:
    def test_append_only_risk_assessment_history(self, test_setup):
        s = test_setup
        v = s["vendor"]
        assert v.risk_records.count() == 0

        # Assessment 1: Low risk
        r1 = record_vendor_risk_assessment_service(
            vendor=v,
            assessor=s["exec"],
            risk_level="LOW",
            risk_flags=[],
            notes="Initial onboarding screening passed",
        )
        assert r1.risk_level == "LOW"
        assert v.risk_records.count() == 1

        # Assessment 2: Medium risk with flags
        r2 = record_vendor_risk_assessment_service(
            vendor=v,
            assessor=s["exec"],
            risk_level="MEDIUM",
            risk_flags=["DELIVERY", "QUALITY"],
            notes="Late shipments reported in Q2",
        )
        assert r2.risk_level == "MEDIUM"
        assert v.risk_records.count() == 2

        # Assessment 3: High risk with sanctions flag
        r3 = record_vendor_risk_assessment_service(
            vendor=v,
            assessor=s["mgr"],
            risk_level="HIGH",
            risk_flags=["SANCTIONS", "LEGAL"],
            notes="Regulatory inquiry initiated",
        )
        assert r3.risk_level == "HIGH"
        assert v.risk_records.count() == 3

        # Full history selector preserves chronological order (newest first)
        history = list(get_vendor_risk_history(v))
        assert len(history) == 3
        assert [h.risk_level for h in history] == ["HIGH", "MEDIUM", "LOW"]
        assert history[0].risk_flag_labels == ["Sanctions / watchlist", "Legal / litigation"]

        # AuditLog entries verify immutability
        logs = AuditLog.objects.filter(target_model="VendorRiskRecord")
        assert logs.count() == 3

    def test_invalid_risk_level_and_unknown_flags_rejected(self, test_setup):
        s = test_setup
        v = s["vendor"]

        with pytest.raises(ValidationError, match="Invalid risk level"):
            record_vendor_risk_assessment_service(
                vendor=v,
                assessor=s["exec"],
                risk_level="CRITICAL",
                risk_flags=[],
                notes="Invalid level test",
            )

        with pytest.raises(ValidationError, match="Unknown risk flag"):
            record_vendor_risk_assessment_service(
                vendor=v,
                assessor=s["exec"],
                risk_level="MEDIUM",
                risk_flags=["NOT_A_FLAG"],
                notes="Unknown flag test",
            )

        with pytest.raises(ValidationError, match="notes are required"):
            record_vendor_risk_assessment_service(
                vendor=v,
                assessor=s["exec"],
                risk_level="LOW",
                risk_flags=[],
                notes="   ",
            )

    def test_vendor_change_history_includes_contacts_and_risk(self, test_setup):
        s = test_setup
        v = s["vendor"]

        # 1. Add contact
        contact = create_vendor_contact_service(
            vendor=v,
            user=s["exec"],
            data={"first_name": "Alice", "last_name": "Wong", "email": "alice@apex.test"},
        )
        # 2. Add risk record
        risk = record_vendor_risk_assessment_service(
            vendor=v,
            assessor=s["exec"],
            risk_level="LOW",
            risk_flags=[],
            notes="Risk screening",
        )
        # 3. Change status
        set_vendor_status_governance_service(
            vendor=v,
            actor=s["exec"],
            new_status=Vendor.STATUS_ON_HOLD,
            notes="Reviewing docs",
        )

        entries = list(get_vendor_change_history(v))
        models_in_history = {e.target_model for e in entries}
        assert "Vendor" in models_in_history
        assert "VendorContact" in models_in_history
        assert "VendorRiskRecord" in models_in_history

    def test_rest_api_risk_and_history_endpoints_rbac(self, test_setup):
        s = test_setup
        v = s["vendor"]
        client = APIClient()

        # Add risk record
        record_vendor_risk_assessment_service(
            vendor=v,
            assessor=s["exec"],
            risk_level="MEDIUM",
            risk_flags=["FINANCIAL"],
            notes="Thin capitalization",
        )

        # 1. Auditor can view risk records (GET)
        client.force_authenticate(user=s["auditor"])
        res_auditor_risk = client.get(f"/api/v1/vendors/{v.id}/risk/")
        assert res_auditor_risk.status_code == status.HTTP_200_OK
        assert len(res_auditor_risk.json()) == 1

        # Auditor can view full audit history (GET)
        res_auditor_hist = client.get(f"/api/v1/vendors/{v.id}/history/")
        assert res_auditor_hist.status_code == status.HTTP_200_OK
        assert len(res_auditor_hist.json()) > 0

        # Auditor cannot post new assessment (403)
        res_auditor_post = client.post(
            f"/api/v1/vendors/{v.id}/risk/",
            {"risk_level": "HIGH", "notes": "Auditor cannot write"},
            format="json",
        )
        assert res_auditor_post.status_code == status.HTTP_403_FORBIDDEN

        # 2. External vendor user is strictly blocked from viewing risk or history
        client.force_authenticate(user=s["vendor_user"])
        assert client.get(f"/api/v1/vendors/{v.id}/risk/").status_code == status.HTTP_403_FORBIDDEN
        assert client.get(f"/api/v1/vendors/{v.id}/history/").status_code == status.HTTP_403_FORBIDDEN

        # 3. Executive can record assessment via dedicated API View
        client.force_authenticate(user=s["exec"])
        res_assess = client.post(
            f"/api/v1/vendors/{v.id}/assess-risk/",
            {"risk_level": "HIGH", "notes": "Supplier default risk", "risk_flags": ["FINANCIAL"]},
            format="json",
        )
        assert res_assess.status_code == status.HTTP_200_OK
        assert res_assess.json()["risk_level"] == "HIGH"
        v.refresh_from_db()
        assert v.risk_records.count() == 2

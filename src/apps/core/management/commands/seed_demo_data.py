from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.services import allocate_budget_service
from apps.contracts.services import (
    activate_contract_service,
    add_contract_milestone_service,
    approve_legal_review_service,
    create_contract_service,
    submit_for_legal_review_service,
)
from apps.invoices.services import (
    create_supplier_invoice_service,
    run_3_way_match_service,
)
from apps.orders.services import (
    acknowledge_purchase_order_service,
    amend_purchase_order_service,
    generate_purchase_order_service,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.services import create_goods_receipt_service
from apps.requisitions.services import (
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)
from apps.scorecards.services import calculate_vendor_scorecard_service
from apps.sourcing.models import BidInvite, SourcingEvent, VendorBid
from apps.vendors.models import Vendor, VendorCategory, VendorDocument
from apps.vendors.services import (
    approve_vendor_service,
    register_vendor_service,
    submit_vendor_kyc_service,
    verify_vendor_document_service,
)


class Command(BaseCommand):
    help = "Seeds ProcureSphere 360 with realistic demonstration data. Fully idempotent — safe to run multiple times."

    def get_or_create_user(self, email, role, is_superuser=False, password="Password123!"):
        user, _ = User.objects.get_or_create(email=email, defaults={"role": role, "username": email})
        user.role = role
        user.set_password(password)
        user.is_active = True
        user.is_staff = is_superuser
        user.is_superuser = is_superuser
        user.save()
        return user

    def _activate_vendor(self, vendor, kyc_doc, proc_exec, legal_user, proc_mgr):
        """Submit KYC, verify doc, and approve vendor if not already active."""
        if vendor.status == Vendor.STATUS_ACTIVE:
            return
        if vendor.status in (Vendor.STATUS_DRAFT, Vendor.STATUS_CHANGES_REQUIRED):
            submit_vendor_kyc_service(vendor=vendor, user=proc_exec)
        if not kyc_doc.is_verified:
            verify_vendor_document_service(document=kyc_doc, verifier=legal_user)
        if vendor.status not in (Vendor.STATUS_ACTIVE,):
            approve_vendor_service(vendor=vendor, manager=proc_mgr, notes="Verified & Approved")

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting ProcureSphere 360 idempotent seed..."))

        today = timezone.now().date()

        # ── 1. Roles ──────────────────────────────────────────────────────────
        roles_data = [
            (Role.SUPER_ADMIN, "Super Admin"),
            (Role.REQUESTER, "Requester"),
            (Role.DEPT_APPROVER, "Department Approver"),
            (Role.PROC_EXEC, "Procurement Executive"),
            (Role.PROC_MGR, "Procurement Manager"),
            (Role.FINANCE_AP, "Finance / AP Specialist"),
            (Role.STORES_RECEIVER, "Stores / Receiver"),
            (Role.LEGAL_MGR, "Legal / Contract Manager"),
            (Role.AUDITOR, "Compliance Auditor"),
            (Role.VENDOR_USER, "Vendor Portal User"),
        ]
        roles = {}
        for code, name in roles_data:
            role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
            roles[code] = role

        # ── 2. Users ──────────────────────────────────────────────────────────
        admin_hpe = self.get_or_create_user("admin@hpe.com", roles[Role.SUPER_ADMIN], is_superuser=True)
        self.get_or_create_user("admin@procuresphere.com", roles[Role.SUPER_ADMIN], is_superuser=True, password="ProcureAdmin@360!")
        requester = self.get_or_create_user("requester@hpe.com", roles[Role.REQUESTER])
        self.get_or_create_user("requester01@procuresphere.com", roles[Role.REQUESTER], password="Requester@360!")
        approver = self.get_or_create_user("approver@hpe.com", roles[Role.DEPT_APPROVER])
        self.get_or_create_user("dept.approver01@procuresphere.com", roles[Role.DEPT_APPROVER], password="DeptApprover@360!")
        proc_exec = self.get_or_create_user("procexec@hpe.com", roles[Role.PROC_EXEC])
        self.get_or_create_user("proc.executive01@procuresphere.com", roles[Role.PROC_EXEC], password="ProcExec@360!")
        proc_mgr = self.get_or_create_user("procmgr@hpe.com", roles[Role.PROC_MGR])
        self.get_or_create_user("proc.manager01@procuresphere.com", roles[Role.PROC_MGR], password="ProcManager@360!")
        finance_user = self.get_or_create_user("finance@hpe.com", roles[Role.FINANCE_AP])
        self.get_or_create_user("finance.ap01@procuresphere.com", roles[Role.FINANCE_AP], password="FinanceAP@360!")
        receiver_user = self.get_or_create_user("receiver@hpe.com", roles[Role.STORES_RECEIVER])
        self.get_or_create_user("stores.recv01@procuresphere.com", roles[Role.STORES_RECEIVER], password="StoresRecv@360!")
        legal_user = self.get_or_create_user("legal@hpe.com", roles[Role.LEGAL_MGR])
        self.get_or_create_user("legal.contract01@procuresphere.com", roles[Role.LEGAL_MGR], password="LegalContract@360!")
        self.get_or_create_user("auditor@hpe.com", roles[Role.AUDITOR])
        self.get_or_create_user("compliance.audit01@procuresphere.com", roles[Role.AUDITOR], password="ComplianceAudit@360!")
        vendor_user = self.get_or_create_user("vendoruser@cisco.com", roles[Role.VENDOR_USER])
        self.get_or_create_user("vendor.user01@procuresphere.com", roles[Role.VENDOR_USER], password="VendorUser@360!")

        # ── 3. Organization ───────────────────────────────────────────────────
        org, _ = Organization.objects.get_or_create(code="HPE-GLOBAL", defaults={"name": "Hewlett Packard Enterprise"})
        org.admin_user = admin_hpe
        org.save(update_fields=["admin_user"])
        admin_hpe.organization = org
        admin_hpe.save(update_fields=["organization"])

        dept_it, _ = Department.objects.get_or_create(organization=org, code="IT-OPS", defaults={"name": "IT Infrastructure Operations"})
        dept_ops, _ = Department.objects.get_or_create(organization=org, code="OPS", defaults={"name": "Global Operations"})
        dept_eng, _ = Department.objects.get_or_create(organization=org, code="ENG", defaults={"name": "Hardware Engineering & R&D"})

        for u in [approver, requester]:
            if u.department != dept_it:
                u.department = dept_it
                u.save(update_fields=["department"])

        cost_center, _ = CostCenter.objects.get_or_create(department=dept_it, code="CC-IT-101", defaults={"name": "Compute & Cloud Center", "manager": approver})
        cost_center_ops, _ = CostCenter.objects.get_or_create(department=dept_ops, code="CC-OPS-201", defaults={"name": "Supply Chain & Facilities", "manager": approver})
        cost_center_eng, _ = CostCenter.objects.get_or_create(department=dept_eng, code="CC-ENG-301", defaults={"name": "Hardware Systems R&D", "manager": approver})

        fiscal_period, _ = FiscalPeriod.objects.get_or_create(
            organization=org, year=2026, period_number=1,
            defaults={"name": "FY2026-Q1", "start_date": today - timezone.timedelta(days=30), "end_date": today + timezone.timedelta(days=150)},
        )

        # ── 4. Budgets ────────────────────────────────────────────────────────
        from apps.budgets.models import Budget
        if not Budget.objects.filter(cost_center=cost_center, fiscal_period=fiscal_period).exists():
            allocate_budget_service(cost_center=cost_center, fiscal_period=fiscal_period, allocated_amount=Decimal("250000.00"))
        if not Budget.objects.filter(cost_center=cost_center_ops, fiscal_period=fiscal_period).exists():
            allocate_budget_service(cost_center=cost_center_ops, fiscal_period=fiscal_period, allocated_amount=Decimal("150000.00"))
        if not Budget.objects.filter(cost_center=cost_center_eng, fiscal_period=fiscal_period).exists():
            allocate_budget_service(cost_center=cost_center_eng, fiscal_period=fiscal_period, allocated_amount=Decimal("350000.00"))

        # ── 5. Vendor Categories ──────────────────────────────────────────────
        cat_hw, _ = VendorCategory.objects.get_or_create(code="HW-SERVERS", defaults={"name": "Server Hardware & Appliances"})
        cat_net, _ = VendorCategory.objects.get_or_create(code="NET-EQUIP", defaults={"name": "Networking Equipment"})
        cat_sw, _ = VendorCategory.objects.get_or_create(code="SW-LICENSE", defaults={"name": "Software & Licensing"})

        # ── 6. Three Active Vendors with Organization ─────────────────────────
        def _get_or_create_vendor(tin, legal_name, category, email, address):
            v = Vendor.objects.filter(tax_identification_number=tin).first()
            if not v:
                v = register_vendor_service(
                    legal_name=legal_name, tax_identification_number=tin,
                    category=category, email=email, address=address,
                    created_by_user=proc_exec,
                )
            # Ensure vendor is linked to org so scoping works
            if not v.organization:
                v.organization = org
                v.save(update_fields=["organization"])
            return v

        vendor1 = _get_or_create_vendor("TAX-CISCO-991", "Cisco Systems Inc", cat_net, "enterprise-sales@cisco.com", "170 West Tasman Dr, San Jose, CA")
        vendor2 = _get_or_create_vendor("TAX-INTEL-882", "Intel Corporation", cat_hw, "sales@intel.com", "2200 Mission College Blvd, Santa Clara, CA")
        vendor3 = _get_or_create_vendor("TAX-MSFT-773", "Microsoft Corporation", cat_sw, "enterprise@microsoft.com", "One Microsoft Way, Redmond, WA")

        def _ensure_kyc_doc(vendor, doc_type=VendorDocument.DOC_TYPE_CERT):
            doc = VendorDocument.objects.filter(vendor=vendor, document_type=doc_type).first()
            if not doc:
                doc = VendorDocument.objects.create(
                    vendor=vendor, document_type=doc_type,
                    title="Certificate of Incorporation",
                    file=SimpleUploadedFile("cert.pdf", b"%PDF-1.4 Content", content_type="application/pdf"),
                )
            return doc

        for v in [vendor1, vendor2, vendor3]:
            doc = _ensure_kyc_doc(v)
            self._activate_vendor(v, doc, proc_exec, legal_user, proc_mgr)

        vendor_user.vendor = vendor1
        vendor_user.save(update_fields=["vendor"])
        v_ps = User.objects.filter(email="vendor.user01@procuresphere.com").first()
        if v_ps:
            v_ps.vendor = vendor1
            v_ps.save(update_fields=["vendor"])

        # ── 7. Multi-Step Approval Policy ─────────────────────────────────────
        from apps.approvals.models import ApprovalPolicy
        from apps.approvals.services import add_approval_step_service, create_approval_policy_service

        if not ApprovalPolicy.objects.filter(module=ApprovalPolicy.MODULE_PR, is_active=True).exists():
            policy = create_approval_policy_service(
                name="Standard PR Approval — Two-Level",
                module=ApprovalPolicy.MODULE_PR,
                min_amount=Decimal("0.00"),
                department=None,
            )
            add_approval_step_service(policy=policy, step_number=1, approver_role=roles[Role.DEPT_APPROVER], description="Department Manager Approval")
            add_approval_step_service(policy=policy, step_number=2, approver_role=roles[Role.PROC_MGR], description="Procurement Manager Approval")

        if not ApprovalPolicy.objects.filter(module=ApprovalPolicy.MODULE_PO_AMENDMENT, is_active=True).exists():
            po_amend_policy = create_approval_policy_service(
                name="PO Amendment Approval",
                module=ApprovalPolicy.MODULE_PO_AMENDMENT,
                min_amount=Decimal("0.00"),
            )
            add_approval_step_service(policy=po_amend_policy, step_number=1, approver_role=roles[Role.PROC_MGR], description="Procurement Manager Approval")

        # ── 8. Requisition ────────────────────────────────────────────────────
        from apps.requisitions.models import PurchaseRequisition
        pr = PurchaseRequisition.objects.filter(title="Enterprise Core Routers Upgrade", requester=requester).first()
        if not pr:
            pr = create_purchase_requisition_service(
                title="Enterprise Core Routers Upgrade",
                justification="Upgrade data center core networking switches to support high-throughput cloud cluster.",
                requester=requester, department=dept_it, cost_center=cost_center,
                requested_delivery_date=today + timezone.timedelta(days=30),
                line_items=[
                    {"item_description": "Cisco Catalyst 9600 Switch", "quantity": 2, "estimated_unit_price": 25000.00},
                    {"item_description": "100G SFP Transceiver Module", "quantity": 10, "estimated_unit_price": 1200.00},
                ],
            )
            submit_purchase_requisition_service(requisition=pr, user=requester)

        # ── 9. RFQ with 3 Bids ────────────────────────────────────────────────
        rfq = SourcingEvent.objects.filter(title="RFQ — Core Network Infrastructure Upgrade").first()
        if not rfq:
            rfq = SourcingEvent.objects.create(
                event_number="RFQ-DEMO-001",
                title="RFQ — Core Network Infrastructure Upgrade",
                event_type=SourcingEvent.TYPE_RFQ,
                status=SourcingEvent.STATUS_BID_WINDOW,
                created_by=proc_exec,
                bid_start_date=timezone.now() - timezone.timedelta(days=5),
                bid_end_date=timezone.now() + timezone.timedelta(days=10),
                description="Open RFQ for core network hardware refresh.",
            )
            bid_amounts = {vendor1.id: Decimal("52000.00"), vendor2.id: Decimal("49500.00"), vendor3.id: Decimal("55000.00")}
            for idx, vendor in enumerate([vendor1, vendor2, vendor3], start=1):
                BidInvite.objects.get_or_create(event=rfq, vendor=vendor)
                if not VendorBid.objects.filter(event=rfq, vendor=vendor).exists():
                    VendorBid.objects.create(
                        event=rfq, vendor=vendor,
                        bid_number=f"BID-DEMO-00{idx}",
                        status=VendorBid.STATUS_SUBMITTED,
                        technical_proposal="Technical proposal for network hardware.",
                        commercial_proposal=f"Total bid: {bid_amounts[vendor.id]}",
                        total_bid_amount=bid_amounts[vendor.id],
                    )

        # ── 10. Purchase Order ────────────────────────────────────────────────
        from apps.orders.models import PurchaseOrder
        po = PurchaseOrder.objects.filter(requisition=pr).first()
        if not po:
            po = generate_purchase_order_service(
                vendor=vendor1, cost_center=cost_center, requisition=pr,
                line_items=[
                    {"item_description": "Cisco Catalyst 9600 Switch", "quantity": 2, "unit_price": 25000.00},
                    {"item_description": "100G SFP Transceiver Module", "quantity": 10, "unit_price": 1200.00},
                ],
                created_by_user=proc_exec,
            )
            acknowledge_purchase_order_service(po=po, vendor_user=vendor_user)

        # ── 11. PO Amendment (pending approval) ───────────────────────────────
        from apps.orders.models import POAmendment
        if not POAmendment.objects.filter(po=po).exists():
            try:
                amend_purchase_order_service(
                    po=po, user=proc_exec,
                    amendment_reason="Price adjustment for additional SFP modules",
                    line_items=[
                        {"item_description": "Cisco Catalyst 9600 Switch", "quantity": 2, "unit_price": 25000.00},
                        {"item_description": "100G SFP Transceiver Module", "quantity": 15, "unit_price": 1200.00},
                    ],
                )
            except Exception:
                pass  # Amendment may fail if PO is not in amendable state; non-blocking

        # ── 12. Partial GRN then Final GRN ────────────────────────────────────
        from apps.receipts.models import GoodsReceipt
        grns = GoodsReceipt.objects.filter(po=po)
        if not grns.exists():
            # Partial receipt — only 1 of 2 switches
            create_goods_receipt_service(
                po=po, received_by=receiver_user,
                receipt_items=[
                    {"po_line": po.lines.all()[0], "quantity_received": 1, "quantity_accepted": 1},
                ],
            )
            # Final receipt — remaining switch + all SFP modules
            create_goods_receipt_service(
                po=po, received_by=receiver_user,
                receipt_items=[
                    {"po_line": po.lines.all()[0], "quantity_received": 1, "quantity_accepted": 1},
                    {"po_line": po.lines.all()[1], "quantity_received": 10, "quantity_accepted": 10},
                ],
            )

        # ── 13. Invoice with Tolerance Exception ──────────────────────────────
        from apps.invoices.models import SupplierInvoice
        invoice_number = "INV-CISCO-DEMO-001"
        if not SupplierInvoice.objects.filter(invoice_number=invoice_number).exists():
            # Slightly over-invoiced on unit price to trigger a tolerance exception
            invoice = create_supplier_invoice_service(
                vendor=vendor1, po=po,
                invoice_number=invoice_number,
                invoice_date=today,
                due_date=today + timezone.timedelta(days=30),
                line_items=[
                    {"po_line": po.lines.all()[0], "item_description": "Cisco Catalyst 9600 Switch", "quantity": 2, "unit_price": 26500.00},
                    {"po_line": po.lines.all()[1], "item_description": "100G SFP Transceiver Module", "quantity": 10, "unit_price": 1200.00},
                ],
                created_by_user=finance_user,
            )
            try:
                run_3_way_match_service(invoice=invoice, user=finance_user)
            except Exception:
                pass  # Exception is expected — tolerance breach creates MatchException record

        # ── 14. Contract Near Renewal ─────────────────────────────────────────
        from apps.contracts.models import Contract
        if not Contract.objects.filter(title="Cisco Enterprise Hardware Support SLA").exists():
            contract = create_contract_service(
                title="Cisco Enterprise Hardware Support SLA",
                vendor=vendor1,
                contract_value=Decimal("62000.00"),
                start_date=today - timezone.timedelta(days=335),
                end_date=today + timezone.timedelta(days=25),  # expires in 25 days — within 30-day notice window
                renewal_notice_days=30,
                contract_owner=legal_user,
            )
            submit_for_legal_review_service(contract=contract, user=legal_user, notes="Initial submission.")
            approve_legal_review_service(contract=contract, user=legal_user, notes="Legal terms approved.")
            activate_contract_service(contract=contract, user=proc_mgr)
            add_contract_milestone_service(
                contract=contract,
                title="Quarterly SLA Audit",
                due_date=today + timezone.timedelta(days=14),
                amount=Decimal("15000.00"),
            )

        # ── 15. Second Contract (Microsoft — active, not near renewal) ────────
        if not Contract.objects.filter(title="Microsoft Enterprise License Agreement").exists():
            contract2 = create_contract_service(
                title="Microsoft Enterprise License Agreement",
                vendor=vendor3,
                contract_value=Decimal("120000.00"),
                start_date=today - timezone.timedelta(days=60),
                end_date=today + timezone.timedelta(days=305),
                renewal_notice_days=60,
                contract_owner=legal_user,
            )
            submit_for_legal_review_service(contract=contract2, user=legal_user, notes="ELA submission.")
            approve_legal_review_service(contract=contract2, user=legal_user, notes="Approved.")
            activate_contract_service(contract=contract2, user=proc_mgr)

        # ── 16. Vendor Scorecard ──────────────────────────────────────────────
        from apps.scorecards.models import VendorScorecard
        if not VendorScorecard.objects.filter(vendor=vendor1, evaluation_period="Q1-2026").exists():
            calculate_vendor_scorecard_service(
                vendor=vendor1, evaluation_period="Q1-2026",
                evaluated_by_user=proc_mgr,
                comments="Excellent 100% GRN delivery performance.",
            )

        self.stdout.write(self.style.SUCCESS(
            "Seed completed. Demo dataset:\n"
            "  - 3 active vendors (Cisco, Intel, Microsoft) with organization\n"
            "  - 2-step PR approval policy + PO amendment policy\n"
            "  - 1 open RFQ with 3 bids from all vendors\n"
            "  - 1 PO with pending amendment\n"
            "  - Partial + final GRN\n"
            "  - Invoice with tolerance exception (price variance)\n"
            "  - Contract expiring in 25 days (within renewal notice window)\n"
            "  - Second active contract (Microsoft ELA)\n"
            "  - Vendor scorecard for Cisco Q1-2026"
        ))

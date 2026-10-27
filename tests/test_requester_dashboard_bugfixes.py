from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.notifications.models import Notification
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PRLine, PurchaseRequisition


@pytest.mark.django_db
class TestRequesterDashboardBugfixes:
    @pytest.fixture(autouse=True)
    def setup_entities(self):
        self.org = Organization.objects.create(name="HPE Tech", code="HPE")
        self.dept = Department.objects.create(name="Engineering", code="ENG", organization=self.org)
        self.cc = CostCenter.objects.create(name="R&D Lab", code="CC-ENG-01", department=self.dept)

        self.requester_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
        self.approver_role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})

        self.requester = User.objects.create_user(
            email="requester.test@example.com",
            password="Password123!",
            role=self.requester_role,
            department=self.dept,
        )

        self.approver = User.objects.create_user(
            email="approver.test@example.com",
            password="Password123!",
            role=self.approver_role,
            department=self.dept,
        )

    def test_requisitions_list_pagination_and_reset_button(self, client):
        """BUG-005 & BUG-006: Requisitions list contains pagination and clear 'Reset' button."""
        # Create 15 requisitions to verify 10-per-page pagination
        for i in range(15):
            pr = PurchaseRequisition.objects.create(
                pr_number=f"PR-PAGINATE-{i:03d}",
                title=f"Paginated Item {i}",
                requester=self.requester,
                department=self.dept,
                cost_center=self.cc,
                total_amount=Decimal("100.00"),
                requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
                status=PurchaseRequisition.STATUS_DRAFT,
            )
            PRLine.objects.create(
                requisition=pr,
                item_description=f"Part {i}",
                quantity=Decimal("1"),
                unit_of_measure="EA",
                estimated_unit_price=Decimal("100.00"),
                estimated_total=Decimal("100.00"),
            )

        client.force_login(self.requester)

        # Page 1
        resp = client.get("/requisitions/")
        assert resp.status_code == 200
        assert "page_obj" in resp.context
        assert len(resp.context["items"]) == 10
        assert resp.context["page_obj"].paginator.count == 15
        assert resp.context["page_obj"].paginator.num_pages == 2
        content = resp.content.decode()
        assert "Showing 1–10 of 15" in content or "Showing 1" in content
        assert "Previous" in content
        assert "Next" in content

        # Page 2
        resp2 = client.get("/requisitions/?page=2")
        assert resp2.status_code == 200
        assert len(resp2.context["items"]) == 5

        # With search/filter active, 'Reset' button is present with text
        resp_filtered = client.get("/requisitions/?q=Paginated")
        assert resp_filtered.status_code == 200
        assert "Reset" in resp_filtered.content.decode()

    def test_approvals_inbox_pagination_and_controls(self, client):
        """BUG-008 & BUG-007: Approvals inbox paginates both pending and history queues with clean filter controls."""
        # Create 12 pending PRs
        for i in range(12):
            pr = PurchaseRequisition.objects.create(
                pr_number=f"PR-APPR-PEND-{i:03d}",
                title=f"Pending Item {i}",
                requester=self.requester,
                department=self.dept,
                cost_center=self.cc,
                total_amount=Decimal("200.00"),
                requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
                status=PurchaseRequisition.STATUS_SUBMITTED,
            )
            PRLine.objects.create(
                requisition=pr,
                item_description=f"Part {i}",
                quantity=Decimal("1"),
                unit_of_measure="EA",
                estimated_unit_price=Decimal("200.00"),
                estimated_total=Decimal("200.00"),
            )

        client.force_login(self.approver)

        resp = client.get("/approvals/inbox/")
        assert resp.status_code == 200
        assert "page_obj" in resp.context
        assert len(resp.context["pending_prs"]) == 10
        assert resp.context["pending_count"] == 12
        content = resp.content.decode()
        assert "Pending (12)" in content
        # Check that onchange="this.form.submit()" is removed from the status filter to avoid premature submit
        assert '<select name="status" class="form-select text-secondary fw-medium" onchange="this.form.submit()">' not in content
        assert '<select name="status" class="form-select text-secondary fw-medium">' in content

        # History tab
        resp_history = client.get("/approvals/inbox/?tab=history")
        assert resp_history.status_code == 200
        assert resp_history.context["active_tab"] == "history"

    def test_requisition_detail_submit_confirmation_modal(self, client):
        """BUG-009: Draft PR detail view contains submit confirmation modal matching cancelConfirmModal."""
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-CONFIRM-MODAL-001",
            title="Laptops for developers",
            requester=self.requester,
            department=self.dept,
            cost_center=self.cc,
            total_amount=Decimal("1500.00"),
            requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
            status=PurchaseRequisition.STATUS_DRAFT,
        )
        PRLine.objects.create(
            requisition=pr,
            item_description="Laptop ThinkPad",
            quantity=Decimal("1"),
            unit_of_measure="EA",
            estimated_unit_price=Decimal("1500.00"),
            estimated_total=Decimal("1500.00"),
        )

        client.force_login(self.requester)
        resp = client.get(f"/requisitions/{pr.pk}/")
        assert resp.status_code == 200
        content = resp.content.decode()

        # Check button opens submitConfirmModal
        assert 'data-bs-target="#submitConfirmModal"' in content
        assert 'id="submitConfirmModal"' in content
        assert 'Confirm Requisition Submission' in content
        assert 'Yes, Submit PR' in content
        assert 'data-bs-target="#cancelConfirmModal"' in content

    def test_edit_requisition_save_button_disabled_initially(self, client):
        """BUG-002: Edit requisition template renders Save Changes button disabled initially until modified."""
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-EDIT-DIRTY-001",
            title="Office Desk and Monitor",
            requester=self.requester,
            department=self.dept,
            cost_center=self.cc,
            total_amount=Decimal("450.00"),
            requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
            status=PurchaseRequisition.STATUS_DRAFT,
        )
        PRLine.objects.create(
            requisition=pr,
            item_description="Ergonomic Desk",
            quantity=Decimal("1"),
            unit_of_measure="EA",
            estimated_unit_price=Decimal("450.00"),
            estimated_total=Decimal("450.00"),
        )

        client.force_login(self.requester)
        resp = client.get(f"/requisitions/{pr.pk}/edit/")
        assert resp.status_code == 200
        content = resp.content.decode()

        assert 'id="save_changes_btn"' in content
        assert 'disabled' in content
        assert 'markFormDirty' in content

    def test_vendor_navbar_text_contrast(self, client):
        """Vendor BUG-001 & BUG-002: Vendor navbar text has high contrast text-white-50 instead of text-muted."""
        from apps.vendors.models import Vendor, VendorCategory
        cat, _ = VendorCategory.objects.get_or_create(code="IT-HW", defaults={"name": "IT Hardware"})
        vendor = Vendor.objects.create(
            legal_name="Cisco Systems Inc.",
            vendor_number="VEN-0001",
            tax_identification_number="US-123456789",
            category=cat,
            status="ACTIVE",
        )
        vendor_role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor Portal User"})
        vendor_user = User.objects.create_user(
            email="vendoruser@cisco.com",
            password="Password123!",
            role=vendor_role,
            vendor=vendor,
        )

        client.force_login(vendor_user)
        resp = client.get("/vendor/dashboard/")
        assert resp.status_code == 200
        content = resp.content.decode()

        # Check vendor info and designation use text-white-50 for high contrast
        assert 'text-white-50 d-block' in content
        assert 'VEN-0001 &bull; US-123456789' in content
        assert 'Authorized Representative' in content

    def test_approvals_status_filter_tab_isolation(self, client):
        """Dept Approver BUG-004: Incompatible status filters do not cross-apply across tabs."""
        pr_pending = PurchaseRequisition.objects.create(
            pr_number="PR-PENDING-TAB-01",
            title="Pending Item",
            requester=self.requester,
            department=self.dept,
            cost_center=self.cc,
            total_amount=Decimal("100.00"),
            requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
            status=PurchaseRequisition.STATUS_SUBMITTED,
        )
        PRLine.objects.create(
            requisition=pr_pending,
            item_description="Part A",
            quantity=Decimal("1"),
            unit_of_measure="EA",
            estimated_unit_price=Decimal("100.00"),
            estimated_total=Decimal("100.00"),
        )
        pr_history = PurchaseRequisition.objects.create(
            pr_number="PR-HISTORY-TAB-01",
            title="History Item",
            requester=self.requester,
            department=self.dept,
            cost_center=self.cc,
            total_amount=Decimal("150.00"),
            requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
            status=PurchaseRequisition.STATUS_APPROVED,
        )
        PRLine.objects.create(
            requisition=pr_history,
            item_description="Part B",
            quantity=Decimal("1"),
            unit_of_measure="EA",
            estimated_unit_price=Decimal("150.00"),
            estimated_total=Decimal("150.00"),
        )

        client.force_login(self.approver)

        # In History tab, passing status=SUBMITTED (pending status) is safely ignored for history
        resp = client.get("/approvals/inbox/?tab=history&status=SUBMITTED")
        assert resp.status_code == 200
        assert len(resp.context["history_prs"]) == 1

        # In Pending tab, passing status=APPROVED (history status) is safely ignored for pending
        resp2 = client.get("/approvals/inbox/?status=APPROVED")
        assert resp2.status_code == 200
        assert len(resp2.context["pending_prs"]) == 1

    def test_requester_dashboard_metric_cards_clickable(self, client):
        """Verify metric cards on Requester Dashboard are clickable and redirect to filtered PR lists."""
        client.force_login(self.requester)
        resp = client.get("/")
        assert resp.status_code == 200
        content = resp.content.decode()

        # Check links
        assert 'href="/requisitions/"' in content
        assert 'href="/requisitions/?status=PENDING"' in content
        assert 'href="/requisitions/?status=APPROVED"' in content
        assert 'metric-card-link' in content

    def test_requester_dashboard_notifications(self, client):
        """Verify notification bell, sidebar link, and alert banner in Requester layout/dashboard."""
        Notification.objects.create(
            recipient=self.requester,
            notification_type=Notification.TYPE_PO_ISSUED,
            title="PO Issued for Requisition",
            message="Your requisition PR-001 has resulted in PO-2026-0001.",
            is_read=False,
        )

        client.force_login(self.requester)
        resp = client.get("/")
        assert resp.status_code == 200
        content = resp.content.decode()

        # Check navbar notification bell and sidebar links
        assert 'href="/notifications/"' in content
        assert 'fa-bell' in content
        assert "You have 1 unread notification" in content
        assert "View Notifications" in content

        # Visit /notifications/ page directly and verify requester sidebar is present
        resp_notif = client.get("/notifications/")
        assert resp_notif.status_code == 200
        notif_content = resp_notif.content.decode()
        assert "requesterSidebar" in notif_content
        assert "My PRs" in notif_content
        assert "PO Issued for Requisition" in notif_content

    def test_finance_dashboard_notifications(self, client):
        """Verify notification bell, sidebar link, and alert banner in Finance layout/dashboard."""
        finance_role, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance AP"})
        finance_user = User.objects.create_user(
            email="finance.test@example.com",
            password="Password123!",
            role=finance_role,
            department=self.dept,
        )

        Notification.objects.create(
            recipient=finance_user,
            notification_type=Notification.TYPE_EXCEPTION_RAISED,
            title="3-Way Match Exception",
            message="Invoice INV-001 requires tolerance exception review.",
            is_read=False,
        )

        client.force_login(finance_user)
        resp = client.get("/")
        assert resp.status_code == 200
        content = resp.content.decode()

        # Check navbar notification bell and sidebar links
        assert 'href="/notifications/"' in content
        assert 'fa-bell' in content
        assert "You have 1 unread notification" in content
        assert "Review budget alerts, invoice match exceptions, and approval escalations." in content


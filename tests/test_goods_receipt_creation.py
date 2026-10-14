from decimal import Decimal

import pytest

from apps.accounts.models import Role, User
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import GoodsReceipt, RejectionRecord
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def receiving_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Tech", code="HPE-GRN-ORG")
    dept = Department.objects.create(organization=org, name="Logistics", code="DEPT-LOG-01")
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-LOG-01", name="Main Warehouse"
    )
    category = VendorCategory.objects.create(name="Electronics", code="CAT-ELEC-01")
    vendor = Vendor.objects.create(
        legal_name="Cisco Global",
        tax_identification_number="TIN-CISCO-GRN",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_grn@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-GRN-TEST",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("20000.00"),
        tax_amount=Decimal("2000.00"),
        total_amount=Decimal("22000.00"),
    )

    line1 = POLine.objects.create(
        po=po,
        item_description="Cisco Catalyst 9300 Switch",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("1500.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="Power Supply Module 1100W",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("500.00"),
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "vendor": vendor,
        "receiver": receiver,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


@pytest.mark.django_db
def test_authenticated_stores_receiver_can_open_grn_form(client, receiving_setup):
    """Test A & K: Unauthenticated redirected; authenticated Stores Receiver can open form."""
    po = receiving_setup["po"]
    receiver = receiving_setup["receiver"]

    # Unauthenticated access redirected
    resp_unauth = client.get(f"/receipts/create/{po.id}/")
    assert resp_unauth.status_code == 302
    assert "/login/" in resp_unauth.url

    # Authenticated access succeeds
    client.force_login(receiver)
    resp = client.get(f"/receipts/create/{po.id}/")
    assert resp.status_code == 200
    content = resp.content.decode()

    assert "PO-2026-GRN-TEST" in content
    assert "Cisco Catalyst 9300 Switch" in content
    assert "Power Supply Module 1100W" in content
    assert "Delivery Note / Waybill Number" in content
    assert "Create Goods Receipt" in content


@pytest.mark.django_db
def test_ineligible_po_cannot_create_grn(client, receiving_setup):
    """Test B: PO in DRAFT, COMPLETED, or CANCELLED status cannot be received."""
    po = receiving_setup["po"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    # Change to DRAFT
    po.status = PurchaseOrder.STATUS_DRAFT
    po.save()

    resp = client.get(f"/receipts/create/{po.id}/")
    assert resp.status_code == 302
    assert "/purchase-orders/" in resp.url

    # Post request also rejected
    resp_post = client.post(
        f"/receipts/create/{po.id}/",
        {
            "delivery_note_number": "DN-001",
            f"quantity_received_{receiving_setup['line1'].id}": "5",
        },
    )
    assert resp_post.status_code == 302
    assert GoodsReceipt.objects.count() == 0


@pytest.mark.django_db
def test_valid_receipt_creates_goods_receipt_and_lines(client, receiving_setup):
    """Test C, D, J: Valid receipt creates GoodsReceipt, ReceiptLine, and updates PO to PARTIAL_RECEIPT."""
    po = receiving_setup["po"]
    line1 = receiving_setup["line1"]
    line2 = receiving_setup["line2"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    post_data = {
        "delivery_note_number": "DN-CISCO-8899",
        "remarks": "Package intact. Delivered via DHL Express.",
        f"quantity_received_{line1.id}": "6.00",
        f"quantity_accepted_{line1.id}": "5.00",  # 1 rejected
        f"notes_{line1.id}": "1 unit had broken casing, rejected.",
        f"quantity_received_{line2.id}": "4.00",
        f"quantity_accepted_{line2.id}": "4.00",
        f"notes_{line2.id}": "All 4 passed inspection.",
    }

    resp = client.post(f"/receipts/create/{po.id}/", post_data)
    assert resp.status_code == 302
    assert "/receipts/" in resp.url

    # Verify GoodsReceipt created
    assert GoodsReceipt.objects.count() == 1
    grn = GoodsReceipt.objects.first()
    assert grn.po == po
    assert grn.received_by == receiver
    assert grn.delivery_note_number == "DN-CISCO-8899"
    assert grn.remarks == "Package intact. Delivered via DHL Express."

    # Verify ReceiptLines created
    assert grn.lines.count() == 2
    r_line1 = grn.lines.get(po_line=line1)
    assert r_line1.quantity_received == Decimal("6.00")
    assert r_line1.quantity_accepted == Decimal("5.00")
    assert r_line1.quantity_rejected == Decimal("1.00")
    assert r_line1.notes == "1 unit had broken casing, rejected."

    r_line2 = grn.lines.get(po_line=line2)
    assert r_line2.quantity_received == Decimal("4.00")
    assert r_line2.quantity_accepted == Decimal("4.00")
    assert r_line2.quantity_rejected == Decimal("0.00")

    # Verify rejection record created for rejected unit
    assert RejectionRecord.objects.count() == 1
    rej = RejectionRecord.objects.first()
    assert rej.receipt_line == r_line1
    assert rej.rejected_quantity == Decimal("1.00")

    # Verify PO line received quantities updated with accepted amounts
    line1.refresh_from_db()
    line2.refresh_from_db()
    po.refresh_from_db()
    assert line1.quantity_received == Decimal("5.00")
    assert line2.quantity_received == Decimal("4.00")
    assert po.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT


@pytest.mark.django_db
def test_full_receipt_transitions_po_to_completed(client, receiving_setup):
    """Test J: Receiving all remaining quantities transitions PO to COMPLETED."""
    po = receiving_setup["po"]
    line1 = receiving_setup["line1"]
    line2 = receiving_setup["line2"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    post_data = {
        "delivery_note_number": "DN-FULL-01",
        f"quantity_received_{line1.id}": "10.00",
        f"quantity_accepted_{line1.id}": "10.00",
        f"quantity_received_{line2.id}": "10.00",
        f"quantity_accepted_{line2.id}": "10.00",
    }

    resp = client.post(f"/receipts/create/{po.id}/", post_data)
    assert resp.status_code == 302

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_COMPLETED


@pytest.mark.django_db
def test_quantity_received_cannot_exceed_remaining_quantity(client, receiving_setup):
    """Test E: Over-receiving is rejected."""
    po = receiving_setup["po"]
    line1 = receiving_setup["line1"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    post_data = {
        "delivery_note_number": "DN-OVER-01",
        f"quantity_received_{line1.id}": "15.00",  # Only 10 ordered
        f"quantity_accepted_{line1.id}": "15.00",
    }

    resp = client.post(f"/receipts/create/{po.id}/", post_data)
    assert resp.status_code == 200  # Re-renders form with error
    assert GoodsReceipt.objects.count() == 0


@pytest.mark.django_db
def test_quantity_accepted_cannot_exceed_quantity_received(client, receiving_setup):
    """Test F: Accepted quantity cannot be greater than received quantity."""
    po = receiving_setup["po"]
    line1 = receiving_setup["line1"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    post_data = {
        "delivery_note_number": "DN-INV-ACC",
        f"quantity_received_{line1.id}": "5.00",
        f"quantity_accepted_{line1.id}": "8.00",  # 8 > 5
    }

    resp = client.post(f"/receipts/create/{po.id}/", post_data)
    assert resp.status_code == 200
    assert GoodsReceipt.objects.count() == 0


@pytest.mark.django_db
def test_negative_quantities_are_rejected(client, receiving_setup):
    """Test G: Negative quantities are rejected."""
    po = receiving_setup["po"]
    line1 = receiving_setup["line1"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    post_data = {
        "delivery_note_number": "DN-NEG",
        f"quantity_received_{line1.id}": "-5.00",
        f"quantity_accepted_{line1.id}": "-5.00",
    }

    resp = client.post(f"/receipts/create/{po.id}/", post_data)
    assert resp.status_code == 200
    assert GoodsReceipt.objects.count() == 0


@pytest.mark.django_db
def test_cross_po_line_tampering_is_rejected(client, receiving_setup):
    """Test H: Submitting a line ID belonging to a different PO is rejected."""
    po = receiving_setup["po"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    # Create another PO and line
    other_po = PurchaseOrder.objects.create(
        po_number="PO-OTHER-99",
        vendor=receiving_setup["vendor"],
        cost_center=receiving_setup["cost_center"],
        status=PurchaseOrder.STATUS_ISSUED,
    )
    other_line = POLine.objects.create(
        po=other_po,
        item_description="Other Item",
        quantity=Decimal("10.00"),
        unit_price=Decimal("100.00"),
    )

    # Attempt to post other_line against po
    post_data = {
        "delivery_note_number": "DN-HACK",
        f"quantity_received_{other_line.id}": "5.00",
        f"quantity_accepted_{other_line.id}": "5.00",
    }

    client.post(f"/receipts/create/{po.id}/", post_data)
    # Form ignores unknown lines or rejects if no matching line
    assert GoodsReceipt.objects.count() == 0


@pytest.mark.django_db
def test_empty_receipt_submission_is_rejected(client, receiving_setup):
    """Test I: Posting with all quantities 0 or blank is rejected."""
    po = receiving_setup["po"]
    receiver = receiving_setup["receiver"]
    client.force_login(receiver)

    post_data = {
        "delivery_note_number": "DN-EMPTY",
        "remarks": "No quantities filled",
    }

    resp = client.post(f"/receipts/create/{po.id}/", post_data)
    assert resp.status_code == 200
    assert GoodsReceipt.objects.count() == 0


@pytest.mark.django_db
def test_unauthorized_roles_cannot_create_goods_receipt(client, receiving_setup, db_roles):
    """Test L: Users without STORES_RECEIVER or SUPER_ADMIN role are blocked by view and service."""
    from django.core.exceptions import ValidationError

    from apps.receipts.services import create_goods_receipt_service

    po = receiving_setup["po"]
    line1 = receiving_setup["line1"]

    auditor = User.objects.create_user(
        email="auditor_grn_block@hpe.com",
        password="Password123!",
        role=db_roles[Role.AUDITOR],
    )
    staff_ordinary = User.objects.create_user(
        email="staff_grn_block@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        is_staff=True,
    )

    for unauth_user in [auditor, staff_ordinary]:
        client.force_login(unauth_user)

        # View GET blocked
        resp_get = client.get(f"/receipts/create/{po.id}/")
        assert resp_get.status_code == 302
        assert "/receipts/" in resp_get.url

        # View POST blocked
        post_data = {
            "delivery_note_number": "DN-UNAUTH-01",
            f"quantity_received_{line1.id}": "5.00",
            f"quantity_accepted_{line1.id}": "5.00",
        }
        resp_post = client.post(f"/receipts/create/{po.id}/", post_data)
        assert resp_post.status_code == 302
        assert "/receipts/" in resp_post.url
        assert GoodsReceipt.objects.count() == 0

        # Service-level backend authorization enforcement
        with pytest.raises(ValidationError) as exc:
            create_goods_receipt_service(
                po=po,
                received_by=unauth_user,
                receipt_items=[
                    {
                        "po_line_id": str(line1.id),
                        "quantity_received": Decimal("5.00"),
                        "quantity_accepted": Decimal("5.00"),
                    }
                ],
                delivery_note_number="DN-SERVICE-UNAUTH",
            )
        assert "Unauthorized: Only Stores Receiver or Super Admin can create goods receipts" in str(
            exc.value
        )
        assert GoodsReceipt.objects.count() == 0

    # Super Admin can create goods receipt
    admin = User.objects.create_user(
        email="admin_grn_ok@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    grn_admin = create_goods_receipt_service(
        po=po,
        received_by=admin,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("2.00"),
                "quantity_accepted": Decimal("2.00"),
            }
        ],
        delivery_note_number="DN-ADMIN-OK",
    )
    assert grn_admin.received_by == admin
    assert grn_admin.lines.count() == 1

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import StockHandoffRecord
from apps.receipts.services import (
    create_goods_receipt_service,
    handoff_goods_to_stock_service,
    record_inspection_service,
)
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def stock_handoff_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Tech", code="HPE-STOCK-ORG")
    dept = Department.objects.create(organization=org, name="Warehouse", code="DEPT-WH-STOCK")
    cost_center = CostCenter.objects.create(
        department=dept, code="CC-WH-STOCK", name="Central Warehouse"
    )
    category = VendorCategory.objects.create(name="Hardware", code="CAT-HW-STOCK")
    vendor = Vendor.objects.create(
        legal_name="Stock Supplies Inc",
        tax_identification_number="TIN-STOCK-001",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    receiver = User.objects.create_user(
        email="receiver_stock@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
    )

    buyer = User.objects.create_user(
        email="buyer_stock@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-STK-001",
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
        item_description="Industrial Routers",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("1000.00"),
    )

    line2 = POLine.objects.create(
        po=po,
        item_description="Fiber Patch Cables",
        quantity=Decimal("20.00"),
        quantity_received=Decimal("0.00"),
        unit_of_measure="EA",
        unit_price=Decimal("50.00"),
    )

    return {
        "org": org,
        "vendor": vendor,
        "cost_center": cost_center,
        "receiver": receiver,
        "buyer": buyer,
        "po": po,
        "line1": line1,
        "line2": line2,
    }


@pytest.mark.django_db
def test_1_accepted_quantity_is_added_to_stock(stock_handoff_setup):
    """1. Accepted quantity is added to stock."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("8.00"),
                "quantity_accepted": Decimal("8.00"),
            }
        ],
    )

    assert grn.stock_handoff_status == "PENDING_HANDOFF"
    assert grn.can_hand_off_to_stock is True

    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-A1",
        handoff_notes="Stored on top shelf",
    )

    grn.refresh_from_db()
    assert grn.stock_handoff_status == "HANDED_OFF"
    assert grn.can_hand_off_to_stock is False

    rl = grn.lines.first()
    assert rl.is_handed_off is True
    assert rl.stock_handoff_record.quantity_handed_off == Decimal("8.00")
    assert rl.stock_handoff_record.storage_location == "BAY-A1"
    assert rl.stock_handoff_record.handoff_notes == "Stored on top shelf"
    assert rl.stock_handoff_record.handed_off_by == receiver


@pytest.mark.django_db
def test_2_rejected_quantity_is_excluded_from_stock(stock_handoff_setup):
    """2. Rejected quantity is excluded from stock."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]

    # 10 received, but 4 accepted, 6 rejected
    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("4.00"),
                "rejection_reason": "Damaged casing",
            }
        ],
    )

    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-RECV-01",
    )

    rl = grn.lines.first()
    assert rl.quantity_received == Decimal("10.00")
    assert rl.quantity_accepted == Decimal("4.00")
    assert rl.quantity_rejected == Decimal("6.00")
    # Only 4.00 is in stock
    assert rl.stock_handoff_record.quantity_handed_off == Decimal("4.00")


@pytest.mark.django_db
def test_3_partial_receipt_adds_only_accepted_quantity(stock_handoff_setup):
    """3. Partial receipt adds only accepted quantity."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]

    # First partial receipt: 3 units accepted
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("3.00"),
                "quantity_accepted": Decimal("3.00"),
            }
        ],
    )
    handoff_goods_to_stock_service(
        receipt=grn1,
        handed_off_by=receiver,
        storage_location="BAY-01",
    )

    rl1 = grn1.lines.first()
    assert rl1.stock_handoff_record.quantity_handed_off == Decimal("3.00")

    # Second partial receipt: 5 units accepted
    po.refresh_from_db()
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
    )
    handoff_goods_to_stock_service(
        receipt=grn2,
        handed_off_by=receiver,
        storage_location="BAY-02",
    )

    rl2 = grn2.lines.first()
    assert rl2.stock_handoff_record.quantity_handed_off == Decimal("5.00")

    # Total stock from line1 across both GRNs = 3.00 + 5.00 = 8.00
    total_stock_for_line = StockHandoffRecord.objects.filter(receipt_line__po_line=line1).count()
    assert total_stock_for_line == 2


@pytest.mark.django_db
def test_4_duplicate_stock_handoff_does_not_double_add_stock(stock_handoff_setup):
    """4. Duplicate stock-handoff action does not double-add stock (idempotent)."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
    )

    # First handoff
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-01",
    )
    assert StockHandoffRecord.objects.filter(receipt_line__receipt=grn).count() == 1

    # Second handoff (repeated action)
    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-01-UPDATED",
        handoff_notes="Updated notes",
    )
    # Still exactly 1 record, updated in place
    assert StockHandoffRecord.objects.filter(receipt_line__receipt=grn).count() == 1
    rl = grn.lines.first()
    rl.refresh_from_db()
    assert rl.stock_handoff_record.quantity_handed_off == Decimal("5.00")
    assert rl.stock_handoff_record.storage_location == "BAY-01-UPDATED"


@pytest.mark.django_db
def test_5_reinspection_does_not_double_add_previously_posted_stock(stock_handoff_setup):
    """5. Reinspection does not double-add previously posted stock."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            }
        ],
    )

    handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=receiver,
        storage_location="BAY-MAIN",
    )

    rl = grn.lines.first()
    assert rl.stock_handoff_record.quantity_handed_off == Decimal("10.00")

    # Re-inspect: find 2 defective units, so 8 accepted, 2 rejected
    record_inspection_service(
        receipt=grn,
        inspected_by=receiver,
        inspection_items=[
            {
                "receipt_line_id": str(rl.id),
                "passed": False,
                "rejected_quantity": Decimal("2.00"),
                "rejection_reason": "2 defective ports discovered",
                "inspection_notes": "QA test re-run",
            }
        ],
    )

    rl.refresh_from_db()
    assert rl.quantity_accepted == Decimal("8.00")
    assert rl.quantity_rejected == Decimal("2.00")
    # Stock handoff record automatically synced to 8.00, count remains 1
    assert StockHandoffRecord.objects.filter(receipt_line=rl).count() == 1
    assert rl.stock_handoff_record.quantity_handed_off == Decimal("8.00")


@pytest.mark.django_db
def test_6_invalid_cross_grn_or_cross_po_stock_handoff_is_blocked(stock_handoff_setup):
    """6. Invalid/cross-GRN or cross-PO stock handoff is blocked."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]
    line2 = stock_handoff_setup["line2"]

    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
    )

    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
    )

    rl2 = grn2.lines.first()

    # Attempting to hand off grn2's line against grn1 must fail
    with pytest.raises(ValidationError) as exc:
        handoff_goods_to_stock_service(
            receipt=grn1,
            handed_off_by=receiver,
            line_items=[
                {
                    "receipt_line_id": str(rl2.id),
                    "storage_location": "BAY-X",
                }
            ],
        )
    assert "does not belong to Goods Receipt" in str(exc.value)


@pytest.mark.django_db
def test_7_unauthorized_roles_cannot_perform_stock_handoff(stock_handoff_setup, client, db_roles):
    """7. Unauthorized roles cannot perform the Stores Receiver stock-handoff action."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    buyer = stock_handoff_setup["buyer"]
    line1 = stock_handoff_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
    )

    staff_ordinary = User.objects.create_user(
        email="staff_stock_block@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        is_staff=True,
    )

    for unauth_user in [buyer, staff_ordinary]:
        # Service-level check
        with pytest.raises(ValidationError) as exc:
            handoff_goods_to_stock_service(
                receipt=grn,
                handed_off_by=unauth_user,
            )
        assert "Unauthorized" in str(exc.value)

        # View-level check
        client.force_login(unauth_user)
        url = reverse("receipt_stock_handoff", kwargs={"grn_id": grn.id})
        resp = client.get(url)
        assert resp.status_code == 302
        assert resp.url == reverse("receipt_detail", kwargs={"grn_id": grn.id})

    # Super Admin can perform stock handoff
    admin = User.objects.create_user(
        email="admin_handoff_ok@hpe.com",
        password="Password123!",
        role=db_roles[Role.SUPER_ADMIN],
        is_superuser=True,
    )
    rec_admin = handoff_goods_to_stock_service(
        receipt=grn,
        handed_off_by=admin,
        storage_location="ADMIN-BAY",
    )
    assert rec_admin.stock_handoff_status == "HANDED_OFF"


@pytest.mark.django_db
def test_8_stock_quantity_remains_correct_after_multiple_grns(stock_handoff_setup):
    """8. Stock quantity remains correct after multiple GRNs for the same PO/item."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]  # ordered 10.00

    # GRN 1: 4 received, 3 accepted, 1 rejected
    grn1 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("4.00"),
                "quantity_accepted": Decimal("3.00"),
            }
        ],
    )
    handoff_goods_to_stock_service(receipt=grn1, handed_off_by=receiver, storage_location="BAY-01")

    # GRN 2: 6 received, 6 accepted, 0 rejected
    po.refresh_from_db()
    grn2 = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("6.00"),
                "quantity_accepted": Decimal("6.00"),
            }
        ],
    )
    handoff_goods_to_stock_service(receipt=grn2, handed_off_by=receiver, storage_location="BAY-02")

    records = StockHandoffRecord.objects.filter(receipt_line__po_line=line1)
    total_handed_off = sum(r.quantity_handed_off for r in records)
    assert total_handed_off == Decimal("9.00")  # 3 + 6 = 9 accepted in total


@pytest.mark.django_db
def test_9_transaction_rollback_prevents_partial_inventory_updates(
    stock_handoff_setup, monkeypatch
):
    """9. Transaction rollback prevents partial inventory updates if the operation fails."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]
    line2 = stock_handoff_setup["line2"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            },
            {
                "po_line_id": str(line2.id),
                "quantity_received": Decimal("10.00"),
                "quantity_accepted": Decimal("10.00"),
            },
        ],
    )

    call_count = [0]
    orig_update_or_create = StockHandoffRecord.objects.update_or_create

    def exploding_update_or_create(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise IntegrityError("Simulated database write failure on line 2")
        return orig_update_or_create(*args, **kwargs)

    monkeypatch.setattr(StockHandoffRecord.objects, "update_or_create", exploding_update_or_create)

    with pytest.raises(IntegrityError):
        handoff_goods_to_stock_service(
            receipt=grn,
            handed_off_by=receiver,
        )

    # Rollback must leave 0 records created
    assert StockHandoffRecord.objects.filter(receipt_line__receipt=grn).count() == 0


@pytest.mark.django_db
def test_10_stores_receiver_ui_stock_handoff_flow(stock_handoff_setup, client):
    """10. Stores Receiver UI flow: GET and POST stock handoff view."""
    po = stock_handoff_setup["po"]
    receiver = stock_handoff_setup["receiver"]
    line1 = stock_handoff_setup["line1"]

    grn = create_goods_receipt_service(
        po=po,
        received_by=receiver,
        receipt_items=[
            {
                "po_line_id": str(line1.id),
                "quantity_received": Decimal("8.00"),
                "quantity_accepted": Decimal("8.00"),
            }
        ],
    )

    client.force_login(receiver)

    # GET handoff page
    url = reverse("receipt_stock_handoff", kwargs={"grn_id": grn.id})
    resp = client.get(url)
    assert resp.status_code == 200
    assert "Stock Handoff" in resp.content.decode()
    assert "MAIN-WH" in resp.content.decode()

    # POST handoff confirmation
    resp = client.post(
        url,
        {
            "storage_location": "BAY-NORTH-04",
            "handoff_notes": "Placed in high security cage",
        },
    )
    assert resp.status_code == 302
    assert resp.url == reverse("receipt_detail", kwargs={"grn_id": grn.id})

    # Detail page shows updated stock info
    detail_resp = client.get(resp.url)
    assert detail_resp.status_code == 200
    content = detail_resp.content.decode()
    assert "In Stock" in content or "Stock Handoff Complete" in content or "BAY-NORTH-04" in content
    assert "BAY-NORTH-04" in content

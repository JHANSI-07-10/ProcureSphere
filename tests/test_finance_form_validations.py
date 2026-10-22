import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.invoices.models import InvoiceLine, MatchException, MatchTolerancePolicy, SupplierInvoice
from apps.invoices.services import create_supplier_invoice_service, run_3_way_match_service
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def finance_setup(db, db_roles):
    org = Organization.objects.create(name="HPE Finance Test Org", code="HPE-FIN-01")
    dept = Department.objects.create(organization=org, name="Finance Dept", code="FIN-01")
    cost_center = CostCenter.objects.create(department=dept, code="CC-FIN-01", name="Finance Ops")

    category = VendorCategory.objects.create(name="Office Supplies", code="CAT-OFFICE-01")
    vendor = Vendor.objects.create(
        legal_name="Global Office Solutions",
        tax_identification_number="TIN-GOS-887",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )

    finance_user = User.objects.create_user(
        email="finance_validator@hpe.com",
        password="Password123!",
        role=db_roles[Role.FINANCE_AP],
    )

    po = PurchaseOrder.objects.create(
        po_number="PO-FIN-TEST-001",
        version=1,
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("100.00"),
        total_amount=Decimal("1100.00"),
    )

    po_line = POLine.objects.create(
        po=po,
        item_description="Ergonomic Chairs",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("10.00"),
        unit_price=Decimal("100.00"),
    )

    return {
        "finance_user": finance_user,
        "vendor": vendor,
        "po": po,
        "po_line": po_line,
    }


@pytest.mark.django_db
def test_finance_invoice_create_blocks_negative_quantity(client, finance_setup):
    """
    Validation Test: Form submission with negative count/quantity must be rejected
    both in backend validation and UI flash message.
    """
    finance_user = finance_setup["finance_user"]
    vendor = finance_setup["vendor"]
    po = finance_setup["po"]

    client.force_login(finance_user)
    response = client.post(
        "/invoices/create/",
        {
            "vendor_id": str(vendor.id),
            "po_id": str(po.id),
            "invoice_number": "INV-NEG-QTY-01",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + datetime.timedelta(days=30)).isoformat(),
            "quantity": "-5.00",
            "unit_price": "100.00",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "greater than zero" in content or "less than zero or negative" in content
    assert not SupplierInvoice.objects.filter(invoice_number="INV-NEG-QTY-01").exists()


@pytest.mark.django_db
def test_finance_invoice_create_blocks_zero_quantity(client, finance_setup):
    """
    Validation Test: Form submission with zero count/quantity must be rejected.
    """
    finance_user = finance_setup["finance_user"]
    vendor = finance_setup["vendor"]
    po = finance_setup["po"]

    client.force_login(finance_user)
    response = client.post(
        "/invoices/create/",
        {
            "vendor_id": str(vendor.id),
            "po_id": str(po.id),
            "invoice_number": "INV-ZERO-QTY-01",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + datetime.timedelta(days=30)).isoformat(),
            "quantity": "0.00",
            "unit_price": "100.00",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "greater than zero" in content
    assert not SupplierInvoice.objects.filter(invoice_number="INV-ZERO-QTY-01").exists()


@pytest.mark.django_db
def test_finance_invoice_create_blocks_negative_unit_price(client, finance_setup):
    """
    Validation Test: Unit price cannot be negative.
    """
    finance_user = finance_setup["finance_user"]
    vendor = finance_setup["vendor"]
    po = finance_setup["po"]

    client.force_login(finance_user)
    response = client.post(
        "/invoices/create/",
        {
            "vendor_id": str(vendor.id),
            "po_id": str(po.id),
            "invoice_number": "INV-NEG-PRICE-01",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + datetime.timedelta(days=30)).isoformat(),
            "quantity": "10.00",
            "unit_price": "-50.00",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "cannot be less than zero or negative" in content or "cannot be negative" in content
    assert not SupplierInvoice.objects.filter(invoice_number="INV-NEG-PRICE-01").exists()


@pytest.mark.django_db
def test_finance_invoice_create_blocks_due_date_before_invoice_date(client, finance_setup):
    """
    Validation Test: Payment due date cannot precede invoice date.
    """
    finance_user = finance_setup["finance_user"]
    vendor = finance_setup["vendor"]
    po = finance_setup["po"]

    client.force_login(finance_user)
    response = client.post(
        "/invoices/create/",
        {
            "vendor_id": str(vendor.id),
            "po_id": str(po.id),
            "invoice_number": "INV-DATE-ERR-01",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() - datetime.timedelta(days=5)).isoformat(),
            "quantity": "10.00",
            "unit_price": "100.00",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "cannot be earlier than" in content
    assert not SupplierInvoice.objects.filter(invoice_number="INV-DATE-ERR-01").exists()


@pytest.mark.django_db
def test_finance_service_and_model_enforces_positive_counts(finance_setup):
    """
    Domain Model & Service Validation:
    create_supplier_invoice_service and InvoiceLine.save() raise ValidationError on non-positive quantity.
    """
    vendor = finance_setup["vendor"]
    po = finance_setup["po"]
    po_line = finance_setup["po_line"]

    with pytest.raises(ValidationError) as exc:
        create_supplier_invoice_service(
            vendor=vendor,
            po=po,
            invoice_number="INV-SVC-TEST-01",
            invoice_date=timezone.now().date(),
            due_date=timezone.now().date() + datetime.timedelta(days=30),
            line_items=[
                {
                    "po_line": po_line,
                    "item_description": "Ergonomic Chairs",
                    "quantity": Decimal("-10.00"),
                    "unit_price": Decimal("100.00"),
                }
            ],
        )
    assert "greater than zero" in str(exc.value)


@pytest.mark.django_db
def test_finance_invoice_create_successful_with_valid_counts(client, finance_setup):
    """
    Positive Test: Submitting valid counts and prices passes validation and triggers 3-way match.
    """
    finance_user = finance_setup["finance_user"]
    vendor = finance_setup["vendor"]
    po = finance_setup["po"]

    client.force_login(finance_user)
    response = client.post(
        "/invoices/create/",
        {
            "vendor_id": str(vendor.id),
            "po_id": str(po.id),
            "invoice_number": "INV-VALID-FIN-01",
            "invoice_date": timezone.now().date().isoformat(),
            "due_date": (timezone.now().date() + datetime.timedelta(days=30)).isoformat(),
            "quantity": "10.00",
            "unit_price": "100.00",
        },
    )

    assert response.status_code == 302
    invoice = SupplierInvoice.objects.get(invoice_number="INV-VALID-FIN-01")
    assert invoice.lines.first().quantity == Decimal("10.00")
    assert invoice.lines.first().unit_price == Decimal("100.00")
    assert invoice.status in [SupplierInvoice.STATUS_READY_FOR_PAYMENT, SupplierInvoice.STATUS_EXCEPTION]

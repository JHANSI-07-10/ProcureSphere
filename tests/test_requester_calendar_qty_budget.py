from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.api_urls import PRLineSerializer, PurchaseRequisitionSerializer
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import create_purchase_requisition_service


@pytest.fixture
def pr_setup(db):
    org = Organization.objects.create(name="HPE Corp", code="HPE")
    dept = Department.objects.create(name="Engineering", code="ENG", organization=org)
    cc = CostCenter.objects.create(name="Cloud Ops", code="CC-ENG-001", department=dept)
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    requester = User.objects.create_user(
        email="req_user@example.com",
        password="Password123!",
        role=role_req,
        department=dept,
    )
    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "requester": requester,
    }


@pytest.mark.django_db
def test_past_delivery_date_rejected_in_service(pr_setup):
    yesterday = timezone.now().date() - timezone.timedelta(days=1)
    with pytest.raises(ValidationError, match="Requested delivery date cannot be in the past"):
        create_purchase_requisition_service(
            title="Past Delivery PR",
            justification="Testing past delivery date",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=yesterday,
            line_items=[
                {
                    "item_description": "Servers",
                    "quantity": 5,
                    "estimated_unit_price": Decimal("100.00"),
                }
            ],
        )


@pytest.mark.django_db
def test_quantity_must_be_positive_whole_number_in_service(pr_setup):
    future_date = timezone.now().date() + timezone.timedelta(days=10)

    # 1. Quantity = 0
    with pytest.raises(ValidationError, match="greater than zero"):
        create_purchase_requisition_service(
            title="Zero Qty PR",
            justification="Testing zero qty",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Laptops",
                    "quantity": 0,
                    "estimated_unit_price": Decimal("500.00"),
                }
            ],
        )

    # 2. Quantity < 0
    with pytest.raises(ValidationError, match="greater than zero"):
        create_purchase_requisition_service(
            title="Negative Qty PR",
            justification="Testing neg qty",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Laptops",
                    "quantity": -2,
                    "estimated_unit_price": Decimal("500.00"),
                }
            ],
        )

    # 3. Decimal quantity (e.g. 2.5)
    with pytest.raises(ValidationError, match="whole number"):
        create_purchase_requisition_service(
            title="Decimal Qty PR",
            justification="Testing decimal qty",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Cables",
                    "quantity": Decimal("2.5"),
                    "estimated_unit_price": Decimal("10.00"),
                }
            ],
        )


@pytest.mark.django_db
def test_valid_integer_quantity_and_future_date_succeeds(pr_setup):
    future_date = timezone.now().date() + timezone.timedelta(days=15)
    pr = create_purchase_requisition_service(
        title="Valid PR",
        justification="Valid justification",
        requester=pr_setup["requester"],
        department=pr_setup["dept"],
        cost_center=pr_setup["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Monitors",
                "quantity": 3,
                "estimated_unit_price": Decimal("250.00"),
            }
        ],
    )
    assert pr.status == PurchaseRequisition.STATUS_DRAFT
    assert pr.lines.first().quantity == Decimal("3")
    assert pr.total_amount == Decimal("750.00")


@pytest.mark.django_db
def test_serializer_validates_whole_quantity_and_future_date():
    yesterday = timezone.now().date() - timezone.timedelta(days=1)
    ser = PurchaseRequisitionSerializer(data={"requested_delivery_date": yesterday})
    assert not ser.is_valid()
    assert "requested_delivery_date" in ser.errors

    line_ser = PRLineSerializer(
        data={"item_description": "Item", "quantity": 1.75, "estimated_unit_price": 10}
    )
    assert not line_ser.is_valid()
    assert "quantity" in line_ser.errors

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.orders.models import PurchaseOrder

from .forms import POAmendForm

RECEIVABLE_PO_STATUSES = [
    PurchaseOrder.STATUS_ISSUED,
    PurchaseOrder.STATUS_ACKNOWLEDGED,
    PurchaseOrder.STATUS_PARTIAL_RECEIPT,
]


@login_required(login_url="/login/")
def list_view(request):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )

    if role_code == Role.STORES_RECEIVER:
        # Stores / Receiver sees only POs eligible for receiving
        items = (
            PurchaseOrder.objects.filter(status__in=RECEIVABLE_PO_STATUSES)
            .select_related("vendor", "cost_center")
            .prefetch_related("lines")
            .order_by("-created_at")
        )
    elif role_code == Role.VENDOR_USER and getattr(user, "vendor", None):
        items = (
            PurchaseOrder.objects.filter(vendor=user.vendor)
            .select_related("vendor", "cost_center")
            .prefetch_related("lines")
            .order_by("-created_at")
        )
    else:
        items = (
            PurchaseOrder.objects.all()
            .select_related("vendor", "cost_center")
            .prefetch_related("lines")
            .order_by("-created_at")
        )

    return render(
        request,
        "pages/orders/list.html",
        {
            "items": items,
            "role_code": role_code,
            "is_stores_receiver": role_code == Role.STORES_RECEIVER,
            "receivable_statuses": RECEIVABLE_PO_STATUSES,
        },
    )


@login_required(login_url="/login/")
def detail_view(request, pk):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )

    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "vendor", "cost_center", "requisition", "acknowledged_by"
        ).prefetch_related(
            "lines",
            "delivery_schedules",
            "amendments",
            "receipts__received_by",
            "receipts__lines__inspection",
        ),
        pk=pk,
    )

    # Scoping check: Stores Receiver only views receivable POs
    if role_code == Role.STORES_RECEIVER and po.status not in RECEIVABLE_PO_STATUSES:
        messages.warning(
            request,
            f"Purchase Order {po.po_number} is in '{po.get_status_display()}' status and is not currently eligible for goods receiving.",
        )
        return redirect("orders_list")

    # Scoping check: Vendor portal user can only view their own POs
    if (
        role_code == Role.VENDOR_USER
        and getattr(user, "vendor", None)
        and po.vendor_id != user.vendor_id
    ):
        messages.error(request, "You do not have permission to view this purchase order.")
        return redirect("orders_list")

    lines = po.lines.all()
    delivery_schedules = po.delivery_schedules.select_related("po_line").all()
    amendments = po.amendments.select_related("requested_by").order_by("-amendment_number")
    can_amend = role_code in (Role.PROC_EXEC, Role.PROC_MGR, Role.SUPER_ADMIN) or (
        user.is_superuser
    )

    return render(
        request,
        "pages/orders/detail.html",
        {
            "po": po,
            "lines": lines,
            "delivery_schedules": delivery_schedules,
            "amendments": amendments,
            "can_amend": can_amend,
            "amend_form": POAmendForm(po=po) if can_amend else None,
            "role_code": role_code,
            "is_stores_receiver": role_code in (Role.STORES_RECEIVER, Role.SUPER_ADMIN)
            or user.is_superuser,
            "is_receivable": po.status in RECEIVABLE_PO_STATUSES,
        },
    )


def _require_po_execute(user):
    """Procurement Executive PO execution RBAC (backend enforcement).

    PROC_EXEC owns PO execution; PROC_MGR governs amendments; SUPER_ADMIN retains
    full access. Vendor, Stores, Finance, Legal and Auditor roles are denied.
    """
    role_code = getattr(user, "role_code", None)
    if user.is_superuser:
        return
    if role_code not in (Role.PROC_EXEC, Role.PROC_MGR, Role.SUPER_ADMIN):
        raise PermissionDenied("Purchase order execution is restricted to procurement roles.")


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


@login_required(login_url="/login/")
def awards_pending_po_tab_view(request):
    """Pillar 4 HTMX tab: approved awards awaiting PO generation (real DB rows)."""
    _require_po_execute(request.user)
    from apps.sourcing.models import SourcingEvent

    pending = (
        SourcingEvent.objects.filter(
            status=SourcingEvent.STATUS_AWARDED,
            award_decisions__status="APPROVED",
        )
        .exclude(
            purchase_orders__status__in=[
                PurchaseOrder.STATUS_DRAFT,
                PurchaseOrder.STATUS_APPROVAL,
                PurchaseOrder.STATUS_ISSUED,
                PurchaseOrder.STATUS_ACKNOWLEDGED,
                PurchaseOrder.STATUS_PARTIAL_RECEIPT,
                PurchaseOrder.STATUS_COMPLETED,
            ]
        )
        .select_related("requisition")
        .prefetch_related("award_decisions__winning_bid__vendor")
        .order_by("-updated_at")
    )
    return render(request, "orders/partials/htmx_awards_pending.html", {"pending": pending})


@login_required(login_url="/login/")
@require_POST
def po_generate_from_award_htmx_view(request, event_id):
    """Wire button -> generate_po_from_award_service -> generate_purchase_order_service."""
    _require_po_execute(request.user)
    from apps.sourcing import services as sourcing_services
    from apps.sourcing.models import SourcingEvent

    event = get_object_or_404(SourcingEvent, pk=event_id)
    try:
        po = sourcing_services.generate_po_from_award_service(event=event, user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        if _is_htmx(request):
            return render(
                request,
                "orders/partials/htmx_action_result.html",
                {"success": False, "errors": exc.messages},
                status=400,
            )
        return redirect("evaluation_event", event_id=event.id)
    messages.success(request, f"Purchase Order {po.po_number} generated from the award.")
    if _is_htmx(request):
        response = render(
            request, "orders/partials/htmx_action_result.html", {"success": True, "po": po}
        )
        response["HX-Trigger"] = "po-generated"
        return response
    return redirect("order_detail", pk=po.id)


@login_required(login_url="/login/")
def po_amend_modal_view(request, pk):
    """HTMX modal: GET shows reason + line edits; POST calls amend_purchase_order_service."""
    _require_po_execute(request.user)
    po = get_object_or_404(
        PurchaseOrder.objects.select_related("vendor", "cost_center").prefetch_related("lines"),
        pk=pk,
    )
    if po.status == PurchaseOrder.STATUS_CANCELLED:
        raise PermissionDenied("Cancelled purchase orders cannot be amended.")

    def _modal_context(bound_form):
        rows = []
        for line in po.lines.all():
            rows.append(
                {
                    "line": line,
                    "qty_field": bound_form[f"quantity_{line.id}"],
                    "price_field": bound_form[f"unit_price_{line.id}"],
                }
            )
        return {"po": po, "form": bound_form, "line_rows": rows}

    if request.method == "POST":
        form = POAmendForm(request.POST, po=po)
        if form.is_valid():
            from .services import amend_purchase_order_service

            try:
                amendment = amend_purchase_order_service(
                    po=po,
                    reason=form.cleaned_data["reason"],
                    updated_line_items=form.build_updated_line_items(),
                    requested_by_user=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
                return render(
                    request,
                    "orders/partials/htmx_amend_modal.html",
                    _modal_context(form),
                    status=400,
                )
            messages.success(
                request,
                f"PO {po.po_number} amended to V{po.version} "
                f"(Amendment #{amendment.amendment_number}). Prior version preserved.",
            )
            if _is_htmx(request):
                response = render(
                    request,
                    "orders/partials/htmx_action_result.html",
                    {"success": True, "po": po, "amendment": amendment},
                )
                response["HX-Trigger"] = "po-amended"
                return response
            return redirect("order_detail", pk=po.id)
        return render(
            request, "orders/partials/htmx_amend_modal.html", _modal_context(form), status=400
        )
    return render(
        request, "orders/partials/htmx_amend_modal.html", _modal_context(POAmendForm(po=po))
    )


@login_required(login_url="/login/")
def po_amendments_partial_view(request, pk):
    """HTMX fragment: immutable amendment/version history for a PO."""
    _require_po_execute(request.user)
    po = get_object_or_404(PurchaseOrder, pk=pk)
    amendments = po.amendments.select_related("requested_by").order_by("-amendment_number")
    return render(
        request, "orders/partials/htmx_amendments.html", {"po": po, "amendments": amendments}
    )


@login_required(login_url="/login/")
def po_create_view(request):
    """View & modal to issue a purchase order."""
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else Role.SUPER_ADMIN
    )
    if role_code not in [Role.SUPER_ADMIN, Role.PROC_EXEC, Role.PROC_MGR]:
        raise PermissionDenied("Only Procurement users can issue purchase orders.")

    if request.method == "POST":
        from decimal import Decimal

        from apps.organization.models import CostCenter
        from apps.requisitions.models import PurchaseRequisition
        from apps.vendors.models import Vendor

        from .services import generate_purchase_order_service

        vendor_id = request.POST.get("vendor_id")
        cost_center_id = request.POST.get("cost_center_id")
        req_id = request.POST.get("requisition_id")
        item_name = request.POST.get("item_name", "Line Item")
        qty = request.POST.get("quantity", "1")
        price = request.POST.get("unit_price", "0")
        terms = request.POST.get(
            "terms_and_conditions",
            "Standard HPE Enterprise Procurement Terms & Conditions",
        )

        vendor = get_object_or_404(Vendor, pk=vendor_id)
        cost_center = get_object_or_404(CostCenter, pk=cost_center_id)
        requisition = PurchaseRequisition.objects.filter(pk=req_id).first() if req_id else None

        line_items = [
            {
                "item_description": item_name,
                "quantity": Decimal(str(qty)),
                "unit_price": Decimal(str(price)),
                "unit_of_measure": "EA",
            }
        ]

        po = generate_purchase_order_service(
            vendor=vendor,
            cost_center=cost_center,
            line_items=line_items,
            created_by_user=user,
            requisition=requisition,
            terms_and_conditions=terms,
        )
        messages.success(request, f"Purchase Order {po.po_number} issued successfully.")
        return redirect("order_detail", pk=po.pk)

    from apps.organization.models import CostCenter
    from apps.requisitions.models import PurchaseRequisition
    from apps.vendors.models import Vendor

    vendors = Vendor.objects.filter(status=Vendor.STATUS_ACTIVE).order_by("legal_name")
    cost_centers = CostCenter.objects.all().order_by("code")
    requisitions = PurchaseRequisition.objects.filter(
        status=PurchaseRequisition.STATUS_APPROVED
    ).order_by("-created_at")

    return render(
        request,
        "orders/partials/po_create_modal.html",
        {
            "vendors": vendors,
            "cost_centers": cost_centers,
            "requisitions": requisitions,
        },
    )

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from apps.accounts.models import Role
from apps.orders.models import PurchaseOrder
from apps.receipts.models import (
    GoodsReceipt,
    ReceiptLine,
    RejectionRecord,
)
from apps.receipts.services import (
    correct_receipt_line_quantity_service,
    create_goods_receipt_service,
    handoff_goods_to_stock_service,
    process_vendor_return_service,
    record_inspection_service,
)


def _filter_receipts_by_status(queryset, status_filter: str):
    if status_filter == "PENDING":
        return queryset.filter(lines__inspection__isnull=True).distinct()
    if status_filter == "PASSED":
        return (
            queryset.exclude(lines__inspection__isnull=True)
            .exclude(lines__inspection__passed=False)
            .exclude(lines__quantity_rejected__gt=Decimal("0.00"))
            .distinct()
        )
    if status_filter in ["FAILED", "REJECTED"]:
        return queryset.filter(
            Q(lines__inspection__passed=False)
            | Q(lines__quantity_rejected__gt=Decimal("0.00"))
            | Q(lines__rejections__isnull=False)
        ).distinct()
    if status_filter == "PARTIALLY_INSPECTED":
        return (
            queryset.filter(lines__inspection__isnull=False)
            .filter(lines__inspection__isnull=True)
            .distinct()
        )
    return queryset


def _filter_receipts_queryset(
    queryset,
    *,
    search_query: str,
    status_filter: str,
    po_filter: str,
    date_from_str: str,
    date_to_str: str,
):
    if search_query:
        queryset = queryset.filter(
            Q(grn_number__icontains=search_query)
            | Q(po__po_number__icontains=search_query)
            | Q(po__vendor__legal_name__icontains=search_query)
            | Q(delivery_note_number__icontains=search_query)
        )

    if po_filter:
        queryset = queryset.filter(Q(po__id=po_filter) | Q(po__po_number__iexact=po_filter))

    if date_from_str:
        parsed_from = parse_date(date_from_str)
        if parsed_from:
            queryset = queryset.filter(received_date__date__gte=parsed_from)

    if date_to_str:
        parsed_to = parse_date(date_to_str)
        if parsed_to:
            queryset = queryset.filter(received_date__date__lte=parsed_to)

    return _filter_receipts_by_status(queryset, status_filter)


@login_required(login_url="/login/")
def receipts_list_view(request):
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot access internal stores receiving queues.",
        )
        return redirect("vendor_dashboard")

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()
    po_filter = request.GET.get("po", "").strip()
    date_from_str = request.GET.get("date_from", "").strip()
    date_to_str = request.GET.get("date_to", "").strip()

    base_qs = (
        GoodsReceipt.objects.select_related("po__vendor", "received_by")
        .prefetch_related("lines__po_line", "lines__inspection", "lines__rejections")
        .order_by("-received_date")
    )

    receipts_qs = _filter_receipts_queryset(
        base_qs,
        search_query=search_query,
        status_filter=status_filter,
        po_filter=po_filter,
        date_from_str=date_from_str,
        date_to_str=date_to_str,
    )

    total_count = receipts_qs.count()

    paginator = Paginator(receipts_qs, 10)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.get_page(1)

    open_pos = PurchaseOrder.objects.filter(
        status__in=[
            PurchaseOrder.STATUS_ISSUED,
            PurchaseOrder.STATUS_ACKNOWLEDGED,
            PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        ]
    ).select_related("vendor")

    po_list = (
        PurchaseOrder.objects.filter(receipts__isnull=False)
        .distinct()
        .order_by("po_number")
        .values("id", "po_number")
    )

    query_params = request.GET.copy()
    if "page" in query_params:
        del query_params["page"]
    query_string = query_params.urlencode()

    has_active_filters = bool(
        search_query or status_filter or po_filter or date_from_str or date_to_str
    )

    return render(
        request,
        "receipts/receipts_list.html",
        {
            "receipts": page_obj,
            "page_obj": page_obj,
            "total_count": total_count,
            "open_pos": open_pos,
            "po_list": po_list,
            "search_query": search_query,
            "status_filter": status_filter,
            "po_filter": po_filter,
            "date_from": date_from_str,
            "date_to": date_to_str,
            "query_string": query_string,
            "has_active_filters": has_active_filters,
        },
    )


@login_required(login_url="/login/")
def receipt_detail_view(request, grn_id):
    grn = get_object_or_404(
        GoodsReceipt.objects.select_related(
            "po__vendor", "po__cost_center", "received_by"
        ).prefetch_related(
            "lines__po_line", "lines__inspection__inspected_by", "lines__rejections"
        ),
        id=grn_id,
    )
    from apps.audit.models import AuditLog

    line_ids = [str(line.id) for line in grn.lines.all()]
    rej_ids = [str(rej.id) for line in grn.lines.all() for rej in line.rejections.all()]
    audit_logs = (
        AuditLog.objects.filter(
            Q(target_model="GoodsReceipt", target_object_id=str(grn.id))
            | Q(target_model="ReceiptLine", target_object_id__in=line_ids)
            | Q(target_model="RejectionRecord", target_object_id__in=rej_ids)
        )
        .select_related("actor")
        .order_by("-timestamp")
    )

    return render(
        request,
        "receipts/receipt_detail.html",
        {
            "grn": grn,
            "po": grn.po,
            "lines": grn.lines.all(),
            "audit_logs": audit_logs,
        },
    )


def _parse_single_inspection_line(request, line):
    line_id_str = str(line.id)
    passed_raw = request.POST.get(f"passed_{line_id_str}", "true").strip().lower()
    passed = passed_raw in ["true", "1", "passed", "pass", "yes"]
    notes = request.POST.get(f"inspection_notes_{line_id_str}", "").strip()
    rejected_qty_raw = request.POST.get(f"rejected_quantity_{line_id_str}", "").strip()
    rejection_reason = request.POST.get(f"rejection_reason_{line_id_str}", "").strip()
    returned_to_vendor = request.POST.get(f"returned_to_vendor_{line_id_str}") in [
        "on",
        "true",
        "1",
        "yes",
    ]

    qty_rejected = Decimal("0.00")
    if rejected_qty_raw:
        try:
            qty_rejected = Decimal(rejected_qty_raw)
        except Exception:
            return None, f"Invalid rejected quantity for '{line.po_line.item_description}'."

        if qty_rejected < Decimal("0.00"):
            return (
                None,
                f"Rejected quantity cannot be negative for '{line.po_line.item_description}'.",
            )
        if qty_rejected > line.quantity_received:
            return None, (
                f"Rejected quantity ({qty_rejected}) cannot exceed received quantity "
                f"({line.quantity_received}) for '{line.po_line.item_description}'."
            )
    elif not passed:
        qty_rejected = line.quantity_received

    if qty_rejected > Decimal("0.00") and not rejection_reason:
        return (
            None,
            f"Rejection reason is required when rejecting items for '{line.po_line.item_description}'.",
        )

    item_data = {
        "receipt_line_id": line_id_str,
        "passed": passed and (qty_rejected == Decimal("0.00")),
        "inspection_notes": notes,
        "rejected_quantity": qty_rejected,
        "rejection_reason": rejection_reason,
        "returned_to_vendor": returned_to_vendor,
    }
    return item_data, None


def _extract_inspection_form_data(request, lines):
    inspection_items = []
    validation_errors = []
    for line in lines:
        item_data, error = _parse_single_inspection_line(request, line)
        if error:
            validation_errors.append(error)
        elif item_data:
            inspection_items.append(item_data)
    return inspection_items, validation_errors


@login_required(login_url="/login/")
def receipt_inspect_view(request, grn_id):
    grn = get_object_or_404(
        GoodsReceipt.objects.select_related(
            "po__vendor", "po__cost_center", "received_by"
        ).prefetch_related(
            "lines__po_line", "lines__inspection__inspected_by", "lines__rejections"
        ),
        id=grn_id,
    )
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
    ]
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot perform quality inspections.",
        )
        return redirect("vendor_dashboard")

    if not is_authorized:
        messages.error(
            request,
            "Permission Denied: Only Stores Receiver or Super Admin can perform quality inspections.",
        )
        return redirect("receipt_detail", grn_id=grn.id)

    lines = grn.lines.all()

    if request.method == "POST":
        inspection_items, validation_errors = _extract_inspection_form_data(request, lines)

        if validation_errors:
            for err in validation_errors:
                messages.error(request, err)
            return render(
                request,
                "receipts/receipt_inspect.html",
                {"grn": grn, "po": grn.po, "lines": lines},
            )

        if not inspection_items:
            messages.error(request, "No receipt line items found to inspect.")
            return render(
                request,
                "receipts/receipt_inspect.html",
                {"grn": grn, "po": grn.po, "lines": lines},
            )

        try:
            record_inspection_service(
                receipt=grn,
                inspected_by=request.user,
                inspection_items=inspection_items,
            )
            messages.success(
                request,
                f"Quality inspection recorded successfully for Goods Receipt {grn.grn_number}.",
            )
            return redirect("receipt_detail", grn_id=grn.id)
        except ValidationError as ve:
            error_msg = ve.message if hasattr(ve, "message") else str(ve)
            messages.error(request, error_msg)
            return render(
                request,
                "receipts/receipt_inspect.html",
                {"grn": grn, "po": grn.po, "lines": lines},
            )
        except Exception as e:
            messages.error(request, f"Error recording inspection: {str(e)}")
            return render(
                request,
                "receipts/receipt_inspect.html",
                {"grn": grn, "po": grn.po, "lines": lines},
            )

    return render(
        request,
        "receipts/receipt_inspect.html",
        {"grn": grn, "po": grn.po, "lines": lines},
    )


def _parse_single_receipt_line(request, line):
    qty_recv_raw = request.POST.get(f"quantity_received_{line.id}", "").strip()
    qty_acc_raw = request.POST.get(f"quantity_accepted_{line.id}", "").strip()
    notes = request.POST.get(f"notes_{line.id}", "").strip()

    if not qty_recv_raw:
        return None, None

    try:
        qty_recv = Decimal(qty_recv_raw)
    except Exception:
        return None, f"Invalid quantity received value for '{line.item_description}'."

    if qty_acc_raw:
        try:
            qty_acc = Decimal(qty_acc_raw)
        except Exception:
            return None, f"Invalid quantity accepted value for '{line.item_description}'."
    else:
        qty_acc = qty_recv

    return {
        "po_line_id": str(line.id),
        "quantity_received": qty_recv,
        "quantity_accepted": qty_acc,
        "notes": notes,
    }, None


def _extract_receipt_form_data(request, lines):
    receipt_items = []
    validation_errors = []
    for line in lines:
        item_data, error = _parse_single_receipt_line(request, line)
        if error:
            validation_errors.append(error)
        elif item_data:
            receipt_items.append(item_data)
    return receipt_items, validation_errors


@login_required(login_url="/login/")
def receipt_create_view(request, po_id):
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
    ]
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot create goods receipts.",
        )
        return redirect("vendor_dashboard")

    if not is_authorized:
        messages.error(
            request,
            "Permission Denied: Only Stores Receiver or Super Admin can create goods receipts.",
        )
        return redirect("receipts_list")

    po = get_object_or_404(
        PurchaseOrder.objects.select_related("vendor", "cost_center").prefetch_related("lines"),
        id=po_id,
    )

    eligible_statuses = [
        PurchaseOrder.STATUS_ISSUED,
        PurchaseOrder.STATUS_ACKNOWLEDGED,
        PurchaseOrder.STATUS_PARTIAL_RECEIPT,
    ]
    if po.status not in eligible_statuses:
        messages.error(
            request,
            f"Cannot record goods receipt against Purchase Order {po.po_number} in '{po.get_status_display()}' status.",
        )
        return redirect("orders_list")

    lines = po.lines.all()

    if request.method == "POST":
        delivery_note_number = request.POST.get("delivery_note_number", "").strip()
        remarks = request.POST.get("remarks", "").strip()

        receipt_items, validation_errors = _extract_receipt_form_data(request, lines)

        if validation_errors:
            for err in validation_errors:
                messages.error(request, err)
            return render(
                request,
                "receipts/receipt_create.html",
                {
                    "po": po,
                    "lines": lines,
                    "delivery_note_number": delivery_note_number,
                    "remarks": remarks,
                },
            )

        if not receipt_items:
            messages.error(
                request,
                "Please specify a receiving quantity greater than 0 for at least one line item.",
            )
            return render(
                request,
                "receipts/receipt_create.html",
                {
                    "po": po,
                    "lines": lines,
                    "delivery_note_number": delivery_note_number,
                    "remarks": remarks,
                },
            )

        try:
            receipt = create_goods_receipt_service(
                po=po,
                received_by=request.user,
                receipt_items=receipt_items,
                delivery_note_number=delivery_note_number,
                remarks=remarks,
            )
            messages.success(
                request,
                f"Goods Receipt Note {receipt.grn_number} created successfully for {po.po_number}.",
            )
            return redirect("receipts_list")
        except ValidationError as ve:
            error_msg = ve.message if hasattr(ve, "message") else str(ve)
            messages.error(request, error_msg)
            return render(
                request,
                "receipts/receipt_create.html",
                {
                    "po": po,
                    "lines": lines,
                    "delivery_note_number": delivery_note_number,
                    "remarks": remarks,
                },
            )
        except Exception as e:
            messages.error(request, f"Error creating goods receipt: {str(e)}")
            return render(
                request,
                "receipts/receipt_create.html",
                {
                    "po": po,
                    "lines": lines,
                    "delivery_note_number": delivery_note_number,
                    "remarks": remarks,
                },
            )

    return render(
        request,
        "receipts/receipt_create.html",
        {
            "po": po,
            "lines": lines,
            "delivery_note_number": "",
            "remarks": "",
        },
    )


@login_required(login_url="/login/")
def receipt_stock_handoff_view(request, grn_id):
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
    ]
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot hand off goods to stock.",
        )
        return redirect("vendor_dashboard")

    if not is_authorized:
        messages.error(request, "Access denied: Only Stores Receiver can hand off goods to stock.")
        return redirect("receipt_detail", grn_id=grn_id)

    grn = get_object_or_404(
        GoodsReceipt.objects.select_related(
            "po__vendor", "po__cost_center", "received_by"
        ).prefetch_related(
            "lines__po_line",
            "lines__inspection__inspected_by",
            "lines__rejections",
            "lines__stock_handoff",
        ),
        id=grn_id,
    )

    accepted_lines = [line for line in grn.lines.all() if line.quantity_accepted > Decimal("0.00")]
    if not accepted_lines:
        messages.error(
            request,
            f"Cannot hand off Goods Receipt {grn.grn_number} to stock: No accepted quantities available.",
        )
        return redirect("receipt_detail", grn_id=grn.id)

    if request.method == "POST":
        storage_location = request.POST.get("storage_location", "MAIN-WH").strip()
        handoff_notes = request.POST.get("handoff_notes", "").strip()

        try:
            handoff_goods_to_stock_service(
                receipt=grn,
                handed_off_by=request.user,
                storage_location=storage_location,
                handoff_notes=handoff_notes,
            )
            messages.success(
                request,
                f"Accepted goods for {grn.grn_number} successfully handed off to stock ({storage_location}).",
            )
            return redirect("receipt_detail", grn_id=grn.id)
        except ValidationError as ve:
            error_msg = ve.message if hasattr(ve, "message") else str(ve)
            messages.error(request, error_msg)
        except Exception as e:
            messages.error(request, f"Error processing stock handoff: {str(e)}")

    return render(
        request,
        "receipts/receipt_handoff.html",
        {
            "grn": grn,
            "po": grn.po,
            "lines": grn.lines.all(),
            "accepted_lines": accepted_lines,
        },
    )


@login_required(login_url="/login/")
def receipt_inspection_queue_view(request):
    """
    Dedicated operational queue for Quality Inspection.
    Displays all GRNs requiring or undergoing inspection, item breakdowns, and direct Inspect actions.
    """
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot access internal stores inspection queues.",
        )
        return redirect("vendor_dashboard")

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()

    base_qs = (
        GoodsReceipt.objects.select_related("po__vendor", "received_by")
        .prefetch_related(
            "lines__po_line",
            "lines__inspection__inspected_by",
            "lines__rejections",
        )
        .order_by("-received_date")
    )

    if search_query:
        base_qs = base_qs.filter(
            Q(grn_number__icontains=search_query)
            | Q(po__po_number__icontains=search_query)
            | Q(po__vendor__legal_name__icontains=search_query)
            | Q(lines__po_line__item_description__icontains=search_query)
        ).distinct()

    all_grns = list(base_qs)

    if status_filter == "PENDING":
        filtered_grns = [g for g in all_grns if g.overall_inspection_status == "PENDING"]
    elif status_filter == "PASSED":
        filtered_grns = [g for g in all_grns if g.overall_inspection_status == "PASSED"]
    elif status_filter in ["FAILED", "REJECTED"]:
        filtered_grns = [
            g for g in all_grns if g.overall_inspection_status in ["FAILED", "REJECTED"]
        ]
    elif status_filter == "PARTIALLY_INSPECTED":
        filtered_grns = [
            g for g in all_grns if g.overall_inspection_status == "PARTIALLY_INSPECTED"
        ]
    else:
        filtered_grns = all_grns

    # Real DB-level inspection metrics
    pending_count = sum(1 for g in all_grns if g.overall_inspection_status == "PENDING")
    passed_count = sum(1 for g in all_grns if g.overall_inspection_status == "PASSED")
    rejected_count = sum(
        1 for g in all_grns if g.overall_inspection_status in ["FAILED", "REJECTED"]
    )
    total_count = len(all_grns)

    paginator = Paginator(filtered_grns, 10)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.get_page(1)

    return render(
        request,
        "receipts/inspection_queue.html",
        {
            "grns": page_obj,
            "page_obj": page_obj,
            "search_query": search_query,
            "status_filter": status_filter,
            "pending_count": pending_count,
            "passed_count": passed_count,
            "rejected_count": rejected_count,
            "total_count": total_count,
        },
    )


@login_required(login_url="/login/")
def receipt_stock_handoff_queue_view(request):
    """
    Dedicated operational queue for Stock Handoff.
    Shows GRNs and receipt lines eligible for warehouse stock posting, excluding rejected quantities.
    """
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot access internal stock handoff queues.",
        )
        return redirect("vendor_dashboard")

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()

    base_qs = (
        GoodsReceipt.objects.select_related("po__vendor", "received_by")
        .prefetch_related(
            "lines__po_line",
            "lines__stock_handoff__handed_off_by",
            "lines__inspection",
            "lines__rejections",
        )
        .order_by("-received_date")
    )

    if search_query:
        base_qs = base_qs.filter(
            Q(grn_number__icontains=search_query)
            | Q(po__po_number__icontains=search_query)
            | Q(po__vendor__legal_name__icontains=search_query)
            | Q(lines__po_line__item_description__icontains=search_query)
        ).distinct()

    # Filter to only GRNs with accepted quantity > 0
    eligible_grns = [
        g
        for g in base_qs
        if any(line.quantity_accepted > Decimal("0.00") for line in g.lines.all())
    ]

    if status_filter == "PENDING":
        filtered_grns = [g for g in eligible_grns if g.stock_handoff_status == "PENDING_HANDOFF"]
    elif status_filter == "HANDED_OFF":
        filtered_grns = [g for g in eligible_grns if g.stock_handoff_status == "HANDED_OFF"]
    elif status_filter == "PARTIALLY_HANDED_OFF":
        filtered_grns = [
            g for g in eligible_grns if g.stock_handoff_status == "PARTIALLY_HANDED_OFF"
        ]
    else:
        filtered_grns = eligible_grns

    # Real operational metrics
    all_eligible = [
        g
        for g in GoodsReceipt.objects.prefetch_related("lines__stock_handoff")
        if any(line.quantity_accepted > Decimal("0.00") for line in g.lines.all())
    ]
    pending_handoff_count = sum(
        1 for g in all_eligible if g.stock_handoff_status == "PENDING_HANDOFF"
    )
    completed_handoff_count = sum(1 for g in all_eligible if g.stock_handoff_status == "HANDED_OFF")
    partial_handoff_count = sum(
        1 for g in all_eligible if g.stock_handoff_status == "PARTIALLY_HANDED_OFF"
    )
    total_eligible_count = len(all_eligible)

    paginator = Paginator(filtered_grns, 10)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.get_page(1)

    return render(
        request,
        "receipts/stock_handoff_queue.html",
        {
            "grns": page_obj,
            "page_obj": page_obj,
            "search_query": search_query,
            "status_filter": status_filter,
            "pending_handoff_count": pending_handoff_count,
            "completed_handoff_count": completed_handoff_count,
            "partial_handoff_count": partial_handoff_count,
            "total_eligible_count": total_eligible_count,
        },
    )


@login_required(login_url="/login/")
def receipt_rejections_queue_view(request):
    """
    Dedicated operational queue for Rejections & Returns.
    Displays all rejected receipt lines, rejection reasons, return-to-vendor status, and PO links.
    """
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot access internal rejection queues.",
        )
        return redirect("vendor_dashboard")

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()

    base_qs = (
        ReceiptLine.objects.filter(
            Q(quantity_rejected__gt=Decimal("0.00")) | Q(rejections__isnull=False)
        )
        .select_related(
            "receipt__po__vendor",
            "receipt__received_by",
            "po_line",
            "inspection__inspected_by",
        )
        .prefetch_related("rejections")
        .distinct()
        .order_by("-receipt__received_date")
    )

    if search_query:
        base_qs = base_qs.filter(
            Q(receipt__grn_number__icontains=search_query)
            | Q(receipt__po__po_number__icontains=search_query)
            | Q(receipt__po__vendor__legal_name__icontains=search_query)
            | Q(po_line__item_description__icontains=search_query)
            | Q(rejections__rejection_reason__icontains=search_query)
        ).distinct()

    if status_filter == "RETURNED":
        base_qs = base_qs.filter(rejections__returned_to_vendor=True).distinct()
    elif status_filter == "NOT_RETURNED":
        base_qs = base_qs.filter(
            Q(rejections__returned_to_vendor=False) | Q(rejections__isnull=True)
        ).distinct()

    total_rejected_lines = (
        ReceiptLine.objects.filter(
            Q(quantity_rejected__gt=Decimal("0.00")) | Q(rejections__isnull=False)
        )
        .distinct()
        .count()
    )

    returned_count = RejectionRecord.objects.filter(returned_to_vendor=True).count()
    pending_return_count = RejectionRecord.objects.filter(returned_to_vendor=False).count()

    paginator = Paginator(base_qs, 15)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.get_page(1)

    return render(
        request,
        "receipts/rejections_queue.html",
        {
            "rejected_lines": page_obj,
            "page_obj": page_obj,
            "search_query": search_query,
            "status_filter": status_filter,
            "total_rejected_lines": total_rejected_lines,
            "returned_count": returned_count,
            "pending_return_count": pending_return_count,
        },
    )


@login_required(login_url="/login/")
def record_vendor_return_view(request, rejection_id):
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
    ]
    if not is_authorized:
        messages.error(
            request,
            "Permission Denied: Only Stores Receiver or Super Admin can process vendor returns.",
        )
        return redirect("receipts_rejections_queue")

    rejection = get_object_or_404(
        RejectionRecord.objects.select_related(
            "receipt_line__receipt__po", "receipt_line__po_line"
        ),
        id=rejection_id,
    )

    if request.method == "POST":
        tracking_reference = request.POST.get("tracking_reference", "").strip()
        return_notes = request.POST.get("return_notes", "").strip()
        return_qty_raw = request.POST.get("return_quantity", "").strip()
        return_qty = Decimal(return_qty_raw) if return_qty_raw else None

        try:
            process_vendor_return_service(
                returned_by=request.user,
                rejection_record=rejection,
                return_quantity=return_qty,
                tracking_reference=tracking_reference,
                return_notes=return_notes,
            )
            messages.success(
                request,
                f"Vendor return recorded successfully for line '{rejection.receipt_line.po_line.item_description}'.",
            )
        except ValidationError as ve:
            messages.error(request, ve.message if hasattr(ve, "message") else str(ve))
        except Exception as e:
            messages.error(request, f"Error processing vendor return: {str(e)}")

        next_url = request.POST.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)

    return redirect("receipts_rejections_queue")


@login_required(login_url="/login/")
def receipt_line_correct_view(request, line_id):
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
    ]
    if not is_authorized:
        messages.error(
            request,
            "Permission Denied: Only Stores Receiver or Super Admin can correct receipt quantities.",
        )
        return redirect("receipts_list")

    line = get_object_or_404(
        ReceiptLine.objects.select_related("receipt__po", "po_line"),
        id=line_id,
    )

    if request.method == "POST":
        new_qty_recv_raw = request.POST.get("quantity_received", "").strip()
        new_qty_acc_raw = request.POST.get("quantity_accepted", "").strip()
        reason = request.POST.get("reason", "").strip()
        notes = request.POST.get("notes", "").strip()

        try:
            new_qty_recv = Decimal(new_qty_recv_raw)
            new_qty_acc = Decimal(new_qty_acc_raw) if new_qty_acc_raw else None
            correct_receipt_line_quantity_service(
                receipt_line=line,
                corrected_by=request.user,
                new_quantity_received=new_qty_recv,
                new_quantity_accepted=new_qty_acc,
                reason=reason,
                correction_notes=notes,
            )
            messages.success(
                request,
                f"Quantity correction applied successfully to line '{line.po_line.item_description}'.",
            )
        except ValidationError as ve:
            messages.error(request, ve.message if hasattr(ve, "message") else str(ve))
        except Exception as e:
            messages.error(request, f"Error applying quantity correction: {str(e)}")

    return redirect("receipt_detail", grn_id=line.receipt_id)


def get_stores_dashboard_data(user=None):
    """
    Computes mutually exclusive, database-backed operational metrics for Stores/Receiver desk.
    """
    from apps.audit.models import AuditLog
    from apps.orders.models import PurchaseOrder
    from apps.receipts.models import (
        GoodsReceipt,
        InspectionRecord,
        RejectionRecord,
        StockHandoffRecord,
    )

    all_grns = list(
        GoodsReceipt.objects.select_related("po__vendor", "received_by")
        .prefetch_related(
            "lines__po_line",
            "lines__inspection",
            "lines__rejections",
            "lines__stock_handoff",
        )
        .order_by("-received_date")
    )
    total_grns = len(all_grns)

    pending_inspections = sum(1 for g in all_grns if g.overall_inspection_status == "PENDING")
    passed_receipts = sum(1 for g in all_grns if g.overall_inspection_status == "PASSED")
    failed_receipts = sum(
        1 for g in all_grns if g.overall_inspection_status in ["FAILED", "REJECTED"]
    )
    partially_inspected_receipts = sum(
        1 for g in all_grns if g.overall_inspection_status == "PARTIALLY_INSPECTED"
    )

    pending_deliveries = PurchaseOrder.objects.filter(
        status__in=[
            PurchaseOrder.STATUS_ISSUED,
            PurchaseOrder.STATUS_ACKNOWLEDGED,
            PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        ]
    ).count()
    partially_received_pos = PurchaseOrder.objects.filter(
        status=PurchaseOrder.STATUS_PARTIAL_RECEIPT
    ).count()
    completed_pos = PurchaseOrder.objects.filter(status=PurchaseOrder.STATUS_COMPLETED).count()

    all_rejections = list(
        RejectionRecord.objects.select_related("receipt_line__receipt", "receipt_line__po_line")
    )
    total_rejections = len(all_rejections)
    returned_to_vendor_count = sum(1 for r in all_rejections if r.returned_to_vendor)
    pending_vendor_returns_count = sum(1 for r in all_rejections if not r.returned_to_vendor)

    stock_handoffs_count = StockHandoffRecord.objects.count()
    eligible_handoff_grns = [
        g
        for g in all_grns
        if any(line.quantity_accepted > Decimal("0.00") for line in g.lines.all())
    ]
    pending_stock_handoff_grns = sum(
        1 for g in eligible_handoff_grns if g.stock_handoff_status == "PENDING_HANDOFF"
    )
    completed_stock_handoff_grns = sum(
        1 for g in eligible_handoff_grns if g.stock_handoff_status == "HANDED_OFF"
    )

    quantity_corrections_count = (
        AuditLog.objects.filter(
            target_model="ReceiptLine",
            action=AuditLog.ACTION_UPDATE,
        )
        .filter(new_state__action="QUANTITY_CORRECTION")
        .count()
    )

    total_inspections = InspectionRecord.objects.count()

    metrics = {
        "total_grns": total_grns,
        "pending_inspections": pending_inspections,
        "passed_receipts": passed_receipts,
        "failed_receipts": failed_receipts,
        "partially_inspected_receipts": partially_inspected_receipts,
        "total_inspections": total_inspections,
        "pending_deliveries": pending_deliveries,
        "partially_received_pos": partially_received_pos,
        "completed_pos": completed_pos,
        "total_rejections": total_rejections,
        "returned_to_vendor_count": returned_to_vendor_count,
        "pending_vendor_returns_count": pending_vendor_returns_count,
        "stock_handoffs_count": stock_handoffs_count,
        "pending_stock_handoff_grns": pending_stock_handoff_grns,
        "pending_stock_handoffs": pending_stock_handoff_grns,
        "completed_stock_handoff_grns": completed_stock_handoff_grns,
        "quantity_corrections_count": quantity_corrections_count,
    }

    recent_grns = all_grns[:10]

    # 1. Pending Deliveries: POs issued or in partial receipt awaiting dock receiving
    pending_deliveries_items = list(
        PurchaseOrder.objects.filter(
            status__in=[
                PurchaseOrder.STATUS_ISSUED,
                PurchaseOrder.STATUS_ACKNOWLEDGED,
                PurchaseOrder.STATUS_PARTIAL_RECEIPT,
            ]
        )
        .select_related("vendor", "cost_center")
        .prefetch_related("lines")
        .order_by("-created_at")[:15]
    )

    # 2. Pending Inspections: GRNs received at the dock awaiting quality inspection
    pending_inspections_items = [g for g in all_grns if g.overall_inspection_status == "PENDING"][
        :15
    ]

    # 3. Pending Stock Handoffs: Inspected GRNs with accepted quantities awaiting transfer to warehouse stock
    pending_handoffs_items = [
        g
        for g in eligible_handoff_grns
        if g.stock_handoff_status in ["PENDING_HANDOFF", "PARTIALLY_HANDED_OFF"]
    ][:15]

    # 4. Rejections & Returns: Receipt lines rejected during quality inspection
    pending_rejections_items = list(
        ReceiptLine.objects.filter(
            Q(quantity_rejected__gt=Decimal("0.00")) | Q(rejections__isnull=False)
        )
        .select_related(
            "receipt__po__vendor",
            "receipt__received_by",
            "po_line",
            "inspection__inspected_by",
        )
        .prefetch_related("rejections")
        .distinct()
        .order_by("-receipt__received_date")[:15]
    )

    return {
        "metrics": metrics,
        "recent_grns": recent_grns,
        "pending_deliveries": pending_deliveries_items,
        "pending_inspections": pending_inspections_items,
        "pending_handoffs": pending_handoffs_items,
        "pending_rejections": pending_rejections_items,
    }


@login_required(login_url="/login/")
def receipt_dashboard_view(request):
    """
    Dedicated dashboard view for Stores / Receiver module.
    Renders live database-backed metrics, queue summaries, and status tracking.
    """
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    if role_code == Role.VENDOR_USER:
        messages.error(
            request,
            "Access Denied: Vendor users cannot access internal stores dashboards.",
        )
        return redirect("vendor_dashboard")

    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
        Role.AUDITOR,
    ]
    if not is_authorized:
        messages.error(request, "Permission Denied: Access to Stores Dashboard is restricted.")
        return redirect("receipts_list")

    data = get_stores_dashboard_data(request.user)
    return render(
        request,
        "pages/dashboards/stores_dashboard.html",
        {
            "metrics": data["metrics"],
            "recent_grns": data["recent_grns"],
            "pending_deliveries": data.get("pending_deliveries", []),
            "pending_inspections": data.get("pending_inspections", []),
            "pending_handoffs": data.get("pending_handoffs", []),
            "pending_rejections": data.get("pending_rejections", []),
        },
    )


@login_required(login_url="/login/")
def receipt_delivery_performance_report_view(request):
    """
    Day 16: Receipt, Rejection & Delivery Performance Report view.
    Provides filtered reports, summary KPIs, and export capabilities.
    Restricted to Stores Receiver, Super Admin, and Auditor (read-only audit).
    """
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if hasattr(request.user, "role") and request.user.role else None
    )
    is_authorized = request.user.is_superuser or role_code in [
        Role.STORES_RECEIVER,
        Role.SUPER_ADMIN,
        Role.AUDITOR,
    ]
    if not is_authorized:
        messages.error(
            request, "Permission Denied: Access to Delivery Performance Report is restricted."
        )
        return redirect("receipts_list")

    from apps.audit.models import AuditLog
    from apps.reports.services import (
        get_receipt_delivery_performance_summary,
        get_receipt_rejection_report,
    )

    start_date = request.GET.get("start_date", "").strip() or None
    end_date = request.GET.get("end_date", "").strip() or None
    vendor = request.GET.get("vendor", "").strip() or None
    po_number = request.GET.get("po_number", "").strip() or None
    status_param = request.GET.get("status", "").strip() or None
    export_format = request.GET.get("export", "").strip().upper()

    report_lines = get_receipt_rejection_report(
        start_date=start_date,
        end_date=end_date,
        vendor=vendor,
        po_number=po_number,
        inspection_status=status_param,
    )
    summary = get_receipt_delivery_performance_summary(
        start_date=start_date,
        end_date=end_date,
        vendor=vendor,
    )

    # Handle direct export
    if export_format in ["CSV", "XLSX"]:
        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.ACTION_EXPORT,
            target_model="GoodsReceipt",
            target_object_id=f"DeliveryPerformanceReport_{export_format}",
            new_state={"format": export_format, "rows": len(report_lines)},
        )

        fieldnames = [
            "grn_number",
            "po_number",
            "vendor",
            "delivery_note_number",
            "received_date",
            "expected_delivery_date",
            "delivery_performance",
            "days_variance",
            "qty_received",
            "qty_accepted",
            "qty_rejected",
            "rejection_rate_percent",
            "inspection_status",
            "return_status",
            "stock_handoff_status",
        ]

        if export_format == "CSV":
            import csv

            from django.http import HttpResponse

            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = (
                'attachment; filename="receipt_delivery_performance.csv"'
            )
            writer = csv.DictWriter(response, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in report_lines:
                writer.writerow(row)
            return response

        elif export_format == "XLSX":
            from io import BytesIO

            import openpyxl
            from django.http import HttpResponse

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Delivery Performance"
            ws.append(fieldnames)
            for row in report_lines:
                ws.append([str(row.get(f, "")) for f in fieldnames])
            out = BytesIO()
            wb.save(out)
            response = HttpResponse(
                out.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = (
                'attachment; filename="receipt_delivery_performance.xlsx"'
            )
            return response

    return render(
        request,
        "receipts/delivery_performance_report.html",
        {
            "report_lines": report_lines,
            "summary": summary,
            "filters": {
                "start_date": start_date or "",
                "end_date": end_date or "",
                "vendor": vendor or "",
                "po_number": po_number or "",
                "status": status_param or "",
            },
        },
    )

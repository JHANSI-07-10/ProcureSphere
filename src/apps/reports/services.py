import csv
import io

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.contracts.models import Contract
from apps.invoices.models import MatchException
from apps.orders.models import PurchaseOrder
from apps.receipts.models import ReceiptLine
from apps.requisitions.models import PurchaseRequisition
from apps.scorecards.models import VendorScorecard
from apps.sourcing.models import SourcingEvent

from .models import ExportJob


def get_pr_aging_report():
    """1. PR aging and approval-bottleneck report"""
    prs = PurchaseRequisition.objects.select_related("requester", "department", "cost_center").all()
    now = timezone.now()
    report_data = []
    for pr in prs:
        age_days = (now - pr.created_at).days
        report_data.append(
            {
                "pr_number": pr.pr_number,
                "title": pr.title,
                "department": pr.department.name if pr.department else "N/A",
                "cost_center": pr.cost_center.code if pr.cost_center else "N/A",
                "status": pr.status,
                "total_amount": float(pr.total_amount),
                "created_at": pr.created_at.strftime("%Y-%m-%d"),
                "age_days": age_days,
            }
        )
    return report_data


def get_spend_analytics_report():
    """2. Spend by vendor / category / department / cost center / period"""
    from apps.invoices.models import SupplierInvoice

    invoices = (
        SupplierInvoice.objects.select_related(
            "vendor", "vendor__category", "po", "po__cost_center", "po__requisition__department"
        )
        .exclude(status="REJECTED")
        .order_by("-invoice_date")
    )

    report_data = []
    for inv in invoices:
        po = inv.po
        dept_name = (
            po.requisition.department.name
            if po and po.requisition and po.requisition.department
            else "N/A"
        )
        cc_code = po.cost_center.code if po and po.cost_center else "N/A"
        cat_name = inv.vendor.category.name if inv.vendor.category else "Uncategorized"
        period_str = inv.invoice_date.strftime("%Y-%m")

        report_data.append(
            {
                "vendor_name": inv.vendor.legal_name,
                "vendor_category": cat_name,
                "department": dept_name,
                "cost_center": cc_code,
                "period": period_str,
                "invoice_number": inv.invoice_number,
                "po_number": po.po_number if po else "N/A",
                "status": inv.get_status_display(),
                "actual_spend": float(inv.total_amount),
            }
        )

    return report_data


def get_sourcing_cycle_time_report():
    """3. Sourcing cycle time and bid participation"""
    events = SourcingEvent.objects.prefetch_related("bids", "invitations").all()
    report_data = []
    for e in events:
        bid_count = e.bids.count()
        invite_count = e.invitations.count()
        start_dt = e.bid_start_date.strftime("%Y-%m-%d") if e.bid_start_date else "N/A"
        end_dt = e.bid_end_date.strftime("%Y-%m-%d") if e.bid_end_date else "N/A"
        report_data.append(
            {
                "event_number": e.event_number,
                "title": e.title,
                "event_type": e.event_type,
                "status": e.status,
                "invited_vendors": invite_count,
                "submitted_bids": bid_count,
                "start_date": start_dt,
                "end_date": end_dt,
            }
        )
    return report_data


def get_po_status_report():
    """4. PO open / partial / closed status"""
    pos = PurchaseOrder.objects.select_related("vendor", "cost_center").all()
    report_data = []
    for po in pos:
        report_data.append(
            {
                "po_number": po.po_number,
                "version": po.version,
                "vendor": po.vendor.legal_name,
                "cost_center": po.cost_center.code,
                "status": po.status,
                "total_amount": float(po.total_amount),
                "acknowledged_at": str(po.acknowledged_at) if po.acknowledged_at else None,
            }
        )
    return report_data


def get_receipt_rejection_report(
    start_date=None,
    end_date=None,
    vendor=None,
    po_number=None,
    inspection_status=None,
):
    """5. Receipt / rejection and delivery performance report"""
    from django.utils.dateparse import parse_date

    from apps.orders.models import DeliverySchedule

    receipt_lines = (
        ReceiptLine.objects.select_related(
            "receipt",
            "receipt__po",
            "receipt__po__vendor",
            "po_line",
            "receipt__received_by",
        )
        .prefetch_related("rejections")
        .all()
        .order_by("-receipt__received_date", "receipt__grn_number")
    )

    if start_date:
        parsed_start = parse_date(str(start_date)) if isinstance(start_date, str) else start_date
        if parsed_start:
            receipt_lines = receipt_lines.filter(receipt__received_date__date__gte=parsed_start)
    if end_date:
        parsed_end = parse_date(str(end_date)) if isinstance(end_date, str) else end_date
        if parsed_end:
            receipt_lines = receipt_lines.filter(receipt__received_date__date__lte=parsed_end)
    if vendor:
        receipt_lines = receipt_lines.filter(receipt__po__vendor__legal_name__icontains=vendor)
    if po_number:
        receipt_lines = receipt_lines.filter(receipt__po__po_number__icontains=po_number)

    report_data = []
    for rl in receipt_lines:
        line_inspection_status = rl.inspection_status
        if inspection_status and line_inspection_status != inspection_status:
            continue

        # Look up delivery schedule for delivery performance calculation
        schedule = None
        if rl.po_line:
            schedule = DeliverySchedule.objects.filter(po_line=rl.po_line).first()
        if not schedule and rl.receipt.po:
            schedule = DeliverySchedule.objects.filter(po=rl.receipt.po).first()

        received_dt = rl.receipt.received_date
        recv_date_only = received_dt.date() if hasattr(received_dt, "date") else received_dt
        expected_date = schedule.expected_delivery_date if schedule else None

        if expected_date:
            days_variance = (recv_date_only - expected_date).days
            delivery_performance = "ON_TIME" if days_variance <= 0 else "LATE"
            expected_date_str = expected_date.strftime("%Y-%m-%d")
        else:
            days_variance = 0
            delivery_performance = "ON_SCHEDULE"
            expected_date_str = "N/A"

        qty_received = float(rl.quantity_received)
        qty_accepted = float(rl.quantity_accepted)
        qty_rejected = float(rl.quantity_rejected)
        rejection_rate = (
            round((qty_rejected / qty_received * 100.0), 2) if qty_received > 0 else 0.0
        )

        # Rejection & Return evidence
        rej = rl.rejections.first()
        rejection_reason = rej.rejection_reason if rej else ""
        if rej and rej.returned_to_vendor:
            return_status = "RETURNED"
        elif rej and not rej.returned_to_vendor:
            return_status = "PENDING_RETURN"
        else:
            return_status = "N/A"

        report_data.append(
            {
                "grn_number": rl.receipt.grn_number,
                "po_number": rl.receipt.po.po_number,
                "vendor": rl.receipt.po.vendor.legal_name,
                "item_description": rl.po_line.item_description if rl.po_line else "N/A",
                "delivery_note_number": rl.receipt.delivery_note_number or "N/A",
                "received_date": received_dt.strftime("%Y-%m-%d"),
                "expected_delivery_date": expected_date_str,
                "delivery_performance": delivery_performance,
                "days_variance": days_variance,
                "qty_received": qty_received,
                "qty_accepted": qty_accepted,
                "qty_rejected": qty_rejected,
                "rejection_rate_percent": rejection_rate,
                "inspection_status": line_inspection_status,
                "rejection_reason": rejection_reason or "N/A",
                "return_status": return_status,
                "stock_handoff_status": rl.receipt.stock_handoff_status,
            }
        )
    return report_data


def get_receipt_delivery_performance_summary(
    start_date=None,
    end_date=None,
    vendor=None,
):
    """
    Computes aggregated summary KPIs for receipt, rejection, and delivery performance.
    """
    report = get_receipt_rejection_report(start_date=start_date, end_date=end_date, vendor=vendor)
    total_lines = len(report)
    total_grns = len({r["grn_number"] for r in report})
    total_qty_received = sum(r["qty_received"] for r in report)
    total_qty_accepted = sum(r["qty_accepted"] for r in report)
    total_qty_rejected = sum(r["qty_rejected"] for r in report)

    overall_rejection_rate = (
        round((total_qty_rejected / total_qty_received * 100.0), 2)
        if total_qty_received > 0
        else 0.0
    )

    scheduled_lines = [r for r in report if r["delivery_performance"] in ["ON_TIME", "LATE"]]
    on_time_lines = [r for r in scheduled_lines if r["delivery_performance"] == "ON_TIME"]
    on_time_delivery_rate = (
        round((len(on_time_lines) / len(scheduled_lines) * 100.0), 2) if scheduled_lines else 100.0
    )

    returns_dispatched = len([r for r in report if r["return_status"] == "RETURNED"])
    returns_pending = len([r for r in report if r["return_status"] == "PENDING_RETURN"])

    return {
        "total_grns": total_grns,
        "total_lines": total_lines,
        "total_qty_received": total_qty_received,
        "total_qty_accepted": total_qty_accepted,
        "total_qty_rejected": total_qty_rejected,
        "overall_rejection_rate_percent": overall_rejection_rate,
        "on_time_delivery_rate_percent": on_time_delivery_rate,
        "returns_dispatched": returns_dispatched,
        "returns_pending": returns_pending,
    }


def get_invoice_exception_aging_report():
    """6. Invoice match-exception aging"""
    exceptions = MatchException.objects.select_related("invoice__vendor", "resolved_by").all()
    now = timezone.now()
    report_data = []
    for exc in exceptions:
        age_days = (now - exc.created_at).days
        report_data.append(
            {
                "invoice_number": exc.invoice.invoice_number,
                "vendor": exc.invoice.vendor.legal_name,
                "exception_type": exc.exception_type,
                "status": exc.status,
                "variance_amount": float(exc.variance_amount),
                "age_days": age_days,
                "resolved_by": exc.resolved_by.email if exc.resolved_by else None,
            }
        )
    return report_data


def get_contract_expiry_report(status=None, days=None, start_date=None, end_date=None, user=None):
    """7. Contract expiry / renewal reporting"""
    from datetime import datetime

    from apps.contracts.selectors import get_contracts_qs

    contracts = get_contracts_qs(user=user)

    if status:
        contracts = contracts.filter(status=status)

    today = timezone.now().date()

    if days:
        try:
            days_int = int(days)
            future_date = today + timezone.timedelta(days=days_int)
            contracts = contracts.filter(end_date__lte=future_date)
        except (ValueError, TypeError):
            pass

    if start_date:
        if isinstance(start_date, str):
            try:
                start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                start_date = None
        if start_date:
            contracts = contracts.filter(end_date__gte=start_date)

    if end_date:
        if isinstance(end_date, str):
            try:
                end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                end_date = None
        if end_date:
            contracts = contracts.filter(end_date__lte=end_date)

    report_data = []
    for c in contracts:
        days_to_expiry = (c.end_date - today).days if c.end_date else 0
        notice_days = c.renewal_notice_days or 30
        notice_date = (c.end_date - timezone.timedelta(days=notice_days)) if c.end_date else None
        in_notice_period = (
            (today >= notice_date and today <= c.end_date) if notice_date and c.end_date else False
        )
        is_expired = (
            (today > c.end_date or c.status == Contract.STATUS_EXPIRED) if c.end_date else False
        )

        report_data.append(
            {
                "contract_number": c.contract_number,
                "title": c.title,
                "vendor": c.vendor.legal_name if c.vendor else "N/A",
                "status": c.status,
                "status_display": c.get_status_display(),
                "total_value": float(c.contract_value),
                "start_date": str(c.start_date) if c.start_date else None,
                "end_date": str(c.end_date) if c.end_date else None,
                "renewal_notice_days": notice_days,
                "notice_date": str(notice_date) if notice_date else None,
                "days_to_expiry": days_to_expiry,
                "in_notice_period": in_notice_period,
                "is_expired": is_expired,
            }
        )
    return report_data


def get_contract_obligation_report(
    due_status=None, responsible_party=None, start_date=None, end_date=None, user=None
):
    """Obligation reporting"""
    from datetime import datetime

    from apps.contracts.selectors import get_contract_obligations_qs

    if isinstance(start_date, str):
        try:
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            start_date = None

    if isinstance(end_date, str):
        try:
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            end_date = None

    obligations = get_contract_obligations_qs(
        user=user,
        due_status=due_status,
        responsible_party=responsible_party,
        start_date=start_date,
        end_date=end_date,
    )

    today = timezone.now().date()
    report_data = []
    for ob in obligations:
        if ob.is_fulfilled:
            calc_status = "FULFILLED"
            days_diff = (ob.fulfilled_at.date() - ob.due_date).days if ob.fulfilled_at else 0
        elif ob.due_date < today:
            calc_status = "OVERDUE"
            days_diff = (today - ob.due_date).days
        else:
            calc_status = "UPCOMING"
            days_diff = (ob.due_date - today).days

        report_data.append(
            {
                "contract_number": ob.contract.contract_number,
                "contract_title": ob.contract.title,
                "vendor": ob.contract.vendor.legal_name if ob.contract.vendor else "N/A",
                "obligation_title": ob.title,
                "responsible_party": ob.responsible_party,
                "due_date": str(ob.due_date),
                "is_fulfilled": ob.is_fulfilled,
                "fulfilled_at": (
                    ob.fulfilled_at.strftime("%Y-%m-%d %H:%M:%S") if ob.fulfilled_at else None
                ),
                "due_status": calc_status,
                "days_overdue_or_remaining": days_diff,
            }
        )
    return report_data


def _score(value):
    """Scorecard indicators are NULL when no transactional data exists."""
    return None if value is None else float(value)


def get_supplier_performance_report():
    """8. Supplier performance scorecard and trend"""
    scorecards = VendorScorecard.objects.select_related("vendor", "evaluated_by").all()
    report_data = []
    for sc in scorecards:
        report_data.append(
            {
                "vendor": sc.vendor.legal_name,
                "period": sc.evaluation_period,
                "delivery_score": _score(sc.delivery_score),
                "quality_score": _score(sc.quality_score),
                "price_score": _score(sc.price_score),
                "responsiveness_score": _score(sc.responsiveness_score),
                "compliance_score": _score(sc.compliance_score),
                "sla_score": _score(sc.sla_score),
                "composite_score": _score(sc.composite_score),
                "evaluated_by": sc.evaluated_by.email,
            }
        )
    return report_data


def get_audit_log_report():
    """9. User / approval audit export with filters and immutable references"""
    logs = AuditLog.objects.select_related("actor").all()[:200]
    report_data = []
    for log in logs:
        report_data.append(
            {
                "audit_id": str(log.id),
                "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "actor": log.actor.email if log.actor else "System",
                "action": log.action,
                "target_model": log.target_model,
                "target_object_id": log.target_object_id,
            }
        )
    return report_data


REPORT_DISPATCHER = {
    "pr_aging": get_pr_aging_report,
    "spend_analytics": get_spend_analytics_report,
    "sourcing_cycle": get_sourcing_cycle_time_report,
    "sourcing_cycle_time": get_sourcing_cycle_time_report,
    "po_status": get_po_status_report,
    "receipt_rejection": get_receipt_rejection_report,
    "invoice_exception_aging": get_invoice_exception_aging_report,
    "contract_expiry": get_contract_expiry_report,
    "contract_obligation": get_contract_obligation_report,
    "obligation": get_contract_obligation_report,
    "supplier_scorecard": get_supplier_performance_report,
    "supplier_performance": get_supplier_performance_report,
    "audit_log": get_audit_log_report,
}


def generate_export_job_service(export_job_id: int) -> ExportJob:
    """
    Processes an ExportJob, generates CSV, XLSX, or PDF file, attaches result file, and logs audit event.
    """
    job = ExportJob.objects.get(pk=export_job_id)
    job.status = ExportJob.STATUS_PROCESSING
    job.save(update_fields=["status", "updated_at"])

    try:
        report_fn = REPORT_DISPATCHER.get(job.report_type, get_audit_log_report)
        data = report_fn()

        if not data:
            # Fallback for empty data
            fieldnames = ["Message"]
            data = [{"Message": "No data available for export."}]
        else:
            fieldnames = list(data[0].keys())

        if job.export_format == "CSV":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow(row)
            filename = f"{job.report_type}_export_{job.id}.csv"
            job.result_file.save(
                filename, ContentFile(output.getvalue().encode("utf-8")), save=False
            )

        elif job.export_format == "XLSX":
            from io import BytesIO

            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Report Data"
            ws.append(fieldnames)
            for row in data:
                ws.append([str(row.get(f, "")) for f in fieldnames])

            output = BytesIO()
            wb.save(output)
            filename = f"{job.report_type}_export_{job.id}.xlsx"
            job.result_file.save(filename, ContentFile(output.getvalue()), save=False)

        elif job.export_format == "PDF":
            from io import BytesIO

            from reportlab.lib import colors
            from reportlab.lib.pagesizes import landscape, letter
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.platypus import (
                HRFlowable,
                Paragraph,
                SimpleDocTemplate,
                Table,
                TableStyle,
            )

            output = BytesIO()
            # Landscape letter: 792 pt width, 612 pt height.
            # Margin: 28pt -> Printable width: 736 pt
            doc = SimpleDocTemplate(
                output,
                pagesize=landscape(letter),
                leftMargin=28,
                rightMargin=28,
                topMargin=28,
                bottomMargin=28,
            )

            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                "PDFReportTitle",
                parent=styles["Heading1"],
                fontSize=16,
                leading=20,
                textColor=colors.HexColor("#002B49"),
                spaceAfter=3,
            )
            subtitle_style = ParagraphStyle(
                "PDFReportSubtitle",
                parent=styles["Normal"],
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor("#64748B"),
                spaceAfter=10,
            )
            header_cell_style = ParagraphStyle(
                "PDFHeaderCell",
                parent=styles["Normal"],
                fontSize=7.5,
                leading=9.5,
                fontName="Helvetica-Bold",
                textColor=colors.white,
                alignment=0,
            )
            body_cell_style = ParagraphStyle(
                "PDFBodyCell",
                parent=styles["Normal"],
                fontSize=7,
                leading=8.5,
                fontName="Helvetica",
                textColor=colors.HexColor("#1E293B"),
                alignment=0,
            )

            report_title_display = job.report_type.replace("_", " ").title()
            user_email = job.requested_by.email if job.requested_by else "Compliance Officer"
            now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            elements = []
            elements.append(
                Paragraph(f"<b>ProcureSphere 360</b> &mdash; {report_title_display}", title_style)
            )
            elements.append(
                Paragraph(
                    f"Generated: <b>{now_str}</b> | Requested by: <b>{user_email}</b> | Total Records: <b>{len(data)}</b> | Format: <b>Audited PDF</b>",
                    subtitle_style,
                )
            )
            elements.append(
                HRFlowable(
                    width="100%", thickness=1.5, color=colors.HexColor("#00B0B9"), spaceAfter=10
                )
            )

            num_cols = max(1, len(fieldnames))
            col_w = 736.0 / num_cols

            table_data = []
            hdr_row = [
                Paragraph(f.replace("_", " ").upper(), header_cell_style) for f in fieldnames
            ]
            table_data.append(hdr_row)

            for row in data:
                row_cells = []
                for f in fieldnames:
                    val = str(row.get(f, ""))
                    if val in ["None", "null", ""]:
                        val = "-"
                    row_cells.append(Paragraph(val, body_cell_style))
                table_data.append(row_cells)

            t = Table(table_data, colWidths=[col_w] * num_cols, repeatRows=1)
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002B49")),
                        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, 0), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F8FAFC")],
                        ),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 1), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
                    ]
                )
            )
            elements.append(t)
            doc.build(elements)

            filename = f"{job.report_type}_export_{job.id}.pdf"
            job.result_file.save(filename, ContentFile(output.getvalue()), save=False)

        job.status = ExportJob.STATUS_COMPLETED
        job.save(update_fields=["status", "result_file", "updated_at"])

        AuditLog.objects.create(
            actor=job.requested_by,
            action=AuditLog.ACTION_EXPORT,
            target_model="ExportJob",
            target_object_id=str(job.id),
            new_state={
                "report_type": job.report_type,
                "format": job.export_format,
                "record_count": len(data),
            },
        )
    except Exception as e:
        job.status = ExportJob.STATUS_FAILED
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message", "updated_at"])
        raise e

    return job

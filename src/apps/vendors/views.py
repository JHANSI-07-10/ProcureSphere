from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.scorecards.permissions import can_calculate_scorecards

from .filters import (
    APPROVAL_STAGE_STATUSES,
    PERFORMANCE_GOOD_THRESHOLD,
    PERFORMANCE_ISSUE_THRESHOLD,
    VendorFilter,
)
from .forms import GovernanceStatusForm, RiskAssessmentForm, VendorRegistrationForm
from .models import Vendor, VendorDocument, VendorRiskRecord
from .permissions import (
    can_operate_governance,
    can_view_governance,
    governance_capabilities,
)
from .selectors import (
    get_governance_metrics,
    get_governance_vendor_queryset,
    get_vendor_change_history,
    get_vendor_open_transactions,
    get_vendor_purchase_orders,
    get_vendor_sourcing_history,
)
from .services import (
    allowed_governance_transitions,
    approve_vendor_service,
    issue_vendor_credentials_service,
    record_vendor_risk_assessment_service,
    register_vendor_service,
    set_vendor_status_governance_service,
    start_kyc_review_service,
    verify_vendor_document_service,
)

PAGE_SIZE = 20

TRANSITION_LABELS = {
    Vendor.STATUS_ON_HOLD: ("Place on hold", "warning"),
    Vendor.STATUS_ACTIVE: ("Release / reinstate to Active", "success"),
    Vendor.STATUS_SUSPENDED: ("Suspend / blacklist", "danger"),
    Vendor.STATUS_REJECTED: ("Reject registration", "danger"),
    Vendor.STATUS_CHANGES_REQUIRED: ("Request corrections / changes", "warning"),
}


def _require_governance_view(user):
    if not can_view_governance(user):
        raise PermissionDenied("Vendor governance is restricted to procurement roles.")


def _require_governance_operate(user):
    if not can_operate_governance(user):
        raise PermissionDenied("Your role cannot perform vendor governance actions.")


def _performance_band(score):
    if score is None:
        return None
    if score >= PERFORMANCE_GOOD_THRESHOLD:
        return "good"
    if score >= PERFORMANCE_ISSUE_THRESHOLD:
        return "watch"
    return "issue"


def _vendor_register(request, *, mode):
    _require_governance_view(request.user)
    queryset = get_governance_vendor_queryset()
    if mode == "onboarding":
        queryset = queryset.filter(status__in=APPROVAL_STAGE_STATUSES)
    vendor_filter = VendorFilter(request.GET or None, queryset=queryset)
    for name, field in vendor_filter.form.fields.items():
        field.widget.attrs["class"] = (
            "form-control form-control-sm" if name == "q" else ("form-select form-select-sm")
        )
    filtered = vendor_filter.qs if vendor_filter.is_bound else queryset
    if vendor_filter.is_bound and not vendor_filter.is_valid():
        filtered = queryset.none()

    page = Paginator(filtered, PAGE_SIZE).get_page(request.GET.get("page"))
    for vendor in page.object_list:
        vendor.performance_band = _performance_band(vendor.latest_composite_score)

    query_params = request.GET.copy()
    query_params.pop("page", None)
    context = {
        "mode": mode,
        "page_obj": page,
        "vendors": page.object_list,
        "filter": vendor_filter,
        "querystring": query_params.urlencode(),
        "caps": governance_capabilities(request.user),
        "metrics": get_governance_metrics() if mode == "governance" else None,
    }
    return render(request, "pages/vendors/list.html", context)


@login_required(login_url="/login/")
def vendor_list_view(request):
    """Vendor Master & Risk register (all vendors)."""
    return _vendor_register(request, mode="master")


@login_required(login_url="/login/")
def vendor_governance_view(request):
    """Vendor Governance: KPI dashboard + governance register."""
    return _vendor_register(request, mode="governance")


@login_required(login_url="/login/")
def vendor_onboarding_view(request):
    """Vendor Onboarding & KYC queue (approval-stage vendors)."""
    return _vendor_register(request, mode="onboarding")


@login_required(login_url="/login/")
def vendor_create_view(request):
    _require_governance_operate(request.user)
    form = VendorRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            vendor = register_vendor_service(**form.cleaned_data, created_by_user=request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Vendor {vendor.vendor_number} registered as DRAFT.")
            return redirect("vendor_detail", vendor_id=vendor.id)
    return render(request, "pages/vendors/create.html", {"form": form})


@login_required(login_url="/login/")
def vendor_detail_view(request, vendor_id):
    """Governance detail: master data, status, risk, performance, documents, history."""
    _require_governance_view(request.user)
    vendor = get_object_or_404(Vendor.objects.select_related("category"), pk=vendor_id)

    risk_records = list(vendor.risk_records.select_related("assessed_by")[:20])
    scorecards = list(vendor.scorecards.select_related("evaluated_by").order_by("-created_at")[:12])
    exposure = get_vendor_open_transactions(vendor)
    transitions = [
        {"status": s, "label": TRANSITION_LABELS[s][0], "tone": TRANSITION_LABELS[s][1]}
        for s in allowed_governance_transitions(vendor, request.user)
    ]
    caps = governance_capabilities(request.user)
    context = {
        "vendor": vendor,
        "caps": caps,
        "can_calculate_scorecard": can_calculate_scorecards(request.user),
        "can_kyc_review": caps["can_operate"]
        and vendor.status in (Vendor.STATUS_SUBMITTED, Vendor.STATUS_KYC_REVIEW),
        "latest_risk": risk_records[0] if risk_records else None,
        "risk_records": risk_records,
        "latest_scorecard": scorecards[0] if scorecards else None,
        "scorecards": scorecards,
        "documents": vendor.documents.select_related("verified_by").order_by("-created_at"),
        "contacts": vendor.contacts.order_by("-is_primary", "last_name"),
        "history": get_vendor_change_history(vendor),
        "exposure": exposure,
        "sourcing_history": get_vendor_sourcing_history(vendor),
        "purchase_orders": get_vendor_purchase_orders(vendor),
        "transitions": transitions,
        "last_dispatch": vendor.credential_dispatches.first(),
        "risk_form": RiskAssessmentForm(),
        "risk_flag_choices": VendorRiskRecord.RISK_FLAG_CHOICES,
        "risk_level_choices": VendorRiskRecord.RISK_CHOICES,
    }
    return render(request, "pages/vendors/detail.html", context)


def _redirect_detail(vendor, anchor=""):
    response = redirect("vendor_detail", vendor_id=vendor.id)
    if anchor:
        response["Location"] += f"#{anchor}"
    return response


def _form_errors(form):
    return "; ".join(f"{field}: {', '.join(errs)}" for field, errs in form.errors.items())


@login_required(login_url="/login/")
@require_POST
def vendor_status_change_view(request, vendor_id):
    _require_governance_operate(request.user)
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    form = GovernanceStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Status change rejected — {_form_errors(form)}")
        return _redirect_detail(vendor, "governance")
    try:
        vendor = set_vendor_status_governance_service(
            vendor=vendor,
            actor=request.user,
            new_status=form.cleaned_data["status"],
            notes=form.cleaned_data["notes"],
        )
        messages.success(request, f"Vendor status is now {vendor.get_status_display()}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return _redirect_detail(vendor, "governance")


@login_required(login_url="/login/")
@require_POST
def vendor_risk_assess_view(request, vendor_id):
    _require_governance_operate(request.user)
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    form = RiskAssessmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Risk assessment rejected — {_form_errors(form)}")
        return _redirect_detail(vendor, "risk")
    try:
        record = record_vendor_risk_assessment_service(
            vendor=vendor,
            assessor=request.user,
            risk_level=form.cleaned_data["risk_level"],
            risk_flags=form.cleaned_data["risk_flags"],
            notes=form.cleaned_data["notes"],
        )
        messages.success(request, f"Risk assessment recorded ({record.get_risk_level_display()}).")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return _redirect_detail(vendor, "risk")


@login_required(login_url="/login/")
@require_POST
def vendor_kyc_action_view(request, vendor_id, kyc_action):
    """KYC review actions: start review, verify document, approve."""
    _require_governance_operate(request.user)
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    try:
        if kyc_action == "start-review":
            start_kyc_review_service(vendor=vendor, reviewer=request.user)
            messages.success(request, "KYC review started.")
        elif kyc_action == "verify-document":
            doc = get_object_or_404(
                VendorDocument, pk=request.POST.get("document_id"), vendor=vendor
            )
            verify_vendor_document_service(document=doc, verifier=request.user)
            messages.success(request, f"Document '{doc.title}' verified.")
        elif kyc_action == "approve":
            approve_vendor_service(
                vendor=vendor, manager=request.user, notes=request.POST.get("notes", "").strip()
            )
            messages.success(request, "Vendor approved and activated.")
        elif kyc_action == "request-changes":
            notes = request.POST.get("notes", "").strip()
            if not notes:
                raise ValidationError("Please provide instructions on what changes are required.")
            set_vendor_status_governance_service(
                vendor=vendor,
                actor=request.user,
                new_status=Vendor.STATUS_CHANGES_REQUIRED,
                notes=notes,
            )
            messages.warning(
                request, f"Corrections requested from {vendor.legal_name}. Vendor notified."
            )
        else:
            raise Http404("Unknown KYC action.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return _redirect_detail(vendor, "documents" if kyc_action == "verify-document" else "")


@login_required(login_url="/login/")
def vendor_document_download_view(request, vendor_id, doc_id):
    """Authorised internal download; files are never linked by their storage URL."""
    _require_governance_view(request.user)
    doc = get_object_or_404(VendorDocument, pk=doc_id, vendor_id=vendor_id)
    try:
        return FileResponse(doc.file.open("rb"), as_attachment=True)
    except (FileNotFoundError, ValueError):
        raise Http404("Document file is not available in storage.")


# ------------------------------------------------------------------------------------------
# Procurement Executive Dashboard — Pillar 1 HTMX fragments (qualification queue, KYC docs,
# risk assessment). All actions call the transactional services in services.py; RBAC is
# enforced on the backend via can_view_governance / can_operate_governance.
# ------------------------------------------------------------------------------------------


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


@login_required(login_url="/login/")
def vendor_qualification_tab_view(request):
    """HTMX tab: vendors in SUBMITTED or KYC_REVIEW status awaiting qualification."""
    _require_governance_view(request.user)
    queue = (
        get_governance_vendor_queryset()
        .filter(status__in=[Vendor.STATUS_SUBMITTED, Vendor.STATUS_KYC_REVIEW])
        .prefetch_related("documents", "risk_records")[:50]
    )
    rows = []
    for vendor in queue:
        docs = list(vendor.documents.all())
        latest_risk = vendor.risk_records.order_by("-created_at").first()
        rows.append(
            {
                "vendor": vendor,
                "doc_total": len(docs),
                "doc_verified": sum(1 for d in docs if d.is_verified),
                "latest_risk": latest_risk,
            }
        )
    return render(
        request,
        "vendors/partials/htmx_qualification_tab.html",
        {"rows": rows, "caps": governance_capabilities(request.user)},
    )


@login_required(login_url="/login/")
def vendor_documents_modal_view(request, vendor_id):
    """HTMX modal: review a vendor's VendorDocument (KYC) records + verify actions."""
    _require_governance_view(request.user)
    vendor = get_object_or_404(Vendor.objects.select_related("category"), pk=vendor_id)
    documents = vendor.documents.select_related("verified_by").order_by("-created_at")
    return render(
        request,
        "vendors/partials/htmx_documents_modal.html",
        {
            "vendor": vendor,
            "documents": documents,
            "caps": governance_capabilities(request.user),
        },
    )


@login_required(login_url="/login/")
def vendor_risk_modal_view(request, vendor_id):
    """HTMX modal: GET shows risk form; POST records via record_vendor_risk_assessment_service."""
    _require_governance_operate(request.user)
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    form = RiskAssessmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            record = record_vendor_risk_assessment_service(
                vendor=vendor,
                assessor=request.user,
                risk_level=form.cleaned_data["risk_level"],
                risk_flags=form.cleaned_data["risk_flags"],
                notes=form.cleaned_data["notes"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                "vendors/partials/htmx_risk_modal.html",
                {
                    "vendor": vendor,
                    "form": form,
                    "risk_flag_choices": VendorRiskRecord.RISK_FLAG_CHOICES,
                },
                status=400,
            )
        messages.success(request, f"Risk assessment recorded ({record.get_risk_level_display()}).")
        if _is_htmx(request):
            response = render(
                request,
                "vendors/partials/htmx_action_result.html",
                {"success": True, "vendor": vendor, "record": record},
            )
            response["HX-Trigger"] = "vendor-updated"
            return response
        return redirect("vendor_detail", vendor_id=vendor.id)
    if request.method == "POST":
        return render(
            request,
            "vendors/partials/htmx_risk_modal.html",
            {
                "vendor": vendor,
                "form": form,
                "risk_flag_choices": VendorRiskRecord.RISK_FLAG_CHOICES,
            },
            status=400,
        )
    return render(
        request,
        "vendors/partials/htmx_risk_modal.html",
        {
            "vendor": vendor,
            "form": RiskAssessmentForm(),
            "risk_flag_choices": VendorRiskRecord.RISK_FLAG_CHOICES,
        },
    )


@login_required(login_url="/login/")
@require_POST
def vendor_kyc_htmx_view(request, vendor_id, kyc_action):
    """HTMX KYC transitions: start-review / verify-document / approve (APPROVED via service)."""
    _require_governance_operate(request.user)
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    try:
        if kyc_action == "start-review":
            start_kyc_review_service(vendor=vendor, reviewer=request.user)
            detail = "KYC review started."
        elif kyc_action == "verify-document":
            doc = get_object_or_404(
                VendorDocument, pk=request.POST.get("document_id"), vendor=vendor
            )
            verify_vendor_document_service(document=doc, verifier=request.user)
            detail = f"Document '{doc.title}' verified."
        elif kyc_action == "approve":
            approve_vendor_service(
                vendor=vendor, manager=request.user, notes=request.POST.get("notes", "").strip()
            )
            detail = "Vendor approved and activated."
        elif kyc_action == "request-changes":
            notes = request.POST.get("notes", "").strip()
            if not notes:
                raise ValidationError("Please provide instructions on what changes are required.")
            set_vendor_status_governance_service(
                vendor=vendor,
                actor=request.user,
                new_status=Vendor.STATUS_CHANGES_REQUIRED,
                notes=notes,
            )
            detail = f"Corrections requested from {vendor.legal_name}. Vendor notified."
        else:
            raise Http404("Unknown KYC action.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        if _is_htmx(request):
            return render(
                request,
                "vendors/partials/htmx_action_result.html",
                {"success": False, "errors": exc.messages},
                status=400,
            )
        return redirect("vendor_detail", vendor_id=vendor.id)
    messages.success(request, detail)
    if _is_htmx(request):
        vendor.refresh_from_db()
        response = render(
            request,
            "vendors/partials/htmx_action_result.html",
            {"success": True, "vendor": vendor, "detail": detail},
        )
        response["HX-Trigger"] = "vendor-updated"
        return response
    return redirect("vendor_detail", vendor_id=vendor.id)


# ------------------------------------------------------------------------------------------
# Procurement Executive Dashboard - register a vendor and email portal credentials
# ------------------------------------------------------------------------------------------


@login_required(login_url="/login/")
def vendor_register_modal_view(request):
    """HTMX modal: register a vendor (DRAFT) with basic details, then offer credential issue."""
    _require_governance_operate(request.user)
    form = VendorRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            vendor = register_vendor_service(**form.cleaned_data, created_by_user=request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            response = render(
                request, "vendors/partials/htmx_register_done.html", {"vendor": vendor}
            )
            response["HX-Trigger"] = "vendor-updated"
            return response
    return render(
        request,
        "vendors/partials/htmx_register_modal.html",
        {"form": form},
        status=400 if request.method == "POST" else 200,
    )


@login_required(login_url="/login/")
@require_POST
def vendor_issue_credentials_view(request, vendor_id):
    """Creates/resets the vendor login and emails credentials + portal link(s)."""
    _require_governance_operate(request.user)
    vendor = get_object_or_404(Vendor, pk=vendor_id)
    error = None
    dispatch = None
    try:
        dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=request.user)
    except ValidationError as exc:
        error = " ".join(exc.messages)
    if _is_htmx(request):
        return render(
            request,
            "vendors/partials/htmx_register_done.html",
            {"vendor": vendor, "dispatch": dispatch, "error": error},
            status=400 if error else 200,
        )
    if error:
        messages.error(request, error)
    else:
        messages.success(request, f"Credentials emailed to {dispatch.sent_to}.")
    return redirect("vendor_detail", vendor_id=vendor.id)

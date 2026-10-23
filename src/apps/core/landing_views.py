"""
Public-facing landing (marketing) website views for ProcureSphere 360.

Integration notes
-----------------
* These views live inside apps.core so they use the existing Django project
  configuration, authentication system, and URL routing — no second Django app.
* landing_home: anonymous visitors see the public website; authenticated users
  are forwarded to the existing home_view (role-based dashboard dispatcher) via
  a direct Python call.  The call is safe because:
    - home_view is already decorated with @login_required; since we only call it
      when request.user.is_authenticated, the decorator passes through cleanly.
    - request.path remains '/' so existing sidebar active-state checks (e.g.
      {% if request.path == '/' %}active{% endif %}) continue to work correctly.
    - No HTTP redirect is issued, so all existing hardcoded redirect("/")
      targets in accounts/views.py remain untouched.
* contact_submit uses Django's standard CSRF protection (no csrf_exempt).
"""

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST


# ---------------------------------------------------------------------------
# Home / landing page
# ---------------------------------------------------------------------------

def landing_home(request):
    """
    Root URL handler.

    Anonymous → renders the public marketing home page.
    Authenticated → delegates to the existing role-based home_view dispatcher
                   (imported lazily to avoid circular-import risk at module load).
    """
    if request.user.is_authenticated:
        # Import here to avoid any circular import between core modules.
        from apps.core.views import home_view  # noqa: PLC0415
        return home_view(request)

    context = {
        'page_title': 'ProcureSphere 360 — Intelligent Enterprise Procurement Platform',
        'page_description': (
            'Connect requests, approvals, suppliers, sourcing, purchase orders, '
            'invoices, contracts and financial control in one intelligent procurement platform.'
        ),
        'active_nav': 'home',
        'modules': [
            {'name': 'Vendor Onboarding & KYC', 'detail': 'Turn supplier intake into a clear, reviewable path.', 'state': 'KYC REVIEW', 'icon': 'users', 'category': 'SOURCING'},
            {'name': 'Vendor Master & Risk', 'detail': 'Keep supplier identity, risk and ownership in view.', 'state': 'ACTIVE', 'icon': 'shield', 'category': 'GOVERNANCE'},
            {'name': 'Purchase Requisition', 'detail': 'Capture intent with policy and budget context.', 'state': 'MANAGER REVIEW', 'icon': 'file', 'category': 'PURCHASING'},
            {'name': 'Approval Policy Engine', 'detail': 'Route decisions to the right owner at the right time.', 'state': 'BUDGET REVIEW', 'icon': 'check', 'category': 'GOVERNANCE'},
            {'name': 'RFQ / RFP Sourcing', 'detail': 'Create a transparent path from need to market.', 'state': 'BID WINDOW', 'icon': 'search', 'category': 'SOURCING'},
            {'name': 'Vendor Bid Portal (sealed bids)', 'detail': 'Keep submissions structured and comparable.', 'state': 'PUBLISHED', 'icon': 'layers', 'category': 'SOURCING'},
            {'name': 'Evaluation & Award', 'detail': 'Turn technical and commercial review into a decision.', 'state': 'AWARD APPROVAL', 'icon': 'activity', 'category': 'SOURCING'},
            {'name': 'Purchase Orders & Amendments', 'detail': 'Make committed intent visible to every team.', 'state': 'ACKNOWLEDGED', 'icon': 'file', 'category': 'PURCHASING'},
            {'name': 'Receipt & Inspection', 'detail': 'Connect arrival, inspection and acceptance.', 'state': 'PARTIAL RECEIPT', 'icon': 'check', 'category': 'PURCHASING'},
            {'name': 'Invoice & 3-Way Match', 'detail': 'Resolve exceptions before they become delays.', 'state': '3-WAY MATCH', 'icon': 'finance', 'category': 'FINANCE'},
            {'name': 'Contract Lifecycle', 'detail': 'Keep obligations and renewal decisions visible.', 'state': 'RENEWAL DUE', 'icon': 'file', 'category': 'GOVERNANCE'},
            {'name': 'Budget & Spend Control', 'detail': 'Show how a decision changes the financial picture.', 'state': 'RESERVED', 'icon': 'finance', 'category': 'FINANCE'},
            {'name': 'Supplier Performance', 'detail': 'Make health and collaboration measurable.', 'state': 'ACTIVE', 'icon': 'activity', 'category': 'SOURCING'},
            {'name': 'Reports & Audit Trail', 'detail': 'Give every important action a structured place.', 'state': 'ACTIVE', 'icon': 'shield', 'category': 'GOVERNANCE'},
        ],
        'testimonials': [
            {'quote': 'A connected procurement experience should make the next decision obvious, not add another system to navigate.', 'role': 'Procurement leader', 'note': 'Illustrative perspective'},
            {'quote': 'The value is seeing request, budget, supplier and invoice context in the same operating picture.', 'role': 'Finance stakeholder', 'note': 'Illustrative perspective'},
            {'quote': 'For vendors, a clear onboarding and bid path creates a better experience for everyone involved.', 'role': 'Supplier operations stakeholder', 'note': 'Illustrative perspective'},
        ],
        'platform_modules': [
            {
                'id': 1,
                'name': 'Purchase & Approvals',
                'theme_color': '#0052cc',
                'accent_light': '#00d2ff',
                'icon': 'file',
                'summary': 'Streamline purchase requests with automated approval hierarchies and live budget verification.',
                'short_desc': 'Automated requisition intake and multi-tier approval routing.',
                'points': [
                    {'title': 'Smart Purchase Requisitions', 'desc': 'Dynamic forms automatically validate cost centers, project codes, and line-item requirements.', 'icon': 'file'},
                    {'title': 'Automated Approval Matrix', 'desc': 'Intelligently routes requests to department heads and finance managers based on spend thresholds.', 'icon': 'activity'},
                    {'title': 'Real-Time Budget Validation', 'desc': 'Verifies fund availability instantly before purchase commitments are locked, preventing overspend.', 'icon': 'check'},
                ],
            },
            {
                'id': 2,
                'name': 'Sourcing & Vendors',
                'theme_color': '#6554c0',
                'accent_light': '#907cfc',
                'icon': 'users',
                'summary': 'Centralize supplier management, request for quotations (RFQs), and competitive bid evaluation.',
                'short_desc': 'Supplier onboarding, sealed RFQ bidding, and proposal scoring.',
                'points': [
                    {'title': 'Supplier Self-Service Portal', 'desc': 'Vendors easily register, maintain profiles, upload documentation, and submit quote proposals.', 'icon': 'users'},
                    {'title': 'Structured RFQ & Sourcing Events', 'desc': 'Publish requirement sheets to invited suppliers and collect comparable quotes within set deadlines.', 'icon': 'search'},
                    {'title': 'Comparative Proposal Evaluation', 'desc': 'Evaluate commercial terms, technical ratings, and vendor delivery track records side-by-side.', 'icon': 'check'},
                ],
            },
            {
                'id': 3,
                'name': 'Orders & Invoicing',
                'theme_color': '#172b4d',
                'accent_light': '#60a5fa',
                'icon': 'finance',
                'summary': 'Dispatch official purchase orders and reconcile warehouse receiving notes against supplier invoices.',
                'short_desc': 'PO dispatch, warehouse receiving, and touchless invoice matching.',
                'points': [
                    {'title': 'Purchase Order Execution', 'desc': 'Converts approved requisitions into official POs dispatched directly to suppliers with version tracking.', 'icon': 'finance'},
                    {'title': 'Warehouse Goods Receipt (GRN)', 'desc': 'Receiving docks record delivered items, inspect quality, and flag partial shipments accurately.', 'icon': 'layers'},
                    {'title': 'Automated 3-Way Match', 'desc': 'Cross-verifies PO line items, receiving notes, and vendor invoices to prevent duplicate payments.', 'icon': 'check'},
                ],
            },
            {
                'id': 4,
                'name': 'Contracts & Compliance',
                'theme_color': '#ff5630',
                'accent_light': '#ff8f73',
                'icon': 'shield',
                'summary': 'Maintain a centralized agreement repository with milestone deliverables and proactive renewal alerts.',
                'short_desc': 'Centralized contract vault, SLA milestones, and renewal alerts.',
                'points': [
                    {'title': 'Centralized Contract Vault', 'desc': 'Single secure repository for master service agreements, vendor rate sheets, and contractual terms.', 'icon': 'shield'},
                    {'title': 'Milestone & Obligation Tracking', 'desc': 'Tracks key vendor deliverables against contract milestones and release schedules.', 'icon': 'activity'},
                    {'title': 'Proactive Renewal Notifications', 'desc': 'Automated advance reminders prevent unexpected contract expirations and facilitate timely renegotiation.', 'icon': 'check'},
                ],
            },
        ],
        'cap_modules': [
            {'id': 'vendors', 'step_id': 'vendors', 'title': 'Vendor Management', 'desc': 'Automated KYC and tracking.', 'long_desc': 'Procuresphere automates vendor onboarding, KYC compliance, and performance tracking, ensuring you only work with verified and high-quality suppliers.', 'color': '#3b82f6', 'icon': 'M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z'},
            {'id': 'intake', 'step_id': 'pr', 'title': 'Intake & Approvals', 'desc': 'Dynamic forms & routing.', 'long_desc': 'Empower your teams with dynamic intake forms and intelligent approval routing, ensuring every request is properly categorized and authorized without bottlenecks.', 'color': '#8b5cf6', 'icon': 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z'},
            {'id': 'sourcing', 'step_id': 'sourcing', 'title': 'Strategic Sourcing', 'desc': 'RFx events and bid analysis.', 'long_desc': 'Run competitive RFx events with ease. Our strategic sourcing module provides deep analytics and bid comparison tools to help you secure the best value.', 'color': '#ec4899', 'icon': 'M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z'},
            {'id': 'orders', 'step_id': 'po', 'title': 'Purchase Orders', 'desc': 'Generate and track POs.', 'long_desc': 'Generate, track, and manage Purchase Orders automatically. Keep your inventory and budget in check with real-time visibility into order fulfillment.', 'color': '#f59e0b', 'icon': 'M21 16.5c0 .38-.21.71-.53.88l-7.9 4.44c-.16.12-.36.18-.57.18-.21 0-.41-.06-.57-.18l-7.9-4.44A.991.991 0 013 16.5v-9c0-.38.21-.71.53-.88l7.9-4.44c.16-.12.36-.18.57-.18.21 0 .41.06.57.18l7.9 4.44c.32.17.53.5.53.88v9zM12 4.15L6.04 7.5 12 10.85l5.96-3.35L12 4.15z'},
            {'id': 'invoices', 'step_id': 'match', 'title': 'Invoice Automation', 'desc': 'AI-powered OCR matching.', 'long_desc': 'Eliminate manual data entry with our AI-powered OCR matching. Invoices are automatically verified against POs and receipts for touchless processing.', 'color': '#10b981', 'icon': 'M18 17H6v-2h12v2zm0-4H6v-2h12v2zm0-4H6V7h12v2zM3 22l1.5-1.5L6 22l1.5-1.5L9 22l1.5-1.5L12 22l1.5-1.5L15 22l1.5-1.5L18 22l1.5-1.5L21 22V2l-1.5 1.5L18 2l-1.5 1.5L15 2l-1.5 1.5L12 2l-1.5 1.5L9 2 7.5 3.5 6 2 4.5 3.5 3 2v20z'},
            {'id': 'contracts', 'step_id': 'clm', 'title': 'Contract Management', 'desc': 'Centralized repository alerts.', 'long_desc': 'Maintain a centralized repository of all your supplier agreements. Stay ahead of expirations and renewals with automated alerts and lifecycle management.', 'color': '#6366f1', 'icon': 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z'},
        ],
        'comparison_rows': [
            {'dimension': 'Spend Authorization', 'legacy': 'Manual spreadsheet tracking; overspend discovered weeks after commitment', 'procuresphere': 'Pre-approval budget reservation distinguishing available, reserved, and actual spend to prevent silent overspend', 'advantage': 'Zero Silent Overspend'},
            {'dimension': 'Supplier Sourcing', 'legacy': 'Uncontrolled email quote exchanges with fragmented pricing records', 'procuresphere': 'Restricted vendor portal with sealed bid submissions and weighted technical and commercial scoring', 'advantage': 'Sealed Bidding & Objective Scoring'},
            {'dimension': 'Invoice Processing', 'legacy': 'Manual 3-way matching on paper, prone to quantity and pricing discrepancies', 'procuresphere': 'Automated 3-way matching comparing PO, receipt, and invoice with configurable tolerance rules', 'advantage': 'Automated Tolerance Matching'},
            {'dimension': 'Audit & Compliance', 'legacy': 'Painful retrospective audit sampling taking days of document gathering', 'procuresphere': 'Immutable audit logging capturing actor, state changes, and timestamps with instant audit export', 'advantage': 'Comprehensive Audit Readiness'},
            {'dimension': 'Contract Expirations', 'legacy': 'Forgotten contracts autorenewing at unfavorable rates without review', 'procuresphere': 'Proactive contract renewal alerts, milestone tracking, and vendor obligation governance', 'advantage': 'Proactive Renewal Governance'},
        ],
        'roles': [
            {'key': 'REQUESTER', 'title': 'My Requests', 'items': ['My Requests', 'Policy guidance', 'Budget visibility'], 'open_items': '24', 'table': [{'item': 'Operations services', 'owner': 'Procurement', 'status': 'In review', 'status_class': '', 'value': '$28,400'}, {'item': 'Marketing production', 'owner': 'Finance', 'status': 'Approved', 'status_class': '', 'value': '$8,240'}, {'item': 'Cloud infrastructure', 'owner': 'IT Ops', 'status': 'Attention', 'status_class': 'amber', 'value': '$64,100'}, {'item': 'Lab equipment', 'owner': 'Stores', 'status': 'Received', 'status_class': 'green', 'value': '$12,890'}]},
            {'key': 'PROCUREMENT', 'title': 'Procurement overview', 'items': ['Sourcing', 'Suppliers', 'Purchase Orders'], 'open_items': '24', 'table': [{'item': 'Office supplies RFQ', 'owner': 'Requester', 'status': 'Bid open', 'status_class': '', 'value': '$14,200'}, {'item': 'IT hardware sourcing', 'owner': 'IT Ops', 'status': 'Awarded', 'status_class': 'green', 'value': '$92,500'}, {'item': 'Facility maintenance', 'owner': 'Stores', 'status': 'Attention', 'status_class': 'amber', 'value': '$31,000'}, {'item': 'Stationery renewal PO', 'owner': 'Finance', 'status': 'In review', 'status_class': '', 'value': '$5,600'}]},
            {'key': 'FINANCE', 'title': 'Financial control', 'items': ['Invoices', 'Budget', 'Payments'], 'open_items': '18', 'table': [{'item': 'INV-2024-0481', 'owner': 'Procurement', 'status': '3-way match', 'status_class': 'green', 'value': '$28,400'}, {'item': 'INV-2024-0479', 'owner': 'IT Ops', 'status': 'Exception', 'status_class': 'amber', 'value': '$92,500'}, {'item': 'INV-2024-0476', 'owner': 'Stores', 'status': 'Approved', 'status_class': '', 'value': '$12,890'}, {'item': 'INV-2024-0470', 'owner': 'Finance', 'status': 'In review', 'status_class': '', 'value': '$5,600'}]},
            {'key': 'LEGAL', 'title': 'Contract obligations', 'items': ['Contracts', 'Renewals', 'Obligations'], 'open_items': '07', 'table': [{'item': 'Apex Global SLA', 'owner': 'Procurement', 'status': 'Renewal due', 'status_class': 'amber', 'value': '$120,000'}, {'item': 'Nova Systems MSA', 'owner': 'IT Ops', 'status': 'Active', 'status_class': 'green', 'value': '$44,000'}, {'item': 'Meridian NDA', 'owner': 'Legal', 'status': 'In review', 'status_class': '', 'value': '—'}, {'item': 'Horizon Health MoU', 'owner': 'Compliance', 'status': 'Active', 'status_class': 'green', 'value': '$18,750'}]},
            {'key': 'STORES', 'title': 'Receiving workspace', 'items': ['Receipts', 'Inspection', 'Inventory context'], 'open_items': '24', 'table': [{'item': 'Lab equipment delivery', 'owner': 'Stores', 'status': 'Received', 'status_class': 'green', 'value': '$12,890'}, {'item': 'Office chairs batch', 'owner': 'Procurement', 'status': 'Partial receipt', 'status_class': 'amber', 'value': '$9,400'}, {'item': 'IT server rack', 'owner': 'IT Ops', 'status': 'Inspection', 'status_class': '', 'value': '$64,100'}, {'item': 'Cleaning consumables', 'owner': 'Stores', 'status': 'Received', 'status_class': 'green', 'value': '$2,300'}]},
            {'key': 'COMPLIANCE', 'title': 'Compliance overview', 'items': ['Audit', 'Risk', 'Controls'], 'open_items': '24', 'table': [{'item': 'Q3 Procurement audit', 'owner': 'Compliance', 'status': 'In review', 'status_class': '', 'value': '—'}, {'item': 'Supplier KYC review', 'owner': 'Legal', 'status': 'Attention', 'status_class': 'amber', 'value': '—'}, {'item': 'Budget control check', 'owner': 'Finance', 'status': 'Approved', 'status_class': 'green', 'value': '—'}, {'item': 'Audit trail export', 'owner': 'Compliance', 'status': 'Completed', 'status_class': 'green', 'value': '—'}]},
            {'key': 'VENDOR', 'title': 'Vendor workspace', 'items': ['Onboarding', 'Bids', 'PO acknowledgement'], 'open_items': '24', 'table': [{'item': 'Office supplies RFQ', 'owner': 'Procurement', 'status': 'Bid open', 'status_class': '', 'value': '$14,200'}, {'item': 'IT hardware sourcing', 'owner': 'IT Ops', 'status': 'Submitted', 'status_class': 'green', 'value': '$92,500'}, {'item': 'PO-2024-0318 ack', 'owner': 'Vendor', 'status': 'Acknowledged', 'status_class': 'green', 'value': '$28,400'}, {'item': 'KYC document upload', 'owner': 'Compliance', 'status': 'In review', 'status_class': '', 'value': '—'}]},
        ],
        'faqs': [
            {'question': 'What does ProcureSphere 360 connect?', 'answer': 'Requests, approvals, suppliers, sourcing, purchase orders, receipts, invoices, contracts, finance and governance in one connected workflow.'},
            {'question': 'Is the interface shown live company data?', 'answer': 'No. The interface is illustrative demo content designed to show how the operating picture could work without revealing sensitive information.'},
            {'question': 'Can vendors participate in the workflow?', 'answer': 'Yes. The Vendor workspace brings onboarding, bids, and purchase-order acknowledgement into the same connected experience.'},
            {'question': 'How does the approval policy engine work?', 'answer': 'It routes decisions automatically based on custom rules like department, budget thresholds, or risk categories.'},
        ],
        'trust_cards': [
            {'icon': 'badge', 'stat': '500+', 'label': 'Verified Enterprises', 'desc': 'Across global supply chains', 'color': '#3b82f6'},
            {'icon': 'chart', 'stat': '$2.5B+', 'label': 'Spend Managed', 'desc': 'Annually across all modules', 'color': '#10b981'},
            {'icon': 'receipt', 'stat': '10M+', 'label': 'Monthly Transactions', 'desc': 'Instant POs & invoices', 'color': '#f59e0b'},
            {'icon': 'savings', 'stat': '₹120Cr+', 'label': 'Savings Generated', 'desc': 'Through automated negotiations', 'color': '#8b5cf6'},
            {'icon': 'users', 'stat': '800+', 'label': 'Procurement Leaders', 'desc': 'Active daily users', 'color': '#ec4899'},
        ],
        'benefits': [
            {'title': 'Traceable Workflows', 'description': 'No more approvals lost in email. Every requisition, bid, purchase order, and invoice is strictly traceable to its state and timestamp.', 'color': '#3b82f6', 'icon': 'shield'},
            {'title': 'Zero Silent Overspend', 'description': 'Real-time budget validation distinguishes available, reserved, and actual spend. Exceptions require an explicit, documented route.', 'color': '#10b981', 'icon': 'dollar'},
            {'title': 'Supplier Governance', 'description': 'Vendor onboarding, KYC, and risk management with built-in due-diligence. Keep supplier identity and performance in constant view.', 'color': '#f59e0b', 'icon': 'check-circle'},
            {'title': 'Three-Way Match Precision', 'description': 'Automated validation comparing PO, receipt, and invoice. Configurable tolerances ensure accurate payment and clear exception workflows.', 'color': '#8b5cf6', 'icon': 'file'},
            {'title': 'Contract Lifecycle Control', 'description': 'Never miss a renewal deadline. Keep obligations, SLAs, and version histories organized with automated expiry alerts.', 'color': '#ec4899', 'icon': 'calendar'},
            {'title': 'Traceable Audit Trails', 'description': 'Every critical action captures the actor, state change, and timestamp, providing structured event reporting for audit readiness.', 'color': '#06b6d4', 'icon': 'shield-check'},
        ],
        'debug': settings.DEBUG,
    }
    return render(request, 'landing/home.html', context)



# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------

def about(request):
    """About page."""
    context = {
        'page_title': 'About ProcureSphere 360 — Enterprise Procurement Platform',
        'page_description': "Learn about ProcureSphere 360's mission to transform enterprise procurement from a tactical back-office headache to a strategic, transparent advantage.",
        'active_nav': 'about',
        'badge': 'ABOUT PLATFORM',
        'title': 'A New Era of Enterprise Procurement',
        'subtitle': 'Elevating enterprise procurement from a tactical back-office headache to a strategic, transparent advantage.',
        'pillars': [
            {'tag': 'THE MISSION', 'title': 'Transforming Disconnected Procurement', 'desc': 'ProcureSphere 360 was built to eliminate disconnected emails, uncontrolled spreadsheet trackers, and siloed approvals. We provide enterprises with a single, verifiable operating system that connects intent with execution.', 'icon': '🏢'},
            {'tag': 'FINANCIAL ASSURANCE', 'title': 'Pre-Approval Spend Control', 'desc': 'Traditional ERP systems record spend only after money has already been committed. ProcureSphere 360 validates budget ceilings at the requisition stage, ensuring zero silent overspend.', 'icon': '🛡️'},
            {'tag': 'END-TO-END TRACEABILITY', 'title': 'From Requisition to 3-Way Match', 'desc': 'Every requisition, supplier bid, purchase order, inspection acceptance, and invoice is strictly tracked with timestamped logs, states, and responsible approvers.', 'icon': '⚡'},
        ],
        'capabilities': [
            {'title': 'Requisitions & Approvals', 'desc': 'Pre-budget validation, catalog punchouts, and dynamic multi-tier approval chains that prevent rogue departmental spending.', 'tag': 'INTAKE & POLICY', 'color': '#2456D6'},
            {'title': 'Sourcing & Bids', 'desc': 'Competitive RFQ/RFP events, sealed bid vaults, and commercial normalization that ensure fair supplier competition.', 'tag': 'SOURCING ENGINE', 'color': '#10b981'},
            {'title': 'Orders & Invoicing', 'desc': 'Automated PO generation, physical receipt inspection, and 3-way matching across PO, receipt, and invoice lines.', 'tag': 'AP EXECUTION', 'color': '#f59e0b'},
            {'title': 'Contracts & Compliance', 'desc': 'Centralized agreements, SLA obligations, version-controlled change histories, and automated renewal deadline alerts.', 'tag': 'LEGAL & CLM', 'color': '#8b5cf6'},
        ],
    }
    return render(request, 'landing/about.html', context)


# ---------------------------------------------------------------------------
# Solutions
# ---------------------------------------------------------------------------

def solutions(request):
    """Solutions page."""
    s2p_steps = [
        {'id': 'vendors', 'number': '01', 'title': 'Vendor Management & KYC Onboarding', 'category': 'SUPPLIER GOVERNANCE', 'summary': 'Self-service vendor portal with automated tax, banking, and KYC verification. Establishes verified vendor profiles before any purchasing activity.', 'features': ['Self-service digital vendor onboarding portal', 'Automated KYC, tax identification & banking verification', 'Continuous compliance monitoring & expiry alerts', 'Vendor performance ratings & delivery scorecards'], 'metric_label': 'Onboarding Speed', 'metric_value': '3x Faster', 'status_tag': 'VERIFIED & ACTIVE'},
        {'id': 'pr', 'number': '02', 'title': 'Purchase Requisition & Budget Reserve', 'category': 'PLANNING & REQUISITION', 'summary': 'Line items and services captured with instant department budget validation. Distinguishes available vs reserved spend prior to approvals.', 'features': ['Multi-line requisition with catalog & custom items', 'Instant departmental budget check & lock', 'Dynamic threshold routing (Manager → Finance review)', 'Timestamped submission logging & attachment auditing'], 'metric_label': 'Approval Speed', 'metric_value': '4.2x Faster', 'status_tag': 'BUDGET RESERVED'},
        {'id': 'sourcing', 'number': '03', 'title': 'RFQ / RFP Sourcing & Sealed Bids', 'category': 'SOURCING & EVALUATION', 'summary': 'Direct conversion from approved PR into competitive market events. Vendors participate via a restricted, blind portal until event close.', 'features': ['Invited & public bidding tracks with strict deadlines', 'Sealed bid vaults - no premature pricing disclosures', 'Weighted technical scoring & commercial normalization', 'Automated award memo & audit-ready decision trails'], 'metric_label': 'Spend Savings', 'metric_value': '18.4% Average', 'status_tag': 'SEALED & VERIFIED'},
        {'id': 'po', 'number': '04', 'title': 'Purchase Order Generation & Versioning', 'category': 'ORDER MANAGEMENT', 'summary': 'Formal PO creation with tax schedules, terms, delivery milestones, and automated change-order version tracking.', 'features': ['Direct PO release upon sourcing approval', 'Vendor digital acknowledgement tracking', 'Amendment control with prior-version preservation', 'Split deliveries & multi-location release schedules'], 'metric_label': 'Fulfillment Accuracy', 'metric_value': '99.8%', 'status_tag': 'ACKNOWLEDGED'},
        {'id': 'grn', 'number': '05', 'title': 'Physical Goods Receipt & Quality Inspection', 'category': 'INVENTORY & STORES', 'summary': 'Warehouse goods inspection capturing accepted quantity, rejected counts, inspection notes, and discrepancy flags.', 'features': ['Stores receipt against line items with barcode scanning', 'Partial receipt tracking with balance updates', 'Return-to-vendor (RTV) automated documentation', 'Inspection certificates linked directly to PO'], 'metric_label': 'Receipt Verification', 'metric_value': 'Full-Scope Audited', 'status_tag': 'INSPECTED & ACCEPTED'},
        {'id': 'match', 'number': '06', 'title': 'Automated 3-Way Invoice Matching', 'category': 'ACCOUNTS PAYABLE', 'summary': 'Exact cross-check comparing Purchase Order lines, Physical Receipt logs, and Vendor Invoice charges within tolerance boundaries.', 'features': ['Item-level price & quantity automated reconciliation', 'Configurable tolerance limits for tax & freight variance', 'Exception queues for unmatched invoices with buyer routing', 'Payment scheduling only after three-way lock verification'], 'metric_label': 'Touchless Match', 'metric_value': '92% Automated', 'status_tag': 'CLEARED FOR PAYMENT'},
        {'id': 'clm', 'number': '07', 'title': 'Contract Lifecycle Management & SLA Control', 'category': 'LEGAL & COMPLIANCE', 'summary': 'Centralized repository of master service agreements, non-disclosure pacts, rate cards, and automated renewal milestone alerts.', 'features': ['Clause repository & template authorization', 'Digital signature execution & version archiving', 'Milestone obligation tracking with penalty alerts', 'Proactive renewal notices at 90, 60, and 30 days'], 'metric_label': 'Compliance Adherence', 'metric_value': 'Zero Missed Renewals', 'status_tag': 'ACTIVE & MONITORED'},
    ]
    context = {
        'page_title': 'Solutions — ProcureSphere 360 Source-to-Pay Lifecycle',
        'page_description': 'Explore the complete source-to-pay lifecycle from vendor onboarding to automated 3-way invoice matching.',
        'active_nav': 'solutions',
        'badge': 'S2P LIFECYCLE',
        'title': 'The Complete Source-to-Pay System',
        'subtitle': 'Seven integrated stages that eliminate manual effort and deliver a unified financial operating picture.',
        's2p_steps': s2p_steps,
    }
    return render(request, 'landing/solutions.html', context)


# ---------------------------------------------------------------------------
# Benefits
# ---------------------------------------------------------------------------

def benefits(request):
    """Benefits page."""
    benefits_list = [
        {'title': 'Traceable Workflows', 'description': 'No more approvals lost in email. Every requisition, bid, purchase order, and invoice is strictly traceable to its state and timestamp.', 'color': '#3b82f6'},
        {'title': 'Zero Silent Overspend', 'description': 'Real-time budget validation distinguishes available, reserved, and actual spend. Exceptions require an explicit, documented route.', 'color': '#10b981'},
        {'title': 'Supplier Governance', 'description': 'Vendor onboarding, KYC, and risk management with built-in due-diligence. Keep supplier identity and performance in constant view.', 'color': '#f59e0b'},
        {'title': 'Three-Way Match Precision', 'description': 'Automated validation comparing PO, receipt, and invoice. Configurable tolerances ensure accurate payment and clear exception workflows.', 'color': '#8b5cf6'},
        {'title': 'Contract Lifecycle Control', 'description': 'Never miss a renewal deadline. Keep obligations, SLAs, and version histories organized with automated expiry alerts.', 'color': '#ec4899'},
        {'title': 'Traceable Audit Trails', 'description': 'Every critical action captures the actor, state change, and timestamp, providing structured event reporting for audit readiness.', 'color': '#06b6d4'},
    ]
    comparison_rows = [
        {'dimension': 'Spend Authorization', 'legacy': 'Manual spreadsheet tracking; overspend discovered weeks after commitment', 'procuresphere': 'Pre-approval budget reservation distinguishing available, reserved, and actual spend to prevent silent overspend', 'advantage': 'Zero Silent Overspend'},
        {'dimension': 'Supplier Sourcing', 'legacy': 'Uncontrolled email quote exchanges with fragmented pricing records', 'procuresphere': 'Restricted vendor portal with sealed bid submissions and weighted technical and commercial scoring', 'advantage': 'Sealed Bidding & Objective Scoring'},
        {'dimension': 'Invoice Processing', 'legacy': 'Manual 3-way matching on paper, prone to quantity and pricing discrepancies', 'procuresphere': 'Automated 3-way matching comparing PO, receipt, and invoice with configurable tolerance rules', 'advantage': 'Automated Tolerance Matching'},
        {'dimension': 'Audit & Compliance', 'legacy': 'Painful retrospective audit sampling taking days of document gathering', 'procuresphere': 'Immutable audit logging capturing actor, state changes, and timestamps with instant audit export', 'advantage': 'Comprehensive Audit Readiness'},
        {'dimension': 'Contract Expirations', 'legacy': 'Forgotten contracts autorenewing at unfavorable rates without review', 'procuresphere': 'Proactive contract renewal alerts, milestone tracking, and vendor obligation governance', 'advantage': 'Proactive Renewal Governance'},
    ]
    context = {
        'page_title': 'Benefits — ProcureSphere 360 Enterprise Value',
        'page_description': 'Discover the enterprise benefits of ProcureSphere 360 — from traceable workflows to zero silent overspend.',
        'active_nav': 'benefits',
        'benefits': benefits_list,
        'comparison_rows': comparison_rows,
    }
    return render(request, 'landing/benefits.html', context)


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

def process(request):
    """Process page."""
    process_steps = [
        {'number': '01', 'title': 'Identify & Capture', 'desc': 'A department raises a purchase requisition through a structured form capturing line items, cost centers, and required dates. Budget availability is validated instantly.', 'tag': 'INTAKE'},
        {'number': '02', 'title': 'Policy-Driven Approval', 'desc': 'The approval engine routes the requisition through the correct chain — manager, finance, and CFO — based on spend threshold and category rules, with a full timestamp audit.', 'tag': 'APPROVAL'},
        {'number': '03', 'title': 'Competitive Sourcing', 'desc': 'Procurement creates an RFQ or RFP event, invites qualified vendors, and receives sealed bids. Technical and commercial scoring produces a transparent, defensible award recommendation.', 'tag': 'SOURCING'},
        {'number': '04', 'title': 'Order Execution', 'desc': 'The approved bid converts directly into a purchase order dispatched to the vendor. Amendments and delivery schedules are tracked with version control.', 'tag': 'PURCHASE ORDER'},
        {'number': '05', 'title': 'Goods Receipt & Inspection', 'desc': 'The receiving dock records delivered quantities, flags partial shipments, and records quality inspection results tied to each PO line item.', 'tag': 'RECEIPT'},
        {'number': '06', 'title': 'Automated 3-Way Match', 'desc': 'The system automatically reconciles the purchase order, goods receipt note, and vendor invoice — within configured tolerances — before releasing for payment.', 'tag': 'PAYMENT'},
    ]
    context = {
        'page_title': 'Process — ProcureSphere 360 Procurement Workflow',
        'page_description': 'Understand the end-to-end procurement process from requisition to payment with ProcureSphere 360.',
        'active_nav': 'process',
        'badge': 'HOW IT WORKS',
        'title': 'Six stages. One connected operating system.',
        'subtitle': 'From the first request to the final payment — every step is connected, tracked, and auditable.',
        'process_steps': process_steps,
    }
    return render(request, 'landing/process.html', context)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def roles(request):
    """Roles page."""
    roles_list = [
        {'key': 'REQUESTER', 'label': 'Requester', 'desc': 'Submit requests, track status, and stay within policy — all in one place.', 'items': ['My Requests', 'Policy guidance', 'Budget visibility'], 'color': '#2456D6'},
        {'key': 'PROCUREMENT', 'label': 'Procurement', 'desc': 'Manage sourcing events, suppliers, and purchase orders from a single workspace.', 'items': ['Sourcing', 'Suppliers', 'Purchase Orders'], 'color': '#6554c0'},
        {'key': 'FINANCE', 'label': 'Finance', 'desc': 'Control invoices, budgets, and payment schedules with full financial visibility.', 'items': ['Invoices', 'Budget', 'Payments'], 'color': '#10b981'},
        {'key': 'LEGAL', 'label': 'Legal', 'desc': 'Track contracts, renewals, and compliance obligations with structured oversight.', 'items': ['Contracts', 'Renewals', 'Obligations'], 'color': '#f59e0b'},
        {'key': 'STORES', 'label': 'Stores', 'desc': 'Manage warehouse receipts, quality inspection, and inventory context efficiently.', 'items': ['Receipts', 'Inspection', 'Inventory context'], 'color': '#ec4899'},
        {'key': 'COMPLIANCE', 'label': 'Compliance', 'desc': 'Monitor audit trails, risk indicators, and internal control compliance.', 'items': ['Audit', 'Risk', 'Controls'], 'color': '#ef4444'},
        {'key': 'VENDOR', 'label': 'Vendor', 'desc': 'Onboard, bid on sourcing events, and acknowledge purchase orders — all online.', 'items': ['Onboarding', 'Bids', 'PO acknowledgement'], 'color': '#06b6d4'},
    ]
    context = {
        'page_title': 'Roles — ProcureSphere 360 Team Workspaces',
        'page_description': 'One platform with role-specific workspaces for every team in the procurement cycle.',
        'active_nav': 'roles',
        'badge': 'WHO IS IT FOR',
        'title': 'One platform. Every team.',
        'subtitle': 'One connected system adapts to the decisions each team owns.',
        'roles': roles_list,
    }
    return render(request, 'landing/roles.html', context)


# ---------------------------------------------------------------------------
# FAQ
# ---------------------------------------------------------------------------

def faq(request):
    """FAQ page."""
    faqs = [
        {'question': 'What does ProcureSphere 360 connect?', 'answer': 'Requests, approvals, suppliers, sourcing, purchase orders, receipts, invoices, contracts, finance and governance in one connected workflow.'},
        {'question': 'Is the interface shown live company data?', 'answer': 'No. The interface is illustrative demo content designed to show how the operating picture could work without revealing sensitive information.'},
        {'question': 'Can vendors participate in the workflow?', 'answer': 'Yes. The Vendor workspace brings onboarding, bids, and purchase-order acknowledgement into the same connected experience, creating a seamless bridge between you and your suppliers.'},
        {'question': 'How does the approval policy engine work?', 'answer': 'It routes decisions automatically based on custom rules like department, budget thresholds, or risk categories, ensuring the right owner reviews the request at the exact right time.'},
        {'question': 'What is the 3-way matching process?', 'answer': 'The system cross-verifies the Purchase Order line items, the physical Goods Receipt Note from the warehouse, and the Vendor Invoice charges — within configurable tolerance limits — before releasing payment.'},
        {'question': 'How does ProcureSphere 360 handle contract renewals?', 'answer': 'The system sends proactive renewal notifications at 90, 60, and 30 days before contract expiry, with milestone obligation tracking and escalation to the responsible contract owner.'},
        {'question': 'Is ProcureSphere 360 suitable for multi-entity organizations?', 'answer': 'Yes. The platform supports multi-company, multi-currency, and multi-location procurement with shared vendor master records and consolidated audit reporting.'},
        {'question': 'How is vendor risk managed?', 'answer': 'Vendor onboarding includes KYC verification, tax and banking validation, and continuous compliance monitoring with automated expiry alerts for certifications and licenses.'},
    ]
    context = {
        'page_title': 'FAQ — ProcureSphere 360 Common Questions',
        'page_description': 'Answers to common questions about ProcureSphere 360 enterprise procurement platform.',
        'active_nav': 'faq',
        'badge': 'FAQ',
        'title': 'Clarity before the first conversation.',
        'subtitle': 'A few practical answers for teams evaluating a connected procurement operating system.',
        'faqs': faqs,
    }
    return render(request, 'landing/faq.html', context)


# ---------------------------------------------------------------------------
# Contact (HTMX partial)
# ---------------------------------------------------------------------------

@require_POST
def contact_submit(request):
    """
    Handles the public contact / demo-request form via HTMX POST.

    CSRF protection is enforced by Django's standard CsrfViewMiddleware.
    The form template must include {% csrf_token %}.

    BACKEND NOTE: This view validates fields and returns the success partial.
    CRM / email delivery can be added here when the backend contract is defined.
    """
    name = request.POST.get('name', '').strip()
    email = request.POST.get('email', '').strip()
    company = request.POST.get('company', '').strip()
    scope = request.POST.get('scope', '').strip()
    message = request.POST.get('message', '').strip()

    if not all([name, email, company, scope, message]):
        return render(request, 'landing/partials/contact_error.html', {
            'error': 'Please fill in all required fields.',
        })

    # Placeholder for CRM / email integration.
    # e.g. send_demo_request_email(name, email, company, scope, message)

    return render(request, 'landing/partials/contact_success.html', {
        'name': name,
        'email': email,
    })

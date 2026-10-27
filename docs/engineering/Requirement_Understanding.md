# ProcureSphere 360 — Requirement Understanding Document

**Document Reference:** REQ-PS360-D15  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Issuing Authority:** HPE Team  
**Development Partner:** VPD Technologies Pvt. Ltd.  
**Project Repository:** https://github.com/VPDTechnologies/ProcureSphere_360  
**Target Milestone:** Days 1–15 Engineering Foundation & Core Workflow Baseline  
**Date of Assessment:** 2026-10-12  
**Status:** Review-Ready Baseline  

---

## 1. Document Purpose, Version, Status & Source References

### 1.1 Purpose
This document provides an exhaustive, evidence-grounded baseline of the business requirements, domain workflows, system architecture, functional modules, and regulatory controls for **ProcureSphere 360**. It aligns the authoritative Product Requirements Document (`HPE-PRD-2026-PROC01`) with the real architectural, database, and workflow implementation delivered within the repository across the Days 1–15 foundation milestone.

### 1.2 Version & Status
- **Document Version:** 1.1 (Days 1–15 Foundation & Verification Baseline)
- **Status:** Approved for Coordinator & Client Technical Review
- **Repository Scope:** Exclusively grounded in `https://github.com/VPDTechnologies/ProcureSphere_360`

### 1.3 Authoritative Source References
1. **PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT), issued by HPE Team.
2. **Project Governance:** `AGENTS.md` (Project Charter, Non-Negotiables, and Execution Plan).
3. **Repository Assumptions Register:** `docs/assumptions.md` (Design assumptions, architectural choices, and domain boundary decisions).
4. **Target Review Branch:** `staging` (Candidate integration branch for client demonstrations).

---

## 2. Executive Summary

ProcureSphere 360 is an enterprise-grade, real database-backed ERP solution designed to replace uncoordinated spreadsheet- and email-based purchasing workflows with a unified, auditable **Source-to-Pay (S2P)** and **Accounts Payable (AP)** workflow. Every procurement event—from requisition creation to vendor bidding, award, purchase order issuance, physical goods intake, quality inspection, 3-way invoice matching, and contract lifecycle management—is strictly governed by server-side Role-Based Access Control (RBAC), multi-level approval routing, and append-only audit logging.

The system is built upon a zero-mock philosophy: every button, approval action, filter, status change, and export directly reads and writes persistent PostgreSQL 15+ records within ACID-compliant database transactions.

---

## 3. Business Problem & Objectives

### 3.1 Problem Statement
Enterprise procurement operations frequently suffer from systemic operational breakdowns:
1. **Uncontrolled Spend & Maverick Buying:** Requisitions generated without automated budget verification, resulting in retrospective budget deficits and unauthorized fiscal commitments.
2. **Lack of Vendor Governance & Risk Exposure:** Onboarding vendors without rigorous Know-Your-Customer (KYC) documentation, compliance vetting, and transactional risk controls.
3. **Compromised Sourcing Integrity:** Manual email bids lacking sealed-bid integrity, stage-based visibility controls, and objective multi-criteria scoring.
4. **Disjointed Receiving & Quality Leakage:** Stores teams recording intake on paper or disparate spreadsheets, leading to uninspected items being stocked, lack of defect quarantine, and zero return traceability.
5. **Invoice Mismatches & AP Bottlenecks:** Invoices processed without automated 3-way matching against purchase orders and goods receipt notes, causing duplicate payments or delayed settlements.
6. **Contractual Blindspots:** Expiring contracts, unmet SLA milestones, and unmonitored vendor obligations leading to financial leakage and legal liability.

### 3.2 Core Business Objectives
- **100% Traceability & Accountability:** Guarantee that every transaction records *who* performed the action, *what* state transition occurred, *when* it happened, and *what evidence* accompanied it.
- **Strict Budgetary Control:** Automatically validate and reserve funds upon requisition approval; transition reserved funds to commitments upon PO issuance, and to actual spend upon invoice clearance.
- **Sealed-Bid Sourcing:** Maintain cryptographic/state-gated bid confidentiality until event closure, followed by objective two-envelope technical and commercial evaluations.
- **Defensible 3-Way Matching:** Match PO, Goods Receipt (GRN), and Supplier Invoice with configurable tolerance thresholds and explicit exception-handling resolution workflows.
- **Zero Mock Functionality:** Reject all mock endpoints, hardcoded data, or frontend-only access controls in favor of persistent PostgreSQL 15+ records.

---

## 4. Stakeholders & User Roles

ProcureSphere 360 enforces a 10-role permission model defined in `apps.accounts.models.Role`:

| Role Code | Role Name | Core Responsibilities | Scope of Authorization |
|:---|:---|:---|:---|
| `SUPER_ADMIN` | Super Admin | System configuration, master data, user & role administration, policy definitions, emergency overrides. | Global system access. |
| `REQUESTER` | Requester | Create, edit, and track purchase requisitions (PR) for authorized cost centers. | Scoped to own department/cost center and self-created items. |
| `DEPT_APPROVER` | Department Approver | Review, approve, or reject purchase requisitions within configured financial delegation limits. | Scoped to assigned departments and approval chains. |
| `PROC_EXEC` | Procurement Executive | Vendor onboarding vetting, sourcing RFQ/RFP management, bid clarification, evaluation, PO drafting. | Scoped to operational procurement processes. |
| `PROC_MGR` | Procurement Manager | Sourcing award approvals, vendor suspensions/reinstatements, policy exceptions, executive oversight. | Organizational procurement authority. |
| `FINANCE_AP` | Finance / AP | Budget allocations, invoice verification, 3-way matching exception resolution, payment readiness. | Financial ledger and accounts payable domain. |
| `STORES_RECEIVER` | Stores / Receiver | Physical shipment intake, GRN creation, quality inspection gates, vendor returns, inventory stock handoffs. | Warehouse and receiving dock operations. |
| `LEGAL_MGR` | Legal / Contract Manager | Contract authoring, versioning, clauses, obligation tracking, milestone approvals, renewal/termination. | Legal agreements and contract lifecycle. |
| `AUDITOR` | Compliance / Auditor | Read-only inspection across all modules, approvals, audit logs, and compliance exports. | Global **Read-Only** visibility; state mutations strictly forbidden. |
| `VENDOR_USER` | Vendor User | Self-service onboarding, KYC document upload, RFQ bid submission, PO acknowledgement. | Strictly restricted to vendor's own profile, bids, and POs. |

---

## 5. Functional Requirements Grouped by Business Module

### 5.1 Organization & Cost Center Management (`apps.organization`)
- **Legal Entities & Departments:** Hierarchical structure (`Organization` → `Department` → `CostCenter`).
- **Fiscal Periods & Calendar:** Fiscal year and active quarter modeling with budget association.
- **Approval Limits:** Configurable financial threshold matrices linked to roles and organizational tiers.

### 5.2 Vendor Onboarding, KYC & Vendor Risk (`apps.vendors`)
- **Self-Service & Executive Intake:** Vendor registration portal with company registration, tax identification (TIN), bank details, and contact registry.
- **KYC Document Compliance:** Mandatory file uploads (Tax Certificate, Business License, Banking Verification) with MIME-type and extension validation.
- **Vendor Governance & Risk Records:** Periodic vendor risk assessments (`VendorRiskRecord`), risk scoring (LOW, MEDIUM, HIGH, CRITICAL), and status transitions (`ACTIVE`, `ON_HOLD`, `SUSPENDED`, `BLACKLISTED`).
- **Suspension Defense:** Suspended or blacklisted vendors are strictly blocked from sourcing invitations, bid submissions, awards, and PO issuance.

### 5.3 Purchase Requisitions & Approval Policies (`apps.requisitions`, `apps.approvals`)
- **Requisition Drafting:** Multi-line PRs (`PRLine`) with item description, unit price, quantity, UOM, and delivery dates.
- **Budget Reservation Guard:** Pre-approval validation ensuring `CostCenter` has sufficient available budget; creates immutable `BudgetReservation`.
- **Policy Engine:** Server-side rule evaluation determining approval tiers based on total PR value, requesting department, and spend category. Supports approval delegates with audit attribution.

### 5.4 RFQ/RFP Sourcing, Vendor Bids & Evaluation (`apps.sourcing`)
- **Sourcing Events:** Creation of RFQ/RFP events with defined bid submission windows, terms, and evaluation weighting (Technical vs Commercial).
- **Vendor Invitations & Sealed Bidding:** Invitation of active, vetted vendors; sealed bid portal where competitor bids are hidden, and bids remain unsealed to evaluators until the bid window closes.
- **Two-Envelope Evaluation & Award:** Stage-gated evaluation (Technical Review first, Commercial Review second) producing scored `BidEvaluation` and formal `AwardDecision`.

### 5.5 Purchase Orders & Amendments (`apps.orders`)
- **PO Generation:** Automated generation of POs from approved Requisitions or Sourcing Awards with unique numbering (`PO-YYYY-XXXXX`).
- **Amendment Versioning:** Immutable version snapshots (`POAmendment`) preserving prior values when change orders occur; raw overwrites are forbidden.
- **Delivery Schedules:** Detailed line-level expected delivery dates (`DeliverySchedule`) driving on-time metrics.

### 5.6 Goods Receipt, Quality Inspection & Stock Handoff (`apps.receipts`)
- **Shipment Intake:** Physical delivery intake recording carrier delivery note and received quantities.
- **Quality Inspection Gates:** Line-item inspection recording accepted quantity, rejected quantity, defect reason, and quarantine location.
- **Vendor Returns & Quantity Corrections:** Traceable dispatch of rejected items back to vendor (`RejectionRecord`) and non-destructive quantity adjustments with mandatory reason logging.
- **Stock Handoff:** Traceable posting of accepted goods to warehouse storage bins (`StockHandoffRecord`).

### 5.7 Supplier Invoices & 3-Way Matching (`apps.invoices`)
- **Invoice Intake & Duplicate Guard:** Registration of vendor invoices with strict validation against duplicate invoice numbers for the same vendor.
- **3-Way Match Engine:** Automated validation comparing PO line quantities/rates, GRN accepted quantities, and Invoice billed amounts.
- **Tolerances & Match Exceptions:** Configurable tolerance policies (percentage and flat dollar thresholds); deviations spawn `MatchException` records requiring Finance approval before payment clearance.

### 5.8 Contract Lifecycle Management (`apps.contracts`)
- **Contract Authoring & Versioning:** Contract repository with title, value, dates, clauses, and version history.
- **Milestones & Obligations:** Tracking of contractual deliverables, payment milestones, and regulatory obligations with responsible party assignments.
- **Automated Alerts:** Celery Beat scheduled jobs generating timely notifications for contract expiration, renewal notice windows, and milestone deadlines.

### 5.9 Budget & Spend Management (`apps.budgets`)
- **Tri-State Budget Allocation:** Real-time tracking of *Allocated*, *Reserved* (PR approved), *Committed* (PO issued), and *Actual* (Invoice matched) spend.
- **Spend Ledger:** Append-only financial ledger (`SpendLedger`) maintaining strict accounting integrity.

### 5.10 Supplier Performance Scorecards (`apps.scorecards`)
- **Transactional KPI Aggregation:** Quantitative calculation of delivery timeliness, quality acceptance rates, pricing adherence, and compliance factors.
- **Scorecard Bands:** Periodic composite scoring categorizing vendors into Good (≥85), Watchlist (70–84.9), or Underperforming (<70).

### 5.11 Notifications & Escalation (`apps.notifications`)
- **Multi-Channel Dispatch:** In-app notifications and background asynchronous email alerts via Celery.
- **Approval Escalations:** Scheduled checks alerting supervisors when approval requests exceed configured SLA turnaround windows.

### 5.12 Reports, Audit Trails & Exports (`apps.reports`, `apps.audit`)
- **Mandatory Reporting Engine:** 9 core reports (PR aging, spend analytics, sourcing cycle time, PO status, delivery & rejection performance, invoice exception aging, contract expiry, supplier scorecards, audit trail).
- **Append-Only Audit Log:** Comprehensive immutable audit trail (`AuditLog`) capturing actor, action, model, target ID, prior state, new state, IP address, and timestamp.
- **Controlled Exports:** Direct CSV/XLSX streaming and background Celery `ExportJob`s with audit record creation.

---

## 6. End-to-End Business Workflows & Documented States

All business state transitions are governed by strict server-side transition tables evaluated inside atomic database transactions (`@transaction.atomic`). Direct status assignments (`obj.status = 'X'; obj.save()`) bypassing domain services are prohibited.

### 6.1 Vendor Lifecycle State Machine
```
[DRAFT] ──> [SUBMITTED] ──> [KYC REVIEW] ──> [APPROVED] ──> [ACTIVE] <──> [ON HOLD]
                                    │                                  │
                                    └──> [REJECTED]                   └──> [SUSPENDED] ──> [BLACKLISTED]
```
- **Allowed States:** `DRAFT`, `SUBMITTED`, `KYC_REVIEW`, `APPROVED`, `REJECTED`, `ACTIVE`, `ON_HOLD`, `SUSPENDED`, `BLACKLISTED`.
- **Enforcement Rules:** A suspended or blacklisted vendor cannot receive RFQ invitations, submit bids, be awarded sourcing events, or receive POs.

### 6.2 Purchase Requisition Lifecycle State Machine
```
[DRAFT] ──> [SUBMITTED] ──> [MANAGER REVIEW] ──> [BUDGET REVIEW] ──> [APPROVED] ──> [SOURCING / PO]
                                    │                   │
                                    └──> [REJECTED]     └──> [REJECTED]
```
- **Allowed States:** `DRAFT`, `SUBMITTED`, `MANAGER_REVIEW`, `BUDGET_REVIEW`, `APPROVED`, `REJECTED`, `CANCELLED`.
- **Enforcement Rules:** Approval transitions check budget availability and create an immutable `BudgetReservation`. Multi-tier approvals route sequentially based on dollar thresholds.

### 6.3 Sourcing Event (RFQ/RFP) Lifecycle State Machine
```
[DRAFT] ──> [PUBLISHED] ──> [BID WINDOW] ──> [TECHNICAL REVIEW] ──> [COMMERCIAL REVIEW] ──> [AWARD APPROVAL] ──> [AWARDED]
                                    │                                                                           │
                                    └─── [CANCELLED] <──────────────────────────────────────────────────────────┘
```
- **Allowed States:** `DRAFT`, `PUBLISHED`, `BID_WINDOW_OPEN`, `BID_WINDOW_CLOSED`, `TECHNICAL_REVIEW`, `COMMERCIAL_REVIEW`, `AWARD_APPROVAL`, `AWARDED`, `CANCELLED`.
- **Enforcement Rules:** Bids remain sealed from competitors at all times. Bids remain sealed from evaluators until the bid window closes. Two-envelope evaluation requires Technical Review score validation before Commercial Envelope unsealing.

### 6.4 Purchase Order Lifecycle State Machine
```
[DRAFT] ──> [APPROVAL] ──> [ISSUED] ──> [ACKNOWLEDGED] ──> [PARTIAL RECEIPT] ──> [COMPLETED]
                              │                                                  │
                              └─── [CANCELLED] <─────────────────────────────────┘
```
- **Allowed States:** `DRAFT`, `APPROVAL`, `ISSUED`, `ACKNOWLEDGED`, `PARTIAL_RECEIPT`, `COMPLETED`, `CANCELLED`.
- **Enforcement Rules:** PO amendments create versioned snapshots (`POAmendment`) and do not overwrite original lines. Final `COMPLETED` state triggers only when cumulative accepted line quantities satisfy all line requirements. Post-completion receipts are locked.

### 6.5 Supplier Invoice & 3-Way Match Lifecycle State Machine
```
[RECEIVED] ──> [VALIDATION] ──> [3-WAY MATCH] ──> [READY FOR PAYMENT] ──> [PAID]
                                       │                    ▲
                                       └──> [EXCEPTION] ────┘ (upon Finance approval)
                                                │
                                                └──> [REJECTED]
```
- **Allowed States:** `RECEIVED`, `VALIDATION`, `MATCHED`, `EXCEPTION`, `READY_FOR_PAYMENT`, `PAID`, `REJECTED`.
- **Enforcement Rules:** Duplicate invoice numbers within the same vendor are rejected at validation. Invoices failing line quantity or price tolerances generate `MatchException` records requiring explicit Finance approval.

### 6.6 Contract Lifecycle State Machine
```
[DRAFT] ──> [LEGAL REVIEW] ──> [BUSINESS APPROVAL] ──> [ACTIVE] ──> [RENEWAL DUE] ──> [RENEWED]
                                                           │                              │
                                                           └──> [EXPIRED]                 └──> [TERMINATED]
```
- **Allowed States:** `DRAFT`, `LEGAL_REVIEW`, `BUSINESS_APPROVAL`, `ACTIVE`, `RENEWAL_DUE`, `RENEWED`, `EXPIRED`, `TERMINATED`.
- **Enforcement Rules:** Contract amendments create versioned contracts with prior clause snapshots preserved. Celery Beat executes periodic checks triggering automated alerts for expiry windows, renewal dates, and pending obligations.

---

## 7. Business Rules, Validations, Exceptions & Approval Policies

### 7.1 Server-Side Approval Routing & Delegation
1. **Configurable Thresholds:** Routing is evaluated server-side against transaction value, requesting department, spend category, and cost center.
2. **Multi-Level Chains:** PR values above defined thresholds (e.g., >$10,000, >$50,000) automatically require sequential Manager and Department Head sign-offs.
3. **Delegation Support:** If a designated approver is unavailable, an active `ApprovalDelegate` record permits the delegate to act, with the `ApprovalAction` recording both the acting delegate and delegating user.

### 7.2 Budgetary Tri-State Control
1. **Validation Stages:**
   - **Available Budget:** Total allocation minus reservations, commitments, and actual spend.
   - **Reserved Budget:** Created upon PR approval (`BudgetReservation`).
   - **Committed Budget:** Created upon PO issuance via `SpendLedger`.
   - **Actual Spend:** Recorded upon 3-way match validation and invoice clearance.
2. **Overspend Defense:** Transactions that exceed available cost center funds are blocked at the service layer unless accompanied by a documented, authorized budget-override exception.

### 7.3 Sourcing Integrity & Sealed Bidding
1. **Competitor Sealing:** Bidders can never access other vendors' bids.
2. **Reviewer Sealing:** Evaluators cannot view bid amounts or lines until the bid window is officially closed.
3. **Envelope Gating:** Technical evaluators score specifications without visibility of commercial pricing. Only bids meeting the technical qualifying threshold advance to commercial evaluation.

### 7.4 PO Amendment Immutability
1. **Historical Snapshots:** Every PO change order generates a `POAmendment` containing amendment number, justification, previous line states, and amended line values.
2. **Historical Integrity:** Prior PO version details remain accessible and immutable; no existing PO row is overwritten.

### 7.5 3-Way Match Tolerances & Exception Handling
1. **Tolerance Matrix:** Configurable per item category (e.g., 0% for capital equipment, ±5% quantity tolerance for bulk commodities, or ±$50.00 rate variance).
2. **Match Exceptions:** Any variance outside the configured tolerance immediately transitions the invoice to `EXCEPTION` and generates a `MatchException` record. The invoice cannot transition to `READY_FOR_PAYMENT` without Finance authorization.

### 7.6 Edge Case Defenses
- **Partial Receiving:** Line items support multiple sequential goods receipts (`GoodsReceipt`), tracking `quantity_received` and `remaining_quantity` per PO line.
- **Defect Quarantine & Vendor Return:** Rejected items are split from accepted stock, tagged with reason codes, quarantined, and recorded on a `RejectionRecord` for vendor return.
- **Non-Destructive Corrections:** Stores receiver quantity corrections record offsetting adjustments with mandatory audit comments; historical GRN line values remain intact.
- **Duplicate Invoice Prevention:** Unique constraint across `(vendor_id, invoice_number)` prevents duplicate payments.

---

## 8. Non-Functional Requirements

### 8.1 Security & Access Control
- **Backend-Enforced Authorization:** Every view and API endpoint enforces role authorization in Python; hiding frontend buttons or navigation links is never treated as security.
- **Object-Level Scoping:** Requesters view only their department requisitions; vendors view only their own bids and POs; compliance auditors have global read-only rights.
- **Injection Defense:** 100% parameterization via Django ORM; raw SQL string interpolation is prohibited.
- **File Upload Security:** Extensions capped and restricted (`pdf, png, jpg, jpeg, doc, docx, xlsx, csv`); MIME-types validated; file size capped at 10 MB.

### 8.2 Reliability & Transactional Integrity
- **ACID Transactions:** All multi-step workflow operations (such as GRN creation, stock handoff, and approval routing) execute within `@transaction.atomic` blocks with rollback guarantees.
- **Foreign Key Protection:** All transactional references employ `on_delete=models.PROTECT` to prevent accidental orphaned references or cascade deletions.

### 8.3 Auditability & Traceability
- **Append-Only Audit Log:** Comprehensive immutable audit trail (`AuditLog`) capturing actor, action, model, target ID, prior state, new state, IP address, request ID, and timestamp.
- **Export Auditing:** Data exports (CSV, XLSX, PDF) generate corresponding audit log events.

### 8.4 Performance & Scalability
- **Query Optimization:** Elimination of N+1 database queries through systematic use of `select_related()` and `prefetch_related()`.
- **Asynchronous Execution:** Heavy reporting, notification emails, and scheduled alert evaluations run asynchronously via Celery workers and Redis.

---

## 9. Technology & Architecture Constraints

| Layer | Selected Architecture | Constraint / Rule |
|:---|:---|:---|
| **Backend Runtime** | Python 3.11+, Django 4.2+ LTS, DRF 3.14+ | Core business logic resides in `services.py` and `selectors.py`. Views remain thin. |
| **Database** | PostgreSQL 15+ | Normalized relational schema, constraints, transactions, B-tree indexes, no SQLite in production. |
| **Async Task Queue** | Celery 5.3+, Celery Beat, Redis 7+ | Periodic alert evaluation and heavy background jobs run via Celery; Windows worker runs with solo pool. |
| **Presentation** | Django Templates, HTMX 1.9+, Bootstrap 5.3 | Server-rendered UI with HTMX partials; no standalone SPA frameworks. |
| **API** | Django REST Framework (`/api/v1/`) | Versioned REST API, Session/Token authentication, OpenAPI schema generation via `drf_spectacular`. |
| **Security Middleware** | `django-axes`, Django CSRF, Clickjacking protection | Brute-force lockout enabled; CSRF cookie attached for HTMX and API operations. |

---

## 10. Data, Integration, Notification, Reporting & Export Requirements

### 10.1 Reporting Engine (9 Mandatory Core Reports)
1. **PR Aging & Bottleneck Report:** Tracks requisitions across approval tiers, identifying delayed reviews.
2. **Spend Analytics by Category/Vendor/Department:** Aggregates spend breakdown across fiscal periods.
3. **Sourcing Cycle Time & Participation:** Measures RFQ publication to award velocity and bidder turnout.
4. **PO Lifecycle & Open Status:** Summarizes open, partial, and completed purchase orders.
5. **Receipt, Rejection & Delivery Performance:** Measures supplier on-time delivery rates and line acceptance percentages.
6. **Invoice Match-Exception Aging:** Flags unresolved 3-way matching exceptions by age and amount.
7. **Contract Expiry, Renewal & Obligation Tracker:** Highlights contracts approaching expiration windows.
8. **Supplier Performance Scorecard & Trend:** Displays composite supplier ratings based on transactional metrics.
9. **User & Approval Audit Trail Report:** Searchable, immutable record of all state transitions and system actions.

### 10.2 Export Capabilities
- All 9 reports support synchronous direct streaming export (CSV) and Excel workbook export (`openpyxl` XLSX).
- Heavy exports run as background Celery `ExportJob`s with persistent download links.

### 10.3 Notifications & Scheduled Jobs
- **In-App Alerts:** Real-time bell notification icons with read/unread status.
- **Email Dispatch:** Background dispatch via Celery.
- **Celery Beat Schedules:** Periodic tasks evaluating contract expiration windows and bid window closures.

---

## 11. Assumptions, Dependencies, Risks & Open Questions

### 11.1 Technical Dependencies
- PostgreSQL 15+ with pg_trgm and JSONB support.
- Redis 7+ for Celery task queuing and Celery Beat scheduler.
- Pinned Python libraries specified in `requirements.txt`.

### 11.2 Registered Assumptions (from `docs/assumptions.md`)
- **ASSUMP-007 (Authentication Model):** Dual JWT/session architecture was unified to secure HTTP-only session cookies with CSRF tokens for both portal views and API requests.
- **ASSUMP-008 (Vendor Sourcing Eligibility):** Sourcing events invite only `ACTIVE` vendors; vendor risk rating informs evaluation decisions but does not automatically disqualify without executive hold.
- **ASSUMP-014 (Two-Envelope Unsealing):** Bids are sealed until event close; technical envelope is visible during Technical Review; commercial envelope unseals strictly during Commercial Review.

### 11.3 Open Decisions & Clarification Points
- **DECISION-01:** What automated policy should apply to open invitations and issued POs when a vendor is suspended mid-contract? *(Current implementation: blocks new awards/POs; keeps existing issued POs active while flagging exposure)*.
- **DECISION-02:** Should the bid submission window allow premature executive closure, or strictly honor the published deadline? *(Current implementation: strictly honors deadline)*.
- **DECISION-03:** Are vendor clarification answers broadcasted to all bidders or restricted to the inquiring vendor? *(Current implementation: restricted to inquiring vendor)*.

---

## 12. Scope, Exclusions & Boundaries

### 12.1 In-Scope Capabilities
- Complete Source-to-Pay (S2P) and Accounts Payable (AP) workflow engine.
- 10-role RBAC architecture with object-level and department-level scoping.
- Organization, vendor, PR, sourcing, PO, receipt, invoice, contract, and budget modules.
- Server-side approval policy routing with delegates.
- Append-only audit logging and 9 core business reports with CSV/XLSX exports.
- Days 1–15 engineering foundation and verified test suites.

### 12.2 Explicit Exclusions & Out-of-Scope Items
- **Third-Party Payment Gateway Integrations:** The system manages invoice clearance to `READY_FOR_PAYMENT` and `PAID` within ERP ledger records; real banking wire or ACH integrations are outside the current milestone scope.
- **Physical Barcode / RFID Hardware Drivers:** Goods intake is managed via web portal delivery note intake; hardware scanner API integration is out of scope.
- **Multi-Tenant SaaS Partitioning:** The application is architected for single-enterprise multi-entity operations (`Organization` model), not multi-tenant SaaS.

---

## 13. Requirements Traceability Matrix

The following matrix maps business requirements to authoritative PRD specifications, repository implementation artifacts, automated test verification evidence, and delivery status:

| Req ID | Requirement Description | Business Module | Source Document / Section | Relevant Repository Implementation | Verification Evidence | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| **REQ-AUTH-01** | Custom User model with UUID PK, email username, and Role association | Accounts & RBAC | PRD Sec 5, AGENTS Sec 5 | `apps/accounts/models.py:User` | `tests/test_all_role_logins.py` | **Implemented** |
| **REQ-AUTH-02** | Login throttling and brute-force lockout via `django-axes` | Accounts & RBAC | PRD Sec 12, AGENTS Sec 3 | `src/config/settings/base.py`, `apps/accounts/views.py` | `tests/test_all_role_logins.py` | **Implemented** |
| **REQ-RBAC-01** | Server-side role enforcement across all views and APIs | Accounts & RBAC | PRD Sec 5, AGENTS Sec 2 | `apps/accounts/permissions.py`, `apps/*/views.py` | `tests/test_all_role_dashboards.py` | **Implemented** |
| **REQ-ORG-01** | Multi-entity Organization, Department, and Cost Center hierarchy | Organization | PRD Sec 6, AGENTS Sec 6 | `apps/organization/models.py`, `apps/organization/services.py` | `tests/test_demo_2_pr_approval_budget.py` | **Implemented** |
| **REQ-VEND-01** | Vendor onboarding, KYC document upload, and validation | Vendors | PRD Sec 6, AGENTS Sec 6 | `apps/vendors/models.py`, `apps/vendors/services.py` | `tests/test_demo_1_vendor_kyc.py` | **Implemented** |
| **REQ-VEND-02** | Vendor risk scoring and status gating (Hold/Suspend/Blacklist) | Vendors | PRD Sec 8, AGENTS Sec 8 | `apps/vendors/services.py`, `apps/vendors/models.py` | `tests/test_pe_vendor_governance.py` | **Implemented** |
| **REQ-PR-01** | Purchase requisition creation with multi-line items and UOM | Requisitions | PRD Sec 6, AGENTS Sec 6 | `apps/requisitions/models.py`, `apps/requisitions/services.py` | `tests/test_demo_2_pr_approval_budget.py` | **Implemented** |
| **REQ-PR-02** | Automated budget pre-check and `BudgetReservation` creation | Budgets | PRD Sec 8, AGENTS Sec 8 | `apps/budgets/models.py`, `apps/budgets/services.py` | `tests/test_demo_2_pr_approval_budget.py` | **Implemented** |
| **REQ-APP-01** | Policy-driven multi-level approval routing and delegates | Approvals | PRD Sec 8, AGENTS Sec 8 | `apps/approvals/models.py`, `apps/approvals/services.py` | `tests/test_demo_2_pr_approval_budget.py` | **Implemented** |
| **REQ-SRC-01** | RFQ/RFP event publishing with sealed bid confidentiality | Sourcing | PRD Sec 8, AGENTS Sec 8 | `apps/sourcing/models.py`, `apps/sourcing/services.py` | `tests/test_demo_3_sealed_sourcing.py` | **Implemented** |
| **REQ-SRC-02** | Two-envelope evaluation (Technical & Commercial Review) | Sourcing | PRD Sec 8, AGENTS Sec 8 | `apps/sourcing/views.py`, `apps/sourcing/services.py` | `tests/test_pe_rfq_sourcing.py` | **Implemented** |
| **REQ-ORD-01** | Purchase order generation from approved PRs and awards | Orders | PRD Sec 6, AGENTS Sec 6 | `apps/orders/models.py`, `apps/orders/services.py` | `tests/test_demo_4_po_amendment_receipts.py` | **Implemented** |
| **REQ-ORD-02** | PO amendment versioning preserving historical snapshots | Orders | PRD Sec 8, AGENTS Sec 8 | `apps/orders/models.py:POAmendment` | `tests/test_demo_4_po_amendment_receipts.py` | **Implemented** |
| **REQ-REC-01** | Goods receipt intake with partial delivery tracking | Receipts | PRD Sec 8, AGENTS Sec 8 | `apps/receipts/services.py:create_goods_receipt_service` | `tests/test_goods_receipt_creation.py` | **Implemented** |
| **REQ-REC-02** | Quality inspection gates, accept/reject split, and quarantine | Receipts | PRD Sec 8, AGENTS Sec 8 | `apps/receipts/services.py:record_inspection_service` | `tests/test_inspection_and_rejection.py` | **Implemented** |
| **REQ-REC-03** | Vendor returns dispatch and non-destructive quantity corrections | Receipts | PRD Sec 8, AGENTS Sec 8 | `apps/receipts/services.py:process_vendor_return_service` | `tests/test_day9_day10_stores_workflows.py` | **Implemented** |
| **REQ-REC-04** | Inventory stock handoff posting with idempotency protection | Receipts | PRD Sec 8, AGENTS Sec 8 | `apps/receipts/services.py:handoff_goods_to_stock_service` | `tests/test_stores_stock_handoff.py` | **Implemented** |
| **REQ-REC-05** | Delivery performance analytics and quality reporting | Receipts / Reports | PRD Sec 11, AGENTS Sec 11 | `apps/reports/services.py:get_receipt_rejection_report` | `tests/test_day15_day18_stores_workflows.py` | **Implemented** |
| **REQ-INV-01** | Supplier invoice intake with duplicate invoice number guard | Invoices | PRD Sec 8, AGENTS Sec 8 | `apps/invoices/models.py:SupplierInvoice` | `tests/test_demo_5_invoice_3way_match.py` | **Implemented** |
| **REQ-INV-02** | Automated 3-way match engine with tolerance policies | Invoices | PRD Sec 8, AGENTS Sec 8 | `apps/invoices/services.py:perform_3way_match_service` | `tests/test_demo_5_invoice_3way_match.py` | **Implemented** |
| **REQ-CON-01** | Contract lifecycle management, versions, and obligations | Contracts | PRD Sec 6, AGENTS Sec 6 | `apps/contracts/models.py:Contract` | `tests/test_contracts_workflow.py` | **Implemented** |
| **REQ-CON-02** | Celery Beat scheduled contract renewal and expiry alerts | Contracts | PRD Sec 8, AGENTS Sec 8 | `apps/contracts/tasks.py:check_contract_expiry_task` | `tests/test_demo_6_contract_celery_alerts.py` | **Implemented** |
| **REQ-REP-01** | 9 mandatory reports with CSV and XLSX export capabilities | Reports | PRD Sec 11, AGENTS Sec 11 | `apps/reports/services.py`, `apps/reports/views.py` | `tests/test_demo_7_spend_scorecard_audit_export.py` | **Implemented** |
| **REQ-AUD-01** | Append-only immutable AuditLog recording all state mutations | Audit | PRD Sec 8, AGENTS Sec 8 | `apps/audit/models.py:AuditLog` | `tests/test_demo_7_spend_scorecard_audit_export.py` | **Implemented** |
| **REQ-SCR-01** | Transactional supplier performance scorecard aggregation | Scorecards | PRD Sec 8, AGENTS Sec 8 | `apps/scorecards/services.py:calculate_vendor_scorecard` | `tests/test_demo_7_spend_scorecard_audit_export.py` | **Implemented** |
| **REQ-NOTIF-01** | In-app and Celery email notification dispatch | Notifications | PRD Sec 6, AGENTS Sec 6 | `apps/notifications/services.py` | `apps/notifications/tasks.py` | **Implemented** |

---

## 14. Initial Foundation Evidence Expected During Days 1–15

Across the Days 1 to 15 milestone, the repository established and verified the core architectural foundation:

1. **Scaffold & Architecture Baseline:** Split Django settings (`base.py`, `dev.py`, `staging.py`, `prod.py`), Request-ID middleware, thread-local audit context middleware, and `/api/v1/health/` monitoring.
2. **Accounts & RBAC Foundation:** Custom `User` model with UUID PK, 10 `Role` choices, login lockout mechanisms (`django-axes`), session authentication, and role-scoped dashboard templates.
3. **Master Organization & Cost Centers:** Hierarchical modeling (`Organization` → `Department` → `CostCenter`) with fiscal calendar periods.
4. **Vendor Onboarding & KYC Baseline:** Self-service registration, mandatory document uploads, risk scoring records, and status machines.
5. **Requisitions & Budget Reservations:** Multi-line PR drafting, cost-center budget pre-checks, and multi-tier approval routing.
6. **Sourcing & RFQ Foundation:** Sealed bidding workflows, two-envelope evaluations, and award decisions.
7. **Purchase Orders & Versioning:** PO generation, amendment version snapshots (`POAmendment`), and delivery schedule tracking.
8. **Stores & Receiving Core (Days 7–15):** Physical shipment intake (`GoodsReceipt`), quality inspection gates (`InspectionRecord`), defect quarantines (`RejectionRecord`), vendor return processing, quantity corrections, and inventory stock handoff records (`StockHandoffRecord`).

---

## 15. Review Readiness & Outstanding Clarification Points

- **Review Readiness:** The requirements understanding baseline is fully verified against the codebase, active models, migrations, and passing automated test suites in `https://github.com/VPDTechnologies/ProcureSphere_360`.
- **Target Branch for Client Review:** `staging` (designated integration branch for milestone evaluation).
- **Outstanding Clarification Points:**
  1. Confirmation on policy for active POs when a vendor is suspended mid-contract (current: blocks new POs, maintains existing orders with warning flags).
  2. Finalization of SLA escalation windows for multi-tier department approvers (currently defaulting to 48 hours before supervisor reminder).

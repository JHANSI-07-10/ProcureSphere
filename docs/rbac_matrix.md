# Role-Based Access Control (RBAC) Matrix — ProcureSphere 360

PRD Reference: HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
Primary Auth Model: Django Custom User + Role/Permission Scoping + Object-Level Ownership Checks

---

## 1. System Roles Summary

1. **Super Admin (`SUPER_ADMIN`)**: System configuration, master data management, global roles, approval policy definitions, full audit log access.
2. **Requester (`REQUESTER`)**: Create, submit, track purchase requisitions within authorized department / cost center scope.
3. **Department Approver (`DEPT_APPROVER`)**: Review, approve, reject purchase requisitions within configured approval limit and department chain.
4. **Procurement Executive (`PROC_EXEC`)**: Vendor onboarding review, RFQ/RFP event management, bid evaluation, purchase order creation/amendment, goods receipt tracking.
5. **Procurement Manager (`PROC_MGR`)**: High-value approval oversight, vendor governance/status changes (hold/suspend/blacklist), sourcing award approval, PO approvals.
6. **Finance / AP (`FINANCE_AP`)**: Cost center budget oversight, invoice capture & validation, 3-way match exception resolution, payment status management.
7. **Stores / Receiver (`STORES_RECEIVER`)**: Goods Receipt Note (GRN) entry, service entry processing, quality inspection logging, rejection records.
8. **Legal / Contract Manager (`LEGAL_MGR`)**: Contract creation, legal review, contract versioning, obligations management, renewal/termination governance.
9. **Compliance / Auditor (`AUDITOR`)**: Read-only system-wide audit access, approval history verification, export generation.
10. **Vendor User (`VENDOR_USER`)**: Self-service onboarding portal, bid response submission (sealed), PO acknowledgement, invoice submission, milestone proof upload.

---

## 2. Module Permission Matrix

Legend: **C** = Create, **R** = Read, **U** = Update, **D** = Delete, **A** = Approve/Action, **V** = Vendor Scoped Read/Submit Only

| Module / Feature | SUPER_ADMIN | REQUESTER | DEPT_APPROVER | PROC_EXEC | PROC_MGR | FINANCE_AP | STORES_RECEIVER | LEGAL_MGR | AUDITOR | VENDOR_USER |
|---|---|---|---|---|---|---|---|---|---|---|
| User & System Config | C R U D | - | - | - | - | - | - | - | R | - |
| Org, Dept & Cost Centers | C R U D | R | R | R | R | R | R | R | R | - |
| Approval Policies | C R U D | R | R | R | R | R | - | - | R | - |
| Vendor Onboarding & KYC | C R U | - | - | C R U A | C R U A | R | - | R V (docs) | R | C R U (Self) |
| Vendor Risk & Status | C R U | - | - | R U | C R U A | R | - | - | R | R (Self) |
| Purchase Requisitions | R D | C R U (Dept) | R A (Dept) | R | R A | R | - | - | R | - |
| Budget & Spend Ledger | C R U | R (Dept) | R (Dept) | R | R | C R U A | - | - | R | - |
| Sourcing Events (RFQ/RFP) | C R U D | - | - | C R U A | C R U A | R | - | R | R | V (Invited) |
| Vendor Sealed Bids | R | - | - | R (Post-Close) | R (Post-Close) | - | - | - | R | C R U (Self, Bid Window) |
| Purchase Orders | C R U D | R (Dept) | R (Dept) | C R U | C R U A | R | R | R | R | R A (Acknowledged) |
| Goods Receipt & Inspection | C R U | - | - | R | R | R | C R U A | - | R | R (Self POs) |
| Invoices & 3-Way Match | C R U | - | - | R | R | C R U A | R | - | R | C R (Self) |
| Contracts & Versioning | C R U D | - | - | R | R | R | - | C R U A | R | R (Self) |
| Supplier Scorecards | C R U | - | - | R U | C R U A | R | R | - | R | R (Self Summary) |
| Audit Logs & Exports | R | - | - | - | - | - | - | - | R | - |

---

## 3. Object-Level Scoping Rules

- **Requester**: Restricted to items created by user or owned by user's assigned Department / Cost Center.
- **Vendor User**: Enforced at database selector level (`Vendor.objects.filter(id=user.vendor_id)`). Bids from competing vendors return `403 Forbidden` / filtered out at ORM query level.
- **Approver**: Restricted to requisitions/POs currently assigned in approval chain or department scope.
- **Auditor**: Global read-only access; state-changing endpoints return `403 Forbidden`.

---

## 4. Procurement Executive — Sourcing & Vendor Governance (action level)

Enforced server-side in `apps/sourcing/permissions.py`, `apps/vendors/permissions.py`, `apps/scorecards/permissions.py`, and re-checked in the services for governance status changes and risk assessments.

| Action | Endpoint (UI / API) | PROC_EXEC | PROC_MGR | FINANCE_AP | AUDITOR | LEGAL_MGR | VENDOR_USER | Others |
|---|---|---|---|---|---|---|---|---|
| View RFQ/RFP register & event | `/sourcing-events/`, `GET /api/v1/sourcing-events/events/` | ✔ | ✔ | R | R | R | Invited & published only (API) | 403 |
| Create / edit draft event | `/sourcing-events/create/`, `/edit/`, `POST/PATCH events/` | ✔ | ✔ | – | – | – | – | 403 |
| Invite / publish / close / cancel / answer clarification | `/sourcing-events/{id}/actions/*`, `POST events/{id}/…` | ✔ | ✔ | – | – | – | – | 403 |
| Read bids (after close only; commercial at COMMERCIAL_REVIEW) | event detail, `events/{id}/bids/`, `bids/` | ✔ | ✔ | – | R | – | Own bids only | none |
| Technical / commercial scoring, negotiation notes | `actions/technical-score`, `commercial-score`, `negotiation-note` | ✔ | ✔ | – | – | – | – | 403 |
| Recommend award | `actions/recommend-award` | ✔ | ✔ | – | – | – | – | 403 |
| Approve / reject award | `actions/approve-award`, `reject-award` | **✘** | ✔ | – | – | – | – | 403 |
| Generate PO from award | `actions/generate-po` | ✔ | ✔ | – | – | – | – | 403 |
| Withdraw bid (before close) | `POST /api/v1/sourcing-events/bids/{id}/withdraw/` | – | – | – | – | – | Own bid | 403/404 |
| Vendor governance pages | `/vendors/`, `/vendors/governance/`, `/vendors/onboarding/`, `/vendors/{id}/` | ✔ | ✔ | R | R | – | – (portal only) | 403 |
| Register vendor | `/vendors/create/`, `/vendors/htmx/register/` | ✔ | ✔ | – | – | – | – | 403 |
| Start KYC review; **activate vendor** (approve) | `/vendors/{id}/kyc/start-review/`, `/kyc/approve/`, `/vendors/{id}/htmx/kyc/*` | ✔ | ✔ | – | – | **403** | – | 403 |
| Verify / reject KYC document (reason required); request corrections | `/vendors/{id}/kyc/verify-document/`, `/reject-document/`, `/request-changes/` | ✔ | ✔ | – | – | ✔ | – | 403 |
| KYC activation gates (server-side, `approve_vendor_service`) | status SUBMITTED/KYC_REVIEW, ≥1 document, mandatory types present (`VENDOR_MANDATORY_KYC_DOC_TYPES`), every current document VERIFIED, none rejected/pending | applies to all roles |
| Record risk assessment (level + flags) | `/vendors/{id}/risk/`, `POST /api/v1/vendors/{id}/risk/` | ✔ | ✔ | – | – | – | – | 403 |
| Hold / release hold / reject registration | `/vendors/{id}/status/`, `POST …/set-status/` | ✔ | ✔ | – | – | – | – | 403 |
| Suspend (blacklist) / reinstate | same | **✘** | ✔ | – | – | – | – | 403 |
| Download vendor document | `/vendors/{id}/documents/{doc}/download/`, API `…/documents/{doc}/download/` | ✔ | ✔ | R | R | – | Own vendor only | 403/404 |
| Supplier scorecards (view / calculate) | `/scorecards/`, `/scorecards/calculate/`, `/api/v1/scorecards/` | ✔ / ✔ | ✔ / ✔ | R | R | – | Own (API) | Stores R; others none |

Object-level rules: vendor users are scoped by `user.vendor_id` in every queryset (cross-vendor IDs return 404). Draft events are never exposed to vendors, even invited ones. Status fields are read-only in every serializer; state changes go only through explicit POST actions backed by transition tables (`SOURCING_TRANSITIONS`, `VENDOR_GOVERNANCE_TRANSITIONS`).

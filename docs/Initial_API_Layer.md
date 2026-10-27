# ProcureSphere 360 — Initial API Layer Report

**Document Reference:** DOC-API-PS360-D30  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Target Milestone:** Days 16–30 Checkpoint — Initial REST API Layer & Documentation  
**Issuing Authority:** HPE Team  
**Development Partner:** VPD Technologies Pvt. Ltd.  
**Repository:** `https://github.com/VPDTechnologies/ProcureSphere_360`  
**Assessment Date:** 2026-10-26  
**Status:** Verification-Ready Documentation  

---

## 1. Executive Summary

This report documents the **Initial REST API Layer** delivered in ProcureSphere 360 for the Days 16–30 checkpoint.

The API architecture is built on **Django REST Framework (DRF 3.14+)**, fully versioned under the `/api/v1/` route prefix, strictly authenticated via session and token mechanisms, enforced with server-side Role-Based Access Control (RBAC), and documented via automated OpenAPI 3.0 schema generation (`drf-spectacular`) mounted at `/api/docs/` (Interactive Swagger UI) and `/api/schema/` (OpenAPI JSON/YAML).

Every state-changing operation maps to explicit POST action endpoints backed by domain services inside atomic database transactions (`@transaction.atomic`), rejecting raw, unvalidated `PATCH status` mutations.

---

## 2. API Architecture & Standards

### 2.1 Standardized Request & Response Envelope

1. **Successful Responses (`200 OK`, `201 Created`):**
   Standard DRF serialized JSON representation with ISO 8601 timestamps and decimal string representations for currency amounts:
   ```json
   {
       "id": "e9b5f3a1-7c2a-4f8a-9e12-3b4c5d6e7f8a",
       "pr_number": "PR-2026-00042",
       "title": "Datacenter Rack Server Hardware",
       "total_amount": "75000.00",
       "status": "APPROVED",
       "created_at": "2026-10-26T08:30:00Z"
   }
   ```

2. **Standardized Error Envelope (`400`, `401`, `403`, `404`, `500`):**
   Consistent structured error response payload ensuring client predictability:
   ```json
   {
       "error": {
           "code": "PERMISSION_DENIED",
           "message": "Only Finance / AP personnel can execute invoice payments.",
           "details": {
               "role": "REQUESTER",
               "required_role": "FINANCE_AP"
           }
       }
   }
   ```

### 2.2 Authentication & Authorization Mechanisms

- **Session Authentication:** Handled via `/api/v1/auth/login/` and `/api/v1/auth/logout/` with secure HTTP-only cookies and live CSRF synchronization.
- **JWT / Token Authentication:** Provided via `rest_framework_simplejwt` for programmatic machine-to-machine integrations.
- **Login Brute-Force Throttling:** Governed by `django-axes` (locks accounts after 5 consecutive failures within lockout window).
- **Backend Authorization:** Enforced via custom DRF `BasePermission` classes tied to `apps.accounts.models.Role`:
  - `CanLegalReviewContract`: Restricts contract legal approvals to `LEGAL_MGR` and `SUPER_ADMIN`.
  - `CanBusinessApproveContract`: Restricts contract business approvals to `PROC_MGR`, `DEPT_APPROVER`, `SUPER_ADMIN`.
  - `CanManageSupplierInvoice`: Restricts payment execution and exception resolution to `FINANCE_AP` and `SUPER_ADMIN`.
  - `CanSourcingAward`: Restricts award approval to `PROC_MGR` and `SUPER_ADMIN`.
  - `IsVendorUserOwner`: Object-level tenancy guard restricting vendor users strictly to their own records (`vendor_id == user.vendor_id`).

---

## 3. Implemented API Surface Matrix (`/api/v1/`)

The following matrix documents the complete implemented API surface as routed in `src/config/urls.py` and domain `api_urls.py`:

| Domain App | Endpoint Route | HTTP Methods | Allowed Roles | Summary / Action Description | Implemented Status |
|:---|:---|:---:|:---|:---|:---:|
| **Auth** | `/api/v1/auth/login/` | `POST` | Anonymous | Authenticates credentials; establishes session and CSRF cookie. | **IMPLEMENTED** |
| **Auth** | `/api/v1/auth/logout/` | `POST` | Authenticated | Clears session cookie and invalidates auth tokens. | **IMPLEMENTED** |
| **Accounts** | `/api/v1/accounts/users/` | `GET`, `POST` | `SUPER_ADMIN` | List users; create new employee/vendor users with role scoping. | **IMPLEMENTED** |
| **Accounts** | `/api/v1/accounts/users/<uuid:id>/` | `GET`, `PUT`, `PATCH` | `SUPER_ADMIN` | Retrieve or update user attributes, assigned department, and role. | **IMPLEMENTED** |
| **Organization** | `/api/v1/organization/organizations/` | `GET`, `POST` | `SUPER_ADMIN`, All (R) | Manage legal entities and parent organizations. | **IMPLEMENTED** |
| **Organization** | `/api/v1/organization/departments/` | `GET`, `POST` | `SUPER_ADMIN`, All (R) | Manage operating departments and organizational codes. | **IMPLEMENTED** |
| **Organization** | `/api/v1/organization/cost-centers/` | `GET`, `POST` | `SUPER_ADMIN`, All (R) | Cost center registry, department linkage, cost center manager mapping. | **IMPLEMENTED** |
| **Organization** | `/api/v1/organization/fiscal-periods/` | `GET`, `POST` | `SUPER_ADMIN`, All (R) | Accounting fiscal calendar periods and quarter closing controls. | **IMPLEMENTED** |
| **Approvals** | `/api/v1/approvals/policies/` | `GET`, `POST` | `SUPER_ADMIN` | Define approval policy rule headers, module targets, and limits. | **IMPLEMENTED** |
| **Approvals** | `/api/v1/approvals/policies/<uuid:id>/` | `GET`, `PUT`, `DELETE`| `SUPER_ADMIN` | Policy configuration, step management, and limit updates. | **IMPLEMENTED** |
| **Approvals** | `/api/v1/approvals/actions/` | `GET` | All Roles (R) | Read-only immutable audit trail of approval submissions and decisions. | **IMPLEMENTED** |
| **Vendors** | `/api/v1/vendors/` | `GET`, `POST` | `PROC_EXEC`, `PROC_MGR`, `SUPER_ADMIN` | List and register vendors; query vendor risk and governance status. | **IMPLEMENTED** |
| **Vendors** | `/api/v1/vendors/<uuid:id>/` | `GET`, `PUT` | Procurement, Vendor (Self) | Retrieve or update vendor profile details and banking information. | **IMPLEMENTED** |
| **Vendors** | `/api/v1/vendors/<uuid:id>/set-status/` | `POST` | `PROC_MGR`, `SUPER_ADMIN` | Governance state transition: Hold, Release Hold, Suspend, Blacklist. | **IMPLEMENTED** |
| **Vendors** | `/api/v1/vendors/<uuid:id>/documents/` | `GET`, `POST` | Procurement, Vendor (Self) | Upload KYC compliance documents; validate file MIME types and size. | **IMPLEMENTED** |
| **Requisitions**| `/api/v1/requisitions/` | `GET`, `POST` | `REQUESTER`, `DEPT_APPROVER`, `SUPER_ADMIN` | List scoped PRs; create multi-line purchase requisitions in DRAFT. | **IMPLEMENTED** |
| **Requisitions**| `/api/v1/requisitions/<uuid:id>/submit/` | `POST` | `REQUESTER` | Submit PR; validates budget availability; creates `BudgetReservation`. | **IMPLEMENTED** |
| **Requisitions**| `/api/v1/requisitions/<uuid:id>/approve/` | `POST` | `DEPT_APPROVER`, `PROC_MGR` | Approve PR for active approval step; logs `ApprovalAction`. | **IMPLEMENTED** |
| **Requisitions**| `/api/v1/requisitions/<uuid:id>/reject/` | `POST` | `DEPT_APPROVER`, `PROC_MGR` | Reject PR with comments; releases budget reservation back to pool. | **IMPLEMENTED** |
| **Sourcing** | `/api/v1/sourcing-events/` | `GET`, `POST` | `PROC_EXEC`, `PROC_MGR`, `SUPER_ADMIN` | Create and list RFQ/RFP sourcing events with bid submission windows. | **IMPLEMENTED** |
| **Sourcing** | `/api/v1/sourcing-events/bids/` | `GET`, `POST` | Procurement, Vendor (Self) | Submit sealed bid responses; enforce sealed-bid privacy until close. | **IMPLEMENTED** |
| **Sourcing** | `/api/v1/sourcing-events/<uuid:id>/award/`| `POST` | `PROC_MGR`, `SUPER_ADMIN` | Formal award decision; generates award approval audit record. | **IMPLEMENTED** |
| **Orders** | `/api/v1/purchase-orders/` | `GET`, `POST` | `PROC_EXEC`, `PROC_MGR`, `SUPER_ADMIN` | List POs; generate PO from approved Requisition or Sourcing Award. | **IMPLEMENTED** |
| **Orders** | `/api/v1/purchase-orders/<uuid:id>/acknowledge/` | `POST` | `VENDOR_USER` | Vendor PO acknowledgement; transitions status to `ACKNOWLEDGED`. | **IMPLEMENTED** |
| **Orders** | `/api/v1/purchase-orders/<uuid:id>/amend/` | `POST` | `PROC_EXEC`, `PROC_MGR` | Issue PO change order; snapshots prior version; updates lines in-place. | **IMPLEMENTED** |
| **Receipts** | `/api/v1/receipts/` | `GET`, `POST` | `STORES_RECEIVER`, `SUPER_ADMIN` | Record Goods Receipt Note (GRN) physical shipment dock intake. | **IMPLEMENTED** |
| **Receipts** | `/api/v1/receipts/<uuid:id>/inspect/` | `POST` | `STORES_RECEIVER`, `SUPER_ADMIN` | Quality inspection gate; record accepted/rejected quantities. | **IMPLEMENTED** |
| **Invoices** | `/api/v1/invoices/` | `GET`, `POST` | `FINANCE_AP`, Vendor (Self) | Intake supplier invoices; enforce duplicate invoice number detection. | **IMPLEMENTED** |
| **Invoices** | `/api/v1/invoices/<uuid:id>/match/` | `POST` | `FINANCE_AP`, `SUPER_ADMIN` | Execute automated 3-way match engine against PO and Goods Receipt. | **IMPLEMENTED** |
| **Invoices** | `/api/v1/invoices/<uuid:id>/pay/` | `POST` | `FINANCE_AP`, `SUPER_ADMIN` | Execute payment settlement; post actual spend entry to `SpendLedger`. | **IMPLEMENTED** |
| **Invoices** | `/api/v1/invoices/exceptions/<uuid:id>/resolve/` | `POST` | `FINANCE_AP`, `SUPER_ADMIN` | Resolve price/quantity tolerance exception with audit notes. | **IMPLEMENTED** |
| **Contracts** | `/api/v1/contracts/` | `GET`, `POST` | `LEGAL_MGR`, `PROC_MGR`, `SUPER_ADMIN` | Author contracts, define terms, SLA obligations, and milestones. | **IMPLEMENTED** |
| **Contracts** | `/api/v1/contracts/<uuid:id>/legal_approve/` | `POST` | `LEGAL_MGR`, `SUPER_ADMIN` | Legal review sign-off; advances contract to `BUSINESS_APPROVAL`. | **IMPLEMENTED** |
| **Contracts** | `/api/v1/contracts/<uuid:id>/business_approve/` | `POST` | `PROC_MGR`, `DEPT_APPROVER` | Business approval sign-off; activates contract; blocks bypass. | **IMPLEMENTED** |
| **Budgets** | `/api/v1/budgets/` | `GET`, `POST` | `FINANCE_AP`, `SUPER_ADMIN` | Cost center budget management; allocation, available funds balance. | **IMPLEMENTED** |
| **Budgets** | `/api/v1/budgets/ledger/` | `GET` | `FINANCE_AP`, `AUDITOR` | Append-only financial ledger entries (`RESERVED`, `COMMITTED`, `ACTUAL`). | **IMPLEMENTED** |
| **Notifications**| `/api/v1/notifications/` | `GET` | Authenticated (Self) | User-scoped notification list; filters unread items and alerts. | **IMPLEMENTED** |
| **Notifications**| `/api/v1/notifications/summary/` | `GET` | Authenticated (Self) | Live badge counters: total notifications, unread count, approval alerts. | **IMPLEMENTED** |
| **Audit** | `/api/v1/audit/logs/` | `GET` | `AUDITOR`, `SUPER_ADMIN` | Filtered append-only audit trail search (actor, action, target, timestamp). | **IMPLEMENTED** |
| **Monitoring** | `/health`, `/readyz` | `GET` | Public / Monitoring | Liveness and readiness probes verifying database and redis connectivity. | **IMPLEMENTED** |

---

## 4. OpenAPI / Swagger Documentation Verification

- **Schema Endpoint:** `/api/schema/` (returns complete OpenAPI 3.0 YAML/JSON specification).
- **Interactive Documentation UI:** `/api/docs/` (Swagger UI).
- **Verification Evidence:**
  The schema and Swagger UI are mounted in `src/config/urls.py` via `drf-spectacular`:
  ```python
  path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
  path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
  ```
  Verified functional via `tests/test_health.py` and client HTTP requests.

---

## 5. API Testing Evidence & Execution Results

Automated API test coverage across authentication, positive authorization, negative authorization, and payload validation is established in the test reports under `reports/`:

| Module Test Suite | Report Location | Tests Executed | Passed | Failed | Key Findings |
|:---|:---|:---:|:---:|:---:|:---|
| **Department Approvals API** | `reports/approvals/DEPARTMENT_APPROVAL_API_TESTING_REPORT.md` | 59 | 58 | 1* | Comprehensive ViewSet and action route coverage (*1 expected security assertion). |
| **Finance & Invoicing API** | `reports/finance/FINANCE_API_TEST_RESULTS.md` | 42 | 42 | 0 | 3-way match, tolerance calculation, and payment authorization verified. |
| **Vendor Onboarding API** | `reports/vendor/VENDOR_API_TEST_RESULTS.md` | 38 | 38 | 0 | Self-service intake, document uploads, and vendor tenancy scoping verified. |
| **Auditor API Suite** | `reports/auditor/AUDITOR_API_TEST_RESULTS.md` | 24 | 24 | 0 | Read-only enforcement; all mutation attempts cleanly return HTTP 403. |

---

## 6. Incomplete & Planned API Endpoints (Roadmap)

The following endpoints are identified as **PLANNED** for subsequent phases:

1. **Bulk Approval Batch API (`/api/v1/approvals/bulk-approve/`):**
   - *Status:* Planned for Phase 2.
   - *Current State:* Approvals are currently executed per individual requisition ID to ensure distinct audit logging.
2. **External ERP Webhook Connectors (`/api/v1/integrations/sap/`, `/oracle/`):**
   - *Status:* Planned for Phase 3 enterprise integrations.
   - *Current State:* Internal REST API is complete; third-party ERP webhook listeners are not in Days 16–30 scope.

# ProcureSphere 360 — Core Masters Implementation Report

**Document Reference:** DOC-MAS-PS360-D30  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Target Milestone:** Days 16–30 Checkpoint — Core Masters & Baseline Architecture  
**Issuing Authority:** HPE Team  
**Development Partner:** VPD Technologies Pvt. Ltd.  
**Repository:** `https://github.com/VPDTechnologies/ProcureSphere_360`  
**Assessment Date:** 2026-10-26  
**Status:** Verification-Ready Documentation  

---

## 1. Executive Summary

This document provides a comprehensive technical review of the **Core Masters** implemented across the ProcureSphere 360 enterprise ERP codebase as of the Days 16–30 checkpoint. 

In strict adherence to project non-negotiables, all core master entities are backed by **real PostgreSQL normalized relational tables**, with full foreign key constraints, unique and check constraints, field-level validations, database migrations, and server-side Role-Based Access Control (RBAC). No mock data layers, in-memory stubs, or unpersisted UI states exist in the core domain models.

---

## 2. Core Masters Architecture & Domain Model Registry

The core master records are partitioned across dedicated Django domain apps under `src/apps/`, adhering to domain-driven boundaries:

```
src/apps/
├── accounts/         # Identity, RBAC Roles, User Profiles, Auth Security
├── organization/     # Enterprise Entity, Departments, Cost Centers, Fiscal Periods
├── approvals/        # Approval Policy Rules, Approval Tiers, Policy Steps
├── vendors/          # Vendor Master, Categories, Contacts, KYC Documents, Risk Records
├── budgets/          # Cost Center Budgets, Allocations, Spend Ledgers
├── contracts/        # Contract Master, Version Snapshots, Clauses, Milestones
└── sourcing/         # Sourcing Event Headers, Evaluation Weighting Masters
```

---

## 3. Implemented Master Entities & Data Contracts

### 3.1 Organization & Cost Center Master (`src/apps/organization`)

The organizational structure defines legal entities, accounting departments, budget-owning cost centers, and fiscal accounting periods.

| Model | Table Name | Key Fields & Types | Constraints & Integrity | Domain Role |
|:---|:---|:---|:---|:---|
| **`Organization`** | `organization_organization` | `id` (UUID, PK)<br>`name` (CharField 255)<br>`code` (CharField 50, Unique)<br>`tax_identifier` (CharField 100)<br>`address` (TextField)<br>`is_active` (BooleanField) | `unique=True` on `code` | Root enterprise legal entity. Represents the contracting and accounting parent company. |
| **`Department`** | `organization_department` | `id` (UUID, PK)<br>`organization` (FK -> Organization)<br>`name` (CharField 150)<br>`code` (CharField 50, Unique)<br>`description` (TextField) | `on_delete=models.CASCADE`<br>`unique=True` on `code` | Operating department (e.g., Engineering, IT Infrastructure, Finance, Operations). |
| **`CostCenter`** | `organization_costcenter` | `id` (UUID, PK)<br>`department` (FK -> Department)<br>`code` (CharField 50, Unique)<br>`name` (CharField 150)<br>`manager` (FK -> accounts.User, Nullable) | `on_delete=models.CASCADE` on department<br>`on_delete=models.SET_NULL` on manager<br>`unique=True` on `code` | Primary financial spend unit. Holds budget allocations and is referenced by PRs, POs, and Invoices. |
| **`FiscalPeriod`** | `organization_fiscalperiod` | `id` (UUID, PK)<br>`organization` (FK -> Organization)<br>`year` (PositiveInteger)<br>`period_number` (PositiveInteger)<br>`name` (CharField 50)<br>`start_date` (DateField)<br>`end_date` (DateField)<br>`is_closed` (BooleanField) | `unique_together = ('organization', 'year', 'period_number')`<br>Chronological date range checks | Defines budget accounting cycles (e.g. FY2026-Q1). Precludes spend against closed accounting periods. |

---

### 3.2 Identity & Role-Based Access Control Master (`src/apps/accounts`)

The access control model strictly enforces the 10 PRD-defined system personas without reliance on generic Django groups or unauthenticated bypasses.

| Model | Table Name | Key Fields & Types | Constraints & Integrity | Domain Role |
|:---|:---|:---|:---|:---|
| **`Role`** | `accounts_role` | `id` (UUID, PK)<br>`code` (CharField 50, Unique)<br>`name` (CharField 100)<br>`description` (TextField)<br>`created_at` (DateTimeField) | `unique=True` on `code`<br>Strict choice validation against 10 system roles | Canonical role registry: `SUPER_ADMIN`, `REQUESTER`, `DEPT_APPROVER`, `PROC_EXEC`, `PROC_MGR`, `FINANCE_AP`, `STORES_RECEIVER`, `LEGAL_MGR`, `AUDITOR`, `VENDOR_USER`. |
| **`User`** | `accounts_user` | `id` (UUID, PK)<br>`email` (EmailField, Unique)<br>`username` (CharField, Email mirrored)<br>`password` (PBKDF2 SHA-256)<br>`role` (FK -> Role, Nullable)<br>`department` (FK -> Department, Nullable)<br>`vendor` (FK -> Vendor, Nullable)<br>`must_change_password` (BooleanField)<br>`is_active` (BooleanField) | `USERNAME_FIELD = 'email'`<br>`unique=True` on `email`<br>`on_delete=models.SET_NULL` for foreign keys | Custom User model replacing default integer Django User. Supports object-level tenancy scoping (internal vs vendor). |

---

### 3.3 Vendor Master & Governance (`src/apps/vendors`)

The vendor master governs commercial partners, vendor compliance, bank details, and risk ratings.

| Model | Table Name | Key Fields & Types | Constraints & Integrity | Domain Role |
|:---|:---|:---|:---|:---|
| **`VendorCategory`** | `vendors_vendorcategory` | `id` (UUID, PK)<br>`code` (CharField 50, Unique)<br>`name` (CharField 100)<br>`description` (TextField) | `unique=True` on `code` | Categorization taxonomy (IT Hardware, Software, Logistics, Facilities). |
| **`Vendor`** | `vendors_vendor` | `id` (UUID, PK)<br>`vendor_number` (CharField 50, Unique)<br>`legal_name` (CharField 255)<br>`tax_identification_number` (CharField 100)<br>`category` (FK -> VendorCategory)<br>`status` (CharField 30)<br>`event_eligibility_status` (CharField 30)<br>`is_event_eligible` (BooleanField)<br>`address` (TextField)<br>`bank_name` (CharField)<br>`bank_account_number` (CharField)<br>`created_by` (FK -> User) | `unique=True` on `vendor_number`<br>`on_delete=models.PROTECT` on category<br>Status validation against transition table | Primary vendor master record. Manages state machine: `DRAFT` -> `SUBMITTED` -> `KYC_REVIEW` -> `APPROVED` -> `ACTIVE` <-> `ON_HOLD` / `SUSPENDED` / `BLACKLISTED`. |
| **`VendorContact`** | `vendors_vendorcontact` | `id` (UUID, PK)<br>`vendor` (FK -> Vendor)<br>`contact_type` (CharField 30)<br>`name` (CharField 100)<br>`email` (EmailField)<br>`phone` (CharField 30)<br>`is_primary` (BooleanField) | `on_delete=models.CASCADE` on vendor | Authorized vendor personnel directory (Primary, Finance, Legal, Technical). |
| **`VendorDocument`** | `vendors_vendordocument` | `id` (UUID, PK)<br>`vendor` (FK -> Vendor)<br>`document_type` (CharField 50)<br>`title` (CharField 255)<br>`file` (FileField)<br>`expiry_date` (DateField, Nullable)<br>`is_verified` (BooleanField)<br>`verified_by` (FK -> User, Nullable) | File validation (MIME + extension)<br>Storage outside web root<br>`on_delete=models.CASCADE` | KYC compliance repository (Certificate of Inc., Tax Registration, Banking Proof, Insurance). |
| **`VendorRiskRecord`** | `vendors_vendorriskrecord` | `id` (UUID, PK)<br>`vendor` (FK -> Vendor)<br>`risk_level` (CharField 20)<br>`assessment_notes` (TextField)<br>`assessed_by` (FK -> User)<br>`created_at` (DateTimeField) | Choice validation: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` | Periodic risk assessment audits driving vendor governance decisions. |

---

### 3.4 Approval Policy Master Engine (`src/apps/approvals`)

The rule engine routes purchase requisitions and commercial commitments across approval chains.

| Model | Table Name | Key Fields & Types | Constraints & Integrity | Domain Role |
|:---|:---|:---|:---|:---|
| **`ApprovalPolicy`** | `approvals_approvalpolicy` | `id` (UUID, PK)<br>`name` (CharField 150)<br>`module` (CharField 30)<br>`min_amount` (DecimalField 14,2)<br>`max_amount` (DecimalField 14,2, Nullable)<br>`department` (FK -> Department, Nullable)<br>`is_active` (BooleanField) | Choice validation on `module` (`PR`, `PO`, `SOURCING`, `INVOICE`, `CONTRACT`) | Configurable approval rule headers triggered by financial thresholds and department scope. |
| **`ApprovalStep`** | `approvals_approvalstep` | `id` (UUID, PK)<br>`policy` (FK -> ApprovalPolicy)<br>`step_number` (PositiveInteger)<br>`approver_role` (FK -> Role)<br>`specific_approver` (FK -> User, Nullable)<br>`approval_limit` (DecimalField 14,2, Nullable) | `unique_together = ('policy', 'step_number')`<br>`ordering = ['step_number']` | Sequential steps in an approval chain (e.g. Step 1: Dept Approver, Step 2: Procurement Manager). |
| **`ApprovalAction`** | `approvals_approvalaction` | `id` (UUID, PK)<br>`target_model` (CharField 50)<br>`target_object_id` (UUIDField)<br>`step` (FK -> ApprovalStep, Nullable)<br>`actor` (FK -> User)<br>`action` (CharField 30)<br>`comments` (TextField)<br>`created_at` (DateTimeField) | Immutable append-only logging | Traceable audit record of every submission, approval, rejection, or delegation decision. |

---

### 3.5 Budget & Financial Spend Ledger Master (`src/apps/budgets`)

The budget engine enforces strict financial controls over company spend, preventing maverick buying.

| Model | Table Name | Key Fields & Types | Constraints & Integrity | Domain Role |
|:---|:---|:---|:---|:---|
| **`Budget`** | `budgets_budget` | `id` (UUID, PK)<br>`cost_center` (FK -> CostCenter)<br>`fiscal_period` (FK -> FiscalPeriod)<br>`allocated_amount` (DecimalField 14,2)<br>`reserved_amount` (DecimalField 14,2)<br>`committed_amount` (DecimalField 14,2)<br>`actual_amount` (DecimalField 14,2)<br>`allow_overspend` (BooleanField) | `unique_together = ('cost_center', 'fiscal_period')`<br>All financial amounts use `DecimalField` (never float) | Tri-state accounting ledger maintaining real-time spend states (Allocated vs Reserved vs Committed vs Actual). |
| **`BudgetReservation`** | `budgets_budgetreservation` | `id` (UUID, PK)<br>`budget` (FK -> Budget)<br>`requisition` (FK -> PurchaseRequisition)<br>`amount` (DecimalField 14,2)<br>`status` (CharField 30) | Status choices: `RESERVED`, `RELEASED`, `COMMITTED` | Encumbers available budget upon PR approval; prevents over-allocation. |
| **`SpendLedger`** | `budgets_spendledger` | `id` (UUID, PK)<br>`budget` (FK -> Budget)<br>`entry_type` (CharField 30)<br>`amount` (DecimalField 14,2)<br>`reference_number` (CharField 100)<br>`description` (TextField)<br>`created_at` (DateTimeField) | Choices: `ALLOCATION`, `RESERVATION`, `COMMITMENT`, `ACTUAL`, `REVERSAL` | Double-entry style append-only audit ledger tracking every fiscal state transition. |

---

### 3.6 Contract Master (`src/apps/contracts`)

The contract repository maintains active agreements, milestones, legal obligations, and alerts.

| Model | Table Name | Key Fields & Types | Constraints & Integrity | Domain Role |
|:---|:---|:---|:---|:---|
| **`Contract`** | `contracts_contract` | `id` (UUID, PK)<br>`contract_number` (CharField 50, Unique)<br>`title` (CharField 255)<br>`vendor` (FK -> Vendor)<br>`status` (CharField 30)<br>`contract_value` (DecimalField 14,2)<br>`start_date` (DateField)<br>`end_date` (DateField)<br>`renewal_notice_days` (PositiveInteger)<br>`contract_owner` (FK -> User) | `unique=True` on `contract_number`<br>`on_delete=models.PROTECT` on vendor | Master agreement entity. Lifecycle states: `DRAFT` -> `LEGAL_REVIEW` -> `BUSINESS_APPROVAL` -> `ACTIVE` -> `RENEWAL_DUE` -> `RENEWED` / `EXPIRED` / `TERMINATED`. |
| **`ContractVersion`** | `contracts_contractversion` | `id` (UUID, PK)<br>`contract` (FK -> Contract)<br>`version_number` (PositiveInteger)<br>`contract_value` (DecimalField 14,2)<br>`start_date` (DateField)<br>`end_date` (DateField)<br>`amendment_summary` (TextField)<br>`created_by` (FK -> User) | `unique_together = ('contract', 'version_number')` | Preserved change-order history. Guarantees prior terms are never overwritten. |
| **`ContractMilestone`** | `contracts_contractmilestone` | `id` (UUID, PK)<br>`contract` (FK -> Contract)<br>`title` (CharField 255)<br>`due_date` (DateField)<br>`amount` (DecimalField 14,2)<br>`is_completed` (BooleanField) | Chronological validation against contract term | Deliverable and milestone schedule driving Celery alert notifications. |
| **`ContractObligation`** | `contracts_contractobligation` | `id` (UUID, PK)<br>`contract` (FK -> Contract)<br>`title` (CharField 255)<br>`responsible_party` (CharField 50)<br>`due_date` (DateField)<br>`is_fulfilled` (BooleanField) | Party choices: `VENDOR`, `INTERNAL` | Compliance and SLA obligation tracking. |

---

## 4. Database Migrations & Integrity Verification

### 4.1 Migration Lineage Across Core Masters
All migrations apply in clean topological order. Migration checks via `python manage.py makemigrations --check --dry-run` demonstrate zero pending schema drifts:

| Domain App | Initial Migration | Latest Migration Applied in Code | Key Schema Capabilities Added |
|:---|:---:|:---:|:---|
| `accounts` | `0001_initial` | `0003_user_must_change_password` | UUID PKs, Custom User, Role choices, password security flags. |
| `organization` | `0001_initial` | `0001_initial` | Organization, Department, CostCenter, FiscalPeriod with unique constraints. |
| `approvals` | `0001_initial` | `0001_initial` | Multi-tier policies, step ordering, immutable approval action logs. |
| `vendors` | `0001_initial` | `0006_vendor_event_eligibility_status_and_more` | KYC document model, risk records, credential dispatch, event eligibility. |
| `budgets` | `0001_initial` | `0001_initial` | Tri-state budget allocations, reservations, spend ledger audit tables. |
| `contracts` | `0001_initial` | `0003_alter_contractalert_alert_type` | Contract versioning, milestone alerts, obligations, legal review tracking. |

### 4.2 Constraints & Foreign Key Protection Rules
1. **Financial References Protected:** All transactional foreign keys referencing `Vendor`, `CostCenter`, or `Organization` enforce `on_delete=models.PROTECT` to prevent accidental deletion of referenced masters.
2. **Safe Cascades for Dependent Lines:** Line-item tables (`PRLine`, `POLine`, `InvoiceLine`, `ReceiptLine`) cascade on master deletion only when in `DRAFT` status; once approved or issued, status transition guards forbid master deletion.
3. **Audit Immutability:** `AuditLog`, `ApprovalAction`, and `SpendLedger` records have no delete or update endpoints in views or APIs; their managers and services only perform `create()`.

---

## 5. Server-Side RBAC Enforcement on Core Masters

In strict accordance with Phase 1 critical backend fixes, access control on core master data is enforced server-side:

```
[Incoming Request]
       │
       ▼
[AuthenticationMiddleware] ──> Anonymous? ──> 401 / Redirect Login
       │
       ▼
[@super_admin_required] (Superadmin Views)
       │
       ├── User has Role.SUPER_ADMIN or is_superuser ──> Allow Action
       └── Non-Superadmin (Requester, Auditor, etc.)  ──> 403 Forbidden
```

- **User & Org Management (`src/apps/core/superadmin_views.py`):**
  - Decorated with `@super_admin_required`. Requesters, Procurement Executives, Finance AP, and Auditors attempting `GET` or `POST` against `/superadmin/users/`, `/superadmin/organizations/`, `/superadmin/budgets/`, `/superadmin/policies/` receive immediate `HTTP 403 Forbidden` responses.
- **Auditor Visibility:**
  - Auditors (`Role.AUDITOR`) possess system-wide `GET` / read-only visibility into Organization, Department, Cost Center, and Budget data via dedicated dashboard routes (`/audit/logs/`, `/reports/`), while state-changing `POST`/`PUT`/`DELETE` calls are blocked.

---

## 6. Remaining Gaps & Roadmap Alignment

| Item | Current Implementation Status | Checkpoint Impact | Planned Remediation Phase |
|:---|:---:|:---:|:---:|
| **Multi-Currency Master** | System operates under base currency (`USD`) assumption per `docs/assumptions.md`. | Single-currency ERP operations fully supported; FX exchange rate table not present. | Phase 3 (Multi-currency ERP extension). |
| **Commodity Code Hierarchy** | Standard text-based category code (`CAT-HW`, `CAT-SW`) used on `VendorCategory` and `PRLine`. | Fully functional for procurement categories; standard UNSPSC/eCl@ss trees not modeled. | Phase 3 (Advanced Spend Categorization). |
| **Cost Center Hierarchies** | Cost centers link to a single `Department`. Rollup parent cost centers are flat. | Meets PRD requirement for department-level budget scoping. | Future enhancement. |

---

## 7. Verification Evidence & Test References

The integrity of all core master entities and validations is verified by automated pytest test suites:
- Organization & Department Management: [`tests/test_superadmin_department_management.py`](file:///c:/Users/Dell/Desktop/ProcureSphere_360/tests/test_superadmin_department_management.py) (**7/7 PASSED**)
- Super Admin Server-Side 403 Enforcement: [`tests/test_phase1_critical_backend_fixes.py`](file:///c:/Users/Dell/Desktop/ProcureSphere_360/tests/test_phase1_critical_backend_fixes.py) (`test_superadmin_views_reject_unauthorized_roles`, **PASSED**)
- Budget Allocation & Reservation Master Integrity: [`tests/test_demo_2_pr_approval_budget.py`](file:///c:/Users/Dell/Desktop/ProcureSphere_360/tests/test_demo_2_pr_approval_budget.py) (**2/2 PASSED**)
- Vendor Master State Transitions: [`tests/test_demo_1_vendor_kyc.py`](file:///c:/Users/Dell/Desktop/ProcureSphere_360/tests/test_demo_1_vendor_kyc.py) (**1/1 PASSED**)

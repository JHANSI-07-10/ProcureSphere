# ProcureSphere 360 — Initial Database Migrations Inventory & Schema Architecture

**Document Reference:** MIG-BASE-PS360-D15  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Target Milestone:** Days 1–15 Database Foundation  
**Database Target:** PostgreSQL 15+  
**Date of Assessment:** 2026-10-12  

---

## 1. Overview & Database Architecture

ProcureSphere 360 is built upon a fully normalized, relational PostgreSQL 15+ database schema designed to support enterprise Source-to-Pay (S2P) and Accounts Payable (AP) workflows. 

### Core Database Design Principles:
1. **Financial Precision:** All monetary amounts, exchange rates, unit prices, and inventory quantities utilize `DecimalField` (e.g., `max_digits=14, decimal_places=2`). Floats are strictly prohibited to prevent IEEE-754 precision errors.
2. **Referential Integrity:** Transactional records reference parent master records using `on_delete=models.PROTECT` to prevent accidental cascading deletion of historical audit evidence.
3. **Immutable Audit Trails:** Audit logs, contract versions, and PO amendments are append-only.
4. **Normalized Schema with Explicit Indexes:** B-tree indexes are defined across all primary foreign keys, document numbers, status fields, and timestamp columns.
5. **Fresh-Database Reproducibility:** All migrations apply cleanly from zero onto an empty PostgreSQL instance without manual intervention or circular dependency deadlocks.

---

## 2. Complete Migration Inventory (Days 1–15 Baseline)

The repository contains 16 domain apps. All initial and subsequent migration files are committed, applied, and verified.

| Django App | Migration File | Description & Core Entities Created | Status |
|:---|:---|:---|:---:|
| `accounts` | `0001_initial`<br>`0002_initial` | Custom `User` model, `Role` model with 10 role choices, and user-role relations. | Applied |
| `organization` | `0001_initial` | `Organization`, `Department`, `CostCenter`, and financial `ApprovalLimit` models. | Applied |
| `approvals` | `0001_initial` | `ApprovalPolicy`, `ApprovalStep`, `ApprovalAction`, and rule routing engine. | Applied |
| `vendors` | `0001_initial`<br>`0002_alter_vendorriskrecord...` | `Vendor`, `VendorContact`, `VendorDocument`, `VendorCategory`, and `VendorRiskRecord`. | Applied |
| `requisitions` | `0001_initial`<br>`0002_alter_purchaserequisition...`<br>`0003_prattachment...` | `PurchaseRequisition`, `PRLine`, and `PRAttachment` (with file validation attributes). | Applied |
| `budgets` | `0001_initial` | `Budget`, `BudgetReservation`, and `SpendLedger` financial commitment ledger. | Applied |
| `sourcing` | `0001_initial`<br>`0002_sourcingevent...`<br>`0003_award_approval...` | `SourcingEvent`, `BidInvite`, `VendorBid`, `BidLine`, `BidEvaluation`, and `AwardDecision`. | Applied |
| `orders` | `0001_initial`<br>`0002_purchaseorder...` | `PurchaseOrder`, `POLine`, `POAmendment`, and `DeliverySchedule`. | Applied |
| `receipts` | `0001_initial`<br>`0002_stockhandoffrecord` | `GoodsReceipt`, `ReceiptLine`, `InspectionRecord`, `RejectionRecord`, and `StockHandoffRecord`. | Applied |
| `invoices` | `0001_initial`<br>`0002_paymentstatus_matchresult`<br>`0003_matchtolerancepolicy` | `SupplierInvoice`, `InvoiceLine`, `MatchResult`, `MatchException`, and `MatchTolerancePolicy`. | Applied |
| `contracts` | `0001_initial`<br>`0002_contractobligation...`<br>`0003_alter_contractalert...` | `Contract`, `ContractVersion`, `ContractMilestone`, `ContractObligation`, and `ContractAlert`. | Applied |
| `scorecards` | `0001_initial`<br>`0002_alter_vendorscorecard...` | `VendorScorecard` composite performance scoring and trend historical records. | Applied |
| `notifications` | `0001_initial`<br>`0002_alter_notification...`<br>`0003_alter_notification...` | `Notification` alert queue (in-app and asynchronous email dispatch). | Applied |
| `reports` | `0001_initial` | `ReportDefinition` and `ScheduledReport` registry. | Applied |
| `audit` | `0001_initial`<br>`0002_alter_auditlog_action...` | Immutable `AuditLog` append-only entity capturing actor, target, before/after states, and IP. | Applied |
| `core` | *(No tables)* | Base abstract models (`TimeStampedModel`), state machine helpers, and middleware. | N/A |

### Supporting Third-Party Migrations:
- **`axes` (0001 to 0010):** Login failure logging, lockout records, and access attempt tracking.
- **`django_celery_beat` (0001 to 0019):** Periodic task scheduler models (`PeriodicTask`, `CrontabSchedule`).
- **`admin`, `auth`, `contenttypes`, `sessions`:** Core Django system support.

---

## 3. Migration Consistency & Zero-Drift Verification

To verify that model definitions in `src/apps/*/models.py` match migration files with zero schema drift, official Django commands were executed:

### Command 1: Drift Verification
```powershell
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```
- **Output:** `No changes detected`
- **Exit Code:** `0`
- **Result:** Confirms all model fields, relationships, constraints, and indexes are fully accounted for in existing migration files.

### Command 2: Migration Status Verification
```powershell
.\.venv\Scripts\python.exe manage.py showmigrations
```
- **Output:** Every migration across all 16 applications is marked `[X]` (applied).
- **Exit Code:** `0`

---

## 4. Key Relational Constraints & Schema Guarantees

1. **Document Number Uniqueness:**
   - Unique constraints on `po_number` in `PurchaseOrder`, `grn_number` in `GoodsReceipt`, `invoice_number` in `SupplierInvoice`, `contract_number` in `Contract`.
   - Sequential document numbers generated inside PostgreSQL atomic transactions (`select_for_update`).
2. **Duplicate Invoice Number Guard:**
   - Multi-column unique constraint `unique_together = ("vendor", "invoice_number")` on `SupplierInvoice` ensuring that no vendor can submit the same invoice number more than once.
3. **PO Amendment Immutability:**
   - `POAmendment` enforces `unique_together = ("po", "amendment_number")` with `previous_version_snapshot` stored in PostgreSQL JSONB.
4. **Inspection & Stock Handoff Integrity:**
   - `ReceiptLine` links to `InspectionRecord` via `OneToOneField` and `StockHandoffRecord` via `OneToOneField`, preventing duplicate inspection or double stock-posting.
5. **Append-Only Audit Security:**
   - `AuditLog` overrides `save()` and `delete()` to block in-place mutations and deletions, ensuring compliance with HPE auditor requirements.

---

## 5. Fresh Database Bootstrap Procedure

To verify migrations on a fresh PostgreSQL 15 database from scratch:

```bash
# 1. Create fresh database
psql -U postgres -c "DROP DATABASE IF EXISTS test_procuresphere_fresh;"
psql -U postgres -c "CREATE DATABASE test_procuresphere_fresh;"

# 2. Run migrations
export DATABASE_URL="postgresql://postgres:password@localhost:5432/test_procuresphere_fresh"
python manage.py migrate

# 3. Seed master roles and permissions
python manage.py seed_demo_data
```
All migrations apply in exact dependency order (`accounts` -> `organization` -> `approvals` -> `vendors` -> `requisitions` -> `budgets` -> `sourcing` -> `orders` -> `receipts` -> `invoices` -> `contracts` -> `scorecards` -> `notifications` -> `reports` -> `audit`).

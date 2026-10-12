# Department Approval Module — Integration Test Report

## 1. Test Overview

- **Project Name:** ProcureSphere 360 – Internal (Enterprise Procurement ERP)
- **Module Name:** Department Approval (Source-to-Pay / Requisition Approval Lifecycle)
- **Testing Type:** Integration Testing ONLY
- **Execution Date:** October 12, 2026
- **Testing Environment:**
  - Python: 3.12.10
  - Django: 4.2.30
  - DRF: 3.14.0
  - Pytest: 9.1.1 (`pytest-django` 4.14.0)
  - Database: PostgreSQL 15+ (Isolated transaction-backed test database with `--reuse-db`)
  - Target Branch: `feature/approvals` (Commit: `3cfb0008a505c99d87a6e95c2ba9d401c4269d28`)

---

## 2. Integration Test Summary

| Metric | Result |
|---|---|
| **Total Tests Executed** | 52 |
| **Passed Tests** | 49 |
| **Failed Tests** | 3 |
| **Skipped Tests** | 0 |
| **Blocked Tests** | 0 |
| **Pass Percentage** | **94.23%** (49 / 52) |
| **Execution Duration** | 983.19s (16 minutes, 23 seconds) |
| **Integration Boundaries Tested** | 8 distinct subsystem boundaries |

---

## 3. Detailed Test Cases

### A. Requisition to Department Approval Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-REQ-001** | Requisition submission handoff to Department Approval queue | PR status transitions from `DRAFT` to `MANAGER_REVIEW` | Status updated to `MANAGER_REVIEW` in PostgreSQL | **PASS** |
| **INT-REQ-002** | Department and Cost Center relational preservation | Submitted PR preserves FK links to Department and CostCenter | Department and CostCenter FKs verified | **PASS** |
| **INT-REQ-003** | Authorized approver view in inbox and detail page | Approver sees pending PR in inbox querysets and detail view | Requisition accessible via HTTP 200 in inbox and detail | **PASS** |
| **INT-REQ-004** | Line item and financial total retrieval across views | Approver view displays correct line items, quantities, and totals | Quantities and totals correctly rendered | **PASS** |
| **INT-REQ-005** | State persistence across sequential reads | Requisition remains in `MANAGER_REVIEW` across multiple GET requests | State preserved consistently | **PASS** |
| **INT-REQ-006** | Cross-department requisition isolation | Approver B (Marketing) cannot view Department A (Engineering) PRs in inbox | Dept A PR strictly omitted from Dept B querysets | **PASS** |
| **INT-REQ-007** | Premature draft approval rejection | Service blocks approving PR still in `DRAFT` status | `ValidationError: Cannot approve requisition in status 'DRAFT'` | **PASS** |
| **INT-REQ-008** | Post-terminal approval block | Service blocks approving PR already in `APPROVED` or `REJECTED` | `ValidationError: Cannot approve requisition in status 'APPROVED'` | **PASS** |

### B. Department Approval to Approval Policy Engine Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-POL-001** | Threshold-based policy selection | Engine selects policy matching PR amount threshold ($10k vs $75k) | Correct policy matched by amount and module | **PASS** |
| **INT-POL-002** | Department-specific policy precedence | Department policy overrides global policy for matching department | Dept A evaluates to Dept A policy; Dept B to global | **PASS** |
| **INT-POL-003** | Multi-level approval routing sequence | Multi-tier policy produces ordered steps by `step_number` | Ordered chain returned (Tier 1: Dept Approver, Tier 2: Proc Mgr) | **PASS** |
| **INT-POL-004** | Approver delegation dynamic resolution | Active `ApprovalDelegate` dynamically re-routes step to delegate user | Resolved to delegate when active; reverted when inactive | **PASS** |
| **INT-POL-005** | Unmatched policy configuration fallback | Non-matching module or unconfigured policy returns `[]` safely | Returned `[]` without unhandled exceptions | **PASS** |
| **INT-POL-006** | Budget overrun exception routing | Over-budget PR routes to `BUDGET_REVIEW` instead of `MANAGER_REVIEW` | Routed to `BUDGET_REVIEW` exception queue | **PASS** |
| **INT-POL-007** | Unauthorized user role rejection | Requester attempting HTTP POST approval is rejected | Redirected with permission error; PR status unchanged | **PASS** |
| **INT-POL-008** | Policy metadata and requisition data consistency | Policy limits and PR totals remain synchronized | Amount and department constraints verified | **PASS** |

### C. Department Approval and Rejection Lifecycle Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-WF-001** | Valid approval state transition | PR status updates from `MANAGER_REVIEW` to `APPROVED` | Status updated to `APPROVED` and committed | **PASS** |
| **INT-WF-002** | Valid rejection state transition | PR status updates from `MANAGER_REVIEW` to `REJECTED` | Status updated to `REJECTED` | **PASS** |
| **INT-WF-003** | Rejection justification comment storage | Rejection reason is stored in `ApprovalAction` and `AuditLog` | Comment persisted accurately | **PASS** |
| **INT-WF-004** | Approved PR removal from pending queue | Approved PR disappears from pending queue and appears in history | Excluded from pending; included in history | **PASS** |
| **INT-WF-005** | Rejected PR removal from pending queue | Rejected PR disappears from pending queue and appears in history | Excluded from pending; included in history | **PASS** |
| **INT-WF-006** | Duplicate approval prevention | Re-submitting approval raises `ValidationError` | `ValidationError: Cannot approve requisition in status 'APPROVED'` | **PASS** |
| **INT-WF-007** | Duplicate rejection prevention | Re-submitting approval on rejected PR raises `ValidationError` | `ValidationError: Cannot approve requisition in status 'REJECTED'` | **PASS** |
| **INT-WF-008** | Whitespace-only rejection comment validation | Rejection with whitespace-only comments should raise `ValidationError` | **Failed: DID NOT RAISE ValidationError** (bypassed check) | **FAIL** |
| **INT-WF-009** | Invalid state transitions rejection | Attempting approval from `CANCELLED` status is rejected | `ValidationError: Cannot approve requisition in status 'CANCELLED'` | **PASS** |
| **INT-WF-010** | Database transaction atomicity & rollback | Unhandled failure mid-approval rolls back status change | PR status reverted to `MANAGER_REVIEW` | **PASS** |

### D. Department Approval to Budget Review Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-BDG-001** | Pre-approval budget reservation lock | PR submission locks funds in `SpendLedger` and `BudgetReservation` | Funds locked; available budget decreased | **PASS** |
| **INT-BDG-002** | Budget overrun diversion to Budget Review | PR exceeding CC budget routes to `BUDGET_REVIEW` | Routed to `BUDGET_REVIEW` status | **PASS** |
| **INT-BDG-003** | Rejection releases active budget reservation | Rejection releases reserved funds back to available pool | Reservation status: `RELEASED`; available funds restored | **PASS** |
| **INT-BDG-004** | Role boundary on budget overrun exception | Dept Approver cannot approve a PR in `BUDGET_REVIEW` (Finance role required) | Action blocked; PR preserved in `BUDGET_REVIEW` | **PASS** |
| **INT-BDG-005** | Requisition entity integrity through transitions | PR ID, Department, Requester, CostCenter preserved | All IDs and relationships preserved | **PASS** |

### E. Department Approval Notifications Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-NOTIF-001** | Submission notification to Dept Approver | In-app notification with `TYPE_APPROVAL_REQUIRED` dispatched | Notification record created in PostgreSQL | **PASS** |
| **INT-NOTIF-002** | Budget alert notification to Finance | Over-budget PR submission alerts Finance/AP role | Notification created with `Budget Alert` title | **PASS** |
| **INT-NOTIF-003** | Notification content and URL integrity | Title, body, and target URL match requisition details | Message and target URL verified | **PASS** |
| **INT-NOTIF-004** | Department recipient scoping | Notifications routed exclusively to approvers in PR's department | Dept A approver notified; Dept B approver excluded | **PASS** |
| **INT-NOTIF-005** | Downstream Procurement notification dispatch | Approved PR notifies Procurement Executive for Sourcing Intake | **Failed: AssertionError** (silent `ImportError` swallowed) | **FAIL** |

### F. Department Approval History and Audit Logs Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-AUD-001** | Approval history action creation | Approval creates `ApprovalAction` with actor, comments, timestamp | `ApprovalAction` created with exact state diff | **PASS** |
| **INT-AUD-002** | Immutable AuditLog persistence on approval | Approval writes `AuditLog` record to PostgreSQL | `AuditLog` created with action `APPROVE` | **PASS** |
| **INT-AUD-003** | Rejection history and audit log capture | Rejection records `ApprovalAction` and `AuditLog` with comments | `ApprovalAction` and `AuditLog` created with comments | **PASS** |
| **INT-AUD-004** | AuditLog record immutability (update block) | Invoking `.save()` on existing `AuditLog` raises `PermissionError` | `PermissionError: AuditLog records are append-only` | **PASS** |
| **INT-AUD-005** | AuditLog record immutability (delete block) | Invoking `.delete()` on `AuditLog` raises `PermissionError` | `PermissionError: AuditLog records are append-only` | **PASS** |
| **INT-AUD-006** | Failed action does not write misleading history | Failed approval attempt does not create spurious history record | History count unchanged on failure | **PASS** |

### G. Department Approval API Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-API-001** | Authenticated GET `/api/v1/approvals/policies/` | Returns HTTP 200 and list of policies | HTTP 200 with policy items | **PASS** |
| **INT-API-002** | Unauthenticated GET `/api/v1/approvals/policies/` | Returns HTTP 401 Unauthorized | HTTP 401 returned | **PASS** |
| **INT-API-003** | Authenticated GET `/api/v1/approvals/actions/` | Returns HTTP 200 and list of approval actions | HTTP 200 with action items | **PASS** |
| **INT-API-004** | Unauthenticated GET `/api/v1/approvals/actions/` | Returns HTTP 401 Unauthorized | HTTP 401 returned | **PASS** |
| **INT-API-005** | Requisitions API department scoping | Approver querying `/api/v1/requisitions/` sees only their department | Scoped strictly to approver's department | **PASS** |
| **INT-API-006** | Auditor read-only restriction on policy API | Auditor POST to `/api/v1/approvals/policies/` should return 403 | **Failed: Returned HTTP 201 Created** (missing permission class) | **FAIL** |

### H. Department Approval Database Integration

| Test ID | Integration Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **INT-DB-001** | ForeignKey and reverse query integrity | Relational FKs between Requisition, Department, CostCenter work | Bidirectional queries succeed | **PASS** |
| **INT-DB-002** | ApprovalStep unique constraint enforcement | Unique constraint on `(policy, step_number)` raises `IntegrityError` | `IntegrityError` raised on duplicate step number | **PASS** |
| **INT-DB-003** | Transaction rollback preserves database state | Unhandled error mid-transaction reverts all state changes | PR status reverted to `MANAGER_REVIEW` | **PASS** |
| **INT-DB-004** | No orphaned records on workflow execution | All `ApprovalAction` and `BudgetReservation` records link to PR | Complete relational integrity verified | **PASS** |

---

## 4. Failed Test Analysis

### Failure 1: Downstream Procurement Notification Dispatch Failed
- **Test ID:** INT-NOTIF-005 (`test_downstream_procurement_notification_dispatched_on_approval`)
- **File:** `src/apps/requisitions/services.py:227`
- **Failure Description:** `AssertionError: DEF-APP-001: Missing notification for Procurement Executive due to unimported create_notification_service. assert None is not None`
- **Expected Behavior:** Upon requisition approval, an in-app notification should be dispatched to Procurement Executives (`Role.PROC_EXEC`) alerting them that the approved requisition is ready for Sourcing Intake.
- **Actual Behavior:** In `src/apps/requisitions/services.py`, lines 225–239:
  ```python
  try:
      from apps.accounts.models import Role, User
      from apps.notifications.services import create_notification_service  # <--- Does NOT exist!
      ...
  except Exception:
      pass  # <--- Swallows the ImportError silently!
  ```
  The function `create_notification_service` does not exist in `apps.notifications.services` (the actual functions are `notify_users`, `notify_role_users`, etc.). The resulting `ImportError` is swallowed by the `except Exception: pass` block, leaving `Notification.objects` empty.
- **Root Cause:** Application defect — mismatched function import and silent exception suppression.
- **Severity:** **High (Application Defect)**
- **Fix Applied / Recommended Solution:** Update line 227 of `src/apps/requisitions/services.py` to import and call `notify_role_users`:
  ```python
  from apps.notifications.services import notify_role_users
  notify_role_users(
      role_codes=[Role.PROC_EXEC],
      notification_type="IN_APP",
      title="Approved PR Ready for Sourcing",
      message=f"Requisition {requisition.pr_number} has been approved by {approver.email} and is ready on your Sourcing Intake.",
      target_url=f"/requisitions/{requisition.id}/",
  )
  ```

### Failure 2: Whitespace-Only Comments Bypass Mandatory Rejection Validation
- **Test ID:** INT-WF-008 (`test_rejection_requires_mandatory_comments`)
- **File:** `src/apps/requisitions/services.py:251`
- **Failure Description:** `Failed: DID NOT RAISE ValidationError`
- **Expected Behavior:** Rejection justification comments must be non-empty and non-whitespace. Submitting `"   "` must raise `ValidationError`.
- **Actual Behavior:** Line 251 checks:
  ```python
  if not comments:
      raise ValidationError("Rejection justification comments are mandatory.")
  ```
  In Python, `"   "` evaluates to `True`, so `not "   "` evaluates to `False`. The service accepts whitespace-only comments without error and proceeds to reject the PR.
- **Root Cause:** Missing `.strip()` validation check on the comments string.
- **Severity:** **Medium (Data Integrity Defect)**
- **Fix Applied / Recommended Solution:** Update line 251 in `src/apps/requisitions/services.py`:
  ```python
  if not comments or not comments.strip():
      raise ValidationError("Rejection justification comments are mandatory.")
  ```

### Failure 3: Compliance Auditor Able to Create Approval Policies via API
- **Test ID:** INT-API-006 (`test_compliance_auditor_read_only_restriction_on_api`)
- **File:** `src/apps/approvals/api_urls.py:22`
- **Failure Description:** `assert 201 in [403, 405]` (Auditor POST to `/api/v1/approvals/policies/` returned HTTP 201 Created).
- **Expected Behavior:** In accordance with the PRD and AGENTS.md, Compliance Auditors hold **strictly read-only** access across all masters, policies, and workflows. API mutation attempts must be rejected with HTTP 403 Forbidden.
- **Actual Behavior:** In `src/apps/approvals/api_urls.py`:
  ```python
  class ApprovalPolicyViewSet(viewsets.ModelViewSet):
      queryset = ApprovalPolicy.objects.all()
      serializer_class = ApprovalPolicySerializer
      permission_classes = [IsAuthenticated]  # <--- Missing AuditorReadOnlyPermission!
  ```
  The viewset does not include `AuditorReadOnlyPermission` (which is properly applied in `apps.requisitions.api_urls.py`). As a consequence, any authenticated user—including Compliance Auditors—can create, edit, or delete approval policies via the REST API.
- **Root Cause:** Security / RBAC gap — missing permission class on the DRF ViewSet.
- **Severity:** **High (Security / RBAC Defect)**
- **Fix Applied / Recommended Solution:** Update `ApprovalPolicyViewSet` in `src/apps/approvals/api_urls.py`:
  ```python
  from apps.audit.permissions import AuditorReadOnlyPermission

  class ApprovalPolicyViewSet(viewsets.ModelViewSet):
      queryset = ApprovalPolicy.objects.all()
      serializer_class = ApprovalPolicySerializer
      permission_classes = [IsAuthenticated, AuditorReadOnlyPermission]
  ```

---

## 5. Integration Coverage

| Subsystem Boundary | Tests | Passed | Failed | Pass Rate | Status |
|---|---|---|---|---|:---:|
| **Requisition → Department Approval** | 8 | 8 | 0 | 100.0% | **COMPLETE** |
| **Approval → Policy Engine** | 8 | 8 | 0 | 100.0% | **COMPLETE** |
| **Approval & Rejection Lifecycle** | 10 | 9 | 1 | 90.0% | **DEFECT IDENTIFIED** |
| **Approval → Budget Review Handoff** | 5 | 5 | 0 | 100.0% | **COMPLETE** |
| **Approval → Notifications** | 5 | 4 | 1 | 80.0% | **DEFECT IDENTIFIED** |
| **Approval → History & Audit Logs** | 6 | 6 | 0 | 100.0% | **COMPLETE** |
| **Approval → REST API Endpoints** | 6 | 5 | 1 | 83.3% | **DEFECT IDENTIFIED** |
| **Approval → Database & Relational Integrity** | 4 | 4 | 0 | 100.0% | **COMPLETE** |
| **Total** | **52** | **49** | **3** | **94.23%** | **FAIL (Defects Pending Fix)** |

### Coverage Methodology:
Coverage percentage is calculated strictly as:
$$\text{Integration Pass Rate} = \frac{\text{Passed Integration Scenarios}}{\text{Total Executed Integration Scenarios}} \times 100 = \frac{49}{52} \times 100 = 94.23\%$$

All 8 architectural integration boundaries defined in Phase 2 were comprehensively evaluated with real transactional data in PostgreSQL.

---

## 6. Bugs and Security Risks Discovered

1. **DEF-APP-001 (High Severity): Silent Downstream Notification Failure on PR Approval**
   - *Subsystem:* Requisitions / Notifications integration
   - *Impact:* Sourcing executives are not alerted when requisitions are approved, breaking the S2P automation pipeline.
2. **DEF-APP-002 (High Severity): Missing Cross-Department POST Scoping in Inbox View**
   - *Subsystem:* Approvals UI / Requisitions integration
   - *Impact:* Potential privilege escalation / unauthorized cross-department PR approvals via crafted POST requests.
3. **DEF-APP-004 (High Severity): Compliance Auditor Able to Mutate Approval Policies via REST API**
   - *Subsystem:* Approvals API / Audit RBAC integration
   - *Impact:* Violation of read-only auditor mandate allowing auditors to modify governance rules.
4. **DEF-APP-005 (Medium Severity): Whitespace Rejection Justification Bypass**
   - *Subsystem:* Requisitions domain validators
   - *Impact:* Empty justification comments can be submitted using spaces, evading audit accountability.

---

## 7. Final QA Status

**Final Conclusion: FAIL**

While 49 out of 52 integration tests passed (94.23%) and proved robust data integrity, budget locks, delegation routing, and append-only audit trails, the Department Approval module **cannot be signed off as production-ready** until the three identified defects—specifically the missing notification import (DEF-APP-001) and the unconstrained API policy mutation permission (DEF-APP-004)—are resolved in domain code.

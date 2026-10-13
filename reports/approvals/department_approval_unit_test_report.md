# Department Approver Dashboard — Unit Test Report

## 1. Executive Summary

- **Project Name:** ProcureSphere 360 Internal (Enterprise Procurement ERP)
- **Module Tested:** Department Approver Dashboard (`apps.core.views.home_view` approver branch, `apps.approvals` views/services/models, and related `apps.requisitions` approval workflows & policies)
- **Test Execution Date:** October 12, 2026
- **Target Branch & Commit:** `feature/approvals` (`3cfb0008a505c99d87a6e95c2ba9d401c4269d28`)
- **Python Version:** 3.12.10
- **Django Version:** 4.2.30
- **Pytest Version:** pytest 9.1.1 (pytest-django 4.14.0, pytest-cov 7.1.0)
- **Database Engine:** PostgreSQL 15+ (isolated test DB with `--reuse-db`)
- **Overall Test Status:** **FAIL** (34 Passed, 3 Failed, 0 Skipped, 0 Errors, 0 Blocked)

---

## 2. Test Execution Summary

| Metric | Result |
|---|---|
| **Total Tests Executed** | 37 |
| **Passed** | 34 |
| **Failed** | 3 |
| **Skipped** | 0 |
| **Errors** | 0 |
| **Blocked** | 0 |
| **Pass Percentage** | **91.89%** |
| **Module Statement Coverage** | **75.0%** (345 / 462 statements across target modules) |
| **Total Execution Duration** | 439.57s (7 minutes, 19 seconds) |

---

## 3. Detailed Test Results

| Test ID | Test Scenario | Expected Result | Actual Result | Status |
|---|---|---|---|:---:|
| **UT-DASH-001** | Dashboard access for authorized Dept Approver | HTTP 200, renders `approver_dashboard.html`, displays department name badge & metrics | Dashboard rendered with HTTP 200, user department correctly identified | **PASS** |
| **UT-DASH-002** | Pending approval count aggregation | Counts PRs with status `SUBMITTED`, `MANAGER_REVIEW`, `BUDGET_REVIEW` in user's department | Correct count (3) returned in `metrics["pending_count"]` | **PASS** |
| **UT-DASH-003** | Approved requisition count aggregation | Counts PRs with status `APPROVED` in user's department | Correct count (2) returned in `metrics["approved_count"]` | **PASS** |
| **UT-DASH-004** | Total & rejected requisition count tracking | Aggregates all departmental PRs regardless of lifecycle stage | Total PRs (3) aggregated, rejected PRs accounted for | **PASS** |
| **UT-DASH-005** | Department-specific requisition isolation | Approver of Dept A only sees Dept A PRs; Dept B PRs strictly excluded | Engineering approver sees only Engineering PRs; Marketing PRs omitted | **PASS** |
| **UT-DASH-006** | Dashboard behavior with zero pending PRs | Shows empty-state message `"Your approval inbox is completely clear!"` with count 0 | Empty state UI and zero metric rendered cleanly | **PASS** |
| **UT-DASH-007** | Unauthorized user role dashboard isolation | Requesters see Requester workspace; Approver without department gets safe 0 metrics | Non-approver redirected to proper workspace; null-department handled gracefully | **PASS** |
| **UT-WORKFLOW-001** | Approve valid pending requisition | PR status transitions from `MANAGER_REVIEW` to `APPROVED`, records `ApprovalAction` and `AuditLog` | State transitioned to `APPROVED`, approval action logged | **PASS** |
| **UT-WORKFLOW-002** | Reject requisition with valid reason | PR transitions to `REJECTED`, active budget reservation is released | Status updated to `REJECTED`, reservation released | **PASS** |
| **UT-WORKFLOW-003** | Validate rejection justification requirement | Rejecting without comments raises `ValidationError` | `ValidationError: Rejection justification comments are mandatory.` raised | **PASS** |
| **UT-WORKFLOW-004** | Prevent duplicate approvals | Approving an already `APPROVED` requisition raises `ValidationError` | `ValidationError: Cannot approve requisition in status 'APPROVED'.` raised | **PASS** |
| **UT-WORKFLOW-005** | Prevent approval of rejected requisitions | Approving an already `REJECTED` requisition raises `ValidationError` | `ValidationError: Cannot approve requisition in status 'REJECTED'.` raised | **PASS** |
| **UT-WORKFLOW-006** | Validate invalid state transitions | Approving `DRAFT` or `CANCELLED` requisition raises `ValidationError` | `ValidationError` raised for invalid transitions | **PASS** |
| **UT-WORKFLOW-007** | Next-step routing after department approval | Notification queued for Procurement Executive indicating PR is ready for Sourcing Intake | Notification was not found (`assert None is not None`) due to missing import | **FAIL** |
| **UT-WORKFLOW-008** | Multi-level approval routing | Evaluates multi-tier policy chain ordered by `step_number` | Correct ordered chain returned (Step 1: DEPT_APPROVER, Step 2: PROC_MGR) | **PASS** |
| **UT-WORKFLOW-009** | Budget overrun exception routing | Over-budget PR routes to `BUDGET_REVIEW` instead of `MANAGER_REVIEW`, notifies Finance | Requisition transitioned to `BUDGET_REVIEW`, Finance alert created | **PASS** |
| **UT-POLICY-001** | Approval permission checks in engine & views | Requesters and unauthorized roles blocked from executing approvals | POST to approve view redirected with permission error; PR unchanged | **PASS** |
| **UT-POLICY-002** | Department-specific policy precedence | Department policy takes priority over global fallback policy | Dept A evaluates to Dept A policy; Dept B falls back to Global policy | **PASS** |
| **UT-POLICY-003** | Approval policy amount thresholds | Policy with min amount threshold ($50k) does not match sub-threshold PRs | Empty chain returned for $10k; matching policy returned for $60k | **PASS** |
| **UT-POLICY-004** | Highest matching threshold selection | When multiple policies qualify, highest `min_amount` policy is chosen | Highest threshold policy (Tier 2: $20,000) selected for $35,000 PR | **PASS** |
| **UT-POLICY-005** | Invalid or missing policy handling | Non-matching module or unconfigured threshold returns empty list `[]` | Returns `[]` without unhandled exception | **PASS** |
| **UT-POLICY-006** | Approver active delegation resolution | Active `ApprovalDelegate` returns delegate user; expired delegate returns original approver | Resolved to delegate when active; reverted to approver when inactive | **PASS** |
| **UT-NOTIF-001** | Notification created on PR submission | In-app notification with `TYPE_APPROVAL_REQUIRED` generated for Dept Approver | Notification created with title & summary for target approver | **PASS** |
| **UT-NOTIF-002** | Notification created after approval for Procurement | In-app notification created for `Role.PROC_EXEC` when PR is approved | Notification not found due to silent import failure | **FAIL** |
| **UT-NOTIF-003** | Notification recipient scoping to PR department | Notifications sent exclusively to approvers belonging to the PR's department | Dept A approver notified; Dept B approver excluded | **PASS** |
| **UT-NOTIF-004** | Notification message content and target URL | Message includes PR number, title, amount, and valid `/requisitions/<id>/` URL | Title, description, and link verified | **PASS** |
| **UT-NOTIF-005** | Notification failure graceful handling | Simulated notification error does not abort approval database transaction | Test failed with `AttributeError: module 'apps.notifications.services' has no attribute 'create_notification_service'` | **FAIL** |
| **UT-SEC-001** | Unauthenticated access denied | Unauthenticated requests to `/` and `/approvals/inbox/` redirect to `/login/` | HTTP 302 redirect to `/login/` confirmed | **PASS** |
| **UT-SEC-002** | Compliance Auditor read-only enforcement | Compliance Auditor cannot approve or reject via `/approvals/inbox/` | Action rejected with read-only error message; PR status preserved | **PASS** |
| **UT-SEC-003** | Cross-department approval inbox isolation | Approvals inbox querysets exclude cross-department PRs | Dept B PR excluded from Dept A approver's inbox querysets | **PASS** |
| **UT-SEC-004** | Audit history records correct actor | `AuditLog` captures exact actor, target model, target ID, and state diff | Actor set to approver; previous and new state stored in JSON diff | **PASS** |
| **UT-SEC-005** | ApprovalAction decision timestamp and comments | `ApprovalAction` stores exact comments, previous status, new status, and timestamp | Correct metadata and creation timestamp verified | **PASS** |
| **UT-SEC-006** | Duplicate-action protection | Submitting duplicate POST requests handles gracefully without corrupting state | First approval succeeds, second fails with state validation message | **PASS** |
| **UT-SEC-007** | Append-only AuditLog immutability | Attempting to update or delete `AuditLog` raises `PermissionError` | `PermissionError: AuditLog records are append-only and immutable` raised | **PASS** |
| **UT-INBOX-001** | Approvals inbox search filter | Query parameter `?q=` filters pending list by PR number, title, or requester | Filtered list matches only target PR | **PASS** |
| **UT-INBOX-002** | Approvals inbox status filter | Query parameter `?status=` filters list by status (e.g. `BUDGET_REVIEW`) | Filtered list matches only specified status | **PASS** |
| **UT-INBOX-003** | Approvals inbox history tab | `?tab=history` displays approved and rejected requisitions | Evaluated history items populated; pending items excluded | **PASS** |

---

## 4. Approval Workflow Test Results

### 4.1 Valid Requisition Approval
- Requisitions transitioning from `MANAGER_REVIEW` to `APPROVED` update successfully.
- An immutable `ApprovalAction` record is generated with `action="APPROVED"`.
- An `AuditLog` record is written to PostgreSQL with `previous_state={"status": "MANAGER_REVIEW"}` and `new_state={"status": "APPROVED", "comments": "..."}`.

### 4.2 Requisition Rejection & Budget Release
- Rejection transitions PR to `STATUS_REJECTED`.
- Active budget reservations allocated to the requisition are automatically released via `release_budget_reservation_service`.
- Rejection without comments is blocked: `ValidationError("Rejection justification comments are mandatory.")` is raised.

### 4.3 Safeguards on Duplicate and Invalid Actions
- Requisitions in terminal states (`APPROVED`, `REJECTED`) reject subsequent approval attempts with clear `ValidationError` messages.
- Early draft or cancelled requisitions (`DRAFT`, `CANCELLED`) cannot be approved.
- Re-submitting approval actions through the UI/views is caught by the domain layer and returns error messages without breaking database consistency.

### 4.4 Policy Evaluation & Exception Routing
- Multi-step approval policies are sequenced accurately according to `ApprovalStep.step_number`.
- Pre-approval budget checks correctly detect over-budget purchase requisitions, routing them to `STATUS_BUDGET_REVIEW` instead of `STATUS_MANAGER_REVIEW`.
- Requisitions in `BUDGET_REVIEW` require Finance/AP role approval and cannot be approved by ordinary Department Approvers.

---

## 5. Security and Permission Results

1. **Authentication Enforcement:**
   - Both `/` and `/approvals/inbox/` are protected with `@login_required(login_url="/login/")`. Unauthenticated requests receive HTTP 302 redirects.
2. **Role-Based Access Control:**
   - Compliance Auditors (`Role.AUDITOR`) are strictly prevented from performing state-modifying approval or rejection actions.
   - Requesters (`Role.REQUESTER`) attempting to approve requisitions are blocked with permission errors.
3. **Department Scoping:**
   - Both dashboard metrics and inbox querysets filter strictly by `department=user.department`.
   - Requisitions belonging to other departments are excluded from pending and history querysets.
4. **Audit Trail Immutability:**
   - `AuditLog` enforces strict append-only constraints. Explicit invocations of `.save()` on existing instances or `.delete()` raise `PermissionError`.
   - `ApprovalAction` records store the exact actor, timestamp, comments, and state transition.

---

## 6. Failed Test Analysis

Three tests failed during execution. All three trace directly to a single defect in the existing codebase:

### Failure 1: `test_workflow_next_step_routing_after_department_approval`
- **Location:** `tests/test_temp_department_approvals.py:384`
- **Error Details:** `AssertionError: assert None is not None`
- **Root Cause:** In [src/apps/requisitions/services.py](file:///c:/Users/win%2010/Desktop/abhishek%20files/ProcureSphere_360_Internal/final%201/ProcureSphere_CompanyDev/src/apps/requisitions/services.py#L225-L239), lines 225–239:
  ```python
  try:
      from apps.accounts.models import Role, User
      from apps.notifications.services import create_notification_service

      proc_execs = User.objects.filter(role__code=Role.PROC_EXEC, is_active=True)
      for pe in proc_execs:
          create_notification_service(...)
  except Exception:
      pass
  ```
  The function `create_notification_service` does **not exist** in `apps.notifications.services` (the existing functions in that module are `notify_users`, `notify_role_users`, `notify_vendor_users`, `get_top_unread_for_user`, and `mark_notification_as_read`). Consequently, an `ImportError` is raised on every requisition approval, which is swallowed by the bare `except Exception: pass` block. As a result, no notification is created for Procurement Executives.
- **Severity:** **High (Application Defect)**
- **Recommended Solution:** Replace the non-existent function call with the supported `notify_role_users` service:
  ```python
  from apps.notifications.services import notify_role_users
  notify_role_users(
      role_codes=[Role.PROC_EXEC],
      title="Approved PR Ready for Sourcing",
      message=f"Requisition {requisition.pr_number} has been approved by {approver.email} and is ready on your Sourcing Intake.",
      notification_type="IN_APP",
      target_url=f"/requisitions/{requisition.id}/",
  )
  ```

### Failure 2: `test_notifications_created_after_approval_for_procurement`
- **Location:** `tests/test_temp_department_approvals.py:640`
- **Error Details:** `AssertionError: assert None is not None`
- **Root Cause:** Same underlying defect as Failure 1 (`create_notification_service` does not exist in `apps.notifications.services`).
- **Severity:** **High (Application Defect)**
- **Recommended Solution:** Harmonize the notification dispatch call in `src/apps/requisitions/services.py` with `apps.notifications.services`.

### Failure 3: `test_notifications_failure_handling_graceful_degradation`
- **Location:** `tests/test_temp_department_approvals.py:675`
- **Error Details:** `AttributeError: <module 'apps.notifications.services' ...> does not have the attribute 'create_notification_service'`
- **Root Cause:** The test attempted to mock `apps.notifications.services.create_notification_service`, which was expected based on the import in `src/apps/requisitions/services.py`, but Python's `mock.patch` raised an `AttributeError` because the symbol is absent from the target module.
- **Severity:** **Medium (Test Defect / Dependency on Missing Service)**
- **Recommended Solution:** Once the application defect is corrected to use `notify_role_users`, update the test patch target to `apps.notifications.services.notify_role_users`.

---

## 7. Code Coverage Analysis

Coverage was measured directly using `pytest-cov` on the relevant Department Approval modules:

```
Name                                Stmts   Miss  Cover   Missing
-----------------------------------------------------------------
src\apps\approvals\__init__.py          0      0   100%
src\apps\approvals\admin.py            26      0   100%
src\apps\approvals\api_urls.py         25      0   100%
src\apps\approvals\apps.py              5      0   100%
src\apps\approvals\models.py           54      4    93%   37, 60, 88, 104
src\apps\approvals\services.py         61     11    82%   90, 166-174, 194-205
src\apps\approvals\urls.py              3      0   100%
src\apps\approvals\views.py            70     21    70%   18, 26, 32, 41, 44, 56-77
src\apps\core\views.py                126     72    43%   24-49, 56, 86-88, 92-169, 173-175, 179-214, 218-293, 297-300, 354-364, 368, 372, 376
src\apps\requisitions\services.py      92      9    90%   37, 74-75, 108, 113, 203, 229-231
-----------------------------------------------------------------
TOTAL                                 462    117    75%
```

### Coverage Highlights:
- **`apps.requisitions.services.py`**: **90% coverage** — Core approval, rejection, budget reservation release, and submission logic fully exercised.
- **`apps.approvals.models.py`**: **93% coverage** — Policy, step, delegate, and action models verified.
- **`apps.approvals.services.py`**: **82% coverage** — Approval chain evaluation, delegation resolution, and action logging covered.
- **`apps.approvals.views.py`**: **70% coverage** — Inbox rendering, tab switching, search/status filtering, and auditor protection tested.
- **`apps.core.views.py`**: **43% coverage** — The entire Department Approver dashboard execution path (`role_code == Role.DEPT_APPROVER`) is exercised; remaining uncovered lines belong to other role dashboards (Vendor, Legal, Stores, etc.) excluded from this test scope.

---

## 8. Bugs and Risks Identified

### Defect 1: Broken Downstream Notification Dispatch on Requisition Approval
- **Defect ID:** DEF-APP-001
- **File:** `src/apps/requisitions/services.py` (lines 225–239)
- **Severity:** **High**
- **Description:** `approve_purchase_requisition_service` imports `create_notification_service` which does not exist in `apps.notifications.services`. Because this is wrapped in a silent `except Exception: pass`, no notifications are generated for Procurement Executives when a PR is approved.
- **Impact:** Procurement Executives are never alerted that approved requisitions are ready for RFQ/RFP sourcing, creating a silent operational stall in the Source-to-Pay workflow.

### Defect 2: Missing Cross-Department POST Validation in Approvals Inbox View
- **Defect ID:** DEF-APP-002
- **File:** `src/apps/approvals/views.py` (function `_handle_approval_post`, lines 47–78)
- **Severity:** **High**
- **Description:** In `_handle_approval_post`, the requisition is fetched via `PurchaseRequisition.objects.get(id=pr_id)` without validating that `pr.department == request.user.department`. While the GET querysets correctly filter by department, a malicious or confused user could craft a POST request with another department's `pr_id` and successfully approve or reject it.
- **Impact:** Potential bypass of department authorization boundaries via crafted HTTP POST requests.

### Defect 3: Silent Exception Swallowing in Critical Event Dispatch
- **Defect ID:** DEF-APP-003
- **File:** `src/apps/requisitions/services.py` (line 238)
- **Severity:** **Medium**
- **Description:** Using bare `except Exception: pass` without logging masks configuration errors and import defects during event processing.

---

## 9. Recommendations

1. **Resolve Notification Service API Discrepancy:**
   Update `src/apps/requisitions/services.py` to use `notify_role_users` from `apps.notifications.services`, ensuring `Role.PROC_EXEC` recipients receive notifications when requisitions are approved.
2. **Implement Backend Scoping on Approval POST Endpoints:**
   In `src/apps/approvals/views.py` (`_handle_approval_post`), verify that the target requisition belongs to the approver's department:
   ```python
   if role_code == Role.DEPT_APPROVER:
       if pr.department != getattr(request.user, "department", None):
           messages.error(request, "Permission Denied: You cannot approve requisitions from another department.")
           return redirect("approvals_inbox")
   ```
3. **Structured Logging for External Dispatches:**
   Replace `except Exception: pass` with `logger.exception("Failed to dispatch approval notification for PR %s", requisition.pr_number)` so issues appear in observability pipelines.
4. **Repository Hygiene:**
   As instructed, no testing files were retained in the repository structure. The test suite was executed against the database and removed to preserve production code cleanliness.

---

## 10. Final QA Conclusion

**Status: FAIL**

While 34 of 37 unit tests (91.89%) passed successfully—verifying dashboard loading, metrics calculations, state machines, multi-level policy routing, budget overrun exception routing, and audit log immutability—the suite revealed a **High-severity defect** where downstream notifications to Procurement Executives fail silently due to an unimported service function.

> **Notice:** In accordance with software quality standards, this module **cannot be signed off as production-ready** until DEF-APP-001 (downstream notification dispatch) and DEF-APP-002 (cross-department POST authorization) are corrected in domain code.

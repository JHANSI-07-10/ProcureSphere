# ProcureSphere 360 — Authentication & RBAC Foundation Engineering Assessment

**Document Reference:** SEC-AUTH-RBAC-PS360-D15  
**PRD Reference:** HPE-PRD-2026-PROC01 (VER-1.0-DRAFT)  
**Issuing Authority:** HPE Team  
**Development Partner:** VPD Technologies Pvt. Ltd.  
**Project Repository:** https://github.com/VPDTechnologies/ProcureSphere_360  
**Target Milestone:** Days 1–15 Engineering Foundation & Authentication Baseline  
**Date of Assessment:** 2026-10-12  
**Status:** Review-Ready Engineering Report  

---

## 4.1 Purpose and Scope

### 4.1.1 Purpose
Authentication and Role-Based Access Control (RBAC) form the security bedrock of ProcureSphere 360. In an enterprise Source-to-Pay (S2P) and Accounts Payable (AP) ERP, unauthorized actions directly risk financial loss, regulatory non-compliance, maverick purchasing, vendor collusion, and data tampering. 

This document delivers a rigorous, repository-grounded engineering assessment of the authentication and authorization architecture implemented in `https://github.com/VPDTechnologies/ProcureSphere_360`. It examines user identification, session management, credential protection, backend authorization enforcement, least privilege, separation of duties, test coverage, and identified security gaps.

### 4.1.2 Scope and Boundaries
- **In Scope:** Custom `User` and `Role` models, Django authentication configuration, `django-axes` brute-force defenses, session and CSRF policies, DRF permission classes (`apps.accounts.permissions`), view-level access controls, template visibility guards, audit context propagation, and authorization test coverage.
- **Out of Scope:** External Single Sign-On (SAML 2.0 / OIDC / Azure AD federation), physical biometric hardware, and third-party payment gateway tokenization (not part of the Days 1–15 baseline).

---

## 4.2 Current Authentication Architecture

### 4.2.1 Django Authentication Configuration
- **Settings Path:** `src/config/settings/base.py` (Lines 87–90)
- **Authentication Backends:**
  ```python
  AUTHENTICATION_BACKENDS = [
      "axes.backends.AxesBackend",
      "django.contrib.auth.backends.ModelBackend",
  ]
  ```
- **Configuration Analysis:** Authentications route through `axes.backends.AxesBackend` first to register and count failed attempts before delegating credential validation to `ModelBackend`.

### 4.2.2 User Model Implementation
- **Source Path:** `src/apps/accounts/models.py` (Lines 66–111)
- **Settings Binding:** `AUTH_USER_MODEL = "accounts.User"` in `src/config/settings/base.py` (Line 31)
- **Model Definition:**
  ```python
  class User(AbstractUser):
      id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
      email = models.EmailField(unique=True)
      phone_number = models.CharField(max_length=20, blank=True)
      role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
      department = models.ForeignKey("organization.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
      vendor = models.ForeignKey("vendors.Vendor", on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_users")
      
      USERNAME_FIELD = "email"
      REQUIRED_FIELDS = []
  ```
- **Key Attributes:**
  - Employs UUID primary keys (`id`) to prevent sequential ID harvesting and enumeration attacks.
  - Designates `email` as the sole unique login identifier (`USERNAME_FIELD = "email"`).
  - Maintains strict foreign key associations to `Role`, `Department`, and `Vendor` for multi-dimensional tenancy and operational scoping.

### 4.2.3 Login, Logout, Session Handling & Password Management
- **View Implementation:** `src/apps/accounts/views.py`
  - `portal_login_view(request)` (Lines 6–55): Handles HTTP GET (form presentation) and POST (submission). Uses `authenticate(request=request, username=email, password=password)` ensuring `request` is passed for `django-axes` IP correlation. Enforces `user.is_active` check; triggers `create_audit_log_service()` recording `AuditLog.ACTION_LOGIN`.
  - `portal_logout_view(request)` (Lines 57–77): Clears the session via `logout(request)`, logs `AuditLog.ACTION_LOGOUT`, and redirects to `/login/`.
- **Password Storage & Validation:**
  - Hashing: Standard Django PBKDF2 with SHA-256 and salt.
  - Validators (`src/config/settings/base.py`, Lines 142–150):
    1. `UserAttributeSimilarityValidator`
    2. `MinimumLengthValidator` (`min_length: 10`)
    3. `CommonPasswordValidator`
    4. `NumericPasswordValidator`
- **Password Reset & Account Recovery:** **Not Found in Inspected Repository**. No self-service reset endpoints (`password_reset/`) or token recovery mechanisms exist in the active codebase. Password updates are managed administratively or via seeded credentials.

### 4.2.4 Authentication Middleware & Request Processing
- **Middleware Pipeline:** `src/config/settings/base.py` (Lines 73–85)
  1. `apps.core.middleware.RequestIDMiddleware`: Injects unique `X-Request-ID` UUID and captures real client IP (`X-Forwarded-For` or `REMOTE_ADDR`) into thread-local storage (`_request_context`).
  2. `django.middleware.security.SecurityMiddleware`
  3. `corsheaders.middleware.CorsMiddleware`
  4. `django.contrib.sessions.middleware.SessionMiddleware`: Unpacks signed session cookies.
  5. `django.middleware.common.CommonMiddleware`
  6. `django.middleware.csrf.CsrfViewMiddleware`: Validates CSRF tokens on state-changing requests.
  7. `django.contrib.auth.middleware.AuthenticationMiddleware`: Binds authenticated `User` object to `request.user`.
  8. `apps.core.middleware.AuditContextMiddleware`: Binds `request.user` into thread-local storage so that domain services running inside atomic transactions automatically attribute audit log entries.
  9. `django.contrib.messages.middleware.MessageMiddleware`
  10. `django.middleware.clickjacking.XFrameOptionsMiddleware`
  11. `axes.middleware.AxesMiddleware`: Tracks failed authentication counts and blocks locked IPs.

### 4.2.5 Django REST Framework Authentication Classes & Permissions
- **Settings Path:** `src/config/settings/base.py` (Lines 169–181)
  ```python
  REST_FRAMEWORK = {
      "DEFAULT_AUTHENTICATION_CLASSES": (
          "rest_framework.authentication.SessionAuthentication",
      ),
      "DEFAULT_PERMISSION_CLASSES": (
          "rest_framework.permissions.IsAuthenticated",
      ),
      ...
  }
  ```
- **DRF Custom Permissions (`src/apps/accounts/permissions.py`):**
  - `HasRole(roles=[...])`: Generic role checking class.
  - Dedicated permission classes: `IsSuperAdmin`, `IsRequester`, `IsDeptApprover`, `IsProcurementExec`, `IsProcurementManager`, `IsFinanceAP`, `IsStoresReceiver`, `IsLegalManager`, `IsVendorUser`.
  - `IsAuditorReadOnly`: Explicitly permits only safe HTTP methods (`GET`, `HEAD`, `OPTIONS`) for compliance auditors while returning HTTP 403 Forbidden for unsafe verbs (`POST`, `PUT`, `PATCH`, `DELETE`).

### 4.2.6 API Authentication Behavior
- **API Auth Views:** `src/apps/accounts/api_views.py`
  - `SessionLoginView` (`/api/v1/auth/login/`): Authenticates credentials, establishes session, attaches CSRF token (`ensure_csrf_cookie`), records audit log, and returns user payload with HTTP 200.
  - `SessionLogoutView` (`/api/v1/auth/logout/`): Requires `IsAuthenticated`, records logout audit event, flushes session.
- **JWT / Token Authentication:** **Not Found in Inspected Repository**. Although the PRD references JWT for API consumers, the active repository unifies on CSRF-protected session authentication (`SessionAuthentication`) for both web views and REST endpoints (documented in `docs/assumptions.md:ASSUMP-007`).

### 4.2.7 Frontend & Server-Rendered Authentication (Templates + HTMX)
- **Template Context:** `django.contrib.auth.context_processors.auth` and `apps.core.context_processors.layout_context` supply `user` and `role_code` to templates.
- **Role Layouts:** Role-specific base templates in `templates/layouts/` (`requester_base.html`, `approver_base.html`, `finance_base.html`, `legal_base.html`, etc.) conditionally render navigation drawers and dashboard summaries.
- **HTMX CSRF Header:** Server-rendered forms and HTMX actions pass `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` to satisfy `CsrfViewMiddleware`.

### 4.2.8 Authentication Settings & Environment Variables
- `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_NAME`, `REDIS_URL`: Sourced securely via `django-environ` from `.env`.
- `LOGIN_URL = "/login/"`
- `LOGIN_REDIRECT_URL = "/"`
- `AXES_FAILURE_LIMIT = 5`
- `AXES_COOLOFF_TIME = 0.05` (~3 minutes)
- `AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]`
- `AXES_RESET_ON_SUCCESS = True`

### 4.2.9 Existing Tests Covering Authentication
- **`tests/test_all_role_logins.py`:** Tests successful POST login across all 10 seeded user accounts.
- **`tests/test_all_role_dashboards.py`:** Tests session-authenticated dashboard access across all 10 role codes.
- **`tests/test_health.py`:** Tests that public `/api/v1/health/` endpoints are accessible without credentials.

---

## 4.3 Roles and Responsibility Matrix

The following matrix documents server-side access across the 10 system roles based on active codebase permissions and PRD requirements:

| Business Domain / Function | Super Admin (`SUPER_ADMIN`) | Requester (`REQUESTER`) | Dept Approver (`DEPT_APPROVER`) | Procurement Exec (`PROC_EXEC`) | Procurement Mgr (`PROC_MGR`) | Finance / AP (`FINANCE_AP`) | Stores Receiver (`STORES_RECEIVER`) | Legal Manager (`LEGAL_MGR`) | Compliance Auditor (`AUDITOR`) | Vendor User (`VENDOR_USER`) |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Organization & Cost Centers** | Allow | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Read-Only | Deny |
| **Vendor Onboarding (Self-Service)** | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Read-Only | Allow (Own profile) |
| **Vendor KYC & Risk Governance** | Allow | Deny | Deny | Conditional (Vetting) | Allow (Approval/Hold) | Deny | Deny | Deny | Read-Only | Deny |
| **Purchase Requisition (Create/Edit)** | Allow | Allow (Own dept) | Deny | Deny | Deny | Deny | Deny | Deny | Read-Only | Deny |
| **PR Approval Routing** | Allow | Deny | Conditional (Limits) | Deny | Allow (Escalations) | Deny | Deny | Deny | Read-Only | Deny |
| **Sourcing Event (RFQ/RFP Create)** | Allow | Deny | Deny | Allow | Allow | Deny | Deny | Deny | Read-Only | Deny |
| **Bid Submission (Portal)** | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Read-Only | Allow (Own bids) |
| **Bid Evaluation & Award** | Allow | Deny | Deny | Conditional (Review) | Allow (Award sign-off) | Deny | Deny | Deny | Read-Only | Deny |
| **Purchase Order (Draft / Issue)** | Allow | Deny | Deny | Allow | Allow | Deny | Deny | Deny | Read-Only | Deny |
| **PO Acknowledgement** | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Read-Only | Allow (Own POs) |
| **Goods Intake & GRN Creation** | Allow | Deny | Deny | Deny | Deny | Deny | Allow | Deny | Read-Only | Deny |
| **Quality Inspection & Quarantine** | Allow | Deny | Deny | Deny | Deny | Deny | Allow | Deny | Read-Only | Deny |
| **Vendor Return & Corrections** | Allow | Deny | Deny | Deny | Deny | Deny | Allow | Deny | Read-Only | Deny |
| **Stock Handoff Posting** | Allow | Deny | Deny | Deny | Deny | Deny | Allow | Deny | Read-Only | Deny |
| **Supplier Invoice Processing** | Allow | Deny | Deny | Deny | Deny | Allow | Deny | Deny | Read-Only | Deny |
| **3-Way Match Exception Resolution**| Allow | Deny | Deny | Deny | Deny | Allow | Deny | Deny | Read-Only | Deny |
| **Contract Authoring & Versioning** | Allow | Deny | Deny | Deny | Deny | Deny | Deny | Allow | Read-Only | Deny |
| **Contract Obligation / Milestone** | Allow | Deny | Deny | Deny | Deny | Deny | Deny | Allow | Read-Only | Conditional (Own) |
| **Budget Allocations & Ledger** | Allow | Deny | Deny | Deny | Deny | Allow | Deny | Deny | Read-Only | Deny |
| **Supplier Scorecards** | Allow | Deny | Deny | Allow | Allow | Deny | Deny | Deny | Read-Only | Conditional (Own) |
| **Audit Logs & Compliance Exports** | Allow | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Allow (Read-Only) | Deny |
| **User & Role Administration** | Allow | Deny | Deny | Deny | Deny | Deny | Deny | Deny | Read-Only | Deny |

---

## 4.4 Authorization Enforcement

### 4.4.1 Django Views and Decorators
- Standard web views enforce authentication using `@login_required(login_url="/login/")` (e.g., `src/apps/contracts/views.py`, `src/apps/receipts/views.py`, `src/apps/approvals/views.py`).
- Role-specific checks are implemented via guard checks in view methods:
  ```python
  role_code = request.user.role_code
  if role_code not in [Role.STORES_RECEIVER, Role.SUPER_ADMIN] and not request.user.is_superuser:
      messages.error(request, "Permission denied.")
      return redirect("/")
  ```
- **Finding:** Role enforcement relies on procedural Python checks within individual view bodies rather than a centralized `@role_required` view decorator.

### 4.4.2 Django Permissions and Groups
- The project implements a direct foreign key relationship (`User.role -> Role`) rather than Django's built-in `auth_group` and `auth_permission` tables.
- **Finding:** Django's built-in `Permission` framework and `user.has_perm()` are not utilized in favor of explicit `role.code` comparisons.

### 4.4.3 DRF Permission Classes
- Applied explicitly on ViewSets and APIViews:
  - `UserViewSet` (`apps.accounts.api_views.py`): `permission_classes = [IsAuthenticated]`
  - Contract endpoints (`apps.contracts.api_views.py`): Enforce `IsLegalManager`, `IsAuditorReadOnly`, and vendor object scoping.
  - Receipt endpoints (`apps.receipts.api_urls.py`): Enforce `IsStoresReceiver` and `IsSuperAdmin`.

### 4.4.4 Object-Level Access Restrictions
1. **Requisitions:** Requesters are filtered to requisitions belonging to their assigned `department` (`PurchaseRequisition.objects.filter(department=user.department)`).
2. **Vendors:** Vendor portal users are constrained to their linked company record (`request.user.vendor`). Attempting to view another vendor's bids, documents, or POs returns HTTP 403 or 404.
3. **Auditors:** Global read-only enforcement via `IsAuditorReadOnly` rejecting unsafe HTTP methods (`POST`, `PUT`, `DELETE`).

### 4.4.5 UI Visibility vs. Server-Side Enforcement
- Navigation drawers and action buttons are conditionally hidden in templates (e.g., hiding "Start Goods Receipt" or "Submit Bid").
- **Verification:** Hiding UI elements is backed by server-side Python checks in `services.py` and view methods. Direct HTTP POST attempts by unauthorized roles consistently return HTTP 403 Forbidden or redirect with error alerts.

---

## 4.5 Least Privilege and Separation of Duties

### 4.5.1 Least-Privilege Access
- Each role is restricted to its domain: Stores Receivers cannot draft purchase orders; Requesters cannot access supplier invoices; Vendor Users cannot view competitor bids.

### 4.5.2 Separation of Duties (SoD) Checks
1. **Request Creation vs. Approval:** Requesters cannot approve their own purchase requisitions. The approval policy engine (`apps.approvals.services`) enforces that the approving actor must possess the `DEPT_APPROVER` or `PROC_MGR` role and cannot match the requisition creator.
2. **Vendor Onboarding vs. Approval:** Procurement Executives can review submitted KYC documentation; formal approval transitioning status to `ACTIVE` requires Procurement Manager or Super Admin sign-off.
3. **Bid Submission vs. Evaluation:** Vendor users submit bids; evaluations are restricted to Procurement Executives and Managers. Sealed envelope logic prevents evaluators from viewing pricing before technical scoring.
4. **Physical Intake vs. Invoice Approval:** Stores Receivers manage physical intake and quality inspection (`apps.receipts`); invoice matching and payment clearance are restricted to Finance/AP (`apps.invoices`).

---

## 4.6 Authentication and Authorization Security

### 4.6.1 Password Storage & Validation
- Passwords are encrypted using PBKDF2 with SHA-256 and an individual cryptographic salt.
- Enforces 4 validation layers with a 10-character minimum length constraint.

### 4.6.2 Session Security & Cookie Policies
- `SESSION_COOKIE_HTTPONLY = True`: Blocks JavaScript `document.cookie` access, preventing XSS-based session hijacking.
- `SESSION_COOKIE_SAMESITE = "Lax"`: Defends against Cross-Site Request Forgery during cross-origin navigations.
- `SESSION_COOKIE_SECURE = False`: Configured in development; enabled in `staging.py` and `prod.py` for TLS enforcement.
- `CSRF_COOKIE_HTTPONLY = False`: Configured intentionally to permit HTMX and client scripts to read the token and attach the `X-CSRFToken` header.

### 4.6.3 Login Failure & Brute-Force Protection
- Integrates `django-axes` via `AxesBackend` and `AxesMiddleware`.
- Locks out client combinations of username and IP address after **5 consecutive failed attempts** (`AXES_FAILURE_LIMIT = 5`).
- Enforces a cooloff period of ~3 minutes (`AXES_COOLOFF_TIME = 0.05`).
- Automatically resets failure count upon successful login (`AXES_RESET_ON_SUCCESS = True`).

### 4.6.4 Account Lifecycle & Deactivation
- `User.is_active` is evaluated during both web login (`portal_login_view`) and API authentication (`SessionLoginView`). Deactivated accounts are rejected with error messages before session generation.

### 4.6.5 Audit Logging for Security-Sensitive Events
- `apps.audit.models.AuditLog` records append-only entries for:
  - `LOGIN` (records user ID, email, role, IP address, and user agent).
  - `LOGOUT` (records termination of session).
  - State transitions, approvals, rejections, overrides, and exports.

---

## 4.7 Authentication & Authorization Test Coverage

| Test File Path | Behaviors Covered | Positive RBAC Tests | Negative RBAC Tests (403/Redirect) | Unauthenticated Tests (302) | Verified Status |
|:---|:---|:---:|:---:|:---:|:---:|
| `tests/test_all_role_logins.py` | POST login for all 10 seeded users | 10 | 0 | 0 | **Verified Passing** |
| `tests/test_all_role_dashboards.py` | Dashboard rendering per role | 10 | 0 | 0 | **Verified Passing** |
| `tests/test_contracts_workflow.py` | Legal manager vs Auditor vs Vendor access | 6 | 8 (Auditor mutate, Cross-vendor) | 2 | **Verified Passing** |
| `tests/test_day15_day18_stores_workflows.py` | Stores receiver receipt & inspection RBAC | 4 | 4 (Unauthorized roles) | 1 | **Verified Passing** |
| `tests/test_day19_day22_stores_workflows.py` | PO execution vs Stores receiver boundaries | 5 | 5 (Vendor/Requester blocked) | 2 | **Verified Passing** |
| `tests/test_stores_dedicated_queues.py` | Dedicated queue access & redirect | 3 | 0 | 1 | **Verified Passing** |

---

## 4.8 Gaps and Risks

| Finding ID | Description | Severity | Evidence / File Path | Security / Business Impact | Recommended Remediation | Verification Method | Status |
|:---|:---|:---:|:---|:---|:---|:---|:---:|
| **GAP-SEC-01** | Missing self-service password reset workflow | **Medium** | `src/apps/accounts/views.py` | Users cannot recover forgotten passwords without administrative intervention. | Implement Django's `PasswordResetView` with secure one-time tokens and rate limiting. | Functional test of email reset token link. | **Verified Gap** |
| **GAP-SEC-02** | Absence of unified `@role_required` decorator | **Low** | `src/apps/*/views.py` | Procedural role checks repeated across view bodies increase maintenance overhead. | Implement a centralized `@role_required(*roles)` decorator in `apps.accounts.decorators`. | Code audit and regression testing. | **Verified Gap** |
| **GAP-SEC-03** | Lack of JWT token authentication on REST API | **Low** | `src/config/settings/base.py` (Line 170) | External automated system integration requires session cookies rather than bearer tokens. | Add `rest_framework_simplejwt.authentication.JWTAuthentication` to DRF settings. | Postman / Pytest API token test. | **Potential Gap** *(Aligned with current ASSUMP-007)* |
| **GAP-SEC-04** | Email verification absent during vendor registration | **Medium** | `src/apps/vendors/views.py` | Potential registration of unverified or fraudulent email addresses. | Add automated activation link dispatch upon initial vendor sign-up. | End-to-end registration verification test. | **Potential Gap** |

---

## 4.9 Recommended Foundation and Next Steps

1. **Phase 1 — Centralize Role Decorator:** Create `apps.accounts.decorators.role_required(*allowed_roles)` to replace manual `if role_code != ...` blocks across all views.
2. **Phase 2 — Implement Password Recovery:** Add standard Django password reset and recovery views (`/password-reset/`) using secure cryptographic tokens and rate-limited email dispatch.
3. **Phase 3 — Dual Authentication Layer:** If external programmatic consumers require token auth, configure `djangorestframework-simplejwt` alongside `SessionAuthentication`.
4. **Phase 4 — Automated Role Verification in CI:** Integrate an automated permissions matrix test into `.github/workflows/ci.yml` that iterates every endpoint against every role code to assert positive and negative responses.

---

## 4.10 Acceptance Checklist

| Checklist Item | Evaluation Standard | Assessment Outcome | Notes / Evidence |
|:---|:---|:---:|:---|
| **Authentication Configuration Reviewed** | Django settings and backends inspected | **Verified** | `AxesBackend` + `ModelBackend` active in `base.py`. |
| **User Model and Lifecycle Documented** | UUID PK, email username, custom fields | **Verified** | `apps/accounts/models.py:User` fully documented. |
| **Roles Mapped to Responsibilities** | Complete 10-role matrix covering all modules | **Verified** | Section 4.3 exhaustive matrix documented. |
| **Server-Side Authorization Inspected** | Non-reliance on UI visibility confirmed | **Verified** | Python checks in `views.py` and `services.py`. |
| **Sensitive Endpoints Reviewed** | Approval, export, and financial endpoints scoped | **Verified** | Scoped across contracts, receipts, and audit modules. |
| **Object-Level Boundaries Assessed** | Department and vendor scoping verified | **Verified** | Requisitions (dept), Vendors (own data), Auditor (read-only). |
| **Approval Separation Assessed** | SoD between creation and approval verified | **Verified** | Approver cannot match requester in `approvals.services`. |
| **Security Settings Reviewed** | Session, CSRF, Password, Axes inspected | **Verified** | All settings recorded from `src/config/settings/base.py`. |
| **Relevant Tests Identified & Recorded** | Positive and negative test suites inventoried | **Verified** | 6 test suites identified with specific test cases. |
| **Confirmed Gaps Assigned Priorities** | Prioritized remediation table provided | **Verified** | 4 gaps documented with impact and remediation. |

# Security Review & OWASP Top 10 Checklist — ProcureSphere 360

PRD Reference: HPE-PRD-2026-PROC01 / VER-1.0-DRAFT  
Issuing Authority: HPE Team · Development Partner: VPD Technologies Pvt. Ltd.  
Compliance Standard: OWASP Top 10 Enterprise ERP Security Baseline

---

## 1. Executive Summary

ProcureSphere 360 implements defense-in-depth security across every architecture tier. All sensitive business state transitions, approval authorizations, financial limits, and object queries are enforced strictly in server-side domain logic and PostgreSQL database constraints—never exclusively in JavaScript or templates.

---

## 2. OWASP Top 10 (2021/2026) Verification & Controls

| Vulnerability Category | Risk Level | Architectural & Implementation Controls | Verification / Test Reference |
|---|---|---|---|
| **A01: Broken Access Control** | **Critical** | • RBAC enforced on every view and DRF viewset via backend permission classes (`IsSuperAdminUser`, `IsAuditorUser`, `IsProcurementUser`, `can_operate_governance`).<br>• Super Admin views explicitly block non-superadmins returning HTTP 403 (`A-1`).<br>• Compliance Auditor views enforce read-only visibility and block mutating actions (`A-2`).<br>• Vendor users strictly isolated to own records (`vendor_id` scoping); attempts to view internal staff views or competitor records return HTTP 403 (`A-3`, `SMK-AUTH-005`).<br>• Requesters restricted to departmental cost centers and cannot create requisitions in foreign departments (`ORG-CR-003`, `SMK-PR-064`). | `tests/test_audit_workstream1_rbac_hardening.py`<br>`docs/rbac_matrix.md` |
| **A02: Cryptographic Failures** | **High** | • Passwords hashed using PBKDF2 with SHA256 (Django default) + strict validator suite (`UserAttributeSimilarityValidator`, `MinimumLengthValidator`, `CommonPasswordValidator`, `NumericPasswordValidator`).<br>• Zero secrets committed to Git; all credentials, DB connection URIs, SMTP credentials, and signing keys loaded via environment variables (`.env.example` committed).<br>• Session cookies flagged with `HttpOnly`, `SameSite=Lax`, and `Secure` in production environments. | `.env.example`<br>`src/config/settings/base.py` |
| **A03: Injection** | **High** | • 100% parameterization via Django ORM; zero raw string SQL concatenation.<br>• Output auto-escaping enabled across all Django templates and HTMX partials.<br>• Safe document number generator (`generate_document_number`) parameterized by model and field name. | `src/apps/core/services.py` |
| **A04: Insecure Design** | **High** | • Multi-step approval policy engine fails closed if policies are missing or unconfigured (`PR-08`).<br>• Cumulative 3-way match quantity variance enforcement (`INV-CR-016`) prevents invoice over-billing across multiple receipts.<br>• PO and contract versions preserved immutably with snapshots on amendment.<br>• Sealed bidding envelope confidentiality enforced until formal stage opening (`ASSUMP-003`, `ASSUMP-014`). | `src/apps/invoices/services.py`<br>`src/apps/requisitions/services.py`<br>`tests/test_audit_workstream2_core_workflows.py` |
| **A05: Security Misconfiguration** | **Medium** | • `DEBUG=False` in staging and production.<br>• `django-axes` configured for brute-force login lockout (throttles IP and username after 5 failed attempts).<br>• Explicit `ALLOWED_HOSTS` and restricted CORS whitelist (`CORS_ALLOWED_ORIGINS`).<br>• Security headers enforced: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`. | `src/config/settings/base.py`<br>`src/config/settings/prod.py` |
| **A06: Vulnerable & Outdated Components** | **Medium** | • Pinned dependencies in `requirements.txt` and `pyproject.toml`.<br>• Python 3.11+ / 3.13, Django 4.2 LTS, DRF 3.14+.<br>• Automated Dependabot and GitHub Actions CI vulnerability scanning. | `pyproject.toml`<br>`requirements.txt` |
| **A07: Identification & Authentication Failures** | **High** | • Standardized on secure HTTP-Only session cookies with CSRF validation.<br>• Login lockout via `django-axes` with IP + username tracking.<br>• Forced password change on first login for provisioned credentials (`must_change_password`). | `src/apps/accounts/views.py`<br>`tests/test_all_role_logins.py` |
| **A08: Software & Data Integrity Failures** | **High** | • File upload validation enforces allowed extension, magic byte MIME-type inspection via `python-magic`, and 10MB size limits (`VEN-07`, `validate_file_upload`).<br>• Disallowed executable, script, or polyglot files are rejected with domain `ValidationError`.<br>• Uploaded files stored outside web root with randomized paths; access gated through authenticated views. | `src/apps/core/validators.py`<br>`tests/test_all_upload_rejections.py` |
| **A09: Security Logging & Monitoring Failures** | **Medium** | • Append-only, immutable `AuditLog` table capturing: `actor`, `action`, `target_model`, `target_object_id`, `previous_state`, `new_state`, `ip_address`, `request_id`, and `timestamp`.<br>• Model-level `delete()` and `update()` overrides block tampering or pruning of audit logs.<br>• Every approval, rejection, escalation, state transition, and document export records an audit event. | `src/apps/audit/models.py`<br>`src/apps/audit/services.py` |
| **A10: Server-Side Request Forgery (SSRF)** | **Low** | • No user-supplied URLs fetched asynchronously without strict internal IP address filtering and domain whitelisting.<br>• Webhook and notification dispatches restricted to pre-configured provider endpoints. | `src/apps/notifications/` |

---

## 3. Defense-in-Depth Implementation Details

### 3.1 File Upload Security Architecture
1. **Extension Whitelist**: Only `.pdf`, `.docx`, `.xlsx`, `.csv`, `.png`, `.jpg`, `.jpeg` allowed.
2. **MIME-Type & Magic Byte Inspection**: Uses `magic.from_buffer(chunk, mime=True)` to inspect actual file header signatures, blocking spoofed files (e.g. executable renamed to `.pdf`).
3. **Size Limit**: Enforced max upload limit of 10 MB.
4. **Tenant & Vendor Isolation**: Vendors may only download documents associated with their own legal entity (`vendor_id` match).

### 3.2 Audit Log Immutability
`AuditLog` records cannot be updated or deleted:
```python
def save(self, *args, **kwargs):
    if self.pk:
        raise ValidationError("AuditLog records are immutable and cannot be modified.")
    super().save(*args, **kwargs)

def delete(self, *args, **kwargs):
    raise ValidationError("AuditLog records are immutable and cannot be deleted.")
```

### 3.3 Authorization & Scoping Test Pack
Automated regression tests verify all access boundaries:
- `tests/test_audit_workstream1_rbac_hardening.py` (6 tests: SuperAdmin, Auditor, Vendor, Requester scoping)
- `tests/test_audit_workstream2_core_workflows.py` (7 tests: 3-way match, PR policy fail-closed, multi-step role checks, soft-delete, safe doc numbering, sourcing approval gating, MIME upload checks)
- `tests/test_audit_workstream3_async_beat_escalations.py` (5 tests: SuperAdmin dashboard KPIs, Celery Beat periodic schedules, 48h/72h escalation SLA engine, notifications)
- `tests/test_demo_1_vendor_kyc.py` through `tests/test_demo_7_spend_scorecard_audit_export.py` (8 tests: 100% Day-90 acceptance demonstrations)

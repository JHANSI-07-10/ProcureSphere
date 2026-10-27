# Provisional Vendor KYC Compliance, Legal Approval & Event Eligibility Policy

**Status:** PROVISIONAL / PENDING OFFICIAL RATIFICATION  
**Reference Document:** HPE-PRD-2026-PROC01  
**Target Authority:** HPE Legal & Compliance Team  
**Development Partner:** VPD Technologies Pvt. Ltd.  
**Policy Identifier:** `VER-1.0-PROVISIONAL-2026`  

---

## 1. Executive Summary & Purpose

This document articulates the provisional rules and technical architecture governing vendor registration, KYC document verification, legal approval, event participation eligibility, and lifecycle compliance monitoring for ProcureSphere 360.

**Important Notice:** The official approval policy has not yet been received from the HPE Legal Team. This proposed policy isolates policy-dependent behavior behind configurable rules so the core workflow operates securely without hardcoded business assumptions.

---

## 2. Distinction of Rulesets

To ensure compliance integrity, rules are categorized into three levels:

### A. Confirmed Project Requirements (PRD Mandated)
1. **Separation of Concerns:** Procurement registers vendors and issues portal access; Legal verifies KYC documents and authorizes event eligibility.
2. **Access Security:** Vendors cannot authenticate until approved by the Legal Manager or Super Admin.
3. **Sealed Bids & Sourcing Isolation:** Only `ACTIVE` and eligible vendors can be invited to sourcing events or submit bids.
4. **Append-Only Auditing:** Every status change, document verification decision, and eligibility transition writes an immutable `AuditLog` entry.

### B. Provisional Internal Business Rules (Working Defaults)
1. **Mandatory KYC Document Suite:**
   - **Certificate of Incorporation (`INC_CERT`)**: Validates corporate existence.
   - **Tax Clearance Certificate (`TAX_CLEARANCE`)**: Validates regulatory compliance.
   - **Bank Verification Letter (`BANK_LETTER`)**: Validates banking integrity.
2. **Document Validity Standard:**
   - Must have an attached physical file (verified non-empty).
   - If an expiry date is specified, it must be greater than or equal to the current system date.
   - Document verification must be recorded explicitly by a Legal Manager or Super Admin.
3. **Automated Expiry Alerts:** Expiration warnings dispatched at **30, 15, and 7 days** before document expiration.

### C. Legal & Regulatory Requirements Requiring Authorized Confirmation
1. *Jurisdiction-specific tax exemptions and alternate identification structures.*
2. *Periodic re-validation intervals for perpetual (non-expiring) incorporation certificates.*
3. *Explicit waiver and exception delegation authority for foreign or governmental vendors.*

---

## 3. Workflow State Transition Table

| Current Vendor Status | Event Eligibility | Allowed Next States | Trigger / Condition | Authorized Roles |
|---|---|---|---|---|
| `DRAFT` | Ineligible (`PENDING`) | `SUBMITTED`, `KYC_REVIEW` | Procurement registers vendor & dispatches credentials | Procurement Executive, Super Admin |
| `KYC_REVIEW` | Ineligible (`PENDING`) | `ACTIVE` (Eligible), `CHANGES_REQUIRED`, `REJECTED` | Legal Manager verifies KYC documents & evaluates policy | Legal Manager, Super Admin |
| `CHANGES_REQUIRED` | Ineligible (`INELIGIBLE`) | `KYC_REVIEW` | Vendor uploads corrected or replacement documents | Vendor User, Procurement Executive |
| `ACTIVE` | Eligible (`ELIGIBLE`) | `ON_HOLD`, `SUSPENDED`, `CHANGES_REQUIRED` | Compliance breach, document expiry, or legal hold | Legal Manager, Procurement Manager, Celery Beat (expiry) |
| `ON_HOLD` | Ineligible (`SUSPENDED`) | `ACTIVE` | Hold released with audit rationale | Procurement Executive, Procurement Manager, Legal |
| `SUSPENDED` | Ineligible (`SUSPENDED`) | `ACTIVE` | Reinstatement with formal audit evidence | Procurement Manager, Super Admin |

---

## 4. KYC Document Verification Life Cycle

```
[Uploaded by Vendor / PE]
           │
           ▼
     Status: PENDING
           │
     ┌─────┴──────────────────┐
     ▼                        ▼
[VERIFIED by Legal]    [REJECTED / CHANGES_REQUIRED]
     │                        │
     ▼                        ▼
Active in Profile     Vendor Notified & Resubmission Prompted
     │
     ▼ (When Expiry Date Passes)
Status: EXPIRED
     │
     ▼
Vendor Event Eligibility Suspended -> Automated Expiry Notifications
```

---

## 5. Event Eligibility Enforcement

Event participation is gated at the service layer:
1. **Event Invitation (`apps.sourcing.services.invite_vendors_to_event_service`):** Rejects vendors whose `is_event_eligible` is `False` or status is not `ACTIVE`.
2. **Draft Bid Preparation (`apps.sourcing.services.save_draft_bid_service`):** Blocks non-eligible vendors with a `ValidationError`.
3. **Bid Submission (`apps.sourcing.services.validate_bid_service`):** Enforces active event eligibility check before allowing bid finalization.

---

## 6. Document Expiry & Renewal Monitoring

1. A daily scheduled Celery Beat task (`scan_vendor_document_expirations_task`) scans active vendor documents.
2. Documents passing their expiry date are marked `EXPIRED`.
3. If an expired document is mandatory, the vendor's `is_event_eligible` is transitioned to `False` and `event_eligibility_status` is updated to `EXPIRED`.
4. Idempotent notifications are dispatched to the Vendor, the registering Procurement Executive, and the Legal Manager.

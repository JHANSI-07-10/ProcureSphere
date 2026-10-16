"""
Vendor governance RBAC (docs/rbac_matrix.md, "Vendor Onboarding & KYC" / "Vendor Risk & Status").

- PROC_EXEC: read governance, record risk assessments, place/release HOLD, KYC review.
- PROC_MGR: everything PROC_EXEC can do + suspend/blacklist and reinstate (PRD 3.2:
  "vendor governance").
- FINANCE_AP, AUDITOR: read-only.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.models import Role

GOVERNANCE_READ_ROLES = {
    Role.SUPER_ADMIN,
    Role.PROC_EXEC,
    Role.PROC_MGR,
    Role.FINANCE_AP,
    Role.LEGAL_MGR,
    Role.AUDITOR,
}
GOVERNANCE_OPERATE_ROLES = {Role.SUPER_ADMIN, Role.PROC_EXEC, Role.PROC_MGR}
GOVERNANCE_SUSPEND_ROLES = {Role.SUPER_ADMIN, Role.PROC_MGR}
# KYC document review (verify / reject / request corrections). Legal reviews the legal and
# regulatory documents; activation itself stays with GOVERNANCE_OPERATE_ROLES (docs/assumptions.md).
KYC_REVIEW_ROLES = GOVERNANCE_OPERATE_ROLES | {Role.LEGAL_MGR}


def _role(user):
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return Role.SUPER_ADMIN
    return user.role_code


def can_view_governance(user) -> bool:
    return _role(user) in GOVERNANCE_READ_ROLES


def can_operate_governance(user) -> bool:
    """Risk assessments, hold/release, KYC review actions."""
    return _role(user) in GOVERNANCE_OPERATE_ROLES


def can_review_kyc(user) -> bool:
    """Verify/reject KYC documents and request corrections."""
    return _role(user) in KYC_REVIEW_ROLES


def can_suspend_vendor(user) -> bool:
    """Suspend / blacklist / reinstate."""
    return _role(user) in GOVERNANCE_SUSPEND_ROLES


def governance_capabilities(user) -> dict:
    return {
        "can_view": can_view_governance(user),
        "can_operate": can_operate_governance(user),
        "can_suspend": can_suspend_vendor(user),
        "can_review_kyc": can_review_kyc(user),
    }


class VendorAccessPermission(BasePermission):
    """
    API access to vendor master data:
    - Vendor users: read their own vendor only (scoped in get_queryset).
    - Governance readers: read all.
    - Writes (create/update master data): governance operators only.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_vendor:
            return request.method in SAFE_METHODS
        if request.method in SAFE_METHODS:
            return can_view_governance(user)
        return can_operate_governance(user)


class GovernanceOperatePermission(BasePermission):
    def has_permission(self, request, view):
        return can_operate_governance(request.user)

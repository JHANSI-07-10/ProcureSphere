from rest_framework import permissions

from apps.accounts.models import Role


class IsLegalManager(permissions.BasePermission):
    """
    Allows access only to users with LEGAL_MGR role or SUPER_ADMIN.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        return role_code in [Role.LEGAL_MGR, Role.SUPER_ADMIN]

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class IsNotAuditor(permissions.BasePermission):
    """
    Prevents Compliance Auditors (strictly read-only role) from performing state changes or write operations.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        return role_code != Role.AUDITOR

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class CanManageContract(permissions.BasePermission):
    """
    Permission required to create, amend, terminate, or update core contract structure:
    Legal Manager, Procurement Manager, Procurement Executive, Super Admin.
    Requesters, Auditors, and Vendors are strictly prohibited (403) from write operations.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        return role_code in [
            Role.SUPER_ADMIN,
            Role.LEGAL_MGR,
            Role.PROC_MGR,
            Role.PROC_EXEC,
        ]

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class CanViewContract(permissions.BasePermission):
    """
    RBAC permission for viewing contracts:
    - Admin, Legal Mgr, Proc Mgr, Proc Exec, Auditor: View all
    - Vendor User: View only vendor's contracts
    - Contract Owner: View owned contracts
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        # Requesters have no contract access per RBAC matrix
        if role_code == Role.REQUESTER:
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )

        if role_code in [
            Role.SUPER_ADMIN,
            Role.LEGAL_MGR,
            Role.PROC_MGR,
            Role.PROC_EXEC,
            Role.AUDITOR,
        ]:
            return True

        if role_code == Role.VENDOR_USER:
            vendor = getattr(request.user, "vendor", None)
            return vendor is not None and obj.vendor_id == vendor.id

        return obj.contract_owner_id == request.user.id

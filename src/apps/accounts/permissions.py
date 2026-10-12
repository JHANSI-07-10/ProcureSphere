from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Role


class HasRole(BasePermission):
    """
    Custom permission class verifying user's assigned role against allowed roles list.
    Usage in views: permission_classes = [HasRole([Role.PROC_EXEC, Role.PROC_MGR])]
    """

    allowed_roles = []

    def __init__(self, roles=None):
        if roles:
            self.allowed_roles = roles

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        user_role = request.user.role_code
        return user_role in self.allowed_roles


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_superuser or request.user.role_code == Role.SUPER_ADMIN)
        )


class IsNotVendorUser(BasePermission):
    """
    Blocks external vendor users from accessing internal resources and directories.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return not (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        )


class IsRequester(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.REQUESTER or request.user.is_superuser)
        )


class IsDeptApprover(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.DEPT_APPROVER or request.user.is_superuser)
        )


class IsProcurementExec(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (
                request.user.role_code in [Role.PROC_EXEC, Role.PROC_MGR]
                or request.user.is_superuser
            )
        )


class IsProcurementManager(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.PROC_MGR or request.user.is_superuser)
        )


class IsFinanceAP(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.FINANCE_AP or request.user.is_superuser)
        )


class IsStoresReceiver(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.STORES_RECEIVER or request.user.is_superuser)
        )


class IsLegalManager(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.LEGAL_MGR or request.user.is_superuser)
        )


class IsAuditorReadOnly(BasePermission):
    """
    Auditor has global read-only access (GET, HEAD, OPTIONS). State changes return 403.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.role_code == Role.AUDITOR:
            return request.method in SAFE_METHODS
        return True


class IsVendorUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role_code == Role.VENDOR_USER or request.user.is_superuser)
        )

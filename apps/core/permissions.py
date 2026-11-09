from rest_framework.permissions import BasePermission

from apps.accounts.models import Role


def role_permission(*roles):
    """Build a DRF permission class allowing only users whose role is in `roles` (admin always allowed)."""
    allowed = set(roles) | {Role.ADMIN}

    class _RolePermission(BasePermission):
        def has_permission(self, request, view):
            u = request.user
            return bool(u and u.is_authenticated and u.is_active and u.role in allowed)

    _RolePermission.__name__ = "Has" + "Or".join(sorted(r.title() for r in allowed))
    return _RolePermission


IsDesigner = role_permission(Role.DESIGNER)
IsOperations = role_permission(Role.OPERATIONS)
IsSupport = role_permission(Role.SUPPORT)
IsAdminRole = role_permission()
IsBackoffice = role_permission(Role.DESIGNER, Role.OPERATIONS, Role.SUPPORT)

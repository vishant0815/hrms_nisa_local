from rest_framework.permissions import BasePermission

from .models import Role


def _has_role(user, *roles) -> bool:
    return bool(user and user.is_authenticated and user.role in roles)


class IsOwner(BasePermission):
    def has_permission(self, request, view):
        return _has_role(request.user, Role.OWNER)


class IsOwnerOrHR(BasePermission):
    """Owner and HR/Admin: the people who manage accounts across all stores."""

    def has_permission(self, request, view):
        return _has_role(request.user, Role.OWNER, Role.HR)


class IsManagerOrAbove(BasePermission):
    def has_permission(self, request, view):
        return _has_role(request.user, Role.OWNER, Role.HR, Role.MANAGER)

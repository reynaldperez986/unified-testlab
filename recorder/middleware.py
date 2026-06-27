"""
Tenant resolution middleware.

Sets ``request.tenant_id`` (UUID | None) on every request by looking up
the authenticated user's UserProfile.  Views and the replay engine use this
value to scope DB queries and in-memory job registries to a single tenant.
"""
from __future__ import annotations


class TenantMiddleware:
    """Resolve and attach tenant_id to every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant_id = self._resolve(request)
        return self.get_response(request)

    @staticmethod
    def _resolve(request) -> "str | None":
        if not getattr(request, "user", None):
            return None
        if not request.user.is_authenticated:
            return None
        try:
            profile = request.user.profile  # OneToOneField from UserProfile
            if profile.tenant_id:
                return str(profile.tenant_id)
        except Exception:
            pass
        return None

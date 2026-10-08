"""Bearer authentication for the studio JSON endpoints.

``Authorization: Bearer trellum_pk_…`` resolves to the key's user for this
one request -- no session, no cookie, no CSRF. A header that carries our
prefix and does not verify is answered 401 right here rather than falling
through to session auth: a revoked or mistyped key must never quietly
succeed because a browser cookie happened to be present too.
"""
from __future__ import annotations

from django.http import JsonResponse

from .models import API_KEY_PREFIX, ApiKey

_BEARER = "Bearer " + API_KEY_PREFIX
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class ApiKeyMiddleware:
    """After ``AuthenticationMiddleware`` and ``SessionSecurityMiddleware``
    (see ``trellum_portal/settings/base.py``). Sets ``request.user``,
    ``request.api_key`` and ``request._dont_enforce_csrf_checks``. Only studio
    JSON and MCP routes accept this identity; their view guards still check
    the user's roles, and ``get_effective`` confines the key to its org."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith(_BEARER):
            return self.get_response(request)
        key = ApiKey.authenticate(header[len("Bearer "):].strip())
        if key is None:
            return JsonResponse({"error": "invalid_api_key"}, status=401)
        request.user = key.user
        request.api_key = key
        request._dont_enforce_csrf_checks = True
        key.touch()
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):  # noqa: ARG002
        """``write`` scope for any non-GET -- unless the view says it checks
        scope per operation itself (``checks_api_key_scope``: the MCP
        endpoint, where every method is a POST and only mutating tools
        write). The same shape as ``csrf_exempt``."""
        key = getattr(request, "api_key", None)
        if key is None:
            return None
        route = request.resolver_match.route
        studio_prefix = "s/<slug:org_slug>/<slug:studio_slug>/"
        if not (route.startswith(studio_prefix + "api/") or route == studio_prefix + "mcp"):
            return JsonResponse({"error": "session_required"}, status=403)
        if view_kwargs.get("org_slug") != key.org.slug:
            return JsonResponse({"error": "not_found"}, status=404)
        if key.can_write or request.method in _SAFE_METHODS:
            return None
        if getattr(view_func, "checks_api_key_scope", False):
            return None
        return JsonResponse({"error": "write_scope_required"}, status=403)

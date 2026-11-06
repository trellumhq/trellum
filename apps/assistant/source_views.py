"""Session-owned source query status and cancellation."""
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.assistant import source_queries
from apps.assistant.models import SourceQuery
from apps.assistant.source_tools import evidence
from apps.assistant.views import _get_session, _session_only
from apps.core import roles
from apps.core.permissions import require_studio_role


@require_studio_role(roles.VIEWER)
@require_http_methods(["GET", "POST"])
@_session_only
def query_status(request, org_slug, studio_slug, session_id, query_id):
    session = _get_session(request, session_id)
    try:
        fn = source_queries.request_cancel if request.method == "POST" else source_queries.public_result
        payload = fn(query_id, actor=request.user, studio=request.studio, session=session)
        if payload.get("result"):
            job = SourceQuery.objects.get(pk=query_id, session=session, user=request.user)
            payload["evidence"] = evidence(job, payload)
        payload["status_url"] = request.path
        response = JsonResponse(payload, status=202 if payload["state"] in SourceQuery.ACTIVE_STATES else 200)
    except PermissionDenied:
        response = JsonResponse({"error": "This source read is no longer accessible."}, status=403)
    response["Cache-Control"] = "no-store"
    return response

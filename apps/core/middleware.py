"""Request-scoped logging context.

Gives every request an id, echoes it back on the response, and puts it where
the logging filter can find it. When something goes wrong the operator has one
string to grep for across web, worker and sandbox logs instead of a timestamp
range and hope.

An inbound ``X-Request-ID`` is honoured so a reverse proxy or load balancer that
already stamps one stays the source of truth — but it is bounded and stripped of
anything that is not safe in a log line, because it is client-controlled and
ends up in files people later grep.
"""
from __future__ import annotations

import re
import uuid

from apps.core.logging import current_request_id

HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"
#: Log-safe characters only, and short enough that it cannot pad a log file.
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_LEN = 64


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(HEADER, "")
        request_id = _SAFE.sub("", incoming)[:_MAX_LEN] if incoming else ""
        if not request_id:
            request_id = uuid.uuid4().hex[:16]

        request.request_id = request_id
        token = current_request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            # Reset rather than leave it set: this context may be reused for
            # unrelated work, which would then log under the wrong id.
            current_request_id.reset(token)

        response[RESPONSE_HEADER] = request_id
        return response


class ContentSecurityPolicyMiddleware:
    """A CSP for the management surface only.

    The portal serves two very different kinds of page from one origin:

    * the management surface (hub, settings, dashboard shell), whose markup we
      write, and
    * **built report pages**, which are tenant-authored HTML and JavaScript
      produced by the reporting framework.

    A policy strict enough to be worth having would break the second, and one
    loose enough for it — `unsafe-inline`, `unsafe-eval` — is barely a policy at
    all. So report content is exempt and the rest is covered. That is not a
    dodge: it is the honest boundary, and it is written down here rather than
    discovered later.

    The residual risk is real and stays on the P5 list: tenant JS runs on the
    same origin as the session cookie. The durable fix is serving report output
    from a separate origin, which is a bigger change than a header.

    Report-Only by default. A policy that silently breaks a customer's console
    is worse than one that reports first, and the header name is the only
    difference.
    """

    #: The report-content routes, by URL name rather than by path substring.
    #: A substring test would exempt anything that merely contains "/r/", and
    #: an exemption that drifts with URL shapes is a security control that
    #: quietly stops applying. The public share routes serve the same
    #: tenant-authored output (internal planning ticket #92), and an embed link sets its own
    #: ``frame-ancestors`` header there (apps.reports.views._share_response).
    _EXEMPT = {"report-page", "report-asset", "vendor-asset", "share-entry", "share-asset"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        from django.conf import settings

        policy = getattr(settings, "CONTENT_SECURITY_POLICY", "")
        if not policy:
            return response
        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name in self._EXEMPT:
            return response

        header = (
            "Content-Security-Policy-Report-Only"
            if getattr(settings, "CSP_REPORT_ONLY", True)
            else "Content-Security-Policy"
        )
        response.setdefault(header, policy)
        return response

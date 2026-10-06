"""``/_vendor/*`` route: adapter between Django and the framework's
``serve_vendor_request`` contract function, which expects an
``http.server``-style handler. Reports reference vendor assets by absolute
path; without this route every report renders without charts."""
from __future__ import annotations

import io

from django.http import HttpResponse

from trellum.rendering.cdn import serve_vendor_request


class _HandlerShim:
    """Just enough of BaseHTTPRequestHandler for serve_vendor_request."""

    def __init__(self, path: str):
        self.path = path
        self.status = 200
        self.headers_out: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def send_response(self, code: int):
        self.status = code

    def send_error(self, code: int, message: str | None = None):  # noqa: ARG002
        self.status = code

    def send_header(self, key: str, value: str):
        self.headers_out[key] = value

    def end_headers(self):
        pass


def vendor_asset(request, path: str):
    shim = _HandlerShim(f"/_vendor/{path}")
    handled = serve_vendor_request(shim)
    if not handled:
        shim.status = 404
    resp = HttpResponse(
        shim.wfile.getvalue(),
        status=shim.status,
        content_type=shim.headers_out.get("Content-Type", "application/octet-stream"),
    )
    for key, value in shim.headers_out.items():
        if key.lower() not in ("content-type", "content-length"):
            resp[key] = value
    return resp

"""Injecting the overlay into the SERVED copy of a report.

The file on disk never contains the script tag -- review mode is a
property of the dev server, not of the artifact.
"""

from __future__ import annotations

import os

from trellum.assets import load_css, load_js
from trellum.review.state import STATE

#: Concatenation order is execution order: these land in one IIFE, and later
#: modules call into earlier ones at load. ``state`` first (everything reads
#: it), ``status`` last (it starts the poll that drives the rest).
OVERLAY_MODULES: tuple[str, ...] = (
    "state",
    "styles",
    "dom",
    "identify",
    "select",
    "chart_target",
    "popover",
    "queue",
    "panel",
    "presence",
    "log",
    "net",
    "end_session",
    "keyboard",
    "status",
)

_OVERLAY_TAG = b'<script src="/_fw/review.js" defer></script>'


def overlay_js() -> str:
    """The overlay script, assembled from static/js/review/ and its stylesheet.

    Served, never written to disk. The stylesheet is substituted into
    ``styles.js`` rather than fetched separately so the overlay stays a single
    request that cannot half-arrive.
    """
    parts = [load_js(f"review/{name}.js") for name in OVERLAY_MODULES]
    body = "\n".join(parts).replace(
        "__FW_REVIEW_CSS__", _js_string(load_css("review.css")),
    )
    return (
        "/* Review overlay -- injected by the dev server ONLY while review mode\n"
        " * is active (see trellum.review). The file on disk never contains this\n"
        " * tag. Source: trellum/static/js/review/*.js + static/css/review.css.\n"
        " */\n"
        "(function () {\n"
        "    'use strict';\n"
        "    if (window._fwReviewInit) return;\n"
        "    window._fwReviewInit = true;\n\n"
        f"{body}\n"
        "})();\n"
    )


def _js_string(text: str) -> str:
    """A JS string literal holding *text*, safe inside a <script> block."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        # `</script` inside a string still ends the block in an HTML parser.
        .replace("</", "<\\/")
    )
    return f"'{escaped}'"

def inject_overlay(body: bytes) -> bytes:
    """Insert the overlay script tag before the last </body> (or append)."""
    idx = body.rfind(b"</body>")
    if idx == -1:
        idx = body.rfind(b"</BODY>")
    if idx == -1:
        return body + b"\n" + _OVERLAY_TAG + b"\n"
    return body[:idx] + _OVERLAY_TAG + body[idx:]

def maybe_serve_injected_html(handler, served_dir: str) -> bool:
    """Serve report HTML with the overlay injected while review is active.

    Off (the common case) this is a single boolean check. On, it takes over
    only requests that resolve to an existing .html file (directories map to
    their index.html), served uncompressed so injection stays trivial.

    An ended session stops injection immediately: the page that was open
    when the session ended shows its own ended banner, but a FRESH page load
    must get a clean report -- not an overlay whose first act is announcing
    a session that no longer exists.
    """
    STATE.bind_dir(served_dir)
    if not STATE.enabled or STATE.ended:
        return False
    if handler.command != "GET":
        return False
    fs_path = handler.translate_path(handler.path)
    if os.path.isdir(fs_path):
        fs_path = os.path.join(fs_path, "index.html")
    if os.path.splitext(fs_path)[1].lower() not in (".html", ".htm"):
        return False
    try:
        with open(fs_path, "rb") as fh:
            body = inject_overlay(fh.read())
    except OSError:
        return False
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True

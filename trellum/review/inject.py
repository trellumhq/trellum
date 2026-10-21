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
    "activity",
    "log",
    "net",
    "end_session",
    "keyboard",
    "status",
)

# The workspace uses the same selection and queue UI as the ordinary review
# overlay, but its adapter sends changes to the parent workspace. In particular
# it must not start the ordinary per-report poll, send, or end-session flows.
WORKSPACE_OVERLAY_MODULES: tuple[str, ...] = (
    "state", "styles", "dom", "identify", "select", "chart_target",
    "popover", "queue", "panel", "presence", "activity", "log", "keyboard",
    "workspace_adapter",
)

# ``workspace.js`` is loaded directly by analysis-workspace.html; the other
# review assets are concatenated by one of the overlay assemblers above.
WORKSPACE_SCRIPT_MODULE = "workspace"
REVIEW_MODULES: tuple[str, ...] = tuple(dict.fromkeys((
    *OVERLAY_MODULES, *WORKSPACE_OVERLAY_MODULES, WORKSPACE_SCRIPT_MODULE,
)))

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
        "/* Review overlay -- injected by the dev server for active reviews and\n"
        " * their read-only final observations. The file on disk never contains this\n"
        " * tag. Source: trellum/static/js/review/*.js + static/css/review.css.\n"
        " */\n"
        "(function () {\n"
        "    'use strict';\n"
        "    if (window._fwReviewInit) return;\n"
        "    window._fwReviewInit = true;\n\n"
        f"{body}\n"
        "})();\n"
    )


def workspace_overlay_js() -> str:
    """Assemble the shared review widget with the workspace child adapter."""
    parts = [load_js(f"review/{name}.js") for name in WORKSPACE_OVERLAY_MODULES]
    body = "\n".join(parts).replace(
        "__FW_REVIEW_CSS__", _js_string(load_css("review.css")),
    )
    return (
        "/* Workspace review widget -- served only for an authenticated child. */\n"
        "(function () {\n"
        "    'use strict';\n"
        "    if (window._fwWorkspaceOverlayInit) return;\n"
        "    window._fwWorkspaceOverlayInit = true;\n\n"
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

def inject_overlay(body: bytes, *, session: int | None = None, ended: bool = False) -> bytes:
    """Insert the overlay script tag before the last </body> (or append)."""
    tag = _OVERLAY_TAG
    if session is not None:
        tag = (f'<script>window._fwReviewSession={{"session":{session},'
               f'"ended":{str(ended).lower()}}};</script>').encode() + tag
    idx = body.rfind(b"</body>")
    if idx == -1:
        idx = body.rfind(b"</BODY>")
    if idx == -1:
        return body + b"\n" + tag + b"\n"
    return body[:idx] + tag + body[idx:]


def _inject_workspace_bridge(body: bytes, slug: str, session: dict,
                             frame_id: str) -> bytes:
    import json
    config = {"slug": slug, "kind": "analysis" if slug == session["slug"] else "report",
              "session": STATE.session, "analysis_slug": session["slug"],
              "frame_id": frame_id}
    payload = (json.dumps(config, ensure_ascii=False).replace("<", "\\u003c")
               .replace(">", "\\u003e").replace("&", "\\u0026")
               .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    tag = ("<script>window._fwWorkspaceChild=" + payload + ";</script>"
           "<script src=\"/_fw/review/workspace-bridge.js?fw_session="
           + str(STATE.session) + "\" defer></script>").encode()
    idx = body.lower().rfind(b"</body>")
    return body[:idx] + tag + body[idx:] if idx >= 0 else body + tag

def maybe_serve_injected_html(handler, served_dir: str) -> bool:
    """Serve report HTML with the active or read-only review overlay.

    Off (the common case) no files are read. On, it takes over only requests
    that resolve to an existing .html file (directories map to
    their index.html), served uncompressed so injection stays trivial.

    Fresh visits after ending get a clean report. An observing tab pins its
    session in the URL when ending, so a final rebuild/reload can restore its
    read-only conversation even after the agent consumes the ended poll.
    """
    STATE.bind_dir(served_dir)
    if handler.command != "GET":
        return False
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(handler.path).query)
    with STATE._cond:
        session, ended = STATE.session, STATE.ended
        observing = query.get("fw_review_session", [""])[0]
        if observing and (observing != str(session) or STATE.analysis_slug):
            return False
        if ended:
            if not observing:
                return False
        elif not STATE.enabled:
            return False
    workspace_session = STATE.analysis_status()
    workspace_mode = (
        query.get("fw_workspace", [""])[0] == workspace_session.get("slug")
        and query.get("fw_session", [""])[0] == str(STATE.session)
        and bool(workspace_session.get("active"))
    )
    fs_path = handler.translate_path(handler.path)
    if os.path.isdir(fs_path):
        fs_path = os.path.join(fs_path, "index.html")
    if os.path.splitext(fs_path)[1].lower() not in (".html", ".htm"):
        return False
    try:
        with open(fs_path, "rb") as fh:
            body = fh.read()
    except OSError:
        return False
    if workspace_mode:
        from trellum.project import get_project_root
        from trellum.review.workspace import catalog
        reports = catalog(get_project_root())
        rel = os.path.relpath(fs_path, served_dir).replace(os.sep, "/")
        slug = rel.split("/", 1)[0]
        frame_id = query.get("fw_frame", [""])[0]
        if (slug not in reports or (slug != workspace_session["slug"]
                and reports[slug]["kind"] != "report")
                or not frame_id or len(frame_id) > 100
                or any(not (c.isalnum() or c in "_-:") for c in frame_id)):
            return False
        from trellum.runner.live_query_dev import inject_dev_live_query_flags
        body = _inject_workspace_bridge(inject_dev_live_query_flags(body, handler), slug,
                                        workspace_session, frame_id)
    else:
        # Review takes over the HTML response before the live-query handler can
        # run, so compose both dev-only additions into this served copy.
        from trellum.runner.live_query_dev import inject_dev_live_query_flags
        body = inject_overlay(inject_dev_live_query_flags(body, handler),
                              session=session, ended=ended)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True

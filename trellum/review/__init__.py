"""Review mode: collect element-anchored change requests from the browser
and hand them to the coding agent driving the session.

The dev servers in ``runner.py`` call two hooks per request:

- :func:`handle_review_request` answers everything under ``/_fw/review``:
  the overlay script, the browser's feedback/status endpoints, and the
  agent's long-poll/reply/end endpoints.
- :func:`maybe_serve_injected_html` serves report HTML with the overlay
  ``<script>`` injected into the SERVED copy only -- the file on disk is
  never touched, so build output stays byte-identical and review mode costs
  nothing when it is off.

State lives in this process, guarded by one condition variable. The browser
keeps its own unsent queue in localStorage, so a server restart loses at
most a batch that was sent but not yet polled -- and even that is usually
recoverable through ``/_fw/review/last``.
"""

from trellum.review.http import handle_review_request
from trellum.review.inject import inject_overlay, maybe_serve_injected_html, overlay_js
from trellum.review.state import STATE, ReviewInactive, ReviewState

__all__ = [
    "STATE",
    "ReviewState",
    "ReviewInactive",
    "handle_review_request",
    "inject_overlay",
    "maybe_serve_injected_html",
    "overlay_js",
]

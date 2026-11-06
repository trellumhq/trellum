"""Console encoding, and the timestamp every log line carries."""

from __future__ import annotations

import sys
from datetime import datetime, timezone


def _force_utf8_console() -> None:
    """Make stdout/stderr UTF-8 so progress output cannot kill a run.

    The Windows console defaults to cp1252, which cannot encode the box
    drawing characters and status glyphs this runner prints. An
    unencodable character raises UnicodeEncodeError mid-run, failing a
    report build for a purely cosmetic reason. errors="replace" degrades
    one character instead of the whole run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

def _ts() -> str:
    """Return a short UTC timestamp for structured output."""
    return datetime.now(timezone.utc).strftime('%H:%M:%S')


def _closing_move() -> None:
    """Point a finished local build toward review, not a plain server."""
    print("\nFor a human review: python -m trellum review start reports/<slug> "
          "(share the printed URL)", flush=True)
    print("Keep review poll --report <slug> active; rebuild with --no-serve "
          "before replying with the feedback session.", flush=True)
    print("Build-only work ends here. Use serve --background only for an "
          "explicitly requested plain preview without feedback.", flush=True)

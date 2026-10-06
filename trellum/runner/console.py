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
    """The session's closing move, said where the agent is already reading:
    a finished build the user cannot click is not done."""
    print("\nWhen you are done: python -m trellum serve --background   "
          "(prints the link -- give it to the user)", flush=True)

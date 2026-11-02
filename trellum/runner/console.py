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
    """Point human review to the appropriate local workspace after a build."""
    print("\nFor human review, open the local workspace and listen for feedback:\n"
          "  Analysis: python -m trellum analysis session reports/<slug>\n"
          "  Report:   python -m trellum review start reports/<slug>\n"
          "  Listen:   python -m trellum review poll --report <slug>\n"
          "Build-only or CI requests need no preview. For an explicitly requested "
          "plain preview: python -m trellum serve --background", flush=True)

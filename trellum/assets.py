"""Where the client-side assets live, and the one way to read them.

The framework ships real ``.js`` and ``.css`` files under ``static/`` and
inlines them into the generated report at build time. It did not always: the
runtime and every component's ``client_js()`` used to be a Python string
literal, which meant ~6,700 lines of JavaScript that no editor highlighted, no
linter read, and that had to double every ``{`` for f-string escaping.
``static/review.js`` was the one piece that had escaped, and it is the model
this generalises.

The output is unchanged by that move: these strings are still concatenated
into a ``<script>`` block in ``index.html``, so a report remains a
self-contained page with no fetches of its own.

Caching is keyed on modification time rather than path alone. The dev server
rebuilds a report in the same process (``runner._schedule_refreshes``), so a
plain cache would serve the file as it was when the process started -- editing
a component's JS would appear to do nothing until you restarted the server.
"""
from __future__ import annotations

from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent / "static"

#: ``{path: (mtime_ns, text)}``. Small and process-lifetime by design; the
#: whole client runtime is well under a megabyte.
_cache: dict[Path, tuple[int, str]] = {}


def _read(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:                                    # pragma: no cover
        raise FileNotFoundError(
            f"asset not found: {path}. Every asset ships inside the package "
            f"(see package-data in pyproject.toml); a miss here usually means "
            f"a file was added to static/ but not to the wheel."
        ) from exc

    hit = _cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    text = path.read_text(encoding="utf-8")
    _cache[path] = (mtime, text)
    return text


def load_js(relative: str) -> str:
    """Return the contents of ``static/js/<relative>``.

    The text is inlined verbatim, so it must be valid on its own at the point
    it is concatenated -- no module syntax, no top-level ``await``.
    """
    return _read(STATIC_ROOT / "js" / relative)


def load_css(relative: str) -> str:
    """Return the contents of ``static/css/<relative>``."""
    return _read(STATIC_ROOT / "css" / relative)


def js_dir(relative: str = "") -> Path:
    """The on-disk directory for a group of JS modules.

    Exists so tests can assert that every file present is actually loaded --
    an orphaned module is dead code that looks live.
    """
    return STATIC_ROOT / "js" / relative if relative else STATIC_ROOT / "js"

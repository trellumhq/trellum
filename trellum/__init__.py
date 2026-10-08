"""BI Report Framework -- build structured, themed reports from Python."""

__version__ = "0.4.0"

from trellum.project import get_project_root, set_project_root
from trellum.report import BaseReport, ReportContext

__all__ = [
    "BaseReport",
    "ReportContext",
    "connect",
    "get_project_root",
    "query",
    "set_project_root",
    "sources",
    "__version__",
]

_ADHOC = ("connect", "query", "sources")


def __getattr__(name: str):
    # `from trellum import query` without paying for pandas on every
    # `import trellum`: trellum.data's __init__ is eager, and measured at
    # +1.2s of cold start -- on every CLI call and the session-start hook.
    if name in _ADHOC:
        from trellum.data import adhoc
        return getattr(adhoc, name)
    raise AttributeError(f"module 'trellum' has no attribute {name!r}")

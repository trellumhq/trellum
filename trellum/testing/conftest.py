"""Shared pytest configuration.

Two guards live here, both against a run that looks fine and isn't.

**A skip is not a pass.** A local run without Playwright installed reports
something like ``270 passed, 106 skipped`` and exits 0. That looks like
success, and it isn't -- roughly a third of the suite, including everything
that exercises the browser runtime and the rendered page, never ran.

CI installs Playwright and fails on any skip, so this cannot rot there. Locally
nothing said anything, which is how somebody concludes their change is fine
after testing two thirds of the code.

The warning goes through ``pytest_terminal_summary`` rather than
``pytest_report_header`` because the documented way to run this suite is
``pytest -q``, and ``-q`` suppresses the header entirely -- a warning nobody
sees in the standard invocation is not a warning. The summary hook also puts
the message directly beneath the "N skipped" line it is explaining.

**A child process must be able to import the framework.** See the PYTHONPATH
block below.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Tests that spawn a subprocess with a working directory of their own -- a
# scaffold into tmp_path, `doctor` running its hook check from a fake project
# root -- hand that child no way to ``import trellum``. The repo root IS the
# package, nothing installs it, and the shim that makes it importable
# (demo/trellum/__init__.py) only applies while demo/ is the working
# directory. The child inherits the environment but not the cwd, so it dies
# with ``No module named trellum`` and the test reads as a product failure
# rather than a missing path.
#
# So pass the same shim down through the environment. demo/ rather than the
# checkout's parent directory because the shim repoints its own ``__path__``
# at the repo root, which works whatever this checkout is called -- CI's is
# named `trellum` and a local worktree is not.
_DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
_PYTHONPATH = os.environ.get("PYTHONPATH", "")
if str(_DEMO_DIR) not in _PYTHONPATH.split(os.pathsep):
    os.environ["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(_DEMO_DIR), _PYTHONPATH) if part
    )


def _browser_available() -> tuple[bool, str]:
    """(usable, reason) for a Playwright+Chromium setup that can actually run."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright is not installed"

    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception as exc:                                  # pragma: no cover
        return False, f"Playwright could not start ({type(exc).__name__})"

    if not path or not os.path.exists(path):
        return False, "Chromium is not downloaded"
    return True, ""


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    skipped = len(terminalreporter.stats.get("skipped", []))
    if not skipped:
        return

    usable, reason = _browser_available()
    if usable:
        return

    w = terminalreporter.write_line
    w("")
    w(f"  !!  {skipped} test(s) skipped: {reason}.", red=True, bold=True)
    w("  !!  That is the browser half of this suite -- the JS runtime and the", red=True)
    w("  !!  rendered page. A green run without it does not mean what it looks", red=True)
    w("  !!  like, and CI fails the build on any skip. To match CI locally:", red=True)
    w("  !!", red=True)
    w("  !!      pip install playwright", red=True)
    w("  !!      python -m playwright install --with-deps chromium", red=True)
    w("")


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """The once-a-day release check (trellum/update_check.py) must never reach
    the network from a test, in-process or in a build this suite spawns. The
    check's own tests lift this and replace the opener."""
    monkeypatch.setenv("FW_UPDATE_CHECK", "0")

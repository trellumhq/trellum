"""Portal processes start without the framework's data libraries.

A gunicorn worker imports the URLconf on its first request, and runworker
runs the system checks (which import the URLconf too) before it starts.
Neither needs pandas, numpy or pyarrow, which cost roughly 75 MB per process;
they load only when a request or build actually uses them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

_PROBE = """
import sys
import django
django.setup()
import trellum_portal.urls  # noqa: F401
from trellum_portal.wsgi import application  # noqa: F401
from django.core import checks
checks.run_checks()
import apps.runner.management.commands.runworker  # noqa: F401
import apps.runner.executor  # noqa: F401
print(",".join(m for m in ("pandas", "numpy", "pyarrow") if m in sys.modules))
"""


@pytest.mark.heavy
def test_web_and_worker_startup_do_not_load_pandas():
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="trellum_portal.settings.test")
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True,
        cwd=ROOT, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    loaded = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert loaded == "", f"loaded at startup: {loaded}"

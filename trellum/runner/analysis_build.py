"""Stage analysis artifacts so a failed rebuild preserves the last page."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def staged_analysis_build(report_dir, output_dir, **options) -> str:
    from trellum.runner.execute import _run_report

    destination = Path(output_dir).absolute()
    if destination.is_symlink():
        raise ValueError("Analysis output directory cannot be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".analysis-build-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "output"
        staging.mkdir()
        _run_report(report_dir, str(staging), **options)
        previous = Path(temporary) / "previous"
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(staging, destination)
        except Exception:
            if previous.exists():
                os.replace(previous, destination)
            raise
    return str(destination)

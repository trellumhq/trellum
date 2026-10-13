"""Stage analysis artifacts so a failed rebuild preserves the last page."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def staged_analysis_build(report_dir, output_dir, **options) -> str:
    from trellum.runner.execute import _run_report

    destination = Path(output_dir).absolute()
    if destination.is_symlink():
        raise ValueError("Analysis output directory cannot be a symlink")
    # The sandbox exposes only this directory as writable. Its parent may be
    # read-only, and the directory itself may be a mount point that cannot be
    # renamed. Stage and back up artifacts within that same writable mount.
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".analysis-build-", dir=destination) as temporary:
        staging = Path(temporary) / "output"
        staging.mkdir()
        _run_report(report_dir, str(staging), **options)
        previous = Path(temporary) / "previous"
        previous.mkdir()
        backed_up = []
        installed = []
        try:
            for artifact in destination.iterdir():
                if artifact == Path(temporary):
                    continue
                os.replace(artifact, previous / artifact.name)
                backed_up.append(artifact.name)
            for artifact in staging.iterdir():
                os.replace(artifact, destination / artifact.name)
                installed.append(artifact.name)
        except Exception:
            for name in installed:
                artifact = destination / name
                if artifact.is_dir() and not artifact.is_symlink():
                    shutil.rmtree(artifact)
                else:
                    artifact.unlink()
            for name in backed_up:
                os.replace(previous / name, destination / name)
            raise
    return str(destination)

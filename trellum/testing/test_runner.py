"""Focused tests for report execution boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trellum.runner.execute import run_report


def _manifest(report_dir: Path, **config: object) -> None:
    report_dir.mkdir(parents=True)
    (report_dir / "report.yaml").write_text(json.dumps(config), encoding="utf-8")


@pytest.mark.parametrize(
    "slug",
    [
        "../escape",
        "nested/report",
        r"nested\report",
        ".",
        "..",
        ".. ",
        "report.",
        "/absolute",
        r"C:\absolute",
        "D:escape",
        "report:stream",
    ],
)
def test_unsafe_manifest_slug_is_rejected_before_output(
    tmp_path, monkeypatch, slug
):
    project_root = tmp_path / "project"
    report_dir = project_root / "reports" / "unsafe"
    _manifest(report_dir, slug=slug)
    monkeypatch.setattr(
        "trellum.runner.execute.get_project_root", lambda: str(project_root)
    )
    monkeypatch.setattr(
        "trellum.runner.execute.discover_report",
        lambda _path: pytest.fail("report discovery ran before slug validation"),
    )

    with pytest.raises(ValueError, match="Report slug .* is invalid"):
        run_report(str(report_dir))

    assert not (project_root / "output").exists()


def test_valid_manifest_slug_keeps_standard_output_path(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    report_dir = project_root / "reports" / "valid"
    _manifest(report_dir, slug="sales-report_2026", disabled=True)
    monkeypatch.setattr(
        "trellum.runner.execute.get_project_root", lambda: str(project_root)
    )

    result = run_report(str(report_dir))

    assert result == str(project_root / "output" / "sales-report_2026")


def test_valid_slug_keeps_explicit_output_override(tmp_path):
    report_dir = tmp_path / "project" / "reports" / "valid"
    custom_output = tmp_path / "outside-standard-output"
    _manifest(report_dir, slug="valid-report", disabled=True)

    result = run_report(str(report_dir), output_dir=str(custom_output))

    assert result == str(custom_output)


def test_standard_output_rejects_existing_symlink_escape(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    report_dir = project_root / "reports" / "linked"
    output_root = project_root / "output"
    outside = tmp_path / "outside"
    _manifest(report_dir, slug="linked-report", disabled=True)
    output_root.mkdir()
    outside.mkdir()
    linked_output = output_root / "linked-report"
    try:
        linked_output.symlink_to(outside, target_is_directory=True)
    except OSError:
        realpath = os.path.realpath
        monkeypatch.setattr(
            "trellum.runner.execute.os.path.realpath",
            lambda path: str(outside) if Path(path) == linked_output else realpath(path),
        )
    monkeypatch.setattr(
        "trellum.runner.execute.get_project_root", lambda: str(project_root)
    )

    with pytest.raises(ValueError, match="resolves outside"):
        run_report(str(report_dir))

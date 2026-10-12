"""Private pending evidence for a project's local analysis session."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from trellum.analysis_capture import (
    MAX_CAPTURE_BYTES, import_capture, read_bounded, safe_name, validate_capture,
)


def inbox_dir(project_root: str | Path) -> Path:
    normalized = os.path.normcase(str(Path(project_root).resolve())).replace("\\", "/")
    project_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return Path.home() / ".trellum" / "analysis-inbox" / project_id


def analysis_target(project_root: str | Path, slug: str) -> Path:
    from trellum.report import BaseReport

    safe_name(slug)
    root = Path(project_root).resolve()
    target = root / "reports" / slug
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Analysis target must stay inside this project's reports directory")
    config = BaseReport.load_config(str(target))
    if config.get("kind", "report") != "analysis":
        raise ValueError("Select a kind: analysis directory")
    if config.get("slug", slug) != slug:
        raise ValueError("Analysis slug must match its reports/<slug> directory")
    return target


def store_capture(project_root: str | Path, slug: str, capture: dict) -> str:
    """Write validated capture bytes atomically, without touching report sources."""
    validate_capture(capture)
    directory = inbox_dir(project_root)
    if any(parent.is_symlink() for parent in (directory, *directory.parents)):
        raise ValueError("Analysis inbox must not contain symlinks")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    capture_id = secrets.token_hex(16)
    payload = dict(capture, _analysis_inbox={"target_slug": slug})
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError("Capture exceeds the size limit")
    temporary = directory / f".{capture_id}.tmp"
    destination = directory / f"{capture_id}.trellum-capture.json"
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return capture_id


def _pending_file(report_dir: str | Path, capture_id: str) -> Path:
    if not isinstance(capture_id, str) or not re.fullmatch(r"[a-f0-9]{32}", capture_id):
        raise ValueError("Invalid pending capture ID")
    target = Path(report_dir).resolve()
    if target.parent.name != "reports":
        raise ValueError("Use this project's reports/<slug> analysis directory")
    analysis_target(target.parent.parent, target.name)
    directory = inbox_dir(target.parent.parent)
    path = directory / f"{capture_id}.trellum-capture.json"
    if path.is_symlink() or any(parent.is_symlink() for parent in (directory, *directory.parents)):
        raise ValueError("Pending captures must not be symlinks")
    capture = json.loads(read_bounded(path, MAX_CAPTURE_BYTES + 1024))
    if capture.get("_analysis_inbox", {}).get("target_slug") != target.name:
        raise ValueError("Pending capture belongs to another analysis")
    return path


def import_pending_capture(report_dir: str, capture_id: str, name: str | None = None) -> str:
    """Consume evidence only after the existing exclusive import succeeds."""
    path = _pending_file(report_dir, capture_id)
    markdown = import_capture(report_dir, str(path), name=name)
    path.unlink()
    return markdown


def discard_pending_capture(report_dir: str, capture_id: str) -> None:
    _pending_file(report_dir, capture_id).unlink()


def list_pending_captures(report_dir: str) -> list[dict]:
    """Recover references even when the browser's relay event was lost."""
    target = Path(report_dir).resolve()
    if target.parent.name != "reports":
        raise ValueError("Use this project's reports/<slug> analysis directory")
    analysis_target(target.parent.parent, target.name)
    directory = inbox_dir(target.parent.parent)
    if any(parent.is_symlink() for parent in (directory, *directory.parents)):
        raise ValueError("Analysis inbox must not contain symlinks")
    pending = []
    for path in sorted(directory.glob("*.trellum-capture.json")):
        if path.is_symlink():
            raise ValueError("Pending captures must not be symlinks")
        capture = json.loads(read_bounded(path, MAX_CAPTURE_BYTES))
        if capture.get("_analysis_inbox", {}).get("target_slug") == target.name:
            source = capture["source"]
            pending.append({"capture_id": path.name.removesuffix(".trellum-capture.json"),
                            "source_slug": (source["report_slug"] or "")[:100],
                            "component_title": (source["component_title"] or "")[:200]})
    return pending

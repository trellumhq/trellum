"""Bounded, uncached reads of the project metric definitions.

The registry has no per-report ACL. Report-bound conversations must not read
it, even when a metric name also occurs in their report's output.
"""
from __future__ import annotations

import json
import os
import re
import stat

import yaml

MAX_FILE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 20_000
MAX_METRICS = 200
SUMMARY_FIELDS = ("name", "label", "description", "owner", "tags")
DETAIL_FIELDS = SUMMARY_FIELDS + (
    "format", "version", "agg", "column", "numerator", "denominator", "sql",
    "dimensions", "dataset", "time_agg", "date_col",
)
UNAVAILABLE = "Metric registry unavailable in this conversation; no canonical definition was checked."


def _load(root, *, document=False):
    # O_NOFOLLOW also closes the check/open symlink race. O_NONBLOCK plus the
    # regular-file check prevents a repository-controlled FIFO from hanging.
    path = root / "metrics.yaml"
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
        raise ValueError("registry size or file type")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        # On platforms without O_NOFOLLOW, compare identity before reading.
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("registry size or file type")
        content = source.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("registry size")
    text = content.decode("utf-8")
    # Bound parser work before constructing objects. Reject aliases/anchors
    # rather than expanding graphs or accepting recursive YAML structures.
    depth = 0
    for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader)):
        if count >= 5000 or getattr(event, "anchor", None) is not None:
            raise ValueError("registry complexity")
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
            if depth > 12:
                raise ValueError("registry depth")
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("metrics"), list):
        raise ValueError("registry schema")
    metrics = raw["metrics"]
    if len(metrics) > MAX_METRICS:
        raise ValueError("registry count")
    names = set()
    for entry in metrics:
        if not isinstance(entry, dict):
            raise ValueError("metric schema")
        name = entry.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_]*", name) or name in names:
            raise ValueError("metric name")
        names.add(name)
        for key in DETAIL_FIELDS:
            value = entry.get(key)
            if key in ("tags", "dimensions"):
                if value is not None and not (
                    isinstance(value, list) and all(isinstance(item, str) for item in value)
                ):
                    raise ValueError("metric list")
            elif value is not None and type(value) not in (str, int, float, bool):
                raise ValueError("metric scalar")
    return raw if document else metrics


def registry_text(toolbox, *, detail=False):
    """Only project-wide access permits registry metadata; never cache it.

    Source details (including SQL) additionally obey source-sharing policy.
    All callers, including MCP and the alert evaluator, share these gates.
    """
    if toolbox.is_report_bound or toolbox._access_error():
        return "Error: " + UNAVAILABLE
    if detail and not toolbox.share_report_source:
        return "Error: reading metric source is turned off for this organization."
    try:
        metrics = _load(toolbox.project_root)
    except (OSError, ValueError, UnicodeError, yaml.YAMLError):
        # Do not echo parser errors: they may quote private file contents.
        return "Error: " + UNAVAILABLE + " File absent, invalid, unsafe or over the registry limits."
    fields = DETAIL_FIELDS if detail else SUMMARY_FIELDS
    lines = []
    size = 0
    for entry in metrics:
        line = json.dumps({key: entry.get(key, [] if key == "tags" else "") for key in fields
                           if key in SUMMARY_FIELDS or key in entry}, ensure_ascii=True)
        if size + len(line) + 1 > MAX_OUTPUT_BYTES - 100:
            lines.append("[Registry excerpt truncated; omitted metrics/fields are not known to be absent.]")
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines) or "(No metrics defined in metrics.yaml.)"

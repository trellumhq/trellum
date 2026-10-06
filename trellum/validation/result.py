"""Check records and the result container that collects them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Check:
    id: str
    level: str  # "pass", "info", "warn", "fail"
    message: str
    component: str = ""
    section: str = ""
    dataset_id: str = ""  # set on per-DS checks (chart-filter-coverage, grain-mismatch)
    suppressed: bool = False  # True if silenced via validation.suppress[_per_dataset]

@dataclass
class ValidationResult:
    slug: str
    checks: list[Check] = field(default_factory=list)

    #: Check groups that ran to completion. Recorded because a clean report
    #: otherwise produces `checks: []` and `total: 0`, which is byte-identical
    #: to a validator that never ran — and a reader cannot tell "nothing is
    #: wrong" from "nothing was examined". One measured agent responded to that
    #: ambiguity by copying a report, breaking it deliberately, and re-running,
    #: which was the only way to find out. This is the evidence that removes
    #: the need.
    #:
    #: Appended AFTER a group returns, never before: a group that raises must
    #: not be counted as coverage.
    groups_run: list[str] = field(default_factory=list)

    def add(self, id: str, level: str, message: str, **kw: Any) -> None:
        self.checks.append(Check(id=id, level=level, message=message, **kw))

    def ran(self, group: str) -> None:
        """Record that a check group completed."""
        if group not in self.groups_run:
            self.groups_run.append(group)

    # convenience helpers
    def passed(self, id: str, message: str, **kw: Any) -> None:
        self.add(id, "pass", message, **kw)

    def info(self, id: str, message: str, **kw: Any) -> None:
        self.add(id, "info", message, **kw)

    def warn(self, id: str, message: str, **kw: Any) -> None:
        self.add(id, "warn", message, **kw)

    def fail(self, id: str, message: str, **kw: Any) -> None:
        self.add(id, "fail", message, **kw)

    @property
    def summary(self) -> dict[str, int]:
        # Suppressed checks are bucketed under `suppressed` and excluded
        # from active counts (pass/info/warn/fail). The `total` row is
        # the active count for legibility — past the headline, the
        # suppressed bucket records what was silenced.
        counts = {"total": 0, "pass": 0, "info": 0, "warn": 0, "fail": 0,
                  "suppressed": 0, "groups_run": len(self.groups_run)}
        for c in self.checks:
            if c.suppressed:
                counts["suppressed"] += 1
            else:
                counts["total"] += 1
                counts[c.level] = counts.get(c.level, 0) + 1
        return counts

    def print_summary(self) -> None:
        s = self.summary
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        color_w = "\033[33m" if s["warn"] else ""
        color_f = "\033[31m" if s["fail"] else ""
        reset = "\033[0m"
        sup_suffix = (
            f", \033[2m{s['suppressed']} suppressed{reset}" if s.get("suppressed") else ""
        )
        print(
            f"[{ts}] Validation: {s['pass']} pass, "
            f"{color_w}{s['warn']} warn{reset}, "
            f"{color_f}{s['fail']} fail{reset}{sup_suffix}"
        )
        for c in self.checks:
            if c.suppressed:
                continue
            if c.level in ("fail", "warn", "info"):
                tag = c.level.upper().rjust(4)
                print(f"  {tag}  {c.id}: {c.message}")

    def to_dict(self) -> dict:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "slug": self.slug,
            "summary": self.summary,
            # Which groups ran, not merely what they found. Consumers -- the
            # a host's health view, the PostToolUse hook, `trellum validate` --
            # can then distinguish a clean report from a validator that did not
            # execute, which an empty `checks` array cannot express.
            "groups_run": list(self.groups_run),
            "checks": [
                {k: v for k, v in {
                    "id": c.id, "level": c.level, "message": c.message,
                    "component": c.component or None,
                    "section": c.section or None,
                    "dataset_id": c.dataset_id or None,
                    "suppressed": True if c.suppressed else None,
                }.items() if v is not None}
                for c in self.checks
            ],
        }

    def write(self, output_dir: str) -> None:
        path = os.path.join(output_dir, "_validation.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

def _deduplicate(checks: list[Check]) -> list[Check]:
    """Remove duplicate checks with the same id + message, keeping one."""
    seen: set[tuple[str, str]] = set()
    unique: list[Check] = []
    for c in checks:
        key = (c.id, c.message)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique

def _apply_suppressions(checks: list[Check], suppress: list[str]) -> list[Check]:
    """Mark checks whose id matches a suppression entry as ``suppressed``.

    Suppressed checks are still kept in the result so the validation
    drawer / skill output can show *what was silenced and why* instead of
    a silent disappearance — readers should know that an inactive filter
    was deliberately accepted, not just not flagged.
    """
    if not suppress:
        return checks
    suppress_set = set(suppress)
    for c in checks:
        if c.id in suppress_set:
            c.suppressed = True
    return checks

def _apply_dataset_suppressions(
    checks: list[Check],
    suppress_per_dataset: dict[str, list[str]] | None,
) -> list[Check]:
    """Mark checks scoped to a specific DataSource as ``suppressed``.

    ``suppress_per_dataset`` is a dict of ``{dataset_id: [check_id, ...]}``.
    A check is marked iff its ``dataset_id`` field matches a key AND its
    ``id`` matches an entry in that key's list.
    """
    if not suppress_per_dataset:
        return checks
    for c in checks:
        ds_id = c.dataset_id
        if ds_id and c.id in (suppress_per_dataset.get(ds_id) or []):
            c.suppressed = True
    return checks

def _mark_matrix_suppressions(
    details: dict,
    suppress: list[str] | None,
    suppress_per_dataset: dict[str, list[str]] | None,
    accept_inactive_filters: dict[str, list[str]] | None = None,
) -> None:
    """Attach `suppressed=True` flags to filter_matrix cells / static
    column rows whose corresponding check is silenced.

    Three suppression layers, from broadest to narrowest:

      1. Global ``validation.suppress: [check_id, ...]`` — every
         instance of that check is silenced.
      2. Per-DataSource ``validation.suppress_per_dataset:
         {ds_id: [check_id]}`` — every instance of that check on that
         DS is silenced (any filter column).
      3. Per-(DS, filter_column) ``validation.accept_inactive_filters:
         {ds_id: [filter_col]}`` — only the listed filter columns are
         silenced. Specific to ``chart-filter-coverage``.

    Cells flagged at any layer render as ``suppressed`` in the matrix.
    The underlying problem is still real — readers see ✗(s) so they
    can challenge the acceptance if circumstances change.
    """
    sup_global = set(suppress or [])
    sup_per_ds = suppress_per_dataset or {}
    accept_per_ds: dict[str, set[str]] = {
        ds: set(cols or []) for ds, cols in (accept_inactive_filters or {}).items()
    }
    cov_global = "chart-filter-coverage" in sup_global
    grain_global = "chart-value-grain-mismatch" in sup_global

    for row in details.get("filter_matrix") or []:
        ds_id = row.get("dataset_id") or ""
        ds_set = set(sup_per_ds.get(ds_id) or [])
        cov_blanket = cov_global or "chart-filter-coverage" in ds_set
        grain_suppressed = grain_global or "chart-value-grain-mismatch" in ds_set
        accept_cols = accept_per_ds.get(ds_id, set())

        for cell in row.get("filters") or []:
            if cell.get("status") != "inactive":
                continue
            if cov_blanket or cell.get("column") in accept_cols:
                cell["suppressed"] = True
        if grain_suppressed and (row.get("static_value_columns") or []):
            row["static_value_columns_suppressed"] = True

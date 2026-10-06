"""Claimed metrics: metrics.yaml, claim expansion, validation, artifacts.

The feature's one promise is consistency by construction: a KPI that claims a
metric computes the definition, not a copy of it. So these tests pin the whole
chain -- the loader's tolerance and its problems list, the expansion precedence
(explicit keys win), the validator checks in both firing and non-firing
states, the `_metrics` / `metrics_used` artifact surface, and the CLI an agent
is told to run before inventing a KPI dict.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from trellum.components import RenderContext
from trellum.metrics import (
    Dataset,
    Metric,
    compute_definition_hash,
    expand_claim,
    invalidate_metrics_cache,
    load_metrics,
    load_metrics_result,
)
from trellum.metrics_report import MetricsReport, _block, _chart
from trellum.themes import DefaultTheme

VALID_YAML = """\
version: 1
metrics:
  - name: gross_revenue
    label: "Gross Revenue"
    description: >
      IAP plus ad revenue, gross of platform fees, daily grain from the
      warehouse fact table.
    owner: finance@example.com
    format: currency
    agg: sum
    column: total_revenue
    sql: "iap_revenue + ad_revenue"
    dimensions: [event_date, title]
    tags: [revenue]
  - name: paying_share
    label: "Paying Share"
    description: >
      Payers as a share of daily active users, filtered rows, daily grain.
    format: percent
    agg: ratio
    numerator: payers
    denominator: dau
  - name: sessions
    label: "Sessions"
    description: "Short."
    format: number
  - name: rows_seen
    label: "Rows Seen"
    description: >
      Number of fact rows surviving the current filters; a data-volume
      sanity metric rather than a business one.
    format: number
    agg: count
"""


@pytest.fixture(autouse=True)
def _fresh_registry():
    """The loader caches per path; tests rewrite files, so never share."""
    invalidate_metrics_cache()
    yield
    invalidate_metrics_cache()


def _write_metrics(tmp_path: Path, text: str = VALID_YAML) -> str:
    (tmp_path / "metrics.yaml").write_text(text, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture()
def project_root(tmp_path):
    """Pin the global project root at tmp_path for code that resolves it
    implicitly (claim expansion at render, the validator, the CLI)."""
    from trellum.project import get_project_root, set_project_root

    original = get_project_root()
    set_project_root(str(tmp_path))
    yield tmp_path
    set_project_root(original)


# ── the loader ───────────────────────────────────────────────────────────

class TestLoader:
    def test_absent_file_is_empty_with_no_problems(self, tmp_path):
        result = load_metrics_result(project_root=str(tmp_path))
        assert result.metrics == {}
        assert result.problems == []

    def test_malformed_yaml_warns_and_returns_empty(self, tmp_path, capsys):
        root = _write_metrics(tmp_path, "metrics: [unclosed\n  - broken: {{{")
        result = load_metrics_result(project_root=root)
        assert result.metrics == {}
        assert any(lvl == "warn" for lvl, _ in result.problems)
        assert "[warn]" in capsys.readouterr().out

    def test_a_mapping_instead_of_a_list_is_a_schema_fail(self, tmp_path):
        root = _write_metrics(
            tmp_path, "metrics:\n  gross_revenue:\n    format: currency\n")
        result = load_metrics_result(project_root=root)
        assert result.metrics == {}
        assert any(lvl == "fail" and "LIST" in msg
                   for lvl, msg in result.problems)

    def test_duplicate_ids_keep_the_first_and_record_a_fail(self, tmp_path):
        root = _write_metrics(tmp_path, VALID_YAML + """\
  - name: gross_revenue
    label: "The Impostor"
    format: number
""")
        result = load_metrics_result(project_root=root)
        assert result.metrics["gross_revenue"].label == "Gross Revenue"
        assert any(lvl == "fail" and "more than once" in msg
                   for lvl, msg in result.problems)

    @pytest.mark.parametrize("snippet, needle", [
        ("  - name: Bad-Name\n    label: X\n    description: 'a good long description of the number, its grain and scope'\n", "not a valid slug"),
        ("  - name: bad_fmt\n    label: X\n    description: 'a good long description of the number, its grain and scope'\n    format: dollars\n", "format 'dollars'"),
        ("  - name: bad_agg\n    label: X\n    description: 'a good long description of the number, its grain and scope'\n    agg: median\n    column: x\n", "agg 'median'"),
        ("  - name: half_ratio\n    label: X\n    description: 'a good long description of the number, its grain and scope'\n    format: percent\n    agg: ratio\n    numerator: payers\n", "missing `denominator`"),
        ("  - name: no_col\n    label: X\n    description: 'a good long description of the number, its grain and scope'\n    agg: sum\n", "missing `column`"),
    ])
    def test_structural_fails_are_recorded(self, tmp_path, snippet, needle):
        root = _write_metrics(tmp_path, "version: 1\nmetrics:\n" + snippet)
        result = load_metrics_result(project_root=root)
        assert any(lvl == "fail" and needle in msg
                   for lvl, msg in result.problems), result.problems

    def test_missing_label_and_description_warn_not_fail(self, tmp_path):
        root = _write_metrics(
            tmp_path,
            "metrics:\n  - name: bare\n    agg: sum\n    column: x\n")
        result = load_metrics_result(project_root=root)
        assert "bare" in result.metrics
        warns = [msg for lvl, msg in result.problems if lvl == "warn"]
        assert any("no `label`" in m for m in warns)
        assert any("no `description`" in m for m in warns)

    def test_cache_and_invalidate(self, tmp_path):
        root = _write_metrics(tmp_path)
        assert "gross_revenue" in load_metrics(project_root=root)
        _write_metrics(tmp_path, "metrics: []\n")
        # Still cached -- an in-flight build must see one consistent registry.
        assert "gross_revenue" in load_metrics(project_root=root)
        invalidate_metrics_cache()
        assert load_metrics(project_root=root) == {}


class TestDefinitionHash:
    def test_stable_across_calls(self):
        args = ("gross_revenue", "currency", "sum", "total_revenue",
                None, None, "iap + ad", 1)
        assert compute_definition_hash(*args) == compute_definition_hash(*args)

    def test_prose_edits_do_not_move_the_hash(self, tmp_path):
        root_a = _write_metrics(tmp_path)
        h1 = load_metrics(project_root=root_a)["gross_revenue"].definition_hash
        _write_metrics(tmp_path, VALID_YAML.replace(
            "gross of platform fees", "GROSS of platform FEES (edited)"))
        invalidate_metrics_cache()
        h2 = load_metrics(project_root=root_a)["gross_revenue"].definition_hash
        assert h1 == h2, "description is prose; editing it is not a redefinition"

    def test_spec_changes_move_the_hash(self, tmp_path):
        root = _write_metrics(tmp_path)
        h1 = load_metrics(project_root=root)["gross_revenue"].definition_hash
        _write_metrics(tmp_path, VALID_YAML.replace(
            "column: total_revenue", "column: net_revenue"))
        invalidate_metrics_cache()
        h2 = load_metrics(project_root=root)["gross_revenue"].definition_hash
        assert h1 != h2

    def test_version_bump_moves_the_hash(self, tmp_path):
        root = _write_metrics(tmp_path)
        h1 = load_metrics(project_root=root)["gross_revenue"].definition_hash
        _write_metrics(tmp_path, VALID_YAML.replace(
            "    format: currency\n    agg: sum",
            "    format: currency\n    version: 2\n    agg: sum"))
        invalidate_metrics_cache()
        h2 = load_metrics(project_root=root)["gross_revenue"].definition_hash
        assert h1 != h2


# ── claim expansion ──────────────────────────────────────────────────────

class TestExpandClaim:
    def _registry(self, tmp_path):
        return load_metrics(project_root=_write_metrics(tmp_path))

    def test_bare_executable_claim_expands_fully(self, tmp_path):
        out = expand_claim({"metric": "gross_revenue"}, self._registry(tmp_path))
        assert out == {
            "metric": "gross_revenue",
            "label": "Gross Revenue",
            "format": "currency",
            "agg": "sum",
            "column": "total_revenue",
        }

    def test_ratio_claim_expands_numerator_and_denominator(self, tmp_path):
        out = expand_claim({"metric": "paying_share"}, self._registry(tmp_path))
        assert out["agg"] == "ratio"
        assert out["numerator"] == "payers"
        assert out["denominator"] == "dau"
        assert out["format"] == "percent"

    def test_explicit_keys_win(self, tmp_path):
        out = expand_claim(
            {"metric": "gross_revenue", "label": "Rev", "column": "net_revenue"},
            self._registry(tmp_path))
        assert out["label"] == "Rev"
        assert out["column"] == "net_revenue"
        assert out["agg"] == "sum"          # not overridden, still filled

    def test_claim_with_value_stays_static(self, tmp_path):
        out = expand_claim({"metric": "gross_revenue", "value": 42},
                           self._registry(tmp_path))
        assert out["value"] == 42
        assert "agg" not in out and "column" not in out
        assert out["label"] == "Gross Revenue"

    def test_descriptive_claim_fills_identity_only(self, tmp_path):
        out = expand_claim({"metric": "sessions", "value": 7},
                           self._registry(tmp_path))
        assert out == {"metric": "sessions", "label": "Sessions",
                       "format": "number", "value": 7}

    def test_unknown_id_passes_through_with_a_warning(self, tmp_path, capsys):
        claim = {"metric": "nope", "label": "X"}
        assert expand_claim(claim, self._registry(tmp_path)) == claim
        assert "[warn]" in capsys.readouterr().out

    def test_non_claim_dict_is_untouched(self, tmp_path):
        kpi = {"label": "Inline", "agg": "sum", "column": "x"}
        assert expand_claim(kpi, self._registry(tmp_path)) is kpi


# ── the validator group ──────────────────────────────────────────────────

def _run_metrics_check(comps, ds_map):
    from trellum.validation.checks.metrics import _check_metrics
    from trellum.validation.result import ValidationResult

    result = ValidationResult(slug="t")
    _check_metrics(None, comps, ds_map, result)
    return result


def _rev_ds():
    from trellum.components import DataSource

    df = pd.DataFrame({
        "event_date": ["2026-08-01", "2026-08-02"],
        "total_revenue": [10.0, 20.0],
        "payers": [1, 2],
        "dau": [10, 20],
    })
    return {"rev": DataSource("rev", df)}


class TestValidatorChecks:
    def test_clean_claims_raise_nothing(self, project_root):
        from trellum.components import KpiRow

        _write_metrics(project_root)
        comps = [(KpiRow([{"metric": "gross_revenue"},
                          {"metric": "paying_share"}],
                         dataset_id="rev"), "Revenue")]
        result = _run_metrics_check(comps, _rev_ds())
        assert not [c for c in result.checks if c.level in ("warn", "fail")], \
            [(c.id, c.message) for c in result.checks]

    def test_metric_undefined_fails(self, project_root):
        from trellum.components import KpiRow

        _write_metrics(project_root)
        comps = [(KpiRow([{"metric": "arpdau"}], dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, _rev_ds())
        fails = [c for c in result.checks if c.id == "metric-undefined"]
        assert fails and fails[0].level == "fail"
        assert "arpdau" in fails[0].message

    def test_metric_column_missing_fails_with_the_dataset(self, project_root):
        from trellum.components import DataSource, KpiRow

        _write_metrics(project_root)
        ds_map = {"rev": DataSource(
            "rev", pd.DataFrame({"event_date": ["2026-08-01"]}))}
        comps = [(KpiRow([{"metric": "gross_revenue"}], dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, ds_map)
        fails = [c for c in result.checks if c.id == "metric-column-missing"]
        assert fails and fails[0].level == "fail"
        assert fails[0].dataset_id == "rev"
        assert "total_revenue" in fails[0].message

    def test_descriptive_metric_claimed_live_fails(self, project_root):
        from trellum.components import KpiRow

        _write_metrics(project_root)
        comps = [(KpiRow([{"metric": "sessions"}], dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, _rev_ds())
        fails = [c for c in result.checks if c.id == "metric-column-missing"]
        assert fails and "no executable spec" in fails[0].message

    def test_count_metric_needs_no_column(self, project_root):
        from trellum.components import KpiRow

        _write_metrics(project_root)
        comps = [(KpiRow([{"metric": "rows_seen"}], dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, _rev_ds())
        assert not [c for c in result.checks
                    if c.id == "metric-column-missing"]

    def test_static_claim_without_value_fails(self, project_root):
        from trellum.components import KpiCard

        _write_metrics(project_root)
        comps = [(KpiCard(metric="gross_revenue"), "s")]
        result = _run_metrics_check(comps, {})
        fails = [c for c in result.checks if c.id == "metric-column-missing"]
        assert fails and "supplies no value" in fails[0].message

    def test_static_claim_with_value_passes(self, project_root):
        from trellum.components import KpiCard, MiniKpi

        _write_metrics(project_root)
        comps = [(KpiCard(metric="gross_revenue", value=1234.5), "s"),
                 (MiniKpi(metric="sessions", value=9), "s")]
        result = _run_metrics_check(comps, {})
        assert not [c for c in result.checks if c.level == "fail"], \
            [(c.id, c.message) for c in result.checks]

    def test_conflicting_override_warns(self, project_root):
        from trellum.components import KpiRow

        _write_metrics(project_root)
        comps = [(KpiRow([{"metric": "gross_revenue",
                           "column": "payers"}], dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, _rev_ds())
        warns = [c for c in result.checks if c.id == "metric-overridden"]
        assert warns and warns[0].level == "warn"
        assert "column" in warns[0].message

    def test_label_override_is_not_a_conflict(self, project_root):
        from trellum.components import KpiRow

        _write_metrics(project_root)
        comps = [(KpiRow([{"metric": "gross_revenue", "label": "Rev"}],
                         dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, _rev_ds())
        assert not [c for c in result.checks if c.id == "metric-overridden"]

    def test_weak_description_is_info_for_claimed_metrics(self, project_root):
        from trellum.components import KpiCard

        _write_metrics(project_root)
        comps = [(KpiCard(metric="sessions", value=1), "s")]
        result = _run_metrics_check(comps, {})
        infos = [c for c in result.checks if c.id == "metric-description-weak"]
        assert infos and infos[0].level == "info"
        assert "sessions" in infos[0].message

    def test_schema_problems_surface_as_metrics_yaml_schema(self, project_root):
        _write_metrics(project_root, VALID_YAML + """\
  - name: gross_revenue
    label: "Duplicate"
""")
        result = _run_metrics_check([], {})
        ids = [(c.id, c.level) for c in result.checks]
        assert ("metrics-yaml-schema", "fail") in ids

    def test_no_metrics_yaml_no_claims_is_silent(self, project_root):
        from trellum.components import KpiRow

        comps = [(KpiRow([{"label": "Inline", "agg": "sum", "column": "x"}],
                         dataset_id="rev"), "s")]
        result = _run_metrics_check(comps, _rev_ds())
        assert result.checks == []

    def test_the_group_registers_in_validate_report(self, project_root, tmp_path):
        """The coverage count is the trust signal; `metrics` must be in it."""
        from trellum.report import ReportContext
        from trellum.validation import validate_report

        ctx = ReportContext(config={}, slug="t", output_dir=str(tmp_path))
        ctx.add_section("Empty", [])
        result = validate_report(ctx)
        assert "metrics" in result.groups_run


# ── the artifact surface ─────────────────────────────────────────────────

def _render_minimal_report(tmp_path, out_name, kpis, dataset_id="rev"):
    from trellum.components import DataSource, KpiRow
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext

    df = pd.DataFrame({
        "event_date": ["2026-08-01", "2026-08-02"],
        "total_revenue": [10.0, 20.0],
        "payers": [1, 2],
        "dau": [10, 20],
    })
    out = tmp_path / out_name
    ctx = ReportContext(config={"name": "T"}, slug="t", output_dir=str(out))
    ctx.add_section("KPIs", [
        DataSource("rev", df),
        KpiRow(kpis, dataset_id=dataset_id),
    ])
    render_report(ctx, str(out), auto_refresh=False)
    data = json.loads((out / "data.json").read_text(encoding="utf-8"))
    meta = json.loads((out / "_meta.json").read_text(encoding="utf-8"))
    return data, meta


class TestArtifactSurface:
    def test_claims_land_in_data_json_and_meta(self, project_root):
        _write_metrics(project_root)
        data, meta = _render_minimal_report(
            project_root, "out",
            [{"metric": "gross_revenue"}, {"metric": "paying_share"}])

        # The registered kpi config IS the expanded definition.
        rows = [c for c in data["components"].values()
                if c.get("type") == "kpi_row_live"]
        assert rows, "kpi_row_live not registered"
        kpi = rows[0]["kpis"][0]
        assert kpi["metric"] == "gross_revenue"
        assert kpi["agg"] == "sum"
        assert kpi["column"] == "total_revenue"
        assert kpi["label"] == "Gross Revenue"
        assert kpi["format"] == "currency"

        # `_metrics`: claimed metrics only, with identity and claimants.
        assert set(data["_metrics"]) == {"gross_revenue", "paying_share"}
        entry = data["_metrics"]["gross_revenue"]
        assert entry["label"] == "Gross Revenue"
        assert entry["format"] == "currency"
        assert entry["agg"] == "sum"
        assert entry["column"] == "total_revenue"
        assert entry["version"] == 1
        assert entry["definition_hash"]
        assert entry["component_ids"]

        # metrics_used (schema v2): id + the build-time definition_hash/version
        # of each claim, not just the id -- see trellum.meta.normalize_metrics_used.
        assert [m["id"] for m in meta["metrics_used"]] == ["gross_revenue", "paying_share"]
        by_id = {m["id"]: m for m in meta["metrics_used"]}
        assert by_id["gross_revenue"]["definition_hash"] == entry["definition_hash"]
        assert by_id["gross_revenue"]["version"] == 1

    def test_unclaimed_report_has_no_metrics_block(self, project_root):
        _write_metrics(project_root)
        data, meta = _render_minimal_report(
            project_root, "out2",
            [{"label": "Inline", "agg": "sum", "column": "total_revenue"}])
        assert "_metrics" not in data
        assert meta["metrics_used"] == []


# ── the CLI ──────────────────────────────────────────────────────────────

class TestCli:
    def _main(self, argv):
        from trellum.cli import main
        return main(argv)

    def test_listing_names_every_metric_and_coverage(self, project_root, capsys):
        _write_metrics(project_root)
        assert self._main(["metrics"]) == 0
        out = capsys.readouterr().out
        for name in ("gross_revenue", "paying_share", "sessions", "rows_seen"):
            assert name in out
        assert "Gross Revenue" in out
        assert "sum(total_revenue)" in out
        assert "coverage unknown" in out          # nothing built yet

    def test_coverage_reads_built_meta(self, project_root, capsys):
        _write_metrics(project_root)
        out_dir = project_root / "output" / "monetization"
        out_dir.mkdir(parents=True)
        (out_dir / "_meta.json").write_text(
            json.dumps({"slug": "monetization",
                        "metrics_used": ["gross_revenue"]}),
            encoding="utf-8")
        assert self._main(["metrics"]) == 0
        out = capsys.readouterr().out
        assert "1 of 4 defined metric(s) claimed" in out
        assert "unclaimed:" in out

    def test_single_metric_prints_the_full_definition(self, project_root, capsys):
        _write_metrics(project_root)
        assert self._main(["metrics", "gross_revenue"]) == 0
        out = capsys.readouterr().out
        assert "IAP plus ad revenue" in out
        assert "iap_revenue + ad_revenue" in out
        assert "finance@example.com" in out
        assert "hash:" in out

    def test_unknown_metric_fails_loudly(self, project_root, capsys):
        _write_metrics(project_root)
        assert self._main(["metrics", "nope"]) == 1
        assert "no such metric" in capsys.readouterr().err

    def test_no_metrics_yaml_teaches_the_format(self, project_root, capsys):
        assert self._main(["metrics"]) == 0
        out = capsys.readouterr().out
        assert "metrics.yaml" in out
        assert "gross_revenue" in out             # the worked example

    def test_lint_fails_on_duplicates(self, project_root, capsys):
        _write_metrics(project_root, VALID_YAML + """\
  - name: gross_revenue
    label: "Duplicate"
""")
        assert self._main(["metrics", "--lint"]) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_lint_reports_orphans_against_built_output(self, project_root, capsys):
        _write_metrics(project_root)
        out_dir = project_root / "output" / "monetization"
        out_dir.mkdir(parents=True)
        (out_dir / "_meta.json").write_text(
            json.dumps({"metrics_used": ["gross_revenue", "paying_share",
                                         "rows_seen"]}),
            encoding="utf-8")
        assert self._main(["metrics", "--lint"]) == 0
        out = capsys.readouterr().out
        assert "ORPHAN  sessions" in out

    def test_lint_without_builds_says_builds_are_needed(self, project_root, capsys):
        _write_metrics(project_root)
        assert self._main(["metrics", "--lint"]) == 0
        assert "none found under output/" in capsys.readouterr().out

    def test_guide_metrics_serves_prose_plus_live_registry(self, project_root, capsys):
        """`metrics` is a topic AND a live inventory; one call answers both."""
        _write_metrics(project_root)
        assert self._main(["guide", "metrics"]) == 0
        out = capsys.readouterr().out
        assert "metrics.yaml" in out              # the anchored prose
        assert "gross_revenue" in out             # the live registry


# ── the demo as a teaching artifact ──────────────────────────────────────

class TestDemoClaims:
    def test_monetization_builds_with_metrics_in_the_artifact(self, tmp_path):
        """The shipped demo claims its revenue KPIs; the artifact proves it."""
        import trellum
        from trellum.project import get_project_root, set_project_root
        from trellum.testing.runner import test_report

        demo_root = Path(trellum.__file__).resolve().parent / "demo"
        original = get_project_root()
        set_project_root(str(demo_root))
        invalidate_metrics_cache()
        try:
            result = test_report(str(demo_root / "reports" / "monetization"),
                                 output_dir=str(tmp_path / "out"))
        finally:
            set_project_root(original)
            invalidate_metrics_cache()

        assert result.passed, result.details

        data = json.loads((tmp_path / "out" / "data.json")
                          .read_text(encoding="utf-8"))
        expected = {"gross_revenue", "iap_revenue", "ad_revenue", "transactions"}
        assert set(data["_metrics"]) == expected
        entry = data["_metrics"]["gross_revenue"]
        assert entry["agg"] == "sum" and entry["column"] == "total_revenue"

        meta = json.loads((tmp_path / "out" / "_meta.json")
                          .read_text(encoding="utf-8"))
        assert {m["id"] for m in meta["metrics_used"]} == expected
        assert all(m["definition_hash"] for m in meta["metrics_used"])

    def test_demo_metrics_yaml_is_schema_clean(self):
        import trellum

        demo_root = Path(trellum.__file__).resolve().parent / "demo"
        result = load_metrics_result(project_root=str(demo_root))
        assert result.problems == [], result.problems
        assert "gross_revenue" in result.metrics


# ── datasets: the binding, the schema, the expansion ─────────────────────

DATASETS_YAML = VALID_YAML + """\
  - name: dau
    label: "DAU"
    description: "Daily active users, summed across dims per day and averaged over the window."
    format: number
    agg: sum
    column: dau
    time_agg: avg
    dataset: daily
datasets:
  daily:
    source: demo_db
    table: fact_daily
    where: "is_test = 0"
    columns: {total_revenue: "iap_revenue + ad_revenue"}
    time: {column: event_date, grain: day}
    dimensions: [title, platform]
    lookback: 30
  snapshot:
    provider: metrics_data:snapshot
    time: none
    dimensions: [segment]
"""


class TestDatasets:
    def test_datasets_block_parses(self, tmp_path):
        result = load_metrics_result(project_root=_write_metrics(tmp_path, DATASETS_YAML))
        assert result.problems == [], result.problems
        daily = result.datasets["daily"]
        assert (daily.source, daily.table, daily.where) == ("demo_db", "fact_daily", "is_test = 0")
        assert daily.columns == {"total_revenue": "iap_revenue + ad_revenue"}
        assert (daily.time_column, daily.grain, daily.lookback) == ("event_date", "day", 30)
        assert daily.dimensions == ("title", "platform")
        snap = result.datasets["snapshot"]
        assert snap.provider == "metrics_data:snapshot"
        assert snap.time_column is None and snap.grain is None
        dau = result.metrics["dau"]
        assert (dau.dataset, dau.time_agg, dau.date_col) == ("daily", "avg", "event_date")

    def test_files_without_datasets_stay_valid(self, tmp_path):
        result = load_metrics_result(project_root=_write_metrics(tmp_path))
        assert result.datasets == {} and result.problems == []
        assert result.metrics["gross_revenue"].dataset is None

    def test_binding_and_rollup_do_not_move_the_hash(self, tmp_path):
        plain = load_metrics(project_root=_write_metrics(tmp_path))["gross_revenue"]
        invalidate_metrics_cache()
        bound = load_metrics(project_root=_write_metrics(
            tmp_path, DATASETS_YAML.replace(
                "    column: total_revenue\n",
                "    column: total_revenue\n    dataset: daily\n    time_agg: last\n", 1),
        ))["gross_revenue"]
        assert bound.dataset == "daily" and bound.time_agg == "last"
        assert bound.definition_hash == plain.definition_hash

    @pytest.mark.parametrize("edit, needle", [
        (("    dataset: daily\n", "    dataset: nope\n"), "unknown dataset 'nope'"),
        (("    source: demo_db\n    table: fact_daily\n", ""), "either `provider"),
        (("    time: {column: event_date, grain: day}\n", ""), "has no `time`"),
        (("grain: day", "grain: fortnight"), "grain one of"),
        (("    time_agg: avg\n", "    time_agg: median\n"), "time_agg 'median'"),
    ])
    def test_schema_fails(self, tmp_path, edit, needle):
        text = DATASETS_YAML.replace(*edit, 1)
        result = load_metrics_result(project_root=_write_metrics(tmp_path, text))
        fails = [msg for lvl, msg in result.problems if lvl == "fail"]
        assert any(needle in m for m in fails), (needle, result.problems)

    def test_time_avg_claim_expands_to_avg_by_date(self, tmp_path):
        registry = load_metrics(project_root=_write_metrics(tmp_path, DATASETS_YAML))
        assert expand_claim({"metric": "dau"}, registry) == {
            "metric": "dau", "label": "DAU", "format": "number",
            "agg": "avg_by_date", "column": "dau", "date_col": "event_date",
        }


class TestMetricsReportShape:
    """The generated report's shape (internal planning ticket #114 slice 2): one anchored block
    per metric carrying its own breakdown, and exactly one shared control --
    the date range. A block is what a host embeds with ``?only=``, so its
    anchor and its self-containedness are the contract, not a detail."""

    DAILY = Dataset(name="daily", source="db", table="fact_daily",
                    time_column="event_date", grain="day",
                    dimensions=("platform", "region"))
    ORDERS = Dataset(name="orders", source="db", table="orders",
                     time_column="order_date", grain="day", dimensions=("channel",))
    SNAPSHOT = Dataset(name="snap", provider="m:snap", dimensions=("tier",))
    REVENUE = Metric(name="gross_revenue", label="Gross Revenue", format="currency",
                     agg="sum", column="total_revenue", dataset="daily")

    def _frame(self, ds: Dataset) -> pd.DataFrame:
        cols = {d: ["a"] for d in ds.dimensions}
        if ds.time_column:
            cols[ds.time_column] = ["2026-01-01"]
        return pd.DataFrame({**cols, "total_revenue": [1.0]})

    def test_block_is_anchored_on_the_metric_id(self):
        block = _block(self.REVENUE, self._frame(self.DAILY), self.DAILY)
        assert block.anchor == "metric-gross_revenue"
        assert block.title == "Gross Revenue"
        assert [type(c).__name__ for c in block.children] == ["KpiRow", "LineChart"]

    def test_anchor_renders_as_the_section_id(self):
        html = _block(self.REVENUE, self._frame(self.DAILY), self.DAILY).render_html(
            RenderContext(theme=DefaultTheme))
        assert 'id="metric-gross_revenue"' in html

    def test_chart_breaks_down_by_its_own_dimensions_only(self):
        chart = _chart(self.REVENUE, self._frame(self.DAILY), self.DAILY)
        # "Total" first (the default) and mapped to no column, so the runtime
        # reads it as "don't pivot".
        assert chart.stack_by_options == {
            "Total": "", "Platform": "platform", "Region": "region"}
        assert list(chart.stack_by_options)[0] == "Total"

    def test_a_timeless_dataset_gets_a_bar_over_its_own_dimension(self):
        m = Metric(name="headcount", agg="sum", column="total_revenue", dataset="snap")
        chart = _chart(m, self._frame(self.SNAPSHOT), self.SNAPSHOT)
        assert type(chart).__name__ == "BarChart" and chart.x == "tier"

    def test_the_subtitle_names_the_file_not_its_path(self, tmp_path, project_root):
        """The registry's ``path`` is absolute on whatever machine ran the
        build, and the subtitle is rendered into an artifact that is served,
        framed in a host and shared. The file's NAME is the whole of the
        provenance a reader could use; the directory above it is somebody's
        filesystem layout."""
        _write_metrics(tmp_path, DATASETS_YAML)
        header: dict = {}
        ctx = SimpleNamespace(
            metrics=lambda ids, by=None: pd.DataFrame(
                {"event_date": ["2026-01-01"], "dau": [1]}),
            set_header=lambda subtitle=None, **kw: header.update(subtitle=subtitle),
            add_section=lambda *a: None,
        )
        MetricsReport().generate(ctx)
        assert header["subtitle"].endswith("monitored from metrics.yaml")
        assert not set(header["subtitle"]) & {"/", "\\"}, header["subtitle"]

    def test_date_range_is_the_only_shared_control(self):
        sections: list = []
        ctx = SimpleNamespace(add_section=lambda title, comps: sections.append((title, comps)))
        datasets = {"daily": self.DAILY, "orders": self.ORDERS, "snap": self.SNAPSHOT}
        frames = {k: self._frame(v) for k, v in datasets.items()}
        MetricsReport._date_range(None, ctx, datasets, list(datasets), frames)

        (title, comps), = sections
        assert title == ""
        bars = [c for c in comps if type(c).__name__ == "FilterBar"]
        assert len(bars) == 1
        assert bars[0].filters == [{"column": "event_date", "type": "date_range"}]
        # Onto each other TIME dataset's own time column; the snapshot dataset
        # has no time axis and is deliberately left out rather than filtered
        # on a column it does not have.
        assert bars[0].propagate_to == {"orders": {"event_date": "order_date"}}

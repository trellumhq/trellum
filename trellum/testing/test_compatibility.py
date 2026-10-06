"""The frozen contract in docs/COMPATIBILITY.md, pinned by tests.

That document promises a specific set of surfaces will not change without a
major version bump, and an application embedding this framework is built
against exactly those. Until now nothing enforced it: a refactor could
change what ``scan_report_configs`` returns, or the shape ``write_meta``
produces, and the whole suite would still pass. The consumer would find out in
production, after a version bump.

These tests are deliberately shaped differently from the rest of the suite.
Elsewhere, asserting that a function returns a ``list`` is a weak test that
raises coverage without pinning behaviour. Here the shape *is* the promise, so
asserting it is the entire job: every key checked below appears in
COMPATIBILITY.md as something a host may rely on.

When one of these fails, the fix is usually not the test. It is either a major
version bump with a migration note, or a change that should not have been made.
"""

from __future__ import annotations

import inspect
import json

import pytest

# ── trellum.meta ─────────────────────────────────────────────────────────
# "_meta.json Python API: read_meta, write_meta, META_SCHEMA_VERSION"

class TestMetaContract:
    def test_the_documented_functions_exist(self):
        from trellum import meta

        assert callable(meta.read_meta)
        assert callable(meta.write_meta)
        assert isinstance(meta.META_SCHEMA_VERSION, int)

    def test_schema_version_is_2(self):
        """A change here is a breaking change to an artifact the host parses.

        If this fails deliberately, COMPATIBILITY.md and MIGRATIONS.md both need
        updating, and the host needs to learn the new shape first. It last moved
        1 -> 2 when `metrics_used` entries became objects (semantic-layer Phase 2,
        MIGRATIONS.md's 0.2.0 entry).
        """
        from trellum.meta import META_SCHEMA_VERSION

        assert META_SCHEMA_VERSION == 2

    def test_read_meta_returns_empty_dict_when_absent(self, tmp_path):
        """Documented behaviour: a missing file is {} rather than an error.

        The host scans directories that may not have been built yet, so this
        being an exception would turn a normal state into a crash.
        """
        from trellum.meta import read_meta

        assert read_meta(str(tmp_path)) == {}

    def test_write_meta_stamps_the_schema_version(self, tmp_path):
        from trellum.meta import META_SCHEMA_VERSION, read_meta, write_meta

        write_meta(str(tmp_path), {"slug": "demo"})
        meta = read_meta(str(tmp_path))

        assert meta["schema_version"] == META_SCHEMA_VERSION
        assert meta["slug"] == "demo"

    def test_write_meta_merges_rather_than_replaces(self, tmp_path):
        """The host writes one key at a time and expects the rest to survive."""
        from trellum.meta import read_meta, write_meta

        write_meta(str(tmp_path), {"slug": "demo", "name": "Demo"})
        write_meta(str(tmp_path), {"last_status": "success"})
        meta = read_meta(str(tmp_path))

        assert meta["slug"] == "demo"
        assert meta["name"] == "Demo"
        assert meta["last_status"] == "success"

    def test_meta_json_is_the_file_on_disk(self, tmp_path):
        """The artifact is read by name from outside this package."""
        from trellum.meta import write_meta

        write_meta(str(tmp_path), {"slug": "demo"})
        written = tmp_path / "_meta.json"

        assert written.is_file()
        assert json.loads(written.read_text(encoding="utf-8"))["slug"] == "demo"

    def test_read_meta_survives_a_gzipped_file(self, tmp_path):
        """Documented defensive behaviour: a gzipped _meta.json still parses.

        It exists because a transfer that ignores Content-Encoding leaves gzip
        bytes on disk, and crashing the host's scheduler on a corrupt sync is
        worse than decompressing.
        """
        import gzip

        from trellum.meta import read_meta

        payload = json.dumps({"slug": "demo", "schema_version": 1}).encode()
        (tmp_path / "_meta.json").write_bytes(gzip.compress(payload))

        assert read_meta(str(tmp_path))["slug"] == "demo"


# ── trellum.runner.scan_report_configs ───────────────────────────────────
# "Discover reports under a project root"

class TestScanReportConfigsContract:
    @staticmethod
    def _make_report(root, slug: str, **config):
        d = root / slug
        d.mkdir(parents=True)
        (d / "generator.py").write_text("", encoding="utf-8")
        body = {"name": config.pop("name", slug.title()), **config}
        lines = []
        for k, v in body.items():
            lines.append(f"{k}: {v}")
        (d / "report.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    def test_returns_dir_config_and_slug_for_each_report(self, tmp_path):
        """The three documented keys. A host indexes on all of them."""
        from trellum.runner import scan_report_configs

        self._make_report(tmp_path, "alpha")
        entries = scan_report_configs(str(tmp_path))

        assert len(entries) == 1
        entry = entries[0]
        assert set(entry) >= {"dir", "config", "slug"}
        assert entry["slug"] == "alpha"
        assert entry["config"]["name"] == "Alpha"

    def test_dir_is_absolute(self, tmp_path):
        """Documented as an absolute path -- a host resolves it from its own cwd."""
        import os

        from trellum.runner import scan_report_configs

        self._make_report(tmp_path, "alpha")
        entry = scan_report_configs(str(tmp_path))[0]

        assert os.path.isabs(entry["dir"])

    def test_missing_directory_returns_empty_not_raises(self, tmp_path):
        from trellum.runner import scan_report_configs

        assert scan_report_configs(str(tmp_path / "nope")) == []

    def test_underscore_directories_are_skipped(self, tmp_path):
        """``_template`` ships in the demo project and is not a report."""
        from trellum.runner import scan_report_configs

        self._make_report(tmp_path, "alpha")
        self._make_report(tmp_path, "_template")

        assert [e["slug"] for e in scan_report_configs(str(tmp_path))] == ["alpha"]

    def test_results_are_sorted(self, tmp_path):
        """A host renders these in order; unstable ordering is a visible bug."""
        from trellum.runner import scan_report_configs

        for slug in ("charlie", "alpha", "bravo"):
            self._make_report(tmp_path, slug)

        slugs = [e["slug"] for e in scan_report_configs(str(tmp_path))]
        assert slugs == sorted(slugs)

    def test_studio_and_category_filters_are_honoured(self, tmp_path):
        from trellum.runner import scan_report_configs

        self._make_report(tmp_path, "alpha", studio="nova", category="Revenue")
        self._make_report(tmp_path, "bravo", studio="other", category="Ops")

        assert [e["slug"] for e in
                scan_report_configs(str(tmp_path), studio="nova")] == ["alpha"]
        assert [e["slug"] for e in
                scan_report_configs(str(tmp_path), category="Ops")] == ["bravo"]

    def test_signature_keeps_its_documented_parameters(self):
        """Hosts call this with keywords; renaming one silently breaks them."""
        from trellum.runner import scan_report_configs

        params = inspect.signature(scan_report_configs).parameters
        assert set(params) >= {"reports_dir", "studio", "category"}
        assert all(p.default is not inspect.Parameter.empty
                   for p in params.values()), "all parameters must stay optional"


# ── trellum.project ──────────────────────────────────────────────────────

class TestProjectContract:
    def test_documented_functions_exist(self):
        from trellum import project

        assert callable(project.get_project_root)
        assert callable(project.set_project_root)

    def test_root_is_settable_and_readable(self, tmp_path):
        from trellum.project import get_project_root, set_project_root

        original = get_project_root()
        try:
            set_project_root(str(tmp_path))
            assert get_project_root() == str(tmp_path)
        finally:
            set_project_root(original)


# ── trellum.themes.THEME_REGISTRY ────────────────────────────────────────

class TestThemeRegistryContract:
    def test_registry_is_a_name_to_theme_mapping(self):
        from trellum.themes import THEME_REGISTRY, Theme

        assert isinstance(THEME_REGISTRY, dict)
        assert THEME_REGISTRY
        for name, theme in THEME_REGISTRY.items():
            assert isinstance(name, str)
            assert isinstance(theme, Theme), f"{name} is not a Theme"

    def test_the_default_is_registered_under_its_name(self):
        """The HTML builder writes DEFAULT_THEME_NAME into data-theme, and the
        browser looks it up in this registry. A name absent here renders a page
        with no palette at all."""
        from trellum.themes import DEFAULT_THEME, DEFAULT_THEME_NAME, THEME_REGISTRY

        assert DEFAULT_THEME_NAME in THEME_REGISTRY
        assert THEME_REGISTRY[DEFAULT_THEME_NAME] is DEFAULT_THEME

    def test_previously_shipped_theme_names_still_resolve(self):
        """Reports in customer repositories name these in report.yaml.

        Removing one does not fail a build -- it raises at render time in
        somebody else's project.
        """
        from trellum.themes import THEME_REGISTRY

        for name in ("trellum light", "trellum dark", "light", "dark",
                     "money", "blossom", "midnight", "sunset", "nord",
                     "dracula", "solarized", "ocean", "monokai"):
            assert name in THEME_REGISTRY, f"theme '{name}' disappeared"

    def test_resolve_theme_rejects_unknown_names_loudly(self):
        from trellum.themes import resolve_theme

        with pytest.raises(ValueError):
            resolve_theme("no-such-theme")


# ── trellum.output_backends ──────────────────────────────────────────────
# "resolve_output_backend" -- how a host decides where built output goes.

class TestOutputBackendContract:
    @pytest.fixture(autouse=True)
    def _clear_resolution_cache(self):
        """resolve_output_backend() memoises into a module global.

        Undocumented, and surprising enough that it broke the first version of
        these tests: once anything has resolved, later calls ignore the
        environment entirely. See test_resolution_is_memoised below, which pins
        it deliberately rather than leaving it as a trap.
        """
        from trellum.output_backends import backends

        backends._resolved = None
        yield
        backends._resolved = None

    def test_unconfigured_environment_returns_none(self, monkeypatch):
        """Documented and load-bearing: None means 'nothing configured', which
        is how a consumer knows to leave output on local disk. Raising here would
        break every default install."""
        from trellum.output_backends.backends import resolve_output_backend

        monkeypatch.delenv("BI_STORAGE_BACKEND", raising=False)

        assert resolve_output_backend() is None

    def test_local_backend_selected_explicitly(self, monkeypatch):
        from trellum.output_backends.backends import resolve_output_backend

        monkeypatch.setenv("BI_STORAGE_BACKEND", "local")
        backend = resolve_output_backend()

        assert backend is not None
        assert hasattr(backend, "publish")

    def test_the_backend_exposes_publish(self, monkeypatch):
        """`publish(output_dir, slug)` is the whole interface a consumer calls."""
        from trellum.output_backends import backends

        monkeypatch.setenv("BI_STORAGE_BACKEND", "local")
        backends._resolved = None

        backend = backends.resolve_output_backend()
        assert type(backend).__name__ == "LocalBackend"
        sig = inspect.signature(backend.publish)
        assert len(sig.parameters) >= 2, "LocalBackend.publish"

    def test_resolution_is_memoised_for_the_process(self, monkeypatch):
        """Pinning undocumented behaviour, because a consumer will meet it.

        The first successful resolution is cached in a module global and every
        later call returns it, whatever the environment then says. That is a
        reasonable design -- storage does not change mid-process -- but it is
        not in COMPATIBILITY.md, and anything reconfiguring at runtime (or a
        test suite) will be surprised by it.
        """
        from trellum.output_backends import backends

        monkeypatch.setenv("BI_STORAGE_BACKEND", "local")
        first = backends.resolve_output_backend()
        assert type(first).__name__ == "LocalBackend"

        monkeypatch.delenv("BI_STORAGE_BACKEND", raising=False)

        assert backends.resolve_output_backend() is first, (
            "resolution stopped being memoised -- either intended, and "
            "COMPATIBILITY.md needs updating, or a regression")


# ── trellum.rendering.cdn.serve_vendor_request ───────────────────────────
# "Every server that hosts report output must handle this route."

class TestVendorRouteContract:
    def test_the_route_prefix_has_not_moved(self):
        """Built HTML references this path. Changing it silently breaks every
        already-published report, which keeps pointing at the old prefix."""
        from trellum.rendering import cdn

        assert cdn._VENDOR_PREFIX == "/_vendor/"

    def test_serve_vendor_request_declines_non_vendor_paths(self):
        """Returning False is how a host knows to continue its own routing."""
        from trellum.rendering.cdn import serve_vendor_request

        class _Handler:
            path = "/some-report/index.html"

        assert serve_vendor_request(_Handler()) is False


# ── trellum.testing.runner.test_all_reports ──────────────────────────────

class TestHostTestRunnerContract:
    def test_it_exists_and_is_importable_by_its_documented_path(self):
        from trellum.testing.runner import test_all_reports

        assert callable(test_all_reports)


# ── trellum.data.query_df ────────────────────────────────────────────────
# "query_df(conn, sql, params=None, cache_ttl=None)"

class TestQueryDfContract:
    def test_signature_matches_the_documented_one(self):
        from trellum.data import query_df

        params = list(inspect.signature(query_df).parameters)
        assert params[:2] == ["conn", "sql"]
        assert "params" in params
        assert "cache_ttl" in params


# ── trellum.__version__ ──────────────────────────────────────────────────

class TestVersionContract:
    def test_version_is_a_dotted_string(self):
        """Read by hosts and written into _meta.json as framework_version."""
        import trellum

        assert isinstance(trellum.__version__, str)
        parts = trellum.__version__.split(".")
        assert len(parts) >= 2
        assert all(p and p[0].isdigit() for p in parts)


# ── The _meta.json artifact shape ──────────────────────────────────────────

#: The keys COMPATIBILITY.md lists for the _meta.json artifact. A host reads
#: these by name, so losing one is a breaking change even though nothing in
#: this repository would notice.
DOCUMENTED_META_KEYS = (
    "schema_version", "slug", "name", "framework_version",
    "last_run", "last_status", "generation_timings", "validation",
    "metrics_used",
)


class TestMetaArtifactShape:
    def test_documented_keys_survive_a_write_read_round_trip(self, tmp_path):
        """Deliberately does not skip when demo output is absent.

        The obvious version of this test reads a real built report, but pytest
        runs before the demo build in CI, so it would skip -- and CI fails the
        build on any skip. A test that can silently not run is worse than one
        with a narrower scope, so this exercises the read/write API directly.
        """
        from trellum.meta import read_meta, write_meta

        write_meta(str(tmp_path), {k: "x" for k in DOCUMENTED_META_KEYS
                                   if k != "schema_version"})
        meta = read_meta(str(tmp_path))

        for key in DOCUMENTED_META_KEYS:
            assert key in meta, f"_meta.json lost documented key '{key}'"

    def test_write_meta_serialises_values_json_cannot_hold(self, tmp_path):
        """The builder writes datetimes and timing floats into this file.

        write_meta passes default=str, so a value json cannot encode is
        stringified rather than raising -- a host parsing the file never sees a
        half-written artifact.
        """
        from datetime import datetime, timezone

        from trellum.meta import read_meta, write_meta

        write_meta(str(tmp_path), {"last_run": datetime.now(timezone.utc)})
        meta = read_meta(str(tmp_path))

        assert isinstance(meta["last_run"], str)
        assert meta["last_run"]


# ── trellum.metrics.load_metrics ─────────────────────────────────────────
# "The project's metrics.yaml as {name: Metric} — the business-metric
# registry a host may present or index."

class TestMetricsRegistryContract:
    def test_the_documented_function_exists(self):
        from trellum import metrics

        assert callable(metrics.load_metrics)

    def test_absent_file_is_an_empty_registry_not_an_error(self, tmp_path):
        """A host scans projects that may define no metrics at all; that is a
        normal state, not an exception."""
        from trellum.metrics import invalidate_metrics_cache, load_metrics

        invalidate_metrics_cache()
        assert load_metrics(project_root=str(tmp_path)) == {}

    def test_project_root_stays_optional(self):
        """Hosts call this bare (project root already resolved) or with an
        explicit root; both are the documented shape."""
        from trellum.metrics import load_metrics

        params = inspect.signature(load_metrics).parameters
        assert "project_root" in params
        assert all(p.default is not inspect.Parameter.empty
                   for p in params.values())

    def test_entries_carry_the_artifact_facing_fields(self, tmp_path):
        """`_metrics` in data.json is assembled from these attributes; a host
        reading the registry sees the same identity the artifact carries."""
        from trellum.metrics import invalidate_metrics_cache, load_metrics

        (tmp_path / "metrics.yaml").write_text(
            "version: 1\n"
            "metrics:\n"
            "  - name: gross_revenue\n"
            "    label: Gross Revenue\n"
            "    format: currency\n"
            "    agg: sum\n"
            "    column: total_revenue\n",
            encoding="utf-8",
        )
        invalidate_metrics_cache()
        try:
            registry = load_metrics(project_root=str(tmp_path))
        finally:
            invalidate_metrics_cache()

        metric = registry["gross_revenue"]
        assert metric.label == "Gross Revenue"
        assert metric.format == "currency"
        assert metric.agg == "sum"
        assert metric.column == "total_revenue"
        assert metric.version == 1
        assert metric.definition_hash


class TestMetricsUsedArtifactShape:
    """`metrics_used` in _meta.json: always present, `[]` when none.

    A host indexes reports by metric without opening data.json, so the key
    being absent would force every consumer to defend against it. Since
    schema v2 (see MIGRATIONS.md's 0.2.0 entry) each entry also carries the
    metric's build-time `definition_hash`/`version`, so a host can tell a
    claim's definition apart from the metric's current one the same way --
    without opening data.json either.
    """

    def _meta_for(self, tmp_path, **kwargs):
        import json

        from trellum.rendering.artifacts import _write_meta

        _write_meta(str(tmp_path), "demo", "Demo", {}, **kwargs)
        return json.loads((tmp_path / "_meta.json").read_text(encoding="utf-8"))

    def test_claims_are_carried_through_sorted_by_id(self, tmp_path):
        meta = self._meta_for(tmp_path, metrics_used=[
            {"id": "b_metric", "definition_hash": "abc123", "version": 2},
            {"id": "a_metric", "definition_hash": "def456", "version": 1},
        ])
        assert meta["metrics_used"] == [
            {"id": "a_metric", "definition_hash": "def456", "version": 1},
            {"id": "b_metric", "definition_hash": "abc123", "version": 2},
        ]

    def test_bare_ids_are_accepted_with_unknown_hash_and_version(self, tmp_path):
        """A caller that only has ids (or a v1-shaped list passed straight
        through) still gets a valid v2 artifact -- 'unknown', not a guess."""
        meta = self._meta_for(tmp_path, metrics_used=["b_metric", "a_metric"])
        assert meta["metrics_used"] == [
            {"id": "a_metric", "definition_hash": None, "version": None},
            {"id": "b_metric", "definition_hash": None, "version": None},
        ]

    def test_no_claims_is_an_empty_list_not_a_missing_key(self, tmp_path):
        meta = self._meta_for(tmp_path)
        assert meta["metrics_used"] == []


class TestNormalizeMetricsUsedContract:
    """`trellum.meta.normalize_metrics_used`: the documented way to read
    `metrics_used` regardless of which schema version wrote it."""

    def test_v2_objects_pass_through_deduped_and_sorted(self):
        from trellum.meta import normalize_metrics_used

        out = normalize_metrics_used([
            {"id": "b", "definition_hash": "h2", "version": 2},
            {"id": "a", "definition_hash": "h1", "version": 1},
        ])
        assert out == [
            {"id": "a", "definition_hash": "h1", "version": 1},
            {"id": "b", "definition_hash": "h2", "version": 2},
        ]

    def test_v1_bare_ids_normalize_to_unknown_hash_and_version(self):
        from trellum.meta import normalize_metrics_used

        out = normalize_metrics_used(["gross_revenue"])
        assert out == [
            {"id": "gross_revenue", "definition_hash": None, "version": None}
        ]

    def test_empty_and_none_are_both_an_empty_list(self):
        from trellum.meta import normalize_metrics_used

        assert normalize_metrics_used(None) == []
        assert normalize_metrics_used([]) == []


# ── report.yaml `display:` ───────────────────────────────────────────────

class TestDisplayBlockContract:
    """The card-display block: icon, colour, sort order.

    Three fields the framework reads not at all -- it carries them into
    `_meta.json` for whatever lists the reports. They are here because a
    passthrough is exactly the kind of thing that stops passing through
    without anyone noticing: nothing in this repository renders an icon, so
    only a test can tell you the value still arrives.
    """

    def _meta_for(self, tmp_path, config):
        import json

        from trellum.rendering.artifacts import _write_meta

        _write_meta(str(tmp_path), "demo", "Demo", config)
        return json.loads((tmp_path / "_meta.json").read_text(encoding="utf-8"))

    def test_display_block_is_carried_through(self, tmp_path):
        block = {"icon": "activity", "color": "#4e79a7", "priority": 1}
        meta = self._meta_for(tmp_path, {"display": block})
        assert meta["display"] == block

    def test_absent_block_is_an_empty_dict_not_a_crash(self, tmp_path):
        meta = self._meta_for(tmp_path, {})
        assert meta["display"] == {}

    def test_the_block_is_written_once(self, tmp_path):
        """One key, not two.

        A draft of this carried the same block under a second name as well.
        A consumer reading the other one works right up until the duplicate
        goes away, which is the worst moment to find out.
        """
        block = {"icon": "activity", "color": "#4e79a7", "priority": 1}
        meta = self._meta_for(tmp_path, {"display": block})
        duplicates = [k for k, v in meta.items() if v == block and k != "display"]
        assert not duplicates, f"the block is also written as {duplicates}"


# ── host-imported helpers ────────────────────────────────────────────────

class TestHostHelpersContract:
    """Two helpers the embedding application imports by name.

    Both were underscore-prefixed until they were promoted into
    COMPATIBILITY.md's embedding-application table; a host binds SQL and
    reads a report's own annotation events through them.
    """

    def test_bind_params_signature_and_behaviour(self):
        from trellum.data.query import bind_params, sql_dialect_for

        assert bind_params("SELECT :a, :b", {"a": 1, "b": "x'y"}) == "SELECT 1, 'x''y'"
        assert (
            bind_params("SELECT :b", {"b": "x'y"}, sql_dialect_for("bigquery"))
            == r"SELECT 'x\'y'"
        )

    def test_local_annotation_events_shape(self):
        from trellum.runner import local_annotation_events

        rows = local_annotation_events(
            {"studio": "s", "annotations": {"events": [{"date": "2026-01-01", "label": "L"}]}}
        )
        assert rows == [{
            "date": "2026-01-01", "end_date": "", "label": "L", "type": "event",
            "studio": "s", "default_visible": True,
        }]
        assert local_annotation_events({}) == []

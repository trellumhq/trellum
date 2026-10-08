"""The assistant's read surface: studio-scoped, read-only, allowlisted."""
import json

import pytest

from apps.assistant import catalog
from apps.assistant.system_prompt import build_system_prompt
from apps.assistant.tools import AssistantToolbox

pytestmark = pytest.mark.django_db


@pytest.fixture
def built(studio_tree, report_row):
    """report_row ('player-overview') with a small built dataset on disk."""
    out = studio_tree.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps({
        "_ds_revenue_daily": {
            "_cols": ["date", "platform", "revenue"],
            "_data": [
                ["2026-03-01", "ios", 100],
                ["2026-03-01", "android", 50],
                ["2026-03-02", "ios", 200],
            ],
            "_dict": {},
        },
    }), encoding="utf-8")
    (out / "_meta.json").write_text(json.dumps({
        "schema_version": 1,
        "slug": report_row.slug,
        "last_run": "2026-03-02T04:00:00Z",
        "last_status": "success",
        "details": {"datasets": [{"id": "revenue_daily", "rows": 3, "columns": 3}]},
    }), encoding="utf-8")
    return out


@pytest.fixture
def toolbox(studio_tree):
    catalog.invalidate()
    return AssistantToolbox(studio_tree)


class TestQueryReportData:
    def test_aggregates(self, toolbox, built):
        result = toolbox.execute("query_report_data", {
            "slug": "player-overview",
            "dataset_id": "revenue_daily",
            "group_by": ["date"],
            "metrics": [{"column": "revenue", "agg": "sum"}],
        })
        assert "3 rows total" in result
        assert "| 2026-03-01 | 150 |" in result
        assert "| 2026-03-02 | 200 |" in result

    def test_filters(self, toolbox, built):
        result = toolbox.execute("query_report_data", {
            "slug": "player-overview",
            "dataset_id": "revenue_daily",
            "filters": {"platform": ["ios"]},
            "metrics": [{"column": "revenue"}],
        })
        assert "2 after filters" in result

    def test_empty_filter_gets_a_self_healing_hint(self, toolbox, built):
        result = toolbox.execute("query_report_data", {
            "slug": "player-overview",
            "dataset_id": "revenue_daily",
            "date_column": "date",
            "date_from": "2030-01-01",
        })
        assert "0 rows after filtering" in result
        assert "2026-03-01 → 2026-03-02" in result

    def test_unknown_dataset_lists_alternatives(self, toolbox, built):
        result = toolbox.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "ghost",
        })
        assert result.startswith("Error")
        assert "revenue_daily" in result

    def test_unbuilt_report(self, toolbox, report_row):
        result = toolbox.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "x",
        })
        assert "no built output" in result

    def test_slug_traversal_rejected(self, toolbox, built):
        result = toolbox.execute("query_report_data", {
            "slug": "../../../etc", "dataset_id": "revenue_daily",
        })
        assert result.startswith("Error: invalid slug")

    def test_unknown_tool(self, toolbox):
        assert toolbox.execute("rm_rf", {}).startswith("Error: unknown tool")

    def test_provenance_and_built_at(self, toolbox, built):
        result = toolbox.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
            "filters": {"platform": ["ios"]}, "group_by": ["date"],
            "metrics": [{"column": "revenue"}],
        })
        assert "(built 2026-03-02T04:00:00Z)" in result
        assert toolbox.provenance == {
            "report": "player-overview",
            "report_name": "Player-Overview",
            "built_at": "2026-03-02T04:00:00Z",
            "dataset": "revenue_daily",
            "filters": {"platform": ["ios"]},
            "date_range": None,
            "group_by": ["date"],
            "aggregate": [{"column": "revenue", "agg": "sum"}],
            "rows_total": 3,
            "rows_after": 2,
            "rows_returned": 2,
            "link": "/s/demo/casino/r/player-overview/",  # no filter bar: no params
        }
        toolbox.execute("list_reports", {})
        assert toolbox.provenance is None


def _add_filter_bars(built, *bars):
    """Register FilterBars in data.json the way the page's url_sync sees them."""
    data = json.loads((built / "data.json").read_text(encoding="utf-8"))
    data["components"] = {f"fb{i}": {"type": "filter_bar", **bar} for i, bar in enumerate(bars)}
    (built / "data.json").write_text(json.dumps(data), encoding="utf-8")


class TestFilteredLinks:
    QUERY = {
        "slug": "player-overview", "dataset_id": "revenue_daily",
        "filters": {"platform": ["ios", "android"], "revenue": [100]},  # revenue: no control
        "date_column": "date", "date_from": "2026-03-01", "date_to": "2026-03-02",
    }

    def test_one_bar_writes_bare_columns(self, toolbox, built):
        _add_filter_bars(built, {
            "dataset_id": "revenue_daily",
            "filters": [{"column": "platform", "type": "dropdown"},
                        {"column": "date", "type": "date_range"}],
        })
        toolbox.execute("query_report_data", self.QUERY)
        assert toolbox.provenance["link"] == (
            "/s/demo/casino/r/player-overview/?platform=ios,android&date=2026-03-01..2026-03-02"
        )

    def test_two_bars_prefix_the_dataset_and_follow_propagate_to(self, toolbox, built):
        _add_filter_bars(
            built,
            {"dataset_id": "other", "filters": [{"column": "os", "type": "toggle"}],
             "propagate_to": {"revenue_daily": {"os": "platform"}}},
            {"dataset_id": "revenue_daily", "filters": [{"column": "date", "type": "date_range"}]},
        )
        toolbox.execute("query_report_data", self.QUERY)
        assert toolbox.provenance["link"] == (
            "/s/demo/casino/r/player-overview/?other.os=ios&revenue_daily.date=2026-03-01..2026-03-02"
        )


class TestStudioScoping:
    def test_another_studios_output_is_invisible(self, org, studio2, built, data_dir):
        """The toolbox is bound to one studio: same slug, no data."""
        studio2.ensure_dirs()
        other = AssistantToolbox(studio2)
        assert other.data_files("player-overview") == []
        result = other.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert "no built output" in result

    def test_list_reports_only_lists_this_studio(self, studio2, toolbox, built, report_row):
        from apps.reports.models import Report

        Report.objects.create(studio=studio2, slug="secret-report", name="Secret")
        listing = toolbox.execute("list_reports", {})
        assert "player-overview" in listing
        assert "secret-report" not in listing


class TestRemoteBackend:
    """The assistant reads what serving reads. On the s3 backend the studio's own
    output directory is the runner's write location — empty or stale on a web
    node — so the toolbox must go through the storage layer, which resolves
    the current build from the bucket and materialises it into the read cache.
    """

    @pytest.fixture
    def remote(self, studio_tree, settings, monkeypatch):
        from apps.core import storage
        from apps.core.tests.test_storage import FakeClient, FakeS3

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        fake = FakeS3()
        monkeypatch.setattr(storage, "_s3", lambda: fake)
        monkeypatch.setattr(storage, "_client", lambda: FakeClient(fake))
        monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 0.0)
        storage._pointer_memo.clear()
        storage._locks.clear()
        return fake

    def test_data_files_come_from_the_bucket_not_the_runner_disk(
        self, studio_tree, report_row, remote, built
    ):
        """`built` wrote to the studio's own output dir — the runner's disk.
        With the bucket holding a different (newer) build, assistant must answer
        from the bucket's copy."""
        from apps.core.tests.test_storage import install_build

        install_build(
            remote,
            slug="player-overview",
            files={"data.json": json.dumps({
                "_ds_revenue_daily": {
                    "_cols": ["date", "revenue"],
                    "_data": [["2026-04-01", 999]],
                    "_dict": {},
                },
            }).encode()},
        )
        box = AssistantToolbox(studio_tree)
        files = box.data_files("player-overview")
        assert len(files) == 1
        assert "999" in files[0].read_text(encoding="utf-8")
        # And it really is the cache copy, not the studio directory.
        assert str(studio_tree.output_dir) not in str(files[0])

    def test_read_meta_comes_from_the_bucket(self, studio_tree, report_row, remote, built):
        from apps.core.tests.test_storage import install_build

        install_build(remote, slug="player-overview", last_run="bucket-run")
        box = AssistantToolbox(studio_tree)
        assert box.read_meta("player-overview")["last_run"] == "bucket-run"

    def test_a_store_outage_degrades_to_a_tool_error(self, studio_tree, report_row, remote, monkeypatch):
        from apps.core import storage

        def down(*_a, **_kw):
            raise OSError("connection refused")

        monkeypatch.setattr(storage, "_client", down)
        monkeypatch.setattr(storage, "_s3", down)
        box = AssistantToolbox(studio_tree)
        result = box.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert result.startswith("Error")


class TestReadDoc:
    def test_reads_allowlisted_file(self, toolbox, studio_tree):
        ctx = studio_tree.project_root / "project_context"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "schema.md").write_text("# Tables\nfact_purchase", encoding="utf-8")
        assert "fact_purchase" in toolbox.execute("read_doc", {"path": "project_context/schema.md"})

    def test_reads_report_source(self, toolbox, studio_tree, write_report):
        write_report("player-overview")
        out = toolbox.execute("read_doc", {"path": "reports/player-overview/report.yaml"})
        assert "player-overview" in out

    def test_traversal_outside_the_studio_blocked(self, toolbox):
        result = toolbox.execute("read_doc", {"path": "../../../../etc/passwd"})
        assert result.startswith("Error")
        assert "outside the readable part" in result

    def test_non_allowlisted_path_blocked(self, toolbox, studio_tree):
        secret = studio_tree.project_root / "config.yaml"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("secret: yes", encoding="utf-8")
        assert "outside the readable part" in toolbox.execute("read_doc", {"path": "config.yaml"})

    def test_missing_file(self, toolbox):
        assert "not found" in toolbox.execute("read_doc", {"path": "project_context/nope.md"})

    def test_datasource_config_is_never_readable(self, toolbox, studio_tree):
        """Connection details live there. It used to be on the allowlist."""
        p = studio_tree.project_root / "data-sources" / "config.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("password: hunter2", encoding="utf-8")
        out = toolbox.execute("read_doc", {"path": "data-sources/config.yaml"})
        assert out.startswith("Error") and "hunter2" not in out

    def test_report_source_refused_when_sharing_is_off(self, studio_tree, write_report):
        write_report("player-overview")
        box = AssistantToolbox(studio_tree, share_report_source=False)
        out = box.execute("read_doc", {"path": "reports/player-overview/report.yaml"})
        assert out.startswith("Error") and "turned off" in out
        # Other allowlisted paths are unaffected.
        assert "not found" in box.execute("read_doc", {"path": "assistant.md"})


class TestCatalogAndPrompt:
    def test_catalog_lists_datasets_with_date_coverage(self, toolbox, built):
        text = catalog.build_catalog(toolbox)
        assert "slug `player-overview`" in text
        assert "dataset `revenue_daily`" in text
        assert "3 rows" in text
        assert "date 2026-03-01 → 2026-03-02" in text

    def test_catalog_skips_reports_without_output(self, toolbox, report_row):
        assert catalog.build_catalog(toolbox) == ""

    def test_catalog_cache_forgets_a_removed_report(self, toolbox, built, studio_tree):
        from apps.reports.models import Report
        from apps.reports.scan import sync_studio_registry

        catalog.build_catalog(toolbox)
        assert (studio_tree.pk, "player-overview") in catalog._CACHE

        # The report vanishes from the repository and the registry follows.
        import shutil

        shutil.rmtree(studio_tree.reports_dir / "player-overview")
        sync_studio_registry(studio_tree)
        assert not Report.objects.filter(studio=studio_tree, slug="player-overview",
                                         present_in_scan=True).exists()
        catalog.build_catalog(AssistantToolbox(studio_tree))
        assert (studio_tree.pk, "player-overview") not in catalog._CACHE

    def test_catalog_cache_refreshes_on_rebuild(self, toolbox, built):
        first = catalog.build_catalog(toolbox)
        data = json.loads((built / "data.json").read_text(encoding="utf-8"))
        data["_ds_revenue_daily"]["_data"].append(["2026-03-03", "ios", 300])
        (built / "data.json").write_text(json.dumps(data), encoding="utf-8")
        second = catalog.build_catalog(toolbox)
        assert first != second
        assert "4 rows" in second

    def test_system_prompt_carries_studio_context_and_catalog(self, toolbox, built):
        blocks = build_system_prompt(toolbox)
        assert len(blocks) == 2
        cached, session = blocks
        assert cached["cache_control"] == {"type": "ephemeral"}
        assert "AI assistant" in cached["text"]
        assert "Casino Studio" in cached["text"]
        assert "dataset `revenue_daily`" in cached["text"]
        assert "STRICTLY read-only" in cached["text"]
        assert "TODAY IS" in session["text"]
        assert "cache_control" not in session


class TestPerTurnParseCache:
    """One turn parses each report's data.json once.

    Parsing costs roughly 12 ms/MB and reports run to tens of megabytes, so
    the catalog, get_report_details and every query in a turn asking the same
    file to be re-read was the largest avoidable cost in the tool path.
    """

    def _count_parses(self, monkeypatch):
        """Count real reads by counting Path.read_text calls on data files."""
        from pathlib import Path

        seen = []
        real = Path.read_text

        def counting(self, *a, **kw):
            if self.name.startswith("data") and self.suffix == ".json":
                seen.append(self.name)
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", counting)
        return seen

    def test_catalog_then_queries_parse_once(self, toolbox, built, monkeypatch):
        seen = self._count_parses(monkeypatch)

        build_system_prompt(toolbox)                       # catalog reads it
        toolbox.execute("get_report_details", {"slug": "player-overview"})
        for _ in range(3):
            toolbox.execute("query_report_data", {
                "slug": "player-overview", "dataset_id": "revenue_daily",
            })

        assert seen.count("data.json") == 1

    def test_a_rebuild_mid_turn_is_picked_up(self, toolbox, built):
        first, _ = toolbox.load_dataset("player-overview", "revenue_daily")
        assert len(first) == 3

        (built / "data.json").write_text(json.dumps({
            "_ds_revenue_daily": {
                "_cols": ["date", "platform", "revenue"],
                "_data": [["2026-03-03", "ios", 999]],
                "_dict": {},
            },
        }), encoding="utf-8")

        second, _ = toolbox.load_dataset("player-overview", "revenue_daily")
        assert len(second) == 1

    def test_the_cache_does_not_grow_without_bound(self, toolbox, studio_tree):
        from apps.assistant.tools import _PARSE_CACHE_ENTRIES

        for i in range(_PARSE_CACHE_ENTRIES + 3):
            out = studio_tree.output_dir / f"r{i}"
            out.mkdir(parents=True, exist_ok=True)
            (out / "data.json").write_text(
                json.dumps({"_ds_d": {"_cols": ["a"], "_data": [[i]], "_dict": {}}}),
                encoding="utf-8",
            )
            toolbox.load_dataset(f"r{i}", "d")

        assert len(toolbox._parsed) == _PARSE_CACHE_ENTRIES


class TestEmptyResultHint:
    """A filter that matches nothing must say what WOULD have matched, and
    must not re-read the file once per filter column to find out."""

    def test_hint_names_the_real_range_and_values(self, toolbox, built, monkeypatch):
        from pathlib import Path

        reads = []
        real = Path.read_text
        monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw: (
            reads.append(self.name) or real(self, *a, **kw)
        ))

        out = toolbox.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
            "date_column": "date", "date_from": "2027-01-01",
            "filters": {"platform": ["ios"]},
        })

        assert "0 rows after filtering" in out
        assert "2026-03-01" in out and "2026-03-02" in out   # the real span
        assert "ios" in out                                   # the real values
        assert reads.count("data.json") == 1


def _write_context(toolbox, text):
    from apps.assistant.system_prompt import PROJECT_CONTEXT_FILE

    p = toolbox.project_root / PROJECT_CONTEXT_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestProjectContextFile:
    """The one file a studio's repository uses to brief the assistant."""

    def test_its_contents_reach_the_cached_system_block(self, toolbox, built):
        _write_context(toolbox, "Revenue means NET revenue, after refunds.")
        cached = build_system_prompt(toolbox)[0]["text"]
        assert "Revenue means NET revenue, after refunds." in cached

    def test_absent_is_fine_and_adds_no_section(self, toolbox, built):
        cached = build_system_prompt(toolbox)[0]["text"]
        assert "WHAT THIS TEAM TOLD YOU" not in cached

    def test_it_is_framed_as_theirs_and_subordinate_to_the_hard_rules(self, toolbox, built):
        """The file is customer-authored text going into a system prompt. It
        must arrive labelled as the team's claims about their data, and
        explicitly unable to widen what the assistant may do -- otherwise the
        section reads to the model as more instructions from us."""
        _write_context(toolbox, "anything")
        cached = build_system_prompt(toolbox)[0]["text"]
        assert "Written by the analysts who own these reports" in cached
        assert "does NOT outrank the hard rules" in cached
        assert "STRICTLY read-only" in cached  # the rules it cannot override

    def test_it_can_be_re_read_on_demand(self, toolbox, built):
        """It is in the read_doc allowlist too, so a truncated file is
        reachable in full and the assistant can quote it back."""
        _write_context(toolbox, "the whole briefing")
        assert "the whole briefing" in toolbox.execute("read_doc", {"path": "assistant.md"})

    def test_an_oversized_file_is_clipped_and_says_so(self, toolbox, built):
        from apps.assistant.system_prompt import _PROMPT_FILE_MAX_CHARS

        # A token that appears nowhere else in the prompt, so counting it
        # measures the clip rather than the rest of the persona.
        _write_context(toolbox, "Zq" * 45_000)
        cached = build_system_prompt(toolbox)[0]["text"]

        assert cached.count("Zq") == _PROMPT_FILE_MAX_CHARS // 2
        assert "truncated at 20,000 of 90,000 chars" in cached
        assert "read_doc('assistant.md')" in cached

    def test_revoked_full_scope_does_not_read_project_context(
        self, selected_viewer, studio_tree
    ):
        toolbox = AssistantToolbox(studio_tree, actor=selected_viewer, scope="full")
        _write_context(toolbox, "STUDIO SECRET")
        assert "STUDIO SECRET" not in build_system_prompt(toolbox)[0]["text"]


@pytest.mark.parametrize("model", ["proxy", "edge-external"])
def test_full_toolbox_private_content_readiness_preserves_management_sources(
    studio_tree, report_row, built, org_admin, settings, model,
):
    report_row.audience = report_row.AUDIENCE_PRIVATE
    report_row.save(update_fields=["audience"])
    catalog.invalidate()
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    full = AssistantToolbox(studio_tree, actor=org_admin, scope="full")
    assert full.read_data(built / "data.json")
    assert full.data_files(report_row.slug)
    settings.TRELLUM_REPORT_ACCESS_MODEL = model
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = model != "proxy"
    assert full.read_data(built / "data.json") is None
    assert full.data_files(report_row.slug) == []
    assert "temporarily unavailable" in full.execute("query_report_data", {
        "slug": report_row.slug, "dataset_id": "revenue_daily",
    })
    # The prechecked execution path still rechecks the content boundary.
    full._executing_prechecked = True
    assert full.read_data(built / "data.json") is None
    full._executing_prechecked = False
    assert full._access_error() is None
    trusted = AssistantToolbox(studio_tree)
    assert trusted.read_data(built / "data.json")


class TestReportBoundToolbox:
    @pytest.fixture
    def bound(self, selected_viewer, studio_tree, report_row):
        catalog.invalidate()
        return AssistantToolbox(
            studio_tree, actor=selected_viewer, scope="report", report=report_row
        )

    def test_catalog_tools_and_provenance_only_name_the_bound_report(
        self, bound, built, studio_tree
    ):
        from apps.reports.models import Report

        other = Report.objects.create(studio=studio_tree, slug="private-report", name="Private")
        out = studio_tree.output_dir / other.slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "data.json").write_text(json.dumps({
            "_ds_secret": {"_cols": ["secret"], "_data": [["hidden"]], "_dict": {}},
        }), encoding="utf-8")
        (out / "_meta.json").write_text(json.dumps({
            "slug": other.slug, "last_status": "success", "details": {},
        }), encoding="utf-8")

        listing = bound.execute("list_reports", {})
        prompt = build_system_prompt(bound)[0]["text"]
        assert "player-overview" in listing and "private-report" not in listing
        assert "revenue_daily" in prompt
        assert "private-report" not in prompt and "hidden" not in prompt

        result = bound.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert "3 rows total" in result
        assert bound.provenance["report"] == "player-overview"

    def test_forbidden_slug_is_rejected_before_storage_or_cache(
        self, bound, monkeypatch
    ):
        from apps.core import storage

        def touched(*_args, **_kwargs):
            raise AssertionError("storage must not be touched")

        monkeypatch.setattr(storage, "output_root", touched)
        monkeypatch.setattr(storage, "read_meta", touched)
        assert "cannot access" in bound.execute(
            "get_report_details", {"slug": "private-report"}
        )
        assert bound.read_meta("private-report") == {}
        assert bound.data_files("private-report") == []
        assert "private-report" not in bound._files

    def test_only_bound_report_source_is_readable(
        self, bound, studio_tree, write_report
    ):
        write_report("private-report")
        (studio_tree.project_root / "assistant.md").write_text(
            "GLOBAL SECRET", encoding="utf-8"
        )
        assert "player-overview" in bound.execute(
            "read_doc", {"path": "reports/player-overview/report.yaml"}
        )
        for path in (
            "assistant.md", "README.md", "project_context/schema.md",
            "reports/private-report/report.yaml",
        ):
            result = bound.execute("read_doc", {"path": path})
            assert result.startswith("Error")
            assert "GLOBAL SECRET" not in result
        assert "GLOBAL SECRET" not in build_system_prompt(bound)[0]["text"]

    def test_source_setting_still_applies(self, selected_viewer, studio_tree, report_row):
        bound = AssistantToolbox(
            studio_tree, share_report_source=False, actor=selected_viewer,
            scope="report", report=report_row,
        )
        assert "turned off" in bound.execute(
            "read_doc", {"path": "reports/player-overview/report.yaml"}
        )

    def test_revocation_blocks_even_already_cached_data(
        self, bound, built, selected_viewer
    ):
        assert bound.data_files("player-overview")
        selected_viewer.selected_report_grant.report_grants.all().delete()
        assert "no longer available" in bound.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert bound.data_files("player-overview") == []

    def test_removed_report_blocks_even_already_cached_data(
        self, bound, built, report_row
    ):
        assert bound.data_files("player-overview")
        report_row.present_in_scan = False
        report_row.save(update_fields=["present_in_scan"])
        assert "no longer available" in bound.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert bound.data_files("player-overview") == []

    def test_inactive_actor_is_blocked_even_with_cached_data(
        self, bound, built, selected_viewer
    ):
        assert bound.data_files("player-overview")
        type(selected_viewer).objects.filter(pk=selected_viewer.pk).update(is_active=False)
        assert "no longer access" in bound.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert bound.data_files("player-overview") == []

    def test_selected_actor_is_fail_closed_until_scoped_access_is_ready(
        self, bound, built, settings
    ):
        assert bound.data_files("player-overview")
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        assert "temporarily unavailable" in bound.execute("query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily",
        })
        assert bound.data_files("player-overview") == []

    def test_promotion_does_not_widen_a_bound_toolbox(
        self, bound, selected_viewer, studio_tree, grant_studio
    ):
        grant_studio(selected_viewer, studio_tree, "viewer")
        assert "cannot access" in bound.execute(
            "get_report_details", {"slug": "private-report"}
        )


class TestContextFileIsActuallySynced:
    """The bug this whole mechanism replaces.

    The previous context paths (``project_context/chat_rules/``,
    ``.cursor/rules/``) were read from the studio project root by the prompt
    builder -- and nothing ever put them there. The portal takes a sparse
    checkout, so only the reports path and a named list of root files are
    materialized. The feature was dead on the primary deployment path and
    nothing failed to say so.

    This is the test that would have caught it: the file the prompt reads and
    the files the sync writes have to be the same set.
    """

    def test_the_shared_list_carries_the_file_the_prompt_reads(self):
        from apps.assistant.system_prompt import PROJECT_CONTEXT_FILE
        from apps.runner.executor import project_root_materialized_files

        assert PROJECT_CONTEXT_FILE in project_root_materialized_files()

    def test_both_ways_a_project_arrives_use_that_same_list(self):
        """A git-backed studio syncs; a seeded one is imported. The file only
        exists on the path that copies it, so the two must not have their own
        opinions -- which they did, and import_project's opinion omitted it."""
        import inspect

        from apps.reports.management.commands import import_project
        from apps.runner.gitsync import StudioGitSync

        for source in (
            inspect.getsource(StudioGitSync._root_synced_files),
            inspect.getsource(import_project),
        ):
            assert "project_root_materialized_files" in source

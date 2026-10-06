"""Experiment overview: the extractor, the lifecycle rule, and the gated view.

See ``apps.reports.overviews.experiments`` for the full contract. This suite mostly
exists to prove two promises: a broken/missing report never breaks the page
(every failure mode degrades one row, never a 500), and the ``past_end``
carve-out -- the one genuinely non-obvious rule in the module -- fires
exactly when documented.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from apps.core import roles
from apps.reports.overviews import experiments as experiments_mod
from apps.reports.overviews.experiments import (
    derive_status,
    extract_ab_component,
    invalidate_experiments_cache,
    is_ab_report,
    primary_metric,
    studio_experiments,
)
from apps.reports.scan import sync_studio_registry

pytestmark = pytest.mark.django_db


# ── is_ab_report ─────────────────────────────────────────────────────────

class TestIsAbReport:
    def test_true_for_a_dict_ab_test_block(self):
        assert is_ab_report({"ab_test": {"start_date": "2026-01-01"}}) is True

    def test_false_when_absent(self):
        assert is_ab_report({}) is False

    def test_false_when_not_a_dict(self):
        assert is_ab_report({"ab_test": "yes please"}) is False

    def test_false_for_none_config(self):
        assert is_ab_report(None) is False


# ── extract_ab_component ─────────────────────────────────────────────────

def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


_AB_COMPONENT_BODY = {
    "type": "ab_compare",
    "header": {"test_name": "Checkout v2", "split": "50/50"},
    "body": {
        "kpi_rows": [
            {"metric": "Revenue", "key": "rev", "delta_pct": 4.2,
             "higher_is_better": True, "control_val": "$1", "test_val": "$2"}
        ],
        "volume_rows": [],
        "timeseries": {},
    },
    "modes": None,
    "default_mode": "",
}


class TestExtractAbComponent:
    def test_missing_file_returns_none(self, tmp_path):
        assert extract_ab_component(tmp_path) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        (tmp_path / "data.json").write_text("{not valid json", encoding="utf-8")
        assert extract_ab_component(tmp_path) is None

    def test_no_ab_component_returns_none(self, tmp_path):
        _write_json(tmp_path / "data.json", {"components": {"fw_c1": {"type": "kpi_grid"}}})
        assert extract_ab_component(tmp_path) is None

    def test_body_variant(self, tmp_path):
        _write_json(tmp_path / "data.json", {
            "components": {"fw_c1": _AB_COMPONENT_BODY},
            "_freshness": {"generated_at": "2026-08-01T00:00:00+00:00"},
            "_framework_version": "9.9.9",
        })
        result = extract_ab_component(tmp_path)
        assert result is not None
        assert result["component"]["type"] == "ab_compare"
        assert result["generated_at"] == "2026-08-01T00:00:00+00:00"
        assert result["framework_version"] == "9.9.9"

    def test_modes_and_default_mode_variant(self, tmp_path):
        comp = {
            "type": "ab_compare",
            "header": {"test_name": "Checkout v2"},
            "body": None,
            "modes": {
                "raw": {"label": "Raw", "kpi_rows": [
                    {"metric": "Revenue", "delta_pct": 1.0, "higher_is_better": True}
                ]},
                "cuped": {"label": "CUPED-adjusted", "kpi_rows": [
                    {"metric": "Revenue", "delta_pct": 4.2, "higher_is_better": True}
                ]},
            },
            "default_mode": "cuped",
        }
        _write_json(tmp_path / "data.json", {"components": {"fw_c1": comp}})
        result = extract_ab_component(tmp_path)
        assert result is not None
        assert result["component"]["default_mode"] == "cuped"
        assert result["component"]["modes"]["cuped"]["label"] == "CUPED-adjusted"

    def test_component_only_in_scope_file(self, tmp_path):
        _write_json(tmp_path / "data.json", {
            "components": {"fw_c1": {"type": "kpi_grid"}},
            "_scopeFiles": {"mobile": "data_mobile.json"},
        })
        _write_json(tmp_path / "data_mobile.json", {"components": {"fw_c2": _AB_COMPONENT_BODY}})
        result = extract_ab_component(tmp_path)
        assert result is not None
        assert result["component"]["header"]["test_name"] == "Checkout v2"

    def test_scope_file_traversal_is_blocked(self, tmp_path):
        """A ``_scopeFiles`` entry that resolves outside output_dir must never
        be followed, however it got there."""
        outside = tmp_path.parent / "outside_data.json"
        _write_json(outside, {"components": {"fw_c2": _AB_COMPONENT_BODY}})
        _write_json(tmp_path / "data.json", {
            "components": {},
            "_scopeFiles": {"evil": "../outside_data.json"},
        })
        assert extract_ab_component(tmp_path) is None


# ── derive_status ────────────────────────────────────────────────────────

class TestDeriveStatus:
    def test_unparseable_start_is_unknown(self):
        result = derive_status({"start_date": "not-a-date"}, date(2026, 8, 20))
        assert result["status"] == "unknown"
        assert result["days"] is None

    def test_missing_start_is_unknown(self):
        assert derive_status({}, date(2026, 8, 20))["status"] == "unknown"

    def test_future_start_is_scheduled(self):
        result = derive_status({"start_date": "2026-09-01"}, date(2026, 8, 20))
        assert result["status"] == "scheduled"
        assert result["days"] == 0

    def test_running_with_no_end(self):
        result = derive_status({"start_date": "2026-08-01"}, date(2026, 8, 20))
        assert result["status"] == "running"
        assert result["days"] == 20
        assert result["no_end_declared"] is True
        assert result["ends_in_days"] is None

    def test_running_with_future_end(self):
        result = derive_status(
            {"start_date": "2026-08-01", "end_date": "2026-08-25"}, date(2026, 8, 20)
        )
        assert result["status"] == "running"
        assert result["ends_in_days"] == 5
        assert result["no_end_declared"] is False

    def test_end_exactly_today_is_still_running(self):
        result = derive_status(
            {"start_date": "2026-08-01", "end_date": "2026-08-20"}, date(2026, 8, 20)
        )
        assert result["status"] == "running"

    def test_concluded_when_end_passed_and_schedule_not_active(self):
        result = derive_status(
            {"start_date": "2026-08-01", "end_date": "2026-08-10"},
            date(2026, 8, 20), schedule_active=False,
        )
        assert result["status"] == "concluded"
        assert result["days"] == 10  # (end - start).days + 1, frozen at conclusion
        assert result["ended_days_ago"] == 10

    def test_past_end_when_schedule_still_active(self):
        result = derive_status(
            {"start_date": "2026-08-01", "end_date": "2026-08-10"},
            date(2026, 8, 20), schedule_active=True,
        )
        assert result["status"] == "past_end"
        assert result["days"] == 20  # still counting elapsed days, like "running"
        assert result["ended_days_ago"] == 10

    def test_planned_end_used_when_end_date_absent(self):
        result = derive_status(
            {"start_date": "2026-08-01", "planned_end": "2026-08-10"}, date(2026, 8, 20)
        )
        assert result["status"] == "concluded"
        assert result["end_date"] == date(2026, 8, 10)

    def test_end_date_wins_over_planned_end_when_both_present(self):
        result = derive_status(
            {"start_date": "2026-08-01", "end_date": "2026-08-15", "planned_end": "2026-08-10"},
            date(2026, 8, 20),
        )
        assert result["end_date"] == date(2026, 8, 15)

    def test_unparseable_end_falls_back_to_no_end_declared(self):
        result = derive_status(
            {"start_date": "2026-08-01", "end_date": "not-a-date"}, date(2026, 8, 20)
        )
        assert result["status"] == "running"
        assert result["no_end_declared"] is True


# ── primary_metric ───────────────────────────────────────────────────────

class TestPrimaryMetric:
    def test_no_body_returns_none(self):
        assert primary_metric(None, {}) is None

    def test_no_kpi_rows_returns_none(self):
        assert primary_metric({"kpi_rows": []}, {}) is None

    def test_defaults_to_first_row(self):
        body = {"kpi_rows": [{"metric": "A", "delta_pct": 1.0}, {"metric": "B", "delta_pct": 2.0}]}
        assert primary_metric(body, {})["metric"] == "A"

    def test_matches_primary_metric_by_key(self):
        body = {"kpi_rows": [
            {"metric": "A", "key": "rev", "delta_pct": 1.0},
            {"metric": "B", "key": "conv", "delta_pct": 2.0},
        ]}
        assert primary_metric(body, {"primary_metric": "conv"})["metric"] == "B"

    def test_matches_primary_metric_by_label_case_insensitively(self):
        body = {"kpi_rows": [
            {"metric": "Revenue", "delta_pct": 1.0},
            {"metric": "Conversion Rate", "delta_pct": 2.0},
        ]}
        result = primary_metric(body, {"primary_metric": "CONVERSION RATE"})
        assert result["metric"] == "Conversion Rate"

    def test_no_match_falls_back_to_first_row(self):
        body = {"kpi_rows": [{"metric": "A", "delta_pct": 1.0}]}
        assert primary_metric(body, {"primary_metric": "nope"})["metric"] == "A"

    def test_ci_straddling_zero_is_not_significant(self):
        body = {"kpi_rows": [{"metric": "A", "delta_pct": 1.0, "ci_pct": [-0.5, 2.0]}]}
        result = primary_metric(body, {})
        assert result["significant"] is False
        assert result["ci_pct"] == [-0.5, 2.0]

    def test_ci_excluding_zero_is_significant(self):
        body = {"kpi_rows": [{"metric": "A", "delta_pct": 4.2, "ci_pct": [1.1, 7.3]}]}
        assert primary_metric(body, {})["significant"] is True

    def test_missing_ci_is_not_significant(self):
        body = {"kpi_rows": [{"metric": "A", "delta_pct": 4.2}]}
        result = primary_metric(body, {})
        assert result["significant"] is False
        assert result["ci_pct"] is None


# ── studio_experiments ───────────────────────────────────────────────────

def _write_ab_payload(studio, slug, *, delta_pct, ci_pct, higher_is_better=True,
                      header_test_name=None):
    out = studio.output_dir / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps({
        "components": {"fw_c1": {
            "type": "ab_compare",
            "header": {"test_name": header_test_name or slug},
            "body": {
                "kpi_rows": [{
                    "metric": "rev", "key": "rev", "delta_pct": delta_pct,
                    "higher_is_better": higher_is_better, "ci_pct": ci_pct,
                    "control_val": "1", "test_val": "2",
                }],
                "volume_rows": [], "timeseries": {},
            },
            "modes": None, "default_mode": "",
        }},
        "_freshness": {"generated_at": "2026-08-01T00:00:00+00:00"},
        "_framework_version": "9.9.9",
    }), encoding="utf-8")


class TestStudioExperiments:
    def test_non_ab_reports_are_excluded(self, studio_tree, write_report):
        write_report("plain")
        sync_studio_registry(studio_tree)
        assert studio_experiments(studio_tree)["rows"] == []

    def test_win_rate_math(self, studio_tree, write_report):
        today = datetime.now(timezone.utc).date()
        write_report("concluded-win", ab_test={
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today - timedelta(days=5)).isoformat(),
        })
        write_report("concluded-loss", ab_test={
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today - timedelta(days=5)).isoformat(),
        })
        sync_studio_registry(studio_tree)
        _write_ab_payload(studio_tree, "concluded-win", delta_pct=4.2, ci_pct=[1.1, 7.3])
        _write_ab_payload(studio_tree, "concluded-loss", delta_pct=-3.1, ci_pct=[-5.2, -1.0])

        data = studio_experiments(studio_tree)
        assert data["tiles"]["concluded"] == 2
        assert data["tiles"]["wins"] == 1
        assert data["tiles"]["win_rate"] == 50

    def test_none_delta_is_excluded_from_win_rate_not_coerced_to_a_loss(
        self, studio_tree, write_report
    ):
        today = datetime.now(timezone.utc).date()
        write_report("concluded-win", ab_test={
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today - timedelta(days=5)).isoformat(),
        })
        write_report("concluded-no-delta", ab_test={
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today - timedelta(days=5)).isoformat(),
        })
        sync_studio_registry(studio_tree)
        _write_ab_payload(studio_tree, "concluded-win", delta_pct=4.2, ci_pct=[1.1, 7.3])
        _write_ab_payload(studio_tree, "concluded-no-delta", delta_pct=None, ci_pct=None)

        data = studio_experiments(studio_tree)
        assert data["tiles"]["concluded"] == 2
        # The no-delta row has a primary metric but no usable number -- it
        # must not count toward the denominator (and must not silently read
        # as a loss via a 0.0 coercion).
        assert data["tiles"]["concluded_with_metric"] == 1
        assert data["tiles"]["wins"] == 1
        assert data["tiles"]["win_rate"] == 100

    def test_declared_identity_beats_payload_header(self, studio_tree, write_report):
        # A copied/regenerated build can carry another experiment's name in its
        # payload header; the report.yaml declaration must win for identity
        # fields, with the header only filling gaps.
        today = datetime.now(timezone.utc).date()
        write_report("declared", ab_test={
            "test_name": "store_layout_v3",
            "start_date": (today - timedelta(days=10)).isoformat(),
        })
        write_report("undeclared", ab_test={
            "start_date": (today - timedelta(days=10)).isoformat(),
        })
        sync_studio_registry(studio_tree)
        _write_ab_payload(studio_tree, "declared", delta_pct=1.0, ci_pct=None,
                          header_test_name="checkout_v2")
        _write_ab_payload(studio_tree, "undeclared", delta_pct=1.0, ci_pct=None,
                          header_test_name="checkout_v2")

        rows = {r["slug"]: r for r in studio_experiments(studio_tree)["rows"]}
        assert rows["declared"]["test_name"] == "store_layout_v3"
        assert rows["undeclared"]["test_name"] == "checkout_v2"

    def test_win_rate_is_none_with_nothing_concluded(self, studio_tree, write_report):
        today = datetime.now(timezone.utc).date()
        write_report("running", ab_test={"start_date": (today - timedelta(days=5)).isoformat()})
        sync_studio_registry(studio_tree)
        assert studio_experiments(studio_tree)["tiles"]["win_rate"] is None

    def test_never_built_report_is_flagged_not_built(self, studio_tree, write_report):
        write_report("never-built", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        row = studio_experiments(studio_tree)["rows"][0]
        assert row["built"] is False
        assert row["url"] == (
            f"/s/{studio_tree.org.slug}/{studio_tree.slug}/r/never-built/"
            "?display=console"
        )

    def test_built_with_no_payload_is_flagged(self, studio_tree, write_report):
        write_report("built-empty", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        (studio_tree.output_dir / "built-empty").mkdir(parents=True, exist_ok=True)
        row = studio_experiments(studio_tree)["rows"][0]
        assert row["built"] is True
        assert row["payload_missing"] is True

    def test_storage_outage_degrades_without_raising(self, studio_tree, write_report, monkeypatch):
        write_report("a", ab_test={"start_date": "2026-01-01"})
        write_report("b", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)

        calls = []

        def _boom(studio, slug):
            calls.append(slug)
            raise RuntimeError("store unreachable")

        monkeypatch.setattr("apps.reports.overviews.experiments.storage.output_root", _boom)
        data = studio_experiments(studio_tree)
        assert data["storage_unavailable"] is True
        assert all(r["storage_unavailable"] for r in data["rows"])
        # Latched after the first failure: the second report is never probed.
        assert calls == ["a"]

    def test_cache_is_invalidated_explicitly(self, studio_tree, write_report):
        write_report("a", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        assert len(studio_experiments(studio_tree)["rows"]) == 1
        write_report("b", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        invalidate_experiments_cache(studio_tree)
        assert len(studio_experiments(studio_tree)["rows"]) == 2


# ── the bounded per-report extract cache ────────────────────────────────

def _cache_entries_for(studio):
    """``_extract_cache`` is a module-level dict shared by the whole test
    session, so counting it directly would pick up every other test's studio.
    Scope by pk, the same way ``invalidate_experiments_cache`` does."""
    return {k: v for k, v in experiments_mod._extract_cache.items() if k[0] == studio.pk}


class TestExtractCacheBound:
    """The extractor memo used to be keyed by the data.json path, so on the
    remote backend -- where every build gets a fresh cache directory -- each
    rebuild left its predecessor's (multi-MB) entry behind forever. It's now
    keyed by (studio, slug), one entry per report that self-replaces on
    rebuild."""

    def test_one_entry_per_report_that_self_replaces_on_rebuild(
        self, studio_tree, write_report
    ):
        write_report("checkout", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        _write_ab_payload(studio_tree, "checkout", delta_pct=1.0, ci_pct=[0.1, 2.0])

        data = studio_experiments(studio_tree)
        assert data["rows"][0]["primary"]["delta_pct"] == 1.0
        assert len(_cache_entries_for(studio_tree)) == 1

        # Simulate a rebuild: new content, mtime moved forward explicitly
        # (some filesystems only have 1s mtime granularity, so a bare rewrite
        # right after the first one might not visibly change mtime).
        _write_ab_payload(studio_tree, "checkout", delta_pct=9.0, ci_pct=[8.0, 10.0])
        path = studio_tree.output_dir / "checkout" / "data.json"
        newer = path.stat().st_mtime + 5
        os.utime(path, (newer, newer))

        invalidate_experiments_cache(studio_tree)  # bypass only the 5s studio-level TTL
        data = studio_experiments(studio_tree)
        assert data["rows"][0]["primary"]["delta_pct"] == 9.0
        # Still exactly one entry for this report: the rebuild replaced the
        # existing cache entry rather than adding a second one.
        assert len(_cache_entries_for(studio_tree)) == 1

    def test_invalidate_clears_the_extract_cache_too(self, studio_tree, write_report):
        write_report("checkout", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        _write_ab_payload(studio_tree, "checkout", delta_pct=1.0, ci_pct=[0.1, 2.0])
        studio_experiments(studio_tree)
        assert len(_cache_entries_for(studio_tree)) == 1

        invalidate_experiments_cache(studio_tree)
        assert len(_cache_entries_for(studio_tree)) == 0


# ── timeline ─────────────────────────────────────────────────────────────

class TestTimeline:
    def test_scheduled_experiments_get_no_bar(self, studio_tree, write_report):
        today = datetime.now(timezone.utc).date()
        write_report("future-test", ab_test={
            "start_date": (today + timedelta(days=10)).isoformat()
        })
        sync_studio_registry(studio_tree)
        timeline = studio_experiments(studio_tree)["timeline"]
        assert timeline["lanes"] == []

    def test_running_and_concluded_still_get_bars(self, studio_tree, write_report):
        today = datetime.now(timezone.utc).date()
        write_report("running-one", ab_test={
            "start_date": (today - timedelta(days=5)).isoformat()
        })
        sync_studio_registry(studio_tree)
        timeline = studio_experiments(studio_tree)["timeline"]
        assert [lane["slug"] for lane in timeline["lanes"]] == ["running-one"]
        assert timeline["lanes"][0]["width_pct"] > 0

    def test_month_widths_are_day_accurate_and_sum_to_100(self, studio_tree, write_report):
        write_report("plain")  # no ab_test block; timeline is computed regardless
        sync_studio_registry(studio_tree)
        timeline = studio_experiments(studio_tree)["timeline"]
        assert len(timeline["months"]) == 4
        assert all({"label", "width_pct"} <= set(m) for m in timeline["months"])
        assert abs(sum(m["width_pct"] for m in timeline["months"]) - 100.0) < 0.5


# ── the view ─────────────────────────────────────────────────────────────

class TestExperimentsPageView:
    def _url(self, studio):
        return f"/s/{studio.org.slug}/{studio.slug}/experiments"

    def test_returns_200_with_rows_and_tiles(
        self, studio_tree, write_report, member, grant_studio, login, settings
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        write_report("checkout", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 200
        assert "checkout" in resp.content.decode()

    def test_viewers_can_open_the_page(
        self, studio_tree, write_report, member, grant_studio, login
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        write_report("checkout", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 200
        assert "checkout" in resp.content.decode()

    def test_non_member_gets_404(self, studio_tree, org, make_user, login, settings):
        outsider = make_user("outsider@demo.example", org=org)  # org member, no studio grant
        client = login(outsider)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 404

    def test_unauthenticated_redirects_to_login(self, studio_tree, client, settings):
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 302
        assert "/login" in resp.url

    def test_never_built_row_shows_not_built_yet(
        self, studio_tree, write_report, member, grant_studio, login, settings
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        write_report("checkout", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert "Not built yet" in resp.content.decode()

    def test_null_delta_renders_a_dash_not_a_styled_percent(
        self, studio_tree, write_report, member, grant_studio, login, settings
    ):
        """A row with a primary metric but no ``delta_pct`` used to fall
        through Django's smart ``>= 0`` (None is falsy, not >= 0) into an
        empty, unstyled "%" -- rendering as a bare percent sign with no
        number. It must render an honest dash instead, and never pick up the
        red "lift-neg" styling a missing number has no business wearing."""
        grant_studio(member, studio_tree, roles.VIEWER)
        write_report("checkout", ab_test={"start_date": "2026-01-01"})
        sync_studio_registry(studio_tree)
        _write_ab_payload(studio_tree, "checkout", delta_pct=None, ci_pct=None)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        body = resp.content.decode()
        assert resp.status_code == 200
        # The page's own <style> block defines .lift-neg/.lift-pos/.lift-ns
        # (a bare substring check would always "pass" against the stylesheet
        # itself) -- check for the actual rendered span, not the class name.
        assert '<span class="lift-neg">' not in body
        assert '<span class="lift-pos">' not in body
        assert '<span class="lift-ns">' not in body

    def test_status_filter(
        self, studio_tree, write_report, member, grant_studio, login, settings
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        today = datetime.now(timezone.utc).date()
        write_report("running-one", ab_test={
            "start_date": (today - timedelta(days=5)).isoformat()
        })
        write_report("concluded-one", ab_test={
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today - timedelta(days=5)).isoformat(),
        })
        sync_studio_registry(studio_tree)

        client = login(member)
        resp = client.get(self._url(studio_tree) + "?status=running")
        body = resp.content.decode()
        assert "running-one" in body
        assert "concluded-one" not in body

        resp = client.get(self._url(studio_tree) + "?status=concluded")
        body = resp.content.decode()
        assert "concluded-one" in body
        assert "running-one" not in body

    def test_empty_state_when_no_ab_reports(
        self, studio_tree, write_report, member, grant_studio, login, settings
    ):
        grant_studio(member, studio_tree, roles.VIEWER)
        write_report("plain")  # no ab_test block
        sync_studio_registry(studio_tree)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 200
        assert "No A/B test reports in this studio" in resp.content.decode()


# ── multi-experiment reports (ab_test as a list) ─────────────────────────

def _write_multi_ab_payload(studio, slug, tests):
    """Several ab_compare components in one data.json, in the given order.

    ``tests`` is a list of ``(header_test_name, delta_pct, ci_pct)``.
    """
    out = studio.output_dir / slug
    out.mkdir(parents=True, exist_ok=True)
    components = {
        f"fw_c{i}": {
            "type": "ab_compare",
            "header": {"test_name": name},
            "body": {
                "kpi_rows": [{
                    "metric": "rev", "key": "rev", "delta_pct": delta,
                    "higher_is_better": True, "ci_pct": ci,
                    "control_val": "1", "test_val": "2",
                }],
                "volume_rows": [], "timeseries": {},
            },
            "modes": None, "default_mode": "",
        }
        for i, (name, delta, ci) in enumerate(tests, 1)
    }
    (out / "data.json").write_text(json.dumps({
        "components": components,
        "_freshness": {"generated_at": "2026-08-01T00:00:00+00:00"},
        "_framework_version": "9.9.9",
    }), encoding="utf-8")


class TestIsAbReportList:
    def test_true_for_a_list_of_dicts(self):
        assert is_ab_report({"ab_test": [{"start_date": "2026-01-01"}]}) is True

    def test_false_for_an_empty_list(self):
        assert is_ab_report({"ab_test": []}) is False

    def test_false_for_a_list_of_garbage(self):
        assert is_ab_report({"ab_test": ["yes please", 3]}) is False


class TestDeriveStatusClosed:
    def test_closed_reads_concluded_even_with_an_active_schedule(self):
        result = derive_status(
            {"start_date": "2026-06-01", "end_date": "2026-07-01",
             "closed": True},
            date(2026, 8, 20), schedule_active=True,
        )
        assert result["status"] == "concluded"

    def test_closed_changes_nothing_while_still_running(self):
        result = derive_status(
            {"start_date": "2026-08-01", "closed": True},
            date(2026, 8, 20), schedule_active=True,
        )
        assert result["status"] == "running"


class TestMultiExperimentReports:
    def test_each_entry_becomes_its_own_row_matched_by_test_name(
        self, studio_tree, write_report
    ):
        today = datetime.now(timezone.utc).date()
        write_report("programme", ab_test=[
            {"test_name": "alpha", "name": "Alpha Test",
             "start_date": (today - timedelta(days=5)).isoformat()},
            {"test_name": "beta",
             "start_date": (today - timedelta(days=40)).isoformat(),
             "end_date": (today - timedelta(days=10)).isoformat(),
             "closed": True},
        ])
        sync_studio_registry(studio_tree)
        # Build order deliberately differs from declared order: matching is
        # by test_name, not position.
        _write_multi_ab_payload(studio_tree, "programme", [
            ("beta", -2.0, [-4.0, -0.5]),
            ("alpha", 3.0, [1.0, 5.0]),
        ])
        invalidate_experiments_cache(studio_tree)

        rows = studio_experiments(studio_tree)["rows"]
        assert [r["test_name"] for r in rows] == ["alpha", "beta"]
        by_name = {r["test_name"]: r for r in rows}
        assert by_name["alpha"]["name"] == "Alpha Test"
        assert by_name["beta"]["name"] == "beta"
        assert by_name["alpha"]["status"] == "running"
        assert by_name["beta"]["status"] == "concluded"
        assert by_name["alpha"]["primary"]["delta_pct"] == 3.0
        assert by_name["beta"]["primary"]["delta_pct"] == -2.0
        # The concluded loss counts into the tiles like any standalone report.
        tiles = studio_experiments(studio_tree)["tiles"]
        assert tiles["running"] == 1
        assert tiles["concluded"] == 1

    def test_entry_statuses_diverge_on_one_scheduled_report(
        self, studio_tree, write_report
    ):
        from apps.reports.models import Report

        today = datetime.now(timezone.utc).date()
        write_report("programme", ab_test=[
            {"test_name": "running-t",
             "start_date": (today - timedelta(days=5)).isoformat()},
            {"test_name": "past-end-t",
             "start_date": (today - timedelta(days=40)).isoformat(),
             "end_date": (today - timedelta(days=10)).isoformat()},
            {"test_name": "concluded-t",
             "start_date": (today - timedelta(days=60)).isoformat(),
             "end_date": (today - timedelta(days=30)).isoformat(),
             "closed": True},
        ])
        sync_studio_registry(studio_tree)
        Report.objects.filter(studio=studio_tree, slug="programme").update(
            schedule_cron="0 9 * * *")
        invalidate_experiments_cache(studio_tree)

        rows = studio_experiments(studio_tree)["rows"]
        assert [r["status"] for r in rows] == ["running", "past_end",
                                               "concluded"]

    def test_entries_without_names_fall_back_to_build_order(
        self, studio_tree, write_report
    ):
        today = datetime.now(timezone.utc).date()
        write_report("programme", ab_test=[
            {"start_date": (today - timedelta(days=5)).isoformat()},
            {"start_date": (today - timedelta(days=6)).isoformat()},
        ])
        sync_studio_registry(studio_tree)
        _write_multi_ab_payload(studio_tree, "programme", [
            ("first_build", 1.0, None),
            ("second_build", 2.0, None),
        ])
        invalidate_experiments_cache(studio_tree)

        rows = studio_experiments(studio_tree)["rows"]
        assert rows[0]["primary"]["delta_pct"] == 1.0
        assert rows[1]["primary"]["delta_pct"] == 2.0
        # With nothing declared, the payload header fills the identity gap.
        assert rows[0]["test_name"] == "first_build"
        assert rows[1]["test_name"] == "second_build"

    def test_declared_name_missing_from_payload_reads_payload_missing(
        self, studio_tree, write_report
    ):
        today = datetime.now(timezone.utc).date()
        write_report("programme", ab_test=[
            {"test_name": "present",
             "start_date": (today - timedelta(days=5)).isoformat()},
            {"test_name": "absent",
             "start_date": (today - timedelta(days=5)).isoformat()},
        ])
        sync_studio_registry(studio_tree)
        _write_multi_ab_payload(studio_tree, "programme", [
            ("present", 1.0, None),
        ])
        invalidate_experiments_cache(studio_tree)

        rows = {r["test_name"]: r for r in studio_experiments(studio_tree)["rows"]}
        assert rows["present"]["payload_missing"] is False
        assert rows["present"]["primary"]["delta_pct"] == 1.0
        # Better an honest "no payload" row than another experiment's numbers.
        assert rows["absent"]["payload_missing"] is True
        assert rows["absent"]["primary"] is None

"""The annotations calendar: events.yaml parsing, merging with per-report
annotations.events, and the gated studio page.

No 90-day cutoff, no scope-based exclusion -- both deliberate departures from
the framework's chart-annotation loading (trellum/runner.py:_load_events),
documented on apps.reports.overviews.annotations.
"""
import re
import textwrap
from pathlib import Path

import pytest
from django.conf import settings

from apps.core import roles
from apps.reports.overviews import annotations
from apps.reports.overviews.annotations import calendar_payload, load_studio_events, report_config_events

pytestmark = pytest.mark.django_db


def _write_events(studio_tree, text):
    path = studio_tree.project_root / "events.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


WELL_FORMED = """\
    type_defaults:
      campaign: false
    events:
      - date: 2026-08-04
        label: Payments outage
        type: incident
      - date: 2026-08-06
        end_date: 2026-08-10
        label: Summer sale — wave 1
        type: campaign
      - date: 2026-08-11
        label: v2.15 rollout
        type: release
        studio: nova-play
      - date: 2026-08-25
        label: Back-to-school
        type: campaign
        default_visible: true
"""


class TestLoadStudioEvents:
    def test_well_formed_file_parses_ranges_and_defaults(self, studio_tree):
        _write_events(studio_tree, WELL_FORMED)
        events, type_defaults, parse_error = load_studio_events(studio_tree)

        assert parse_error == ""
        assert type_defaults == {"campaign": False}
        assert len(events) == 4

        ranged = next(e for e in events if e["label"] == "Summer sale — wave 1")
        assert ranged["date"] == "2026-08-06"
        assert ranged["end_date"] == "2026-08-10"
        assert ranged["type"] == "campaign"
        assert ranged["scope"] == "shared"
        assert ranged["source"] == "events.yaml"
        # type_defaults says campaign is off by default...
        assert ranged["visible_default"] is False

        # ...but a per-event default_visible overrides type_defaults.
        overridden = next(e for e in events if e["label"] == "Back-to-school")
        assert overridden["visible_default"] is True

        scoped = next(e for e in events if e["label"] == "v2.15 rollout")
        assert scoped["scope"] == "nova-play"

    def test_missing_file_is_not_an_error(self, studio_tree):
        events, type_defaults, parse_error = load_studio_events(studio_tree)
        assert events == []
        assert type_defaults == {}
        assert parse_error == ""

    def test_malformed_yaml_surfaces_the_error_but_does_not_raise(self, studio_tree):
        _write_events(studio_tree, "events:\n  - date: 2026-08-04\n  bad indent: [\n")
        events, type_defaults, parse_error = load_studio_events(studio_tree)
        assert events == []
        assert parse_error != ""

    def test_entries_without_a_parseable_date_are_dropped(self, studio_tree):
        _write_events(
            studio_tree,
            """\
            events:
              - label: no date at all
                type: release
              - date: "not-a-date"
                label: garbage date
              - date: 2026-01-05
                label: keeper
            """,
        )
        events, _, parse_error = load_studio_events(studio_tree)
        assert parse_error == ""
        assert [e["label"] for e in events] == ["keeper"]

    def test_a_three_year_old_event_is_present_no_cutoff(self, studio_tree):
        _write_events(
            studio_tree,
            """\
            events:
              - date: 2023-01-15
                label: GDC talk aired
                type: event
            """,
        )
        events, _, _ = load_studio_events(studio_tree)
        assert [e["label"] for e in events] == ["GDC talk aired"]

    def test_scope_is_never_excluded_only_tagged(self, studio_tree):
        # Deliberate difference from the framework's chart loader: every
        # event is returned regardless of its `studio:` (scope) field.
        _write_events(
            studio_tree,
            """\
            events:
              - date: 2026-01-01
                label: other studio event
                studio: some-other-studio
            """,
        )
        events, _, _ = load_studio_events(studio_tree)
        assert len(events) == 1
        assert events[0]["scope"] == "some-other-studio"


class TestReportConfigEvents:
    def test_merges_per_report_annotation_events(self, studio_tree, write_report):
        from apps.reports.models import Report
        from apps.reports.scan import sync_studio_registry

        write_report(
            "checkout-experiment",
            annotations={
                "events": [
                    {"date": "2026-08-13", "end_date": "2026-09-10", "label": "checkout_v2 A/B", "type": "ab_test"}
                ]
            },
            studio="nova-play",
        )
        sync_studio_registry(studio_tree)
        report = Report.objects.get(studio=studio_tree, slug="checkout-experiment")
        assert isinstance(report.config.get("annotations"), dict)

        events = report_config_events(studio_tree)
        assert len(events) == 1
        ev = events[0]
        assert ev["label"] == "checkout_v2 A/B"
        assert ev["scope"] == "nova-play"
        assert ev["source"] == "report:checkout-experiment"
        assert ev["report_url"] == (
            f"/s/{studio_tree.org.slug}/{studio_tree.slug}/r/checkout-experiment/"
            "?display=console"
        )

    def test_reports_without_annotations_contribute_nothing(self, studio_tree, write_report):
        from apps.reports.scan import sync_studio_registry

        write_report("plain")
        sync_studio_registry(studio_tree)
        assert report_config_events(studio_tree) == []

    def test_deleted_reports_are_not_scanned(self, studio_tree, write_report):
        from apps.reports.models import Report
        from apps.reports.scan import sync_studio_registry

        write_report(
            "gone", annotations={"events": [{"date": "2026-01-01", "label": "x"}]}
        )
        sync_studio_registry(studio_tree)
        Report.objects.filter(studio=studio_tree, slug="gone").update(present_in_scan=False)
        assert report_config_events(studio_tree) == []


class TestCalendarPayload:
    def test_merges_both_sources_sorted_by_date(self, studio_tree, write_report):
        from apps.reports.scan import sync_studio_registry

        _write_events(studio_tree, WELL_FORMED)
        write_report(
            "checkout-experiment",
            annotations={"events": [{"date": "2026-08-05", "label": "mid-range report event"}]},
        )
        sync_studio_registry(studio_tree)

        payload = calendar_payload(studio_tree)
        dates = [e["date"] for e in payload["events"]]
        assert dates == sorted(dates)
        assert payload["total_count"] == 5  # 4 from events.yaml + 1 from the report
        assert payload["parse_error"] == ""
        assert payload["too_many"] is False

    def test_type_counts_and_scope_counts(self, studio_tree):
        _write_events(studio_tree, WELL_FORMED)
        payload = calendar_payload(studio_tree)

        type_by_value = {row["value"]: row["count"] for row in payload["type_counts"]}
        assert type_by_value == {"incident": 1, "campaign": 2, "release": 1}

        scope_by_value = {row["value"]: row["count"] for row in payload["scope_counts"]}
        assert scope_by_value == {"shared": 3, "nova-play": 1}
        # "shared" sorts first when present, per the pill order in the UI.
        assert payload["scope_counts"][0]["value"] == "shared"

    def test_too_many_flag_never_truncates(self, studio_tree):
        lines = ["events:"]
        for i in range(5001):
            lines.append(f"  - date: 2020-01-01\n    label: e{i}")
        _write_events(studio_tree, "\n".join(lines))
        payload = calendar_payload(studio_tree)
        assert payload["total_count"] == 5001
        assert payload["too_many"] is True
        assert len(payload["events"]) == 5001  # never truncated

    def test_list_groups_are_newest_month_first(self, studio_tree):
        _write_events(
            studio_tree,
            """\
            events:
              - date: 2024-03-03
                label: old one
              - date: 2026-08-11
                label: new one
              - date: 2026-08-25
                label: newer same month
            """,
        )
        payload = calendar_payload(studio_tree)
        month_labels = [g["month_label"] for g in payload["list_groups"]]
        assert month_labels == ["August 2026", "March 2024"]
        # Newest event first within a group.
        assert [e["label"] for e in payload["list_groups"][0]["events"]] == [
            "newer same month", "new one",
        ]

    def test_unknown_type_falls_back_to_release_css_but_keeps_its_own_label(self, studio_tree):
        _write_events(
            studio_tree,
            """\
            events:
              - date: 2026-01-01
                label: mystery
                type: totally_custom
            """,
        )
        payload = calendar_payload(studio_tree)
        ev = payload["events"][0]
        assert ev["type_css"] == "release"
        assert "custom" in ev["type_label"].lower()


@pytest.fixture
def url(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}/annotations"


class TestCalendarPage:
    def test_studio_member_gets_200_with_labels_and_payload(
        self, login, make_user, org, studio_tree, grant_studio, url
    ):
        _write_events(studio_tree, WELL_FORMED)
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        resp = login(user).get(url)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert f"Annotations — {studio_tree.name}" in html
        assert "Payments outage" in html
        assert "Releases" in html  # a type-filter pill, from the "release"-type event
        assert 'id="calendar-data"' in html
        assert "application/json" in html
        # A multi-line "{# ... #}" once leaked as literal page text: Django's
        # {# #} comment is single-line only, so an unclosed one renders itself.
        assert "{#" not in html

    def test_viewer_gets_the_calendar_page(
        self, login, make_user, org, studio_tree, grant_studio, url
    ):
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        resp = login(user).get(url)
        assert resp.status_code == 200

    def test_non_member_gets_404_not_a_leak(
        self, login, make_user, org, studio_tree, url
    ):
        outsider = make_user("outsider@elsewhere.com")
        resp = login(outsider).get(url)
        assert resp.status_code == 404

    def test_viewer_role_is_enough(
        self, login, make_user, org, studio_tree, grant_studio, url
    ):
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        assert login(user).get(url).status_code == 200

    def test_empty_studio_shows_the_empty_state(
        self, login, make_user, org, studio_tree, grant_studio, url
    ):
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        html = login(user).get(url).content.decode()
        assert "ui-empty" in html
        assert "Nothing has happened yet" in html

    def test_parse_error_banner_and_still_200(
        self, login, make_user, org, studio_tree, grant_studio, url
    ):
        _write_events(studio_tree, "events:\n  - date: 2026-08-04\n  bad: [\n")
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        resp = login(user).get(url)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "events.yaml has a problem" in html


class TestListViewServerRendered:
    def test_rows_render_with_dates_type_scope_and_source(
        self, login, make_user, org, studio_tree, grant_studio, url
    ):
        _write_events(studio_tree, WELL_FORMED)
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        html = login(user).get(url).content.decode()

        assert 'class="evrow"' in html
        assert 'data-type="incident"' in html
        assert 'data-scope="nova-play"' in html
        assert "events.yaml" in html
        assert "August 2026" in html  # month-h grouping heading

    def test_report_sourced_row_links_to_the_report(
        self, login, make_user, org, studio_tree, grant_studio, url, write_report
    ):
        from apps.reports.scan import sync_studio_registry

        write_report(
            "checkout-experiment",
            annotations={"events": [{"date": "2026-08-13", "label": "checkout_v2 A/B"}]},
        )
        sync_studio_registry(studio_tree)
        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        html = login(user).get(url).content.decode()
        assert f"/s/{org.slug}/{studio_tree.slug}/r/checkout-experiment/" in html
        assert "report: checkout-experiment" in html


def _framework_anno_styles() -> dict:
    """Parse ``_ANNO_STYLES`` straight out of the framework's runtime JS (the
    source of truth) rather than hardcoding an expected copy of it here -- that
    way a framework colour or label bump is what the test compares against, not
    a snapshot that ages alongside the bug it's meant to catch.

    It used to live inside a Python string template, with its braces doubled;
    v0.1.0 moved the client JS into real files, so the braces are ordinary now.
    If this assertion fires again, the block has moved rather than vanished --
    find it and repoint the path, because the sync it guards is the reason the
    portal's chips match the bands in the chart.
    """
    source = (
        Path(settings.BASE_DIR) / "trellum" / "static" / "js" / "runtime" / "annotations.js"
    ).read_text(encoding="utf-8")
    block_match = re.search(r"_ANNO_STYLES\s*=\s*\{(.*?)\n\s*\};", source, re.DOTALL)
    assert block_match, "could not locate _ANNO_STYLES in the framework's runtime JS"

    styles: dict[str, dict[str, str | None]] = {}
    for name, body in re.findall(r"(\w+):\s*\{(.*?)\}", block_match.group(1), re.DOTALL):
        color = re.search(r"color:\s*'([^']+)'", body)
        border = re.search(r"border:\s*'([^']+)'", body)
        label = re.search(r"label:\s*'([^']+)'", body)
        styles[name] = {
            # "Solid colour for line/border -- fall back to st.color when no
            # border" -- the framework's own comment, mirrored here: the
            # portal's chip/dot/pill colours are always this solid colour,
            # never the (sometimes translucent) `color` field directly.
            "solid": (border.group(1) if border else color.group(1)) if color else None,
            # The raw `color` field, kept separately: for ab_test and event it
            # IS a literal translucent rgba() fill (not runtime-computed like
            # the other three types', which get their box-fill wash from
            # _annoFill() at a default alpha with no literal to compare
            # against), and the portal copied those two literals verbatim
            # into --anno-ab-soft / --anno-event-soft.
            "raw_color": color.group(1) if color else None,
            "label": label.group(1) if label else None,
        }
    return styles


class TestAnnotationColorsStayInSync:
    """The framework's chart-annotation palette is hand-copied twice on the
    portal side -- apps.reports.overviews.annotations._TYPE_META (label + CSS class per
    type) and the ``--anno-*`` custom properties in
    templates/reports/annotations.html (the actual colour values) -- because
    the control plane must not import framework rendering internals, which are
    outside the contract in trellum/docs/COMPATIBILITY.md. (Before the
    monorepo the reason was simply that the framework was a pinned submodule;
    now that it lives here, a real generator is genuinely possible -- see
    below.) Nothing enforces that either copy stays correct except this test:
    a framework colour or
    label bump should fail here, not surface as the calendar quietly drawing
    the wrong colour or word. (Mirrors the intent of apps.core's
    test_theme_sync for the theme token table; a real generator for this
    smaller palette is a separate follow-up, not required here.)
    """

    #: The five types the calendar actually renders. `power_bet` and
    #: `weekday` exist in the framework's palette for chart-only concerns
    #: (recurring weekday markers, a niche experiment kind) that have no
    #: calendar equivalent, so they are deliberately excluded here.
    CALENDAR_TYPES = ("campaign", "ab_test", "release", "incident", "event")

    def test_type_meta_labels_match_the_framework(self):
        fw = _framework_anno_styles()
        for t in self.CALENDAR_TYPES:
            assert annotations._TYPE_META[t]["label"] == fw[t]["label"], t

    def test_template_colours_match_the_frameworks_solid_colour(self):
        fw = _framework_anno_styles()
        template_source = (
            Path(settings.BASE_DIR) / "templates" / "reports" / "annotations.html"
        ).read_text(encoding="utf-8")
        for t in self.CALENDAR_TYPES:
            css = annotations._TYPE_META[t]["css"]
            declared = re.search(
                r"--anno-" + re.escape(css) + r":\s*(#[0-9A-Fa-f]{3,6})", template_source
            )
            assert declared, f"--anno-{css} not declared in templates/reports/annotations.html"
            assert declared.group(1).lower() == fw[t]["solid"].lower(), t

    def test_template_soft_fills_match_the_frameworks_literal_rgba(self):
        """ab_test and event carry an explicit rgba() fill in the framework
        (not a runtime-computed one, unlike the other three types) -- the
        portal copied it verbatim into --anno-ab-soft / --anno-event-soft,
        so those two can be checked exactly."""
        def normalize(s: str) -> str:
            # Whitespace and case are cosmetic; so is a leading "0" before a
            # decimal point (framework: "0.18", template: ".18" -- same
            # number, and CSS accepts both).
            s = re.sub(r"\s+", "", s).lower()
            return re.sub(r"(?<!\d)0(?=\.\d)", "", s)

        fw = _framework_anno_styles()
        template_source = (
            Path(settings.BASE_DIR) / "templates" / "reports" / "annotations.html"
        ).read_text(encoding="utf-8")
        for t in self.CALENDAR_TYPES:
            raw_color = fw[t]["raw_color"] or ""
            if not raw_color.startswith("rgba"):
                continue  # the other three: no literal fill to compare against
            css = annotations._TYPE_META[t]["css"]
            declared = re.search(
                r"--anno-" + re.escape(css) + r"-soft:\s*(rgba\([^)]*\))", template_source
            )
            assert declared, f"--anno-{css}-soft not declared in templates/reports/annotations.html"
            assert normalize(declared.group(1)) == normalize(raw_color), t


def test_the_calendar_is_a_product_feature():
    assert annotations.calendar_page.__module__ == "apps.reports.overviews.annotations"

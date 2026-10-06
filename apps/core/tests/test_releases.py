"""The operator's Releases section: what the coordinator records and how the
page reads it (internal planning ticket #066). The feed is never reached from here."""
from __future__ import annotations

import io
import json

import pytest

import trellum_portal
from apps.core import releases
from apps.core.models import OpsState
from trellum.update_check import FEED_URL

pytestmark = pytest.mark.django_db

MINE = trellum_portal.__version__


def _release(tag: str, **extra) -> dict:
    return {
        "tag_name": tag, "name": tag, "body": "notes",
        "html_url": f"https://github.com/trellumhq/trellum/releases/tag/{tag}",
        "published_at": "2026-09-05T04:17:00Z",
        "prerelease": False, "draft": False, **extra,
    }


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def serve(monkeypatch, payload) -> list:
    """Replace urlopen with one that answers ``payload`` (or raises it)."""
    calls: list = []

    def _urlopen(req, timeout=None):
        calls.append(req)
        if isinstance(payload, Exception):
            raise payload
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return _Response(raw)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    return calls


class TestRefresh:
    def test_records_the_newest_stable_releases(self, monkeypatch):
        calls = serve(monkeypatch, [
            _release("v9.8.0"),
            _release("v9.9.1", prerelease=True),
            _release("v9.9.0", body="## Highlights\n\n- **Exports** to object storage.\n- Studio themes."),
            _release("v9.9.2", draft=True),
            _release("v9.7.0"),
            _release("v9.6.0"),
        ])
        releases.refresh()
        row = OpsState.objects.get(key="update_check")
        assert row.ok and row.payload["latest"] == "9.9.0"
        assert [r["tag"] for r in row.payload["releases"]] == ["v9.9.0", "v9.8.0", "v9.7.0"]
        assert row.payload["releases"][0] == {
            "tag": "v9.9.0", "name": "v9.9.0", "date": "2026-09-05",
            "excerpt": "Exports to object storage. Studio themes.",
            "url": "https://github.com/trellumhq/trellum/releases/tag/v9.9.0",
        }
        (req,) = calls
        assert req.full_url == FEED_URL, "the portal asks the framework's one feed"

    @pytest.mark.parametrize("payload", [
        OSError("offline"), b"not json", [], [_release("v1.0.0", prerelease=True)],
    ], ids=["raises", "bad json", "empty", "prereleases only"])
    def test_a_failed_fetch_leaves_the_last_answer_alone(self, monkeypatch, payload):
        OpsState.record("update_check", latest="9.9.0", releases=[])
        serve(monkeypatch, payload)
        releases.refresh()
        assert OpsState.objects.get(key="update_check").payload["latest"] == "9.9.0"

    def test_nothing_is_recorded_without_an_answer(self, monkeypatch):
        serve(monkeypatch, OSError("offline"))
        releases.refresh()
        assert not OpsState.objects.filter(key="update_check").exists()
        assert releases.current() is None


class TestExcerpt:
    @pytest.mark.parametrize("notes, expected", [
        ("## Highlights\n\n- **Bold** and _soft_ `TRELLUM_UPDATE_CHECK`\n- [Docs](https://x)",
         "Bold and soft TRELLUM_UPDATE_CHECK Docs"),
        ("### Added\n\n1. First\n2. Second\n\nA second paragraph.", "First Second"),
        ("", ""),
        ("# only headings\n\n## more", ""),
    ])
    def test_first_paragraph_as_plain_text(self, notes, expected):
        assert releases.excerpt(notes) == expected

    def test_long_paragraphs_are_cut_at_a_word(self):
        text = releases.excerpt("word " * 60)
        assert text.endswith("word…") and len(text) <= 161


class TestCurrent:
    def test_none_without_a_row(self):
        assert releases.current() is None

    def test_behind_marks_this_instance_and_links_the_public_page(self):
        OpsState.record("update_check", latest="99.0.0", releases=[
            {"tag": "v99.0.0", "name": "v99.0.0", "date": "2026-09-12", "excerpt": "x", "url": "u"},
            {"tag": f"v{MINE}", "name": f"v{MINE}", "date": "2026-08-26", "excerpt": "y", "url": "u"},
        ])
        cur = releases.current()
        assert cur["state"] == "behind" and cur["latest"] == "99.0.0" and cur["current"] == MINE
        assert [r["this"] for r in cur["releases"]] == [False, True]
        assert cur["feed_host"] == "api.github.com"
        assert cur["all_url"] == "https://github.com/trellumhq/trellum/releases"
        assert cur["checked_at"] is not None

    def test_up_to_date_still_names_the_feed_and_the_public_page(self):
        OpsState.record("update_check", latest=MINE, releases=[])
        cur = releases.current()
        assert cur["state"] == "latest"
        assert cur["feed_host"] == "api.github.com"
        assert cur["all_url"] == "https://github.com/trellumhq/trellum/releases"

    @pytest.mark.parametrize("latest", ["0.0.1", "nightly"], ids=["instance ahead", "unparsable"])
    def test_ahead_or_unparsable_is_unknown_rather_than_up_to_date(self, latest):
        OpsState.record("update_check", latest=latest, releases=[])
        assert releases.current()["state"] == "unknown"

"""The once-a-day release check: one hint when there is something to say, and
byte-for-byte today's output in every other case (internal planning ticket #066).

Every test points the home directory at tmp_path (the stamp lives there) and
replaces urllib's opener, so nothing here touches the network.
"""

from __future__ import annotations

import io
import json
import socket
import threading
from pathlib import Path

import pytest

from trellum import __version__, update_check

NEWER = "9.9.9"
TODAY = "2026-09-05"
HINT = (
    f"\nHint: trellum {NEWER} is available (this is {__version__}).\n"
    f"      Upgrade: pip install -U trellum    "
    f"Notes: https://github.com/trellumhq/trellum/releases/tag/v{NEWER}\n"
)


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
    """Replace urlopen with one that answers ``payload`` (or raises it).
    Returns the list of requests it saw."""
    calls: list = []

    def _urlopen(req, timeout=None):
        calls.append(req)
        if isinstance(payload, Exception):
            raise payload
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return _Response(raw)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    return calls


def build(capsys) -> str:
    """What a build's end prints, with the probe given time to finish. The
    product never joins the thread; the test does, for determinism."""
    handle = update_check.start()
    if handle is not None:
        handle[0].join(5)
    update_check.finish(handle)
    return capsys.readouterr().out


def stamp(home) -> dict | None:
    path = home / ".trellum" / "update-check.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def join_probe() -> None:
    """Let a probe the product left running finish while home is still
    tmp_path, so its stamp never lands in the real home directory."""
    for t in threading.enumerate():
        if t.name == "trellum-update-check":
            t.join(5)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh machine: its own home directory, no opt-out, no config.yaml,
    and a clock that does not cross midnight mid-test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for var in ("FW_UPDATE_CHECK", "CI"):
        monkeypatch.delenv(var, raising=False)
    # config.yaml is read from the project root; an earlier test may have
    # pinned that with set_project_root(), which outranks the variable.
    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(update_check, "_today", lambda: TODAY)
    assert update_check._stamp_path().parent.parent == tmp_path, \
        "Path.home() did not follow HOME/USERPROFILE on this machine"
    return tmp_path


# ── the build end ─────────────────────────────────────────────────────────

def test_newer_release_prints_the_hint_once_a_day(home, monkeypatch, capsys):
    calls = serve(monkeypatch, [_release(f"v{NEWER}")])
    assert build(capsys) == HINT
    assert stamp(home) == {
        "checked": TODAY, "latest": NEWER,
        "url": f"https://github.com/trellumhq/trellum/releases/tag/v{NEWER}",
    }
    # Already told today: no request, no output.
    assert build(capsys) == ""
    assert len(calls) == 1


@pytest.mark.parametrize("tag", [f"v{__version__}", "v0.0.1"])
def test_equal_or_older_release_is_silent(home, monkeypatch, capsys, tag):
    serve(monkeypatch, [_release(tag)])
    assert build(capsys) == ""
    assert stamp(home)["checked"] == TODAY


@pytest.mark.parametrize("payload", [
    OSError("no route to host"),
    TimeoutError("timed out"),
    socket.timeout("timed out"),
    b"<html>rate limited</html>",
    b"",
    [],
    {"message": "Not Found"},
    [{"name": "no tag_name"}],
], ids=lambda p: type(p).__name__ if isinstance(p, Exception) else repr(p)[:24])
def test_every_failure_is_silent_and_still_counts_as_todays_check(home, monkeypatch, capsys, payload):
    calls = serve(monkeypatch, payload)
    assert build(capsys) == ""
    assert stamp(home) == {"checked": TODAY, "latest": None, "url": ""}, \
        "an offline machine asks once a day, not once a build"
    assert build(capsys) == "" and update_check.doctor_detail() == ""
    assert len(calls) == 1


def test_finish_never_waits_for_a_slow_probe(home, monkeypatch, capsys):
    gate = threading.Event()

    def _urlopen(req, timeout=None):
        gate.wait(5)
        return _Response(json.dumps([_release(f"v{NEWER}")]).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    handle = update_check.start()
    update_check.finish(handle)          # probe still blocked: nothing to say
    assert capsys.readouterr().out == ""
    gate.set()
    handle[0].join(5)


def test_no_home_directory_is_silent(home, monkeypatch, capsys):
    """Path.home() raises for a uid with no passwd entry and no HOME; the
    stamp is then unreadable and unwritable, and nothing else changes."""
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", no_home)
    serve(monkeypatch, OSError("offline"))
    assert build(capsys) == ""
    assert update_check.doctor_detail() == ""


@pytest.mark.parametrize("junk", ["[]", "0", "null", '"2026-09-05"'])
def test_a_stamp_that_is_not_an_object_is_ignored(home, monkeypatch, capsys, junk):
    path = home / ".trellum" / "update-check.json"
    path.parent.mkdir()
    path.write_text(junk, encoding="utf-8")
    serve(monkeypatch, [_release(f"v{NEWER}")])
    assert build(capsys) == HINT
    assert stamp(home)["latest"] == NEWER


@pytest.mark.parametrize("opt_out", [
    ("CI", "1"), ("CI", "true"),
    ("FW_UPDATE_CHECK", "0"), ("FW_UPDATE_CHECK", "false"), ("FW_UPDATE_CHECK", "off"),
    ("config", "update_check: false"),
])
def test_opting_out_makes_no_request_at_all(home, monkeypatch, capsys, opt_out):
    key, value = opt_out
    if key == "config":
        (home / "config.yaml").write_text(value, encoding="utf-8")
    else:
        monkeypatch.setenv(key, value)
    calls = serve(monkeypatch, [_release(f"v{NEWER}")])
    assert update_check.start() is None
    assert build(capsys) == ""
    assert update_check.doctor_detail() == ""
    assert calls == [] and stamp(home) is None


def test_a_malformed_config_adds_no_output(home, monkeypatch, capsys):
    """project.load_project_config() prints a [warn] for a config.yaml it
    cannot parse; this check reads the file itself and says nothing."""
    from trellum.cli import main

    serve(monkeypatch, [_release(f"v{NEWER}")])
    main(["doctor", "--dest", str(home)])
    clean = capsys.readouterr().out
    (home / "config.yaml").write_text("update_check: [unclosed", encoding="utf-8")
    main(["doctor", "--dest", str(home)])
    broken = capsys.readouterr().out
    assert broken == clean and "[warn]" not in broken


def test_a_config_whose_top_level_is_a_list_does_not_raise(home):
    (home / "config.yaml").write_text("- update_check\n- false\n", encoding="utf-8")
    assert update_check.enabled()


def test_the_request_names_the_constant_feed_and_sends_only_the_version(home, monkeypatch, capsys):
    """There is one feed and no way to point it elsewhere; the only thing the
    request says about this machine is which trellum it runs."""
    calls = serve(monkeypatch, [_release(f"v{NEWER}")])
    assert build(capsys) == HINT
    (req,) = calls
    assert req.full_url == update_check.FEED_URL == \
        "https://api.github.com/repos/trellumhq/trellum/releases?per_page=10"
    assert req.get_header("User-agent") == f"trellum/{__version__}"


def test_prerelease_and_draft_entries_never_win(home, monkeypatch, capsys):
    serve(monkeypatch, [
        _release("v99.0.0", prerelease=True),
        _release("v98.0.0", draft=True),
        _release(f"v{NEWER}"),
        _release("v1.0.0"),
    ])
    assert build(capsys) == HINT


# ── the feed ──────────────────────────────────────────────────────────────

def test_the_feed_is_normalised_filtered_and_sorted(monkeypatch):
    one = _release("v1.2.3", name="Spring", body="## Notes\n\n- fix")
    serve(monkeypatch, [one])
    assert update_check.fetch_releases(update_check.FEED_URL) == [{
        "tag": "v1.2.3", "name": "Spring", "notes": "## Notes\n\n- fix",
        "url": "https://github.com/trellumhq/trellum/releases/tag/v1.2.3",
        "published_at": "2026-09-05T04:17:00Z",
    }]
    serve(monkeypatch, [one, _release("v1.2.2", draft=True), _release("v1.2.4", prerelease=True),
                        _release("v1.10.0"), _release("nightly")])
    tags = [r["tag"] for r in update_check.fetch_releases(update_check.FEED_URL)]
    assert tags == ["v1.10.0", "v1.2.3"], "drafts, prereleases and unversioned tags dropped, newest first"


@pytest.mark.parametrize("tag, parsed", [
    ("v0.3.0", (0, 3, 0)), ("0.3.0", (0, 3, 0)), ("v1.10", (1, 10)),
    ("v1.2.3rc1", None), ("", None), ("v", None), ("latest", None),
])
def test_parse_version(tag, parsed):
    assert update_check.parse_version(tag) == parsed


def test_is_newer_needs_both_sides_to_parse():
    assert update_check.is_newer("v0.3.0", "0.2.0")
    assert not update_check.is_newer("v0.2.0", "0.2.0")
    assert not update_check.is_newer("v0.1.9", "0.2.0")
    assert not update_check.is_newer("nightly", "0.2.0")


# ── doctor ────────────────────────────────────────────────────────────────

def _version_row(out: str) -> str:
    (row,) = [line for line in out.splitlines() if line.startswith("[  ok  ] version")]
    return row


def test_doctor_row_names_the_newer_release_and_reuses_todays_stamp(home, monkeypatch, capsys):
    from trellum.cli import main

    calls = serve(monkeypatch, [_release(f"v{NEWER}")])
    main(["doctor", "--dest", str(home)])
    row = _version_row(capsys.readouterr().out)
    assert row.endswith(f"{__version__} -- {NEWER} is available (pip install -U trellum)")
    assert stamp(home)["latest"] == NEWER

    main(["doctor", "--dest", str(home)])
    assert _version_row(capsys.readouterr().out) == row
    assert len(calls) == 1, "the second run answers from today's stamp"


def test_doctor_row_says_latest_when_up_to_date(home, monkeypatch, capsys):
    from trellum.cli import main

    serve(monkeypatch, [_release(f"v{__version__}")])
    main(["doctor", "--dest", str(home)])
    assert _version_row(capsys.readouterr().out).endswith(f"{__version__} (latest)")


@pytest.mark.parametrize("payload", [OSError("offline"), [], [_release("v0.0.1")]],
                         ids=["offline", "empty feed", "dev build ahead"])
def test_doctor_row_is_unchanged_when_there_is_nothing_to_say(home, monkeypatch, capsys, payload):
    from trellum.cli import main

    serve(monkeypatch, payload)
    main(["doctor", "--dest", str(home)])
    assert _version_row(capsys.readouterr().out).rstrip().endswith(f" {__version__}")


def test_doctor_waits_no_longer_than_the_timeout(home, monkeypatch):
    """A name lookup is not bounded by the socket timeout; the join is."""
    import time

    gate = threading.Event()

    def _urlopen(req, timeout=None):
        gate.wait(10)
        return _Response(json.dumps([_release(f"v{NEWER}")]).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr(update_check, "TIMEOUT", 0.2)
    started = time.monotonic()
    assert update_check.doctor_detail() == ""
    assert time.monotonic() - started < 2
    gate.set()
    join_probe()

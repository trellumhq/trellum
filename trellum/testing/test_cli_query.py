"""`trellum query` -- the ad-hoc SQL front door.

The command exists so nobody (human or agent) hunts for a database file
path again: a measured session spent three tool calls on `find . -iname
"*.sqlite"` because raw sqlite3 was easier to reach than the configured
connection. These tests pin the behaviours that make the correct route the
easy one, and the drift guards that keep it advertised.
"""

from __future__ import annotations

import sqlite3

import pytest

from trellum import cli


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A minimal project: config.yaml + a two-row sqlite warehouse."""
    ds = tmp_path / "data-sources"
    ds.mkdir()
    db = ds / "tiny.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE fact_daily (event_date TEXT, sessions INT)")
        conn.executemany("INSERT INTO fact_daily VALUES (?, ?)",
                         [("2026-06-14", 100), ("2026-06-15", 250)])
    (ds / "config.yaml").write_text(
        "sources:\n"
        "  tiny_db:\n"
        "    type: sqlite\n"
        "    path: data-sources/tiny.sqlite\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FW_PROJECT_ROOT", raising=False)

    # The config loader caches per process; a test that inherits another
    # test's project would be testing the wrong warehouse.
    import trellum.data.datasource_config as dsc
    monkeypatch.setattr(dsc, "_cache", None)
    return tmp_path


def _run(argv):
    return cli.main(argv)


def test_query_returns_rows(project, capsys):
    rc = _run(["query", "SELECT event_date, sessions FROM fact_daily "
                        "ORDER BY event_date"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2026-06-15" in out and "250" in out
    assert "2 row(s)" in out


def test_single_source_needs_no_flag(project, capsys):
    """The common case: one configured source, zero ceremony."""
    rc = _run(["query", "SELECT COUNT(*) AS n FROM fact_daily"])
    assert rc == 0
    assert "2" in capsys.readouterr().out


def test_param_binds(project, capsys):
    rc = _run(["query",
               "SELECT sessions FROM fact_daily WHERE event_date = :day",
               "--param", "day=2026-06-15"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "250" in out and "100" not in out


def test_max_rows_truncates_with_note(project, capsys):
    rc = _run(["query", "SELECT * FROM fact_daily", "--max-rows", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 of 2" in out


def test_csv_round_trips(project, capsys):
    import io

    import pandas as pd
    rc = _run(["query", "SELECT event_date, sessions FROM fact_daily", "--csv"])
    out = capsys.readouterr().out
    assert rc == 0
    df = pd.read_csv(io.StringIO(out))
    assert list(df.columns) == ["event_date", "sessions"]
    assert int(df["sessions"].sum()) == 350


def test_sqlite_is_read_only(project, capsys):
    """An exploration tool must not be able to mutate the warehouse."""
    rc = _run(["query", "INSERT INTO fact_daily VALUES ('2026-06-16', 1)"])
    assert rc == 1
    assert "query failed" in capsys.readouterr().err
    with sqlite3.connect(project / "data-sources" / "tiny.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0] == 2


def test_unknown_source_names_the_real_ones(project, capsys):
    rc = _run(["query", "SELECT 1", "--source", "nope"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "tiny_db" in err


def test_multiple_sources_demand_a_choice(project, capsys, monkeypatch):
    cfg = project / "data-sources" / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") +
                   "  other_db:\n    type: sqlite\n"
                   "    path: data-sources/tiny.sqlite\n",
                   encoding="utf-8")
    import trellum.data.datasource_config as dsc
    monkeypatch.setattr(dsc, "_cache", None)

    rc = _run(["query", "SELECT 1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "tiny_db" in err and "other_db" in err


def test_empty_result_hints_at_the_date_span_trap(project, capsys):
    rc = _run(["query", "SELECT * FROM fact_daily WHERE event_date = '2030-01-01'"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 rows" in out
    assert "date span" in out


# ── Drift guards: the features stay advertised where agents look ─────────

def test_describe_advertises_query_and_serve():
    text = cli._describe()
    assert "trellum query" in text
    assert "trellum serve --background" in text


def test_data_footer_advertises_query(project, capsys):
    rc = _run(["data"])
    assert rc == 0
    assert "trellum query" in capsys.readouterr().out


def test_agents_md_states_the_closing_move():
    """The done-checklist requires handing over the link -- the rule that
    closes the measured Opus-does-it / Sonnet-doesn't gap."""
    from trellum.agent import agentdoc
    body = agentdoc.sections()["validation"].body
    assert "serve --background" in body
    queries = agentdoc.sections()["queries"].body
    assert "trellum query" in queries

"""`from trellum import query` -- a DataFrame from a configured source, no report.

`trellum query` is the shell front door; this is the same route for a script
or a notebook. It has to work from a subdirectory (a notebook lives in
notebooks/), which is what the walk-up to data-sources/config.yaml buys: the
first line of every script is not a hand-set project root.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A minimal project: config.yaml + a two-row sqlite warehouse. No reports/."""
    ds = tmp_path / "data-sources"
    ds.mkdir()
    conn = sqlite3.connect(ds / "tiny.sqlite")
    with conn:
        conn.execute("CREATE TABLE fact_daily (event_date TEXT, sessions INT)")
        conn.executemany("INSERT INTO fact_daily VALUES (?, ?)",
                         [("2026-06-14", 100), ("2026-06-15", 250)])
    conn.close()
    (ds / "config.yaml").write_text(
        "sources:\n"
        "  tiny_db:\n"
        "    type: sqlite\n"
        "    path: data-sources/tiny.sqlite\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FW_PROJECT_ROOT", raising=False)

    # The config loader caches per process, and a walk-up sets the root
    # override for the process: reset both, or one test inherits another's
    # project.
    import trellum.data.datasource_config as dsc
    import trellum.project as project_mod
    monkeypatch.setattr(dsc, "_cache", None)
    monkeypatch.setattr(project_mod, "_project_root_override", None)
    return tmp_path


def test_facade_is_importable_from_the_package_root():
    from trellum import connect, query, sources
    assert callable(query) and callable(connect) and callable(sources)


def test_query_returns_a_dataframe_with_no_reports_dir(project):
    from trellum import query
    assert not (project / "reports").exists()
    df = query("tiny_db",
               "SELECT event_date, sessions FROM fact_daily ORDER BY event_date")
    assert list(df["sessions"]) == [100, 250]


def test_sources_names_the_configured_source(project):
    from trellum import sources
    assert [s["name"] for s in sources()] == ["tiny_db"]


def test_works_from_a_subdirectory(project, monkeypatch):
    """A notebook in notebooks/ must not have to set the project root by hand."""
    from trellum import query
    nb = project / "notebooks"
    nb.mkdir()
    monkeypatch.chdir(nb)
    df = query("tiny_db", "SELECT COUNT(*) AS n FROM fact_daily")
    assert int(df["n"][0]) == 2


def test_params_bind(project):
    from trellum import query
    df = query("tiny_db",
               "SELECT sessions FROM fact_daily WHERE event_date = :day",
               {"day": "2026-06-15"})
    assert list(df["sessions"]) == [250]


def test_sqlite_is_read_only(project):
    """An answer must not be able to mutate the warehouse."""
    from trellum import query
    with pytest.raises(sqlite3.OperationalError):
        query("tiny_db", "INSERT INTO fact_daily VALUES ('2026-06-16', 1)")
    conn = sqlite3.connect(project / "data-sources" / "tiny.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0] == 2
    finally:
        conn.close()


def test_unknown_source_names_the_real_ones(project):
    from trellum import query
    with pytest.raises(KeyError, match="tiny_db"):
        query("nope", "SELECT 1")


def test_connect_hands_pandas_a_usable_connection(project):
    import pandas as pd

    from trellum import connect
    conn = connect("tiny_db")
    try:
        df = pd.read_sql("SELECT COUNT(*) AS n FROM fact_daily", conn)
    finally:
        conn.close()
    assert int(df["n"][0]) == 2


# ── Drift guards: the route stays advertised where agents look ───────────

def test_describe_routes_a_question_to_an_answer():
    from trellum import cli
    text = cli._describe()
    assert "from trellum import query" in text
    assert "no report" in text, "the description never says a question needs no report"


def test_agents_md_routes_questions_to_an_answer():
    from trellum.agent import agentdoc
    body = agentdoc.sections()["answer"].body
    assert "from trellum import query" in body
    assert "trellum metrics" in body
    text = (agentdoc.PACKAGE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "buys nothing" not in text, \
        "the 'when to use' table still sends one-off questions away from the framework"

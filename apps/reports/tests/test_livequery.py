"""Live queries M1: the governed endpoint (apps/reports/livequery.py).

Fork-safe: sqlite fixtures on disk, no secrets, no network.
"""
import json
import logging
import sqlite3
import threading
import time

import pytest

from apps.core import roles
from apps.core.models import AuditLog
from apps.reports import livequery
from apps.reports.models import Report

pytestmark = pytest.mark.django_db

SLUG = "player-overview"


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_slots(monkeypatch):
    """Concurrency slots are process-global module state; each test starts
    with an empty pool built from the current settings."""
    livequery._org_counts.clear()
    monkeypatch.setattr(livequery, "_global_sem", None)
    yield
    livequery._org_counts.clear()


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def url(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}/api/reports/{SLUG}/live-query"


@pytest.fixture
def sqlite_source(studio_tree):
    from apps.datasources.models import DataSource

    db_path = studio_tree.project_root / "data-sources" / "files" / "lq.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE players (id INTEGER, name TEXT, joined TEXT)")
    conn.executemany(
        "INSERT INTO players VALUES (?, ?, ?)",
        [(1, "ada", "2026-01-01"), (2, "bo", "2026-02-01"), (3, "cy", "2026-03-01")],
    )
    conn.commit()
    conn.close()
    return DataSource.objects.create(
        studio=studio_tree, name="lqdb", type="sqlite",
        config={"path": "data-sources/files/lq.sqlite"},
    )


@pytest.fixture
def write_manifest(studio_tree, report_row):
    def _write(queries: dict, version=1):
        out = studio_tree.output_dir / SLUG
        out.mkdir(parents=True, exist_ok=True)
        (out / "_live_queries.json").write_text(
            json.dumps({"version": version, "queries": queries}), encoding="utf-8"
        )

    return _write


BASIC = {
    "top": {
        "sql": "SELECT id, name FROM players WHERE id >= :min_id ORDER BY id",
        "datasource": "lqdb",
        "params": [{"name": "min_id", "type": "int", "required": True}],
    }
}


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


# ── Auth: the decorator is the whole story ──────────────────────────────────

class TestAuth:
    def test_sessionless_rejected(self, client, url, write_manifest, sqlite_source):
        write_manifest(BASIC)
        response = _post(client, url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 401

    def test_org_member_without_studio_grant_404(
        self, login, member, url, write_manifest, sqlite_source
    ):
        write_manifest(BASIC)
        client = login(member)
        response = _post(client, url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 404

    def test_stranger_from_other_org_404(
        self, login, make_user, other_org, url, write_manifest, sqlite_source
    ):
        write_manifest(BASIC)
        client = login(make_user("out@rival.example", org=other_org))
        response = _post(client, url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 404

    def test_get_is_not_allowed(self, login, viewer, url, write_manifest, sqlite_source):
        write_manifest(BASIC)
        assert login(viewer).get(url).status_code == 405


# ── Manifest resolution ─────────────────────────────────────────────────────

class TestManifest:
    def test_missing_manifest_404(self, login, viewer, url, report_row, sqlite_source):
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 404

    def test_missing_report_404(self, login, viewer, org, studio_tree, sqlite_source):
        url = f"/s/{org.slug}/{studio_tree.slug}/api/reports/no-such/live-query"
        response = _post(login(viewer), url, {"query_id": "top", "params": {}})
        assert response.status_code == 404

    def test_unsupported_manifest_version_404(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        write_manifest(BASIC, version=99)
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 404

    def test_unknown_query_id_400(self, login, viewer, url, write_manifest, sqlite_source):
        write_manifest(BASIC)
        response = _post(login(viewer), url, {"query_id": "nope", "params": {}})
        assert response.status_code == 400
        assert "query_id" in response.json()["error"]

    def test_query_id_required_400(self, login, viewer, url, write_manifest, sqlite_source):
        write_manifest(BASIC)
        assert _post(login(viewer), url, {"params": {}}).status_code == 400

    @pytest.mark.parametrize("raw", ["[1,2,3]", "42", "null", "true", '"hello"'])
    def test_non_object_json_body_400(
        self, login, viewer, url, write_manifest, sqlite_source, raw
    ):
        # A body that parses as valid JSON but isn't an object has no
        # .get() — json_body must not hand callers anything but a dict.
        write_manifest(BASIC)
        response = login(viewer).post(url, data=raw, content_type="application/json")
        assert response.status_code == 400, (raw, response.status_code)


# ── Coercion: the injection defense ─────────────────────────────────────────

class TestCoercion:
    @pytest.fixture
    def typed_manifest(self, write_manifest):
        write_manifest({
            "q": {
                "sql": "SELECT id FROM players WHERE id >= :n AND name = :s "
                       "AND joined >= :d AND id < :f",
                "datasource": "lqdb",
                "params": [
                    {"name": "n", "type": "int", "required": True},
                    {"name": "s", "type": "str", "required": False, "max_length": 5},
                    {"name": "d", "type": "date", "required": False},
                    {"name": "f", "type": "float", "required": False},
                    {"name": "e", "type": "enum", "required": False, "values": ["a", "b", 1]},
                ],
            }
        })

    def _expect_400(self, client, url, params, fragment):
        response = _post(client, url, {"query_id": "q", "params": params})
        assert response.status_code == 400, response.content
        assert fragment in response.json()["error"]

    def test_int_from_garbage_400(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": "abc"}, "integer")

    def test_int_from_huge_digit_string_400(
        self, login, viewer, url, typed_manifest, sqlite_source
    ):
        # A digit string past Python's int-from-str conversion limit (4300
        # digits by default) must fail as a clean 400, not crash the
        # process: int() raises a plain ValueError here, not ParamError.
        self._expect_400(login(viewer), url, {"n": "9" * 5000}, "integer")

    def test_bool_is_not_an_int(self, login, viewer, url, typed_manifest, sqlite_source):
        # bool subclasses int in Python; the trap the coercer must refuse.
        self._expect_400(login(viewer), url, {"n": True}, "integer")

    def test_float_from_garbage_400(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "f": "wide"}, "number")

    def test_float_rejects_nan(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "f": "nan"}, "finite")

    def test_date_must_be_strict_iso(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "d": "01-01-2026"}, "date")

    def test_date_rejects_impossible_days(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "d": "2026-02-30"}, "date")

    def test_str_length_capped(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "s": "toolong"}, "5 characters")

    def test_max_length_zero_is_honored(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        # `spec.get("max_length") or DEFAULT_STR_MAX` treats a declared 0 as
        # falsy and silently widens the cap to 200 — a manifest that means
        # "no string allowed" ends up accepting one.
        write_manifest({
            "q": {
                "sql": "SELECT 1",
                "datasource": "lqdb",
                "params": [{"name": "s", "type": "str", "max_length": 0}],
            }
        })
        response = _post(login(viewer), url, {"query_id": "q", "params": {"s": "x"}})
        assert response.status_code == 400, response.content

    def test_str_must_be_string(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "s": 7}, "string")

    def test_enum_membership(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "e": "z"}, "allowed values")

    def test_enum_bool_cannot_pose_as_one(self, login, viewer, url, typed_manifest, sqlite_source):
        # True == 1 in Python; type-exact matching must still refuse it.
        self._expect_400(login(viewer), url, {"n": 1, "e": True}, "allowed values")

    def test_unknown_param_400(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {"n": 1, "sneaky": 1}, "unknown parameter")

    def test_missing_required_400(self, login, viewer, url, typed_manifest, sqlite_source):
        self._expect_400(login(viewer), url, {}, "required")


# ── The read-only guard on the manifest SQL ─────────────────────────────────

class TestSqlGuard:
    def test_doctored_manifest_write_rejected(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        write_manifest({
            "evil": {"sql": "DELETE FROM players", "datasource": "lqdb", "params": []}
        })
        response = _post(login(viewer), url, {"query_id": "evil", "params": {}})
        assert response.status_code == 400
        assert "SELECT" in response.json()["error"]

    def test_forbidden_keyword_inside_select(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        write_manifest({
            "evil": {
                "sql": "SELECT 1; DROP TABLE players",
                "datasource": "lqdb", "params": [],
            }
        })
        response = _post(login(viewer), url, {"query_id": "evil", "params": {}})
        assert response.status_code == 400

    def test_comment_cannot_hide_a_verb(self):
        assert livequery.check_sql_safe("/* SELECT */ UPDATE t SET x=1") is not None

    def test_second_statement_refused(self):
        assert livequery.check_sql_safe("SELECT 1; SELECT 2") is not None
        assert livequery.check_sql_safe("SELECT 1;") is None

    def test_select_and_with_pass(self):
        assert livequery.check_sql_safe("SELECT * FROM t") is None
        assert livequery.check_sql_safe("WITH x AS (SELECT 1) SELECT * FROM x") is None

    def test_forbidden_word_inside_string_literal_is_not_a_verb(self):
        # An audit-log style report filtering on an action column hits this
        # directly: 'DELETE' as data, not as SQL.
        assert livequery.check_sql_safe(
            "SELECT * FROM events WHERE action = 'DELETE'"
        ) is None

    def test_escaped_quote_inside_string_literal_still_masked(self):
        # The SQL '' escape for a literal quote must not end the literal
        # early and leave the rest of it exposed to the keyword scan.
        assert livequery.check_sql_safe(
            "SELECT * FROM t WHERE note = 'it''s a DELETE test'"
        ) is None

    def test_semicolon_inside_string_literal_is_not_a_second_statement(self):
        assert livequery.check_sql_safe(
            "SELECT * FROM t WHERE msg = 'a;b'"
        ) is None

    def test_limit_injected_when_absent(self):
        assert livequery.inject_limit("SELECT * FROM t").endswith("LIMIT 1000")
        assert livequery.inject_limit("SELECT * FROM t;").endswith("LIMIT 1000")

    def test_existing_limit_respected(self):
        sql = "SELECT * FROM t LIMIT 5"
        assert livequery.inject_limit(sql) == sql

    def test_limit_inside_subquery_does_not_suppress_the_outer_cap(self):
        # A "top-N per group" subquery is ordinary reporting SQL; its LIMIT
        # bounds the subquery, not the statement's own result set, so it
        # must not be mistaken for the outer query already having a cap.
        sql = "SELECT * FROM (SELECT id FROM t ORDER BY id LIMIT 5) sub"
        result = livequery.inject_limit(sql)
        assert result.endswith("LIMIT 1000"), result

    def test_unbalanced_paren_in_string_literal_does_not_double_limit(self):
        # A '(' inside a string literal must not skew paren depth and hide a
        # real top-level LIMIT — otherwise a second LIMIT is appended,
        # producing invalid SQL.
        sql = "SELECT * FROM t WHERE label = '(' LIMIT 5"
        assert livequery.inject_limit(sql) == sql

    def test_limit_word_inside_string_literal_is_not_a_cap(self):
        # The word LIMIT as data must not be read as the statement's own cap.
        sql = "SELECT * FROM t WHERE note = 'no LIMIT here'"
        assert livequery.inject_limit(sql).endswith("LIMIT 1000")


# ── Datasource gating ───────────────────────────────────────────────────────

class TestDatasource:
    def test_unknown_source_name_400(self, login, viewer, url, write_manifest, sqlite_source):
        write_manifest({
            "q": {"sql": "SELECT 1", "datasource": "ghost", "params": []}
        })
        response = _post(login(viewer), url, {"query_id": "q", "params": {}})
        assert response.status_code == 400

    def test_non_sql_source_400(self, login, viewer, url, write_manifest, studio_tree):
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="csvfile", type="file",
            config={"path": "data-sources/files/x.csv"},
        )
        write_manifest({
            "q": {"sql": "SELECT 1", "datasource": "csvfile", "params": []}
        })
        response = _post(login(viewer), url, {"query_id": "q", "params": {}})
        assert response.status_code == 400
        assert "does not support live queries" in response.json()["error"]

    def test_sqlite_missing_file_400(self, login, viewer, url, write_manifest, studio_tree):
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="ghostdb", type="sqlite",
            config={"path": "data-sources/files/ghost.sqlite"},
        )
        write_manifest({
            "q": {"sql": "SELECT 1", "datasource": "ghostdb", "params": []}
        })
        response = _post(login(viewer), url, {"query_id": "q", "params": {}})
        assert response.status_code == 400


# ── Caps ────────────────────────────────────────────────────────────────────

class TestCaps:
    def test_429_when_global_pool_exhausted(
        self, login, viewer, url, write_manifest, sqlite_source, monkeypatch
    ):
        write_manifest(BASIC)
        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(blocking=False)  # hold the only slot
        monkeypatch.setattr(livequery, "_global_sem", sem)
        try:
            response = _post(
                login(viewer), url, {"query_id": "top", "params": {"min_id": 1}}
            )
            assert response.status_code == 429
            assert response["Retry-After"] == "1"
        finally:
            sem.release()

    def test_429_when_org_pool_exhausted(
        self, login, viewer, org, url, write_manifest, sqlite_source
    ):
        write_manifest(BASIC)
        livequery._org_counts[org.pk] = livequery.PER_ORG_MAX
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 429
        assert response["Retry-After"] == "1"

    def test_row_cap_truncates(
        self, login, viewer, url, write_manifest, sqlite_source, monkeypatch
    ):
        write_manifest(BASIC)
        monkeypatch.setattr(livequery, "ROW_CAP", 2)
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 200
        body = response.json()
        assert len(body["rows"]) == 2
        assert body["truncated"] is True

    def test_rate_limit_429(
        self, login, viewer, url, write_manifest, sqlite_source, monkeypatch
    ):
        write_manifest(BASIC)
        # The rate limit is now per-org configurable (apps.reports.models
        # .live_query_rate_limit / OrgLiveQueryPolicy, live-query filter
        # redesign) rather than the module constant -- patch the DEFAULT an
        # org with no policy row reads, which is what every test org here
        # has.
        from apps.reports import models as reports_models

        monkeypatch.setattr(reports_models, "DEFAULT_LIVE_QUERY_RATE_LIMIT", 0)
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 429
        assert response.has_header("Retry-After")

    def test_org_configured_rate_limit_is_enforced(
        self, login, viewer, url, write_manifest, sqlite_source, org,
    ):
        # End-to-end: an org that has configured its own (lower) limit via
        # OrgLiveQueryPolicy -- not a monkeypatch -- gets 429'd at that
        # limit rather than the framework default.
        from apps.reports.models import OrgLiveQueryPolicy

        OrgLiveQueryPolicy.objects.create(org=org, rate_limit_per_minute=1)
        write_manifest(BASIC)
        client = login(viewer)
        first = _post(client, url, {"query_id": "top", "params": {"min_id": 1}})
        assert first.status_code == 200
        second = _post(client, url, {"query_id": "top", "params": {"min_id": 2}})
        assert second.status_code == 429

    def test_org_configured_rate_limit_can_raise_the_default(
        self, login, viewer, url, write_manifest, sqlite_source, org, monkeypatch,
    ):
        # An org may also raise its ceiling above the framework default --
        # confirms the org value isn't clamped to the default in either
        # direction.
        from apps.reports import models as reports_models
        from apps.reports.models import OrgLiveQueryPolicy

        monkeypatch.setattr(reports_models, "DEFAULT_LIVE_QUERY_RATE_LIMIT", 1)
        OrgLiveQueryPolicy.objects.create(org=org, rate_limit_per_minute=5)
        write_manifest(BASIC)
        client = login(viewer)
        for min_id in (1, 2, 3):
            resp = _post(client, url, {"query_id": "top", "params": {"min_id": min_id}})
            assert resp.status_code == 200, f"request {min_id} unexpectedly throttled"

    def test_deadline_504(
        self, login, viewer, url, write_manifest, sqlite_source, monkeypatch
    ):
        write_manifest(BASIC)
        monkeypatch.setattr(livequery, "DEADLINE_SECONDS", 0)
        monkeypatch.setattr(
            livequery, "_execute_sql",
            lambda ds, conn_info, sql, result: time.sleep(0.3),
        )
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 504
        assert "deadline" in response.json()["error"]

        row = AuditLog.objects.get(action="report.live_query")
        assert row.outcome == "failure"  # column, not the old metadata string
        assert row.metadata["timed_out"] is True


# ── Result cache ────────────────────────────────────────────────────────────

class TestResultCache:
    def test_second_identical_call_skips_execution(
        self, login, viewer, url, write_manifest, sqlite_source, monkeypatch
    ):
        write_manifest(BASIC)
        calls = {"n": 0}
        real = livequery._execute_sql

        def _counting(ds, conn_info, sql, result):
            calls["n"] += 1
            real(ds, conn_info, sql, result)

        monkeypatch.setattr(livequery, "_execute_sql", _counting)
        client = login(viewer)
        first = _post(client, url, {"query_id": "top", "params": {"min_id": 2}})
        second = _post(client, url, {"query_id": "top", "params": {"min_id": 2}})
        assert first.status_code == second.status_code == 200
        assert calls["n"] == 1
        assert first.json()["rows"] == second.json()["rows"]
        # different params miss the cache and execute again
        third = _post(client, url, {"query_id": "top", "params": {"min_id": 3}})
        assert third.status_code == 200
        assert calls["n"] == 2


# ── Happy path + audit ──────────────────────────────────────────────────────

class TestHappyPath:
    def test_bind_sql_uses_backslash_dialect_for_mysql(self):
        """MySQL reads a backslash inside a literal as an escape, so a bound
        value ending in one must not be able to close the literal early."""
        assert livequery.bind_sql("SELECT :p", {"p": "\\'"}, "mysql") == "SELECT '\\\\'''"
        assert livequery.bind_sql("SELECT :p", {"p": "\\'"}, "sqlite") == "SELECT '\\'''"

    def test_rows_columns_and_audit(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        write_manifest(BASIC)
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 2}})
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["columns"] == ["id", "name"]
        assert body["rows"] == [[2, "bo"], [3, "cy"]]
        assert body["truncated"] is False
        assert isinstance(body["elapsed_ms"], int)

        row = AuditLog.objects.get(action="report.live_query")
        assert row.target_id == str(Report.objects.get(slug=SLUG).pk)
        assert row.metadata["query_id"] == "top"
        assert "elapsed_ms" in row.metadata
        assert row.outcome == "success"  # column, not a metadata "ok" string
        assert "outcome" not in row.metadata
        assert row.category == "access"
        # NEVER param values or SQL in the trail
        dumped = json.dumps(row.metadata)
        assert "min_id" not in dumped
        assert "SELECT" not in dumped

    def test_execution_error_is_recorded_as_a_failure_outcome(
        self, login, viewer, url, write_manifest, sqlite_source, monkeypatch
    ):
        write_manifest(BASIC)

        def _erroring(ds, conn_info, sql, result):  # noqa: ARG001
            result["error"] = "simulated driver error"

        monkeypatch.setattr(livequery, "_execute_sql", _erroring)
        response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 2}})
        assert response.status_code == 400

        row = AuditLog.objects.get(action="report.live_query")
        assert row.outcome == "failure"

    def test_driver_error_text_stays_out_of_the_response(
        self, login, viewer, url, write_manifest, sqlite_source, caplog
    ):
        # A query against a table that does not exist: sqlite's message
        # names the table, which must reach the log but not the viewer.
        write_manifest({
            "bad": {
                "sql": "SELECT id FROM secret_table WHERE id >= :min_id",
                "datasource": "lqdb",
                "params": [{"name": "min_id", "type": "int", "required": True}],
            }
        })
        with caplog.at_level(logging.WARNING, logger="apps.reports.livequery"):
            response = _post(login(viewer), url, {"query_id": "bad", "params": {"min_id": 1}})
        assert response.status_code == 400
        assert response.json()["error"] == "query failed"
        assert "secret_table" not in response.content.decode()
        assert any("secret_table" in r.getMessage() for r in caplog.records)

    def test_cache_hit_writes_no_second_audit_row(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        write_manifest(BASIC)
        client = login(viewer)
        _post(client, url, {"query_id": "top", "params": {"min_id": 1}})
        _post(client, url, {"query_id": "top", "params": {"min_id": 1}})
        assert AuditLog.objects.filter(action="report.live_query").count() == 1

    def test_str_param_binds_as_literal(
        self, login, viewer, url, write_manifest, sqlite_source
    ):
        write_manifest({
            "byname": {
                "sql": "SELECT id FROM players WHERE name = :who",
                "datasource": "lqdb",
                "params": [{"name": "who", "type": "str", "required": True}],
            }
        })
        client = login(viewer)
        ok = _post(client, url, {"query_id": "byname", "params": {"who": "ada"}})
        assert ok.status_code == 200
        assert ok.json()["rows"] == [[1]]
        # A classic injection payload arrives, gets escaped into a literal,
        # and simply matches nothing.
        inj = _post(
            client, url,
            {"query_id": "byname", "params": {"who": "' OR '1'='1"}},
        )
        assert inj.status_code == 200
        assert inj.json()["rows"] == []


# ── Source types beyond sqlite ───────────────────────────────────────────────

class TestOtherSourceTypes:
    def test_duckdb_missing_file_is_unusable(self, studio_tree):
        from apps.datasources.models import DataSource

        ds = DataSource.objects.create(
            studio=studio_tree, name="lake", type="duckdb",
            config={"path": "data-sources/files/lake.duckdb"},
        )
        with pytest.raises(livequery.SourceUnusable):
            livequery.conn_info_for(ds)

    def test_driver_error_never_echoes_a_token(
        self, login, viewer, url, write_manifest, studio_tree, monkeypatch, caplog
    ):
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="lqdb", type="databricks",
            config={"host": "h", "http_path": "/sql/1"},
            credentials={"access_token": "dapi-secret"},
        )
        write_manifest(BASIC)

        class FakeDriver:
            def connect(self, conn_info):
                raise RuntimeError(f"token {conn_info['access_token']} rejected")

        import trellum.data.drivers as drivers_mod

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: FakeDriver())
        with caplog.at_level(logging.WARNING, logger="apps.reports.livequery"):
            response = _post(login(viewer), url, {"query_id": "top", "params": {"min_id": 1}})
        assert response.status_code == 400
        assert response.json()["error"] == "query failed"
        assert "dapi-secret" not in response.content.decode()
        assert "dapi-secret" not in caplog.text
        assert "***" in caplog.text

    @pytest.mark.parametrize("ds_type, literal", [
        ("postgres", "'O''Brien'"),
        ("bigquery", r"'O\'Brien'"),
        ("databricks", r"'O\'Brien'"),
    ])
    def test_bind_sql_follows_the_framework_dialect(self, ds_type, literal):
        assert livequery.bind_sql("SELECT :who", {"who": "O'Brien"}, ds_type) == f"SELECT {literal}"

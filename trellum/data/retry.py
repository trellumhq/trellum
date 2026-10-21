"""Bounded fresh-connection recovery for materialized report reads."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from trellum.data.retry_errors import is_retryable_connection_error
from trellum.diagnostics import diagnostic_context, progress, record_failure, record_retry, stage

logger = logging.getLogger(__name__)
_ATTEMPTS = 3
_TOKEN = re.compile(
    r"\s+|--[^\r\n]*|/\*[^*]*(?:\*(?!/)[^*]*)*\*/|"
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`(?:[^`]|``)*`|"
    r"\[(?:[^\]]|\]\])*\]|[A-Za-z_][\w$]*|.", re.DOTALL,
)
_STATEFUL = frozenset(
    "INSERT UPDATE DELETE DROP ALTER TRUNCATE CREATE GRANT REVOKE MERGE "
    "UPSERT REPLACE RENAME COPY EXPORT LOCK UNLOCK INTO SET RESET USE CALL "
    "EXEC EXECUTE BEGIN START COMMIT ROLLBACK SAVEPOINT RELEASE VACUUM "
    "ANALYZE PRAGMA ATTACH DETACH LOAD INSTALL UNLOAD OUTFILE DUMPFILE FOR "
    "NEXTVAL SETVAL GET_LOCK RELEASE_LOCK PG_ADVISORY_LOCK "
    "PG_ADVISORY_XACT_LOCK PG_TRY_ADVISORY_LOCK".split()
)


def _can_replay(sql: str, source_type: str = "postgres") -> bool:
    """Recognize one ordinary read; this is not a proof of function purity."""
    tokens = []
    for match in _TOKEN.finditer(sql):
        token = match.group()
        if token.isspace():
            continue
        if token.startswith("--"):
            # MySQL requires whitespace after the second dash.
            if source_type == "mysql" and len(token) > 2 and not token[2].isspace():
                return False
            continue
        if token.startswith("/*"):
            # MySQL/MariaDB can execute SQL inside versioned comments.
            if token.startswith(("/*!", "/*M!")) or "/*" in token[2:]:
                return False
            continue
        if token == "/" and sql[match.end():].startswith("*"):
            return False  # Unterminated block comment.
        # Unsupported quoting remains unsafe outside genuine comments.
        if "\\" in token:
            return False
        if source_type in ("bigquery", "databricks") and token[:3] in ("'" * 3, '"' * 3):
            return False
        if token[0] in "'\"`[":
            if token[0] == "'" and tokens and tokens[-1] == "E":
                return False  # Escape-prefixed literals are not parsed here.
            if len(token) == 1 or (token[0] == "[" and source_type != "sqlserver"):
                return False
            tokens.append("<quoted>")
            continue
        if token in ("$", "#"):
            return False
        tokens.append(token.upper())
    if tokens and tokens[-1] == ";":
        tokens.pop()
    if not tokens or tokens[0] not in ("SELECT", "WITH") or ";" in tokens:
        return False
    # Keep the shared denylist; exempt only recognized Vertica expressions.
    # Each frame tracks SUBSTRING, FROM seen, and FOR seen at this depth.
    parentheses = []
    for index, token in enumerate(tokens):
        previous = tokens[index - 1] if index else None
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if token == "(":
            parentheses.append([previous == "SUBSTRING", False, False])
        elif token == ")":
            if not parentheses:
                return False
            parentheses.pop()
        elif token == "FROM" and parentheses and parentheses[-1][0]:
            parentheses[-1][1] = True
        if token not in _STATEFUL:
            continue
        if source_type == "vertica":
            if token == "REPLACE" and following == "(":
                continue
            if token == "FOR" and parentheses:
                substring = parentheses[-1]
                if substring[0] and substring[1] and not substring[2]:
                    substring[2] = True
                    continue
        return False
    return not parentheses


class ManagedConnection:
    """Stable report handle whose private driver connection can be replaced.

    Calling driver methods or assigning connection properties opts out of
    query replay: a fresh session cannot restore manually created state.
    Vertica opened/closed probes and its lifetime-only context are exempt.
    In new_connection_per_query mode, only framework queries are supported;
    each operation owns a fresh session and context exit closes the handle.
    """

    def __init__(self, source_type: str, factory: Callable[[], Any], *, new_connection_per_query: bool = False):
        self._source_type = source_type
        self._factory = factory
        self._inner = None
        self._lock = threading.RLock()
        self._closed = False
        self._replay_enabled = True
        self._new_connection_per_query = new_connection_per_query
        if not new_connection_per_query:
            with self._lock:
                self._run(lambda raw: raw, replay=True)

    def __repr__(self) -> str:
        return f"<ManagedConnection type={self._source_type!r} closed={self._closed}>"

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        with self._lock:
            if self._new_connection_per_query:
                if name == "closed":
                    return self._closed
                self._reject_session_access()
            if name == "closed" and self._closed:
                return True
            self._ensure_open()
            value = getattr(self._inner, name)
            if not callable(value):
                return value

            def invoke(*args, **kwargs):
                with self._lock:
                    self._ensure_open()
                    if not (self._source_type == "vertica" and name in ("closed", "opened")):
                        self._replay_enabled = False
                        progress("execute", source_type=self._source_type, query_retry_safe=False, unsafe_operation=True)
                    # Resolve at invocation: a saved method must follow replacement.
                    return getattr(self._inner, name)(*args, **kwargs)

            return invoke

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            with self._lock:
                if self._new_connection_per_query:
                    self._reject_session_access()
                self._ensure_open()
                self._replay_enabled = False
                progress("execute", source_type=self._source_type, query_retry_safe=False, unsafe_operation=True)
                setattr(self._inner, name, value)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Connection has been explicitly closed")
        if self._inner is None and not self._new_connection_per_query:
            raise RuntimeError("Connection is unavailable after a failed operation")

    def _reject_session_access(self) -> None:
        raise RuntimeError(
            "new_connection_per_query does not support direct driver session access; "
            "use framework query helpers or disable new_connection_per_query"
        )

    def _discard(self) -> None:
        raw, self._inner = self._inner, None
        if raw is not None:
            try:
                raw.close()
            except Exception:
                pass  # Cleanup must not replace the driver failure.

    def _run(self, operation: Callable[[Any], Any], *, replay: bool) -> Any:
        if self._closed:
            raise RuntimeError("Connection has been explicitly closed")
        if not replay:
            progress("execute", source_type=self._source_type, query_retry_safe=False, unsafe_operation=True)
        limit = _ATTEMPTS if replay else 1
        last_error = None
        for attempt in range(1, limit + 1):
            try:
                if self._inner is None:
                    with stage("connect", source_type=self._source_type, attempt=attempt, query_retry_safe=True):
                        self._inner = self._factory()
                with diagnostic_context(source_type=self._source_type, attempt=attempt, query_retry_safe=replay):
                    result = operation(self._inner)
            except Exception as exc:
                record_failure(exc, source_type=self._source_type, query_retry_safe=replay, attempt=attempt)
                if not replay or not is_retryable_connection_error(exc, self._source_type):
                    raise
                self._discard()
                last_error = type(exc).__name__
                from trellum.data.connections import source_of
                if attempt == limit:
                    logger.warning(
                        "Datasource recovery exhausted: driver=%s source=%s attempt=%s/%s error=%s",
                        self._source_type, source_of(self), attempt, limit, type(exc).__name__,
                    )
                    exc.add_note(f"Datasource recovery exhausted after {limit} attempts ({self._source_type}).")
                    raise
                record_retry(exc, attempt=attempt)
                logger.warning(
                    "Retrying datasource with fresh connection: driver=%s source=%s attempt=%s/%s error=%s",
                    self._source_type, source_of(self), attempt, limit, type(exc).__name__,
                )
                time.sleep(attempt)
            else:
                if attempt > 1:
                    from trellum.data.connections import source_of
                    logger.warning(
                        "Datasource recovered: driver=%s source=%s attempt=%s/%s error=%s",
                        self._source_type, source_of(self), attempt, limit, last_error,
                    )
                return result
            finally:
                if self._new_connection_per_query:
                    self._discard()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            raw, self._inner = self._inner, None
            self._factory = None
            if raw is not None:
                raw.close()

    def __enter__(self):
        with self._lock:
            self._ensure_open()
            if self._new_connection_per_query or self._source_type == "vertica":
                return self
            self._replay_enabled = False
            progress("execute", source_type=self._source_type, query_retry_safe=False, unsafe_operation=True)
            self._inner.__enter__()
            return self

    def __exit__(self, *exc):
        with self._lock:
            if self._new_connection_per_query or self._source_type == "vertica":
                self.close()
                return False
            self._ensure_open()
            return self._inner.__exit__(*exc)


def connect_managed(type_name: str, conn_info: dict) -> Any:
    """Open a native embedded connection or a recoverable remote handle."""
    from trellum.data import drivers

    if type_name in ("sqlite", "duckdb"):
        return drivers.connect(type_name, conn_info)
    from trellum.data.resolvers import parse_new_connection_per_query

    info = dict(conn_info)
    per_query = parse_new_connection_per_query(info.pop("new_connection_per_query", False))
    return ManagedConnection(
        type_name, lambda: drivers.connect(type_name, dict(info)), new_connection_per_query=per_query,
    )


def run_with_retry(conn: Any, operation: Callable[[Any], Any], *, sql: str) -> Any:
    """Retry an entire materialized read; never retry external raw cursors."""
    if not isinstance(conn, ManagedConnection):
        if not _can_replay(sql):
            progress("execute", query_retry_safe=False, unsafe_operation=True)
        return operation(conn)
    with conn._lock:
        if conn._new_connection_per_query:
            return conn._run(operation, replay=_can_replay(sql, conn._source_type))
        if not _can_replay(sql, conn._source_type):
            conn._replay_enabled = False
        return conn._run(operation, replay=conn._replay_enabled)

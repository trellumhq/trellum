"""Pure guard rails for running a declared live query's SQL.

Every host that executes a live query against a real datasource needs the
same two things first: strict parameter coercion against the manifest's
declared schema, and a read-only guard + ``LIMIT`` injection over the
manifest's SQL. Both are pure functions with no host dependency (no
Django, no HTTP), so they live here once and every host imports them —
the portal's production endpoint (``apps/reports/livequery.py``) and the
framework's own ``trellum serve`` dev convenience
(``trellum/runner/live_query_dev.py``) both call into this module rather
than each keeping a copy that can quietly drift on the one thing that
actually matters: nothing reaches the database that was not first forced
into its declared scalar type, and nothing runs that is not a bounded
SELECT/WITH.

Recovered from the removed assistant helpers (``check_sql_safe``/
``inject_limit``, ``apps/assistant/tools.py`` before ``b72a422``) and the
coercion the M1 live-query endpoint always carried, minus assistant's LLM
style rules banning GROUP BY/HAVING, which were never part of the
read-only posture.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date as _date

#: Source types with a SQL driver. Everything else (file/image/onedrive/api)
#: has no cursor to run SQL through, so a host refuses those with a plain
#: 400 -- defense in depth on top of the manifest naming only what the
#: build actually used.
SQL_TYPES = frozenset({
    "sqlite", "duckdb", "postgres", "mysql", "vertica", "clickhouse",
    "snowflake", "bigquery", "sqlserver", "redshift", "trino", "databricks",
})

#: Rows returned to the page, never more. Hosts may run tighter locally;
#: this is the shared default both the portal and the dev server start from.
ROW_CAP = 1000

#: Default cap for ``str`` params when the manifest declares no max_length.
DEFAULT_STR_MAX = 200


# ── Read-only guard + LIMIT injection ────────────────────────────────────────

_SQL_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"MERGE|UPSERT|REPLACE|RENAME|COPY|EXPORT|LOCK|UNLOCK)\b",
    re.IGNORECASE,
)
#: A single-quoted string literal; '' is the standard SQL escape for a
#: literal quote inside one, so it must not be read as the literal's end.
_SQL_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_PAREN_OR_LIMIT = re.compile(r"[()]|\bLIMIT\b\s+\d+", re.IGNORECASE)


def _mask_string_literals(sql: str) -> str:
    """*sql* with every string literal's contents blanked out.

    A literal can legitimately contain any SQL keyword or a semicolon — an
    audit-log report filtering ``action = 'DELETE'``, a free-text column
    holding ``'a;b'`` — and masking keeps that data from being mistaken for
    the statement's own structure, without needing a real SQL parser.
    """
    return _SQL_STRING_LITERAL.sub(lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", sql)


def check_sql_safe(sql: str) -> str | None:
    """None if the SQL passes the read-only guard, else a plain error.

    SELECT/WITH-only, forbidden-keyword scan, single statement. Comments are
    stripped first so ``/* DELETE */`` cannot hide a verb and ``-- SELECT``
    cannot fake one; string literals are masked next so data a report
    filters on is never mistaken for SQL structure.
    """
    stripped = (sql or "").strip()
    no_comments = re.sub(
        r"--[^\n]*\n?|/\*.*?\*/", " ", stripped, flags=re.DOTALL
    ).strip()
    if not no_comments:
        return "empty query"
    first = no_comments.upper().lstrip("(").lstrip()
    if not (first.startswith("SELECT") or first.startswith("WITH")):
        return "only SELECT / WITH queries are allowed"
    scannable = _mask_string_literals(no_comments)
    m = _SQL_FORBIDDEN.search(scannable)
    if m:
        return f"query contains a forbidden keyword ('{m.group(1).upper()}')"
    # One statement only: a trailing semicolon is tolerated, an interior one
    # is a second statement and is refused outright.
    if ";" in scannable.rstrip().rstrip(";"):
        return "only a single SQL statement is allowed"
    return None


def inject_limit(sql: str, cap: int = ROW_CAP) -> str:
    """Append ``LIMIT cap`` unless the statement's own result set already
    carries one.

    Paren-depth aware: a ``LIMIT`` inside a parenthesised subquery (an
    ordinary "top-N per group" pattern) bounds that subquery, not the
    statement's own output. Treating it as the outer query's LIMIT would
    skip injection and let the outer query run unbounded against the
    warehouse — the opposite of what this guard exists for.
    """
    if _has_top_level_limit(sql):
        return sql
    return sql.rstrip().rstrip(";").rstrip() + f" LIMIT {cap}"


def _has_top_level_limit(sql: str) -> bool:
    # Mask string literals first: a paren or the word LIMIT inside one is
    # data, not structure. An unbalanced paren in a literal (WHERE x = '(')
    # would otherwise skew the depth count and hide a real outer LIMIT,
    # which would then get a second LIMIT appended — invalid SQL.
    masked = _mask_string_literals(sql)
    depth = 0
    for token in _PAREN_OR_LIMIT.finditer(masked):
        text = token.group(0)
        if text == "(":
            depth += 1
        elif text == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            return True
    return False


# ── Strict parameter coercion ────────────────────────────────────────────────

class ParamError(ValueError):
    """A client value failed the declared schema. Always a 400, message safe
    to show (names the parameter, never echoes long values)."""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT_RE = re.compile(r"^[+-]?\d+$")


def coerce_params(schema: list, given: dict) -> dict:
    """Coerce ``given`` strictly against the manifest's declared params.

    Everything SQL-bound flows through here first; a value that comes out is
    an int, a float, or a length-capped str that downstream escaping treats
    purely as a literal. Unknown names, missing required params, and any
    type mismatch raise :class:`ParamError`.
    """
    if not isinstance(given, dict):
        raise ParamError("params must be an object")
    declared: dict[str, dict] = {}
    for spec in schema or []:
        if isinstance(spec, dict) and spec.get("name"):
            declared[str(spec["name"])] = spec
    for name in given:
        if name not in declared:
            raise ParamError(f"unknown parameter: {name}")
    out: dict = {}
    for name, spec in declared.items():
        if name not in given or given[name] is None:
            if spec.get("required"):
                raise ParamError(f"missing required parameter: {name}")
            continue
        out[name] = _coerce_one(name, spec, given[name])
    return out


def _coerce_one(name: str, spec: dict, value):
    kind = spec.get("type")
    if kind == "int":
        # bool is an int subclass; True must not become the literal 1.
        if isinstance(value, bool):
            raise ParamError(f"parameter '{name}' must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and _INT_RE.match(value.strip()):
            try:
                return int(value.strip())
            except ValueError:
                # Past Python's int-from-str digit limit (int_max_str_digits,
                # 4300 by default): a real ValueError, not our ParamError, so
                # it must be caught explicitly or it reaches the client as a
                # 500 instead of a 400.
                raise ParamError(f"parameter '{name}' must be an integer") from None
        raise ParamError(f"parameter '{name}' must be an integer")
    if kind == "float":
        if isinstance(value, bool):
            raise ParamError(f"parameter '{name}' must be a number")
        if isinstance(value, (int, float)):
            result = float(value)
        elif isinstance(value, str):
            try:
                result = float(value.strip())
            except ValueError:
                raise ParamError(f"parameter '{name}' must be a number") from None
        else:
            raise ParamError(f"parameter '{name}' must be a number")
        if not math.isfinite(result):
            raise ParamError(f"parameter '{name}' must be a finite number")
        return result
    if kind == "str":
        if not isinstance(value, str):
            raise ParamError(f"parameter '{name}' must be a string")
        cap = spec.get("max_length")
        # `or DEFAULT_STR_MAX` would treat a declared 0 as unset and widen
        # it back to the default — None (undeclared) is the only case that
        # should fall back.
        if cap is None:
            cap = DEFAULT_STR_MAX
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            cap = DEFAULT_STR_MAX
        if len(value) > cap:
            raise ParamError(f"parameter '{name}' exceeds {cap} characters")
        return value
    if kind == "date":
        if not isinstance(value, str) or not _DATE_RE.match(value):
            raise ParamError(f"parameter '{name}' must be a YYYY-MM-DD date")
        try:
            _date.fromisoformat(value)
        except ValueError:
            raise ParamError(f"parameter '{name}' is not a valid date") from None
        return value
    if kind == "enum":
        values = spec.get("values") or []
        # type-exact membership: `True == 1` and `1 == 1.0` are Python
        # equalities that must not admit a value of the wrong type.
        for allowed in values:
            if type(allowed) is type(value) and allowed == value:
                return value
        raise ParamError(f"parameter '{name}' is not one of the allowed values")
    raise ParamError(f"parameter '{name}' has an unsupported type")


# ── Row/value shaping for JSON ───────────────────────────────────────────────
# What a raw DB cursor hands back (Decimals, datetimes, bytes, NaN floats)
# is not what json.dumps accepts. Shared for the same reason as the rest of
# this module: the portal's endpoint and the dev server return rows through
# the identical contract (`{"columns", "rows", "truncated", "elapsed_ms"}`,
# docs/COMPATIBILITY.md), and this is the one place that turns a row of
# arbitrary driver values into JSON-safe ones for it.

def jsonable_value(value):
    """One raw DB cursor value, coerced to a JSON-safe scalar."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)  # dates, datetimes, Decimals, UUIDs, ...


def shape_rows(
    raw_rows: list, *, row_cap: int = ROW_CAP, byte_cap: int | None = None,
) -> tuple[list, bool]:
    """JSON-safe rows within *row_cap* and, when given, *byte_cap* bytes.

    ``byte_cap`` is optional — the portal enforces one (a shared production
    response budget); a single-developer dev server has no equivalent
    pressure and may skip it entirely.
    """
    truncated = len(raw_rows) > row_cap
    rows: list = []
    total = 0
    for raw in raw_rows[:row_cap]:
        row = [jsonable_value(v) for v in raw]
        if byte_cap is not None:
            total += len(json.dumps(row, default=str)) + 1
            if total > byte_cap:
                truncated = True
                break
        rows.append(row)
    return rows, truncated

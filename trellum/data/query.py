"""Query execution utilities with pandas integration."""

from __future__ import annotations

import decimal
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from trellum.data.http_source import read_api as read_api  # re-export for compatibility
from trellum.data.retry import ManagedConnection, run_with_retry
from trellum.data.sql_params import _sql_param_spans as _sql_param_spans
from trellum.data.ssh_tunnel import unwrap

_query_semaphore: threading.Semaphore = threading.Semaphore(4)

_cache_disabled: bool = False
# The cache key is the fully-bound SQL, so an edited query or a changed
# parameter re-queries immediately.  The TTL only guards against the data
# moving under a query that did not change -- a day is the right backstop
# for an authoring loop, where the old 5 minutes meant every coffee break
# cost a full re-query.  Production builds disable the cache outright.
_default_cache_ttl: int = 24 * 3600


def _cache_dir() -> Path:
    """Return the query cache directory, anchored to the project root.

    Must not be a bare relative path: report subprocesses can be spawned
    from a cwd other than the project root, which would otherwise silently
    relocate (or fragment) the cache.
    """
    from trellum.project import get_project_root
    return Path(get_project_root()) / "output" / ".query_cache"


def set_max_concurrent_queries(n: int) -> None:
    """Configure the process-wide limit on simultaneous DB queries."""
    global _query_semaphore
    _query_semaphore = threading.Semaphore(n)


def set_default_cache_ttl(seconds: int) -> None:
    """Set the default cache TTL for all ``query_df`` calls.

    Set to 0 to disable caching by default (individual calls can still
    override with an explicit ``cache_ttl`` parameter).
    """
    global _default_cache_ttl  # noqa: PLW0603
    _default_cache_ttl = seconds


def disable_cache() -> None:
    """Disable query caching globally (e.g. for --no-cache or --production)."""
    global _cache_disabled  # noqa: PLW0603
    _cache_disabled = True


def enable_cache() -> None:
    """Re-enable query caching after a ``disable_cache()`` call."""
    global _cache_disabled  # noqa: PLW0603
    _cache_disabled = False


def clear_cache() -> None:
    """Delete all cached query results."""
    cache_dir = _cache_dir()
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"  Cleared query cache ({cache_dir})", flush=True)


def _query_label(sql: str) -> str:
    """Extract a short label from SQL for display."""
    m = re.search(r'FROM\s+(\S+)', sql, re.IGNORECASE)
    if m:
        table = m.group(1).split('.')[-1]  # Remove schema prefix
        return table[:30].upper()
    return sql.strip()[:30].replace('\n', ' ')


def _execute_query(conn: Any, sql: str) -> pd.DataFrame:
    """Execute SQL and return DataFrame. Dispatches based on connection type."""
    conn = unwrap(conn)
    type_name = type(conn).__module__ + "." + type(conn).__qualname__

    if "google.cloud.bigquery" in type_name:
        return conn.query(sql).to_dataframe()

    if "clickhouse_connect" in type_name:
        return conn.query_df(sql)

    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        if "snowflake.connector" in type_name:
            return cursor.fetch_pandas_all()
        if cursor.description is None:
            return pd.DataFrame()
        columns = [desc[0] for desc in cursor.description]
        data = cursor.fetchall()
        return pd.DataFrame(data, columns=columns)
    finally:
        try:
            cursor.close()
        except Exception:
            pass  # Closing a cursor must not replace execute/fetch failures.


def query_df(
    conn: Any,
    sql: str,
    params: dict | None = None,
    cache_ttl: int | None = None,
    label: str | None = None,
) -> pd.DataFrame:
    """Execute a SQL query and return results as a pandas DataFrame.

    Supports ``:param_name`` style placeholders which are replaced with
    properly quoted literal values before execution.

    Concurrent queries are gated by a process-wide semaphore (default 4)
    to avoid overloading the database.  Use ``set_max_concurrent_queries()``
    to adjust the limit.

    Args:
        conn: Database connection (e.g. from ``ctx.get_connection()``).
        sql: SQL query string with optional ``:param_name`` placeholders.
        params: Dict mapping parameter names to values.
        cache_ttl: Cache results for this many seconds. Defaults to the
            global default (24h for local runs, 0 for production).
            Pass ``0`` to explicitly skip caching for a specific call.
            The cache key is derived from the data source's name plus the
            fully-bound SQL (after parameter substitution), so a change to
            the query text, the parameter values, or which source the
            connection came from all invalidate the cache.
        label: Optional display label for structured output (e.g.
            ``"HOURLY_REVENUE"``). If not provided, a label is
            auto-extracted from the SQL (first table name after FROM).

    Returns:
        pandas DataFrame with query results.
    """
    if isinstance(conn, ManagedConnection) and conn._new_connection_per_query:
        with conn._lock:
            conn._ensure_open()
    if params:
        sql = bind_params(sql, params, dialect=_sql_dialect(conn))

    display_label = label or _query_label(sql)
    is_debug = os.environ.get('FRAMEWORK_DEBUG') == '1'

    if cache_ttl is None:
        cache_ttl = _default_cache_ttl
    use_cache = cache_ttl > 0 and not _cache_disabled
    cache_path: Path | None = None
    cache_key: str | None = None

    if use_cache:
        # Source first: staging and production can hold the same table under
        # the same SQL (see connections.register_source).
        from trellum.data.connections import source_of
        keyed = f"{source_of(conn)}\x00{sql}"
        cache_key = hashlib.sha256(keyed.encode()).hexdigest()[:16]
        cache_path = _cache_dir() / f"{cache_key}.pkl"

        if cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
                if age < cache_ttl:
                    df = pd.read_pickle(cache_path)
                    dots = '.' * max(1, 24 - len(display_label))
                    row_count = f"{len(df):,}"
                    print(f"  -> {display_label} {dots} 0.0s (cache hit, {row_count} rows)", flush=True)
                    if is_debug:
                        print(f"    [DEBUG] Cache key: {cache_key}, age: {age:.0f}s, TTL: {cache_ttl}s", flush=True)
                    return df
            except Exception:
                cache_path.unlink(missing_ok=True)

    if is_debug:
        print(f"    [DEBUG] SQL: {sql.strip()[:120]}", flush=True)
        if cache_key:
            print(f"    [DEBUG] Cache key: {cache_key}, TTL: {cache_ttl}s", flush=True)

    # Print start marker BEFORE running the query so a hung/killed process
    # leaves a breadcrumb showing which query was active at termination.
    dots_start = '.' * max(1, 24 - len(display_label))
    print(f"  -> {display_label} {dots_start} (running...)", flush=True)

    start = time.time()
    with _query_semaphore:
        df = run_with_retry(conn, lambda raw: _execute_query(raw, sql), sql=sql)
        dec_cols = [c for c in df.columns if df[c].dtype == object
                    and df[c].dropna().apply(type).eq(decimal.Decimal).all()]
        if dec_cols:
            df[dec_cols] = df[dec_cols].astype(float)
    elapsed = time.time() - start

    row_count = f"{len(df):,}"
    dots = '.' * max(1, 24 - len(display_label))

    if use_cache and cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".pkl", dir=cache_path.parent,
            )
            os.close(fd)
            df.to_pickle(tmp_path)
            os.replace(tmp_path, cache_path)
        except Exception as exc:
            print(f"  CACHE WRITE ERROR: {exc}", flush=True)

    cache_status = "cache miss" if use_cache else "no cache"
    if is_debug:
        n_cols = len(df.columns)
        mem_mb = df.memory_usage(deep=True).sum() / (1024 * 1024)
        print(f"  -> {display_label} {dots} {elapsed:.1f}s ({cache_status}, {row_count} rows, {n_cols} cols, {mem_mb:.1f} MB)", flush=True)
    else:
        print(f"  -> {display_label} {dots} {elapsed:.1f}s ({cache_status}, {row_count} rows)", flush=True)

    return df


def read_source(
    source_type: str,
    path: str,
    *,
    sheet_name: str | int = 0,
    credentials_path: str | None = None,
    credentials_json: str | dict | None = None,
    credentials: dict | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    """Read a file-based data source into a DataFrame.

    Args:
        source_type: One of "excel", "csv", "parquet", "google_sheets", "onedrive".
        path: File path (local, s3://, gs://) or Google Sheet URL/ID.
        sheet_name: Sheet name or index (Excel, Google Sheets, OneDrive only).
        credentials_path: Path to service account JSON (Google Sheets only).
        credentials_json: Service account JSON inline, as a string or an
            already-parsed dict (Google Sheets only); wins over credentials_path.
        credentials: Full credentials dict from resolver (e.g. OneDrive Azure fields).
        **kwargs: Passed through to the underlying pandas read function.

    Returns:
        pandas DataFrame.
    """
    if source_type == "excel":
        return pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", **kwargs)
    elif source_type == "csv":
        return pd.read_csv(path, **kwargs)
    elif source_type == "parquet":
        return pd.read_parquet(path, **kwargs)
    elif source_type == "google_sheets":
        return _read_google_sheet(path, sheet_name, credentials_path, credentials_json)
    elif source_type == "onedrive":
        creds = credentials or {}
        return _read_onedrive(
            file_path=path,
            sheet_name=sheet_name,
            tenant_id=creds.get("tenant_id", ""),
            client_id=creds.get("client_id", ""),
            client_secret=creds.get("client_secret", ""),
            site_url=creds.get("site_url"),
        )
    else:
        raise ValueError(f"Unsupported source type: {source_type}")


def _read_google_sheet(
    sheet_id_or_url: str,
    sheet_name: str | int,
    credentials_path: str | None,
    credentials_json: str | dict | None = None,
) -> pd.DataFrame:
    """Read a Google Sheet into a DataFrame.

    Auth chain:
        1. Service account JSON inline (credentials_json set: a JSON string or
           an already-parsed dict) — nothing has to be on disk
        2. Service account JSON file (credentials_path set)
        3. Application Default Credentials (gcloud auth application-default login)
    """
    import gspread

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    if credentials_json:
        import json

        from google.oauth2.service_account import Credentials

        try:
            info = json.loads(credentials_json) if isinstance(
                credentials_json, str
            ) else credentials_json
        except ValueError as err:
            raise ValueError(f"credentials_json is not a JSON object: {err}") from None
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    elif credentials_path:
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    else:
        import google.auth

        creds, _ = google.auth.default(scopes=scopes)
    gc = gspread.authorize(creds)

    if sheet_id_or_url.startswith("http"):
        spreadsheet = gc.open_by_url(sheet_id_or_url)
    else:
        spreadsheet = gc.open_by_key(sheet_id_or_url)

    if isinstance(sheet_name, int):
        worksheet = spreadsheet.get_worksheet(sheet_name)
    else:
        worksheet = spreadsheet.worksheet(sheet_name)

    records = worksheet.get_all_records()
    return pd.DataFrame(records)


# ── MSAL token cache (persistent across calls) ──────────────

_msal_cache = None
_msal_cache_lock = threading.Lock()
_MSAL_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".bi-reports", "msal_cache.bin")


def _get_msal_cache():
    """Get or create a persistent MSAL token cache.

    Stores tokens in ~/.bi-reports/msal_cache.bin so device code flow
    only requires browser login once (tokens refresh for ~90 days).
    """
    global _msal_cache
    if _msal_cache is not None:
        return _msal_cache
    with _msal_cache_lock:
        if _msal_cache is not None:
            return _msal_cache
        import msal

        cache = msal.SerializableTokenCache()
        os.makedirs(os.path.dirname(_MSAL_CACHE_PATH), exist_ok=True)
        if os.path.isfile(_MSAL_CACHE_PATH):
            with open(_MSAL_CACHE_PATH) as f:
                cache.deserialize(f.read())
        _msal_cache = cache
        return cache


def _save_msal_cache():
    """Persist the MSAL token cache to disk."""
    if _msal_cache is None or not _msal_cache.has_state_changed:
        return
    os.makedirs(os.path.dirname(_MSAL_CACHE_PATH), exist_ok=True)
    with open(_MSAL_CACHE_PATH, "w", encoding="utf-8") as f:
        f.write(_msal_cache.serialize())


def _get_onedrive_token(tenant_id, client_id, client_secret):
    """Get a Graph API access token.

    Two distinct modes — production and local dev. They do NOT fall through
    to each other. If client credentials are provided but fail, that is a
    hard error (not silently retried with a different auth method).

    Production (client_id + client_secret + tenant_id all set):
        → Client credentials flow (app-only token, no user context).
        → Returns app-only token. /me/ endpoints will NOT work.
        → site_url is REQUIRED in this mode (no user = no default drive).

    Local dev (any credential missing):
        → Try Azure CLI token (az login) first.
        → Fall back to device code flow (one-time interactive browser login).
        → Returns delegated token. /me/ endpoints work.

    Returns:
        (access_token: str, is_app_only: bool)
    """
    import msal

    has_client_creds = bool(client_id and client_secret and tenant_id)

    if has_client_creds:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"],
        )
        if "access_token" in result:
            return result["access_token"], True

        raise RuntimeError(
            "OneDrive client credentials auth failed: "
            f"{result.get('error_description', result.get('error', 'unknown'))}",
        )

    import subprocess as _sp

    try:
        proc = _sp.run(
            [
                "az", "account", "get-access-token",
                "--resource", "https://graph.microsoft.com",
                "--query", "accessToken", "-o", "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip(), False
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass

    PUBLIC_CLIENT_ID = "04b07795-a710-4e51-b41c-6c2c7e2c87e7"
    authority = f"https://login.microsoftonline.com/{tenant_id or 'common'}"
    cache = _get_msal_cache()

    app = msal.PublicClientApplication(
        PUBLIC_CLIENT_ID,
        authority=authority,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(
            scopes=["Files.Read.All", "Sites.Read.All"],
            account=accounts[0],
        )
        if result and "access_token" in result:
            _save_msal_cache()
            return result["access_token"], False

    flow = app.initiate_device_flow(scopes=["Files.Read.All", "Sites.Read.All"])
    if "user_code" not in flow:
        raise RuntimeError(f"Device code flow failed: {flow.get('error_description')}")

    print("\n  OneDrive login required (one-time):")
    print(f"  → Open: {flow['verification_uri']}")
    print(f"  → Enter code: {flow['user_code']}\n")

    result = app.acquire_token_by_device_flow(flow)
    _save_msal_cache()

    if "access_token" not in result:
        raise RuntimeError(
            f"OneDrive auth failed: {result.get('error_description', 'unknown error')}",
        )
    return result["access_token"], False


def _read_onedrive(
    file_path: str,
    sheet_name: str | int,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    site_url: str | None = None,
) -> pd.DataFrame:
    """Download an Excel file from OneDrive/SharePoint and read into DataFrame."""
    import io
    from urllib.parse import quote as _url_quote

    import requests

    token, is_app_only = _get_onedrive_token(tenant_id, client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    encoded_path = _url_quote(file_path, safe="/")

    if site_url:
        site_api_url = f"https://graph.microsoft.com/v1.0/sites/{site_url}"
        site_resp = requests.get(site_api_url, headers=headers, timeout=30)
        site_resp.raise_for_status()
        site_id = site_resp.json()["id"]
        download_url = (
            f"https://graph.microsoft.com/v1.0/sites/{site_id}"
            f"/drive/root:{encoded_path}:/content"
        )
    elif is_app_only:
        raise ValueError(
            "OneDrive source requires 'site_url' when using client credentials "
            "(app-only tokens have no user context). Set site_url in report.yaml "
            "or add SITE_URL to the env var prefix in .env.",
        )
    else:
        download_url = (
            f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded_path}:/content"
        )

    resp = requests.get(download_url, headers=headers, timeout=120)
    resp.raise_for_status()

    return pd.read_excel(io.BytesIO(resp.content), sheet_name=sheet_name, engine="openpyxl")


#: Engines whose string literals treat ``\`` as an escape character (MySQL
#: under its default sql_mode, ClickHouse, Snowflake). ``''`` is also accepted
#: by all of them, so doubling both is correct in every mode.
_BACKSLASH_ESCAPING_TYPES = frozenset({"mysql", "clickhouse", "snowflake"})


def sql_dialect_for(source_type: str) -> str:
    """String-literal dialect for a datasource *type* (``"mysql"``,
    ``"bigquery"``, ...): ``"googlesql"``, ``"backslash"``, ``"bracket"``
    (standard literals with bracket identifiers), or ``"standard"``.

    Hosts that bind parameters without a live connection key on this;
    ``_sql_dialect`` is the same decision for a connection object.
    """
    if source_type in ("bigquery", "databricks"):
        return "googlesql"
    if source_type in _BACKSLASH_ESCAPING_TYPES:
        return "backslash"
    if source_type in ("sqlserver", "sqlite"):
        return "bracket"
    return "standard"


def _sql_dialect(conn: Any) -> str:
    r"""String-literal dialect for a live connection, keyed on the driver module.

    GoogleSQL (BigQuery) is the odd one out: a single quote inside a
    string literal is escaped with a backslash, and ``''`` -- the SQL
    standard's doubled quote, which every other supported engine accepts
    -- is a syntax error there. Databricks SQL escapes the same way, and
    is worse about ``''``: adjacent string literals coalesce, so
    ``'O''Brien'`` silently reads back as ``OBrien`` instead of failing.
    MySQL, ClickHouse and Snowflake accept
    ``''`` but also read ``\`` as an escape character, so both have to be
    doubled for them. See ``sql_dialect_for`` for the by-type form.
    """
    if isinstance(conn, ManagedConnection):
        return sql_dialect_for(conn._source_type)
    conn = unwrap(conn)
    type_name = type(conn).__module__ + "." + type(conn).__qualname__

    if "google.cloud.bigquery" in type_name or "databricks.sql" in type_name:
        return "googlesql"
    if any(m in type_name for m in ("pymysql", "MySQLdb", "clickhouse", "snowflake")):
        return "backslash"
    if any(m in type_name for m in ("pymssql", "pyodbc", "sqlite3")):
        return "bracket"
    return "standard"


def bind_params(sql: str, params: dict, dialect: str = "standard") -> str:
    r"""Replace ``:param_name`` placeholders with literal values.

    Handles string quoting for safety. Numeric types are inserted as-is.
    String escaping follows ``dialect`` (see ``_sql_dialect``): ``standard``
    doubles ``'``; ``backslash`` also doubles ``\``; ``googlesql``
    backslash-escapes both.

    Only placeholders in the original SQL's executable text are replaced;
    strings, identifiers, comments and PostgreSQL casts are left intact.
    Unknown placeholders remain unchanged for the driver to reject.
    """
    literals = {}
    for key, val in params.items():
        if isinstance(val, (int, float)):
            replacement = str(val)
        elif dialect == "googlesql":
            safe = str(val).replace("\\", "\\\\").replace("'", "\\'")
            replacement = f"'{safe}'"
        elif dialect == "backslash":
            safe = str(val).replace("\\", "\\\\").replace("'", "''")
            replacement = f"'{safe}'"
        else:
            safe = str(val).replace("'", "''")
            replacement = f"'{safe}'"
        literals[key] = replacement

    parts = []
    pos = 0
    for start, end, name in _sql_param_spans(sql, dialect):
        parts.extend((sql[pos:start], literals.get(name, sql[start:end])))
        pos = end
    parts.append(sql[pos:])
    return "".join(parts)

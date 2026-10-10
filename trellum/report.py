"""Core report classes: BaseReport and ReportContext."""

from __future__ import annotations

import os
import re
from datetime import timedelta
from typing import Any

import yaml

from trellum.report_config import validate_content_config
from trellum.themes import DEFAULT_THEME, Theme

#: Live-query ids travel into ``_live_queries.json``, data.json and the host's
#: request body, so they are slugs, not prose.
_LIVE_QUERY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_WINDOWS_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _validate_output_filename(filename: str) -> str:
    """Require one portable filename so report artifacts stay together."""
    windows_stem = (
        filename.split(".", 1)[0].rstrip(" .").upper()
        if isinstance(filename, str)
        else ""
    )
    if (
        not isinstance(filename, str)
        or filename in ("", ".", "..")
        or filename != filename.rstrip(" .")
        or os.path.isabs(filename)
        or any(char in filename for char in '<>:"/\\|?*')
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
        or windows_stem in _WINDOWS_DEVICE_NAMES
    ):
        raise ValueError(
            f"Output filename {filename!r} is invalid: use a single path-safe name"
        )
    return filename


class ReportContext:
    """Provides date helpers, data connections, and section management to reports.

    Instantiated by the runner and passed to ``BaseReport.generate(ctx)``.
    """

    def __init__(
        self,
        config: dict,
        slug: str,
        output_dir: str,
        theme: Theme | None = None,
    ):
        self._config = config
        self._slug = slug
        self._output_dir = output_dir
        self._theme = theme or DEFAULT_THEME
        self._sections: list[dict[str, Any]] = []
        self._header_meta: dict[str, Any] = {}
        self._connections: dict[str, Any] = {}
        self._custom_html: str | None = None
        self._custom_data: dict | None = None
        self._scopes: dict[str, list[dict[str, Any]]] = {}
        self._current_scope: str | None = None
        self._scope_labels: dict[str, str] = {}
        self._timings: dict[str, float] = {}
        self._live_queries: dict[str, dict[str, Any]] = {}
        self._extra_validation_checks: list[dict[str, Any]] = []
        self.debug: bool = False

        # Honours FW_NOW so a run can be made reproducible; falls back to the
        # real clock when unset.
        from trellum.project import get_now_utc

        now = get_now_utc()
        self._now_utc = now

        # Date strings (UTC)
        self.today: str = now.strftime("%Y-%m-%d")
        self.yesterday: str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        self.two_days_ago: str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        self.week_ago: str = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        self.now_utc: str = now.isoformat()

    # ── Timing ────────────────────────────────────────────────

    def mark(self, label: str, elapsed_s: float) -> None:
        """Record a timing measurement for profiling."""
        self._timings[label] = round(elapsed_s, 3)

    # ── Public properties ────────────────────────────────────

    @property
    def config(self) -> dict:
        return self._config

    @property
    def slug(self) -> str:
        return self._slug

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def output_dir(self) -> str:
        return self._output_dir

    @property
    def sections(self) -> list[dict[str, Any]]:
        return self._sections

    @property
    def header_meta(self) -> dict[str, Any]:
        return self._header_meta

    # ── Data access ──────────────────────────────────────────

    def _resolve_source(self, name: str) -> dict:
        """Find a data source by name, supporting both dict and string entries.

        Resolution order:
        1. Dict entry in report.yaml ``data_sources`` with ``name: <name>``
        2. String entry in report.yaml matching ``<name>``
        3. Lookup in ``data-sources/config.yaml``
        """
        from trellum.data.datasource_config import get_datasource

        sources = self._config.get("data_sources", [])
        for s in sources:
            if isinstance(s, dict) and s.get("name") == name:
                return s
            if isinstance(s, str) and s == name:
                central = get_datasource(s)
                if central is not None:
                    return central

        # Not listed in report.yaml — try central config directly
        central = get_datasource(name)
        if central is not None:
            return central

        raise ValueError(
            f"Data source '{name}' not found in report.yaml or data-sources/config.yaml"
        )

    def get_connection(self, name: str):
        """Get (or create) a database connection by name.

        Resolves credentials through the registered resolvers (see
        ``trellum.data.resolvers``) using the data_sources section in
        report.yaml or central config.
        """
        if name in self._connections:
            return self._connections[name]

        from trellum.data.connections import register_source, resolve_connection
        conn = resolve_connection(name, self._config.get("data_sources", []))
        # Registered here rather than at each call site: a generator calling
        # query_df(conn, ...) directly is the documented pattern, so the name
        # has to travel with the connection or the cache key loses it.
        register_source(conn, name)
        self._connections[name] = conn
        return conn

    def read_source(self, name: str, **kwargs) -> Any:
        """Read a file-based data source declared in report.yaml or central config.

        Args:
            name: The data source name.
            **kwargs: Passed to trellum.data.read_source (e.g. sheet_name).

        Returns:
            pandas DataFrame.
        """
        import pandas as pd

        from trellum.data.query import read_source as _read_source
        from trellum.data.resolvers import resolve_credentials
        from trellum.project import get_project_root

        source = self._resolve_source(name)
        source_type = source.get("type", "")

        # Handle type: file sources (read directly from disk)
        if source_type == "file":
            rel_path = source.get("path", "")
            if not rel_path:
                raise ValueError(
                    f"Data source '{name}' has type 'file' but no 'path' configured."
                )
            full_path = os.path.join(get_project_root(), rel_path)
            if not os.path.isfile(full_path):
                raise FileNotFoundError(
                    f"Data source '{name}' file not found: {full_path}"
                )
            ext = os.path.splitext(full_path)[1].lower()
            file_size = os.path.getsize(full_path)
            file_mtime = os.path.getmtime(full_path)
            import datetime as _dt
            # fromtimestamp(..., tz=utc) rather than utcfromtimestamp(), which
            # is deprecated in 3.12 and returns a naive datetime that only
            # looks like UTC.
            mtime_str = _dt.datetime.fromtimestamp(
                file_mtime, tz=_dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            sheet_info = f" sheet={kwargs.get('sheet_name', 0)!r}" if ext in (".xlsx", ".xls") else ""
            print(
                f"  [read_source] {name}{sheet_info}"
                f" — {full_path}"
                f" ({file_size:,} bytes, modified {mtime_str})",
                flush=True,
            )
            if ext in (".xlsx", ".xls"):
                return pd.read_excel(
                    full_path,
                    sheet_name=kwargs.pop("sheet_name", 0),
                    engine="openpyxl",
                    **kwargs,
                )
            elif ext == ".csv":
                return pd.read_csv(full_path, **kwargs)
            elif ext == ".parquet":
                return pd.read_parquet(full_path, **kwargs)
            else:
                raise ValueError(
                    f"Unsupported file extension '{ext}' for data source '{name}'."
                )

        cred_info = resolve_credentials(source) or {}

        if source.get("site_url"):
            cred_info["site_url"] = source["site_url"]

        path = cred_info.get("path") or source.get("path")
        if not path:
            raise ValueError(
                f"Data source '{name}' has type '{source_type}' but no path was resolved.",
            )

        return _read_source(
            source_type=source_type,
            path=path,
            credentials_path=cred_info.get("credentials_path"),
            credentials_json=cred_info.get("credentials_json"),
            credentials=cred_info,
            **kwargs,
        )

    def metrics(self, ids: list[str], by: list[str] | None = None, window=None):
        """Rows for metrics.yaml metrics, from the dataset they bind to.

        Returns a long-format DataFrame -- the dataset's time column, the
        ``by`` dimensions, one column per measure the metrics need -- so a
        bare ``{"metric": id}`` claim on a ``KpiRow`` over it computes by
        construction. One call covers one dataset; ids spanning two raise
        with the split spelled out. ``by`` must be declared dimensions of
        that dataset. ``window`` is a number of days back from ``ctx.today``
        or an explicit ``(start, end)``; default is the dataset's
        ``lookback``, else 90 days. Identical requests within one build cost
        one fetch.
        """
        from trellum.datasets import metrics_frame

        return metrics_frame(self, list(ids), by, window)

    def close_connections(self):
        from trellum.data.connections import forget_source
        for conn in self._connections.values():
            forget_source(conn)
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()

    # ── Report building ──────────────────────────────────────

    def add_section(
        self, title: str, components: list, *,
        collapsible: bool = False, default_collapsed: bool = False,
    ) -> None:
        """Add a named section with a list of component instances.

        Args:
            title: Section heading. Empty string for unwrapped sections
                (e.g. DataSource + FilterBar).
            components: List of component instances.
            collapsible: If True, section can be collapsed/expanded by
                clicking the title. Requires a non-empty ``title``.
            default_collapsed: If True (and ``collapsible`` is True), the
                section starts collapsed on initial load.

        If ``set_scope()`` has been called, sections are stored under
        the active scope; otherwise they go to the flat list.
        """
        section = {"title": title, "components": components,
                   "collapsible": collapsible,
                   "default_collapsed": default_collapsed}
        if self._current_scope is not None:
            self._scopes[self._current_scope].append(section)
        else:
            self._sections.append(section)

    def set_header(self, subtitle: str | None = None, meta: dict | None = None) -> None:
        """Set the subtitle and optional labeled values shown below the header."""
        if subtitle:
            self._header_meta["subtitle"] = subtitle
        if meta:
            self._header_meta.update(meta)

    def add_validation_check(
        self,
        check_id: str,
        level: str,
        message: str,
        *,
        component: str = "",
        section: str = "",
        dataset_id: str = "",
    ) -> None:
        """Register a data-quality check merged into ``_validation.json`` after generate."""
        self._extra_validation_checks.append({
            "id": check_id,
            "level": level,
            "message": message,
            "component": component,
            "section": section,
            "dataset_id": dataset_id,
        })

    @property
    def extra_validation_checks(self) -> list[dict[str, Any]]:
        return self._extra_validation_checks

    # ── Live queries ─────────────────────────────────────────

    def declare_live_query(
        self,
        query_id: str,
        sql: str,
        *,
        datasource: str,
        params: list[dict],
        snapshot_params: dict | None = None,
    ):
        """Declare a parameterized query that a serving host may run on demand.

        The built artifact stays a complete snapshot: when ``snapshot_params``
        is given the query runs ONCE now, through the normal data layer, and
        the returned DataFrame is what the declaring component compiles in.
        The SQL itself is written to ``_live_queries.json`` for the host —
        it never reaches data.json, index.html, or anything else a browser
        fetches, so a published report leaks nothing about the warehouse.

        The live query becomes a filter-engine DATASET, not a bespoke
        control: pass the returned snapshot to
        ``trellum.components.filterable.LiveDataSource(id, query=query_id,
        df=snapshot)``, then bind an ordinary ``FilterBar(id, snapshot,
        filters=[{"type": "toggle", "param": "event_type", ...}, ...])`` to
        it -- each filter spec names the declared param it drives. Any
        chart/table/KpiRow built with ``dataset_id=id`` reacts to live
        commits exactly like it reacts to a normal filter change. See
        ``LiveDataSource``/``FilterBar`` and
        ``trellum/demo/reports/user-event-log`` for a worked example.

        Args:
            query_id: Slug (``^[a-z0-9][a-z0-9_-]*$``) a ``LiveDataSource``
                references via its ``query`` argument.
            sql: SQL with ``:name`` placeholders, one per declared param.
            datasource: Name of a SQL data source (sqlite, duckdb, postgres,
                ...). File/API sources cannot serve live lookups — the
                ``live-query-source-not-sql`` validation check enforces this.
            params: List of ``{"name", "type", "required"?, "values"?,
                "max_length"?}`` dicts. ``type`` is one of
                int|float|str|date|enum; enum carries ``values``. Schema
                problems surface as the ``live-query-param-schema``
                validation FAIL rather than raising here.
            snapshot_params: Values to bake the static snapshot with.
                Effectively mandatory: the ``LiveDataSource`` IS the initial
                content now, so omitting this escalated from a WARN to the
                ``live-query-no-snapshot`` FAIL.

        Returns:
            The snapshot DataFrame when ``snapshot_params`` was given,
            else ``None``.
        """
        if not isinstance(query_id, str) or not _LIVE_QUERY_ID.match(query_id):
            raise ValueError(
                f"Live query id {query_id!r} is not a valid slug "
                f"(^[a-z0-9][a-z0-9_-]*$). It travels into artifacts and "
                f"request bodies, so it has to be machine-safe."
            )
        if query_id in self._live_queries:
            raise ValueError(
                f"Live query '{query_id}' is declared twice. The second "
                f"declaration would silently replace the first in "
                f"_live_queries.json — rename one."
            )

        norm_params: list[dict[str, Any]] = []
        names: set[str] = set()
        for p in params or []:
            if not isinstance(p, dict):
                # Recorded (not raised) so the live-query-param-schema
                # validation check can name the problem with context.
                norm_params.append({"name": "", "type": ""})
                continue
            entry: dict[str, Any] = {
                "name": str(p.get("name", "")),
                "type": str(p.get("type", "")),
                "required": bool(p.get("required", True)),
            }
            if "values" in p:
                entry["values"] = list(p["values"] or [])
            if "max_length" in p:
                entry["max_length"] = int(p["max_length"])
            norm_params.append(entry)
            names.add(entry["name"])

        snapshot_df = None
        if snapshot_params is not None:
            unknown = sorted(set(snapshot_params) - names)
            if unknown:
                raise ValueError(
                    f"Live query '{query_id}': snapshot_params has keys not "
                    f"declared in params: {unknown}. Declared: {sorted(names)}"
                )
            missing = sorted(
                p["name"] for p in norm_params
                if p.get("required") and p["name"] not in snapshot_params
            )
            if missing:
                raise ValueError(
                    f"Live query '{query_id}': snapshot_params is missing "
                    f"required param(s) {missing} — the snapshot execution "
                    f"needs a value for every required param."
                )
            # Late import + module-attribute call so the mock-data test
            # harness (which patches trellum.data.query_df and
            # ctx.get_connection) intercepts the snapshot execution exactly
            # like any other report query.
            import trellum.data as _data
            conn = self.get_connection(datasource)
            snapshot_df = _data.query_df(
                conn, sql, params=dict(snapshot_params),
                label=f"LIVE {query_id}"[:30],
            )

        self._live_queries[query_id] = {
            "sql": sql,
            "datasource": datasource,
            "params": norm_params,
            "snapshot_params": dict(snapshot_params or {}),
            "has_snapshot": snapshot_params is not None,
        }
        return snapshot_df

    @property
    def live_queries(self) -> dict[str, dict[str, Any]]:
        """Declared live queries: ``{query_id: {sql, datasource, params, ...}}``."""
        return self._live_queries

    def set_header_component(self, component) -> None:
        """Override the default ``ReportHeader`` with a custom component.

        The component must implement ``render_html()``, ``css()``, and
        ``client_js()`` (i.e. extend ``Component`` or ``ReportHeader``).
        """
        self._header_component = component

    # ── Multi-scope (opt-in for multi-game dashboards) ───────

    def set_scope(self, name: str, label: str | None = None) -> None:
        """Activate a named scope.  Subsequent ``add_section()`` calls are
        stored under this scope.  Call once per game/view (one scope per tenant or view).

        If ``set_scope`` is never called, the report stays flat (single scope)
        and data.json keeps its ``components`` key at the top level.
        """
        self._current_scope = name
        if name not in self._scopes:
            self._scopes[name] = []
        if label:
            self._scope_labels[name] = label
        elif name not in self._scope_labels:
            self._scope_labels[name] = name

    @property
    def scopes(self) -> dict[str, list[dict[str, Any]]]:
        return self._scopes

    @property
    def has_scopes(self) -> bool:
        return len(self._scopes) > 0

    @property
    def default_scope(self) -> str | None:
        return next(iter(self._scopes)) if self._scopes else None

    @property
    def scope_labels(self) -> dict[str, str]:
        return self._scope_labels

    @property
    def stats(self) -> dict[str, int]:
        """Return section and component counts for structured output."""
        all_sections = list(self._sections)
        for scope_sections in self._scopes.values():
            all_sections.extend(scope_sections)
        n_sections = len(all_sections)
        n_components = sum(len(s.get('components', [])) for s in all_sections)
        return {'sections': n_sections, 'components': n_components}

    # ── Custom HTML (legacy / complex dashboards) ─────────

    def set_custom_output(
        self,
        html: str,
        data: dict | None = None,
        *,
        html_filename: str = "index.html",
        data_filename: str = "data.json",
    ) -> None:
        """Set custom HTML output for complex dashboards that bypass the component tree.

        Use this for dashboards with their own JS rendering engine.
        Output names must be portable top-level filenames; the framework writes
        _meta.json and its licence file alongside them.
        """
        html_filename = _validate_output_filename(html_filename)
        data_filename = _validate_output_filename(data_filename)
        self._custom_html = html
        self._custom_data = data
        self._custom_html_filename = html_filename
        self._custom_data_filename = data_filename

    @property
    def custom_html(self) -> str | None:
        return self._custom_html

    @property
    def custom_data(self) -> dict | None:
        return self._custom_data

    @property
    def custom_html_filename(self) -> str:
        return getattr(self, "_custom_html_filename", "index.html")

    @property
    def custom_data_filename(self) -> str:
        return getattr(self, "_custom_data_filename", "data.json")


class BaseReport:
    """Base class for all reports. Subclass this and implement generate().

    COMPONENT RULES — ALWAYS use framework components:
        LineChart, BarChart, StackedBar, AreaChart, ComboChart,
        DoughnutChart, HeatmapChart, FunnelChart, TreemapChart, ScatterChart,
        KpiRow, DataTable, ComparisonTable, PivotTable,
        DataSource + FilterBar for interactive filtering.

    Do NOT write custom HTML/CSS/Chart.js unless no component can express it.
    Every component MUST have dataset_id= when DataSource exists.
    See AGENTS.md for the full component mapping table.
    """

    def generate(self, ctx: ReportContext) -> None:
        """Override this method. Fetch data, build components, add sections.

        Args:
            ctx: ReportContext providing connections, dates, and section management.
        """
        raise NotImplementedError("Subclasses must implement generate()")

    @classmethod
    def load_config(cls, report_dir: str) -> dict:
        """Load and return the report.yaml from a report directory."""
        yaml_path = os.path.join(report_dir, "report.yaml")
        if not os.path.exists(yaml_path):
            raise FileNotFoundError(f"No report.yaml found in {report_dir}")
        # UTF-8 explicitly, or Windows decodes non-ASCII names via the ANSI
        # code page and the header renders mojibake.
        with open(yaml_path, encoding="utf-8") as f:
            return validate_content_config(yaml.safe_load(f))

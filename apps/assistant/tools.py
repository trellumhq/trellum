"""AI assistant tools — strictly read-only, scoped to ONE studio.

Ported from the legacy single-tenant portal, which read the single
project root the portal process was started in; here every tool is bound to
the studio the conversation belongs to (:class:`AssistantToolbox`), so the
assistant can only ever see output the requesting user already has viewer
access to.

Five read tools:
  list_reports        the studio's report index
  get_report_details  one report's charts, datasets, validation, link
  query_report_data   filter/aggregate a report's already-built data.json
  read_doc            read report/project docs inside the studio (allowlist)
  check_repo_changes  what the portal would publish from git (developers)

Six actions (``mutates: True``) that never run inside a turn: calling one
writes a ``ProposedAction`` for the user to approve in the panel
(:mod:`apps.assistant.actions`). Every catalogue entry carries ``mutates``
and the ``role`` it needs; :meth:`AssistantToolbox.allowed` is the one
predicate that decides what a caller sees and may call.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from apps.assistant.actions import rank
from apps.core import roles

_DATA_DISPLAY_CAP = 50
_MAX_DATA_BYTES = 80 * 1024 * 1024  # refuse to load data.json bigger than this
_READ_MAX_CHARS = 20_000
#: How many parsed data files one turn may hold at once. Parsing costs roughly
#: 12 ms/MB and the same file is wanted by the catalog, by get_report_details
#: and by every query_report_data call in the turn, so caching pays for itself
#: on the second read. Four is enough for a question that compares a couple of
#: reports without letting a thirty-step tool loop pin every report it touched.
_PARSE_CACHE_ENTRIES = 4

#: Paths inside the studio project the assistant may read.
#: Paths inside the studio project the assistant may read on demand.
#:
#: This is a permission boundary, not an index. On a git-synced studio the
#: sparse checkout only ever materializes ``reports/`` and the named
#: project-root files, so most of what is permitted here simply will not
#: exist — which is fine (``read_doc`` says "file not found") and is why
#: nothing is force-fed into the prompt from these paths.
READ_ALLOWLIST = (
    "reports",
    "assistant.md",
    "project_context",
    ".cursor/rules",
    "README.md",
)

_SLUG_RE = re.compile(r"[A-Za-z0-9_\-]+")


# ---------------------------------------------------------------------------
# Tool schemas (unchanged from the legacy portal — the prompt depends on them)
# ---------------------------------------------------------------------------

#: The fields of an alert rule, shared by create_alert and update_alert.
_ALERT_PROPERTIES = {
    "name": {"type": "string", "description": "A short label for the rule, e.g. 'Paid revenue watch'"},
    "report": {"type": "string", "description": "Slug of the report to watch (one report per rule)"},
    "instructions": {
        "type": "string",
        "description": (
            "Plain language for the evaluator: what to look at and when to speak up. "
            "Carry the conversation's context into it -- the report, the filters, the "
            "dimension and the numbers just discussed -- e.g. 'Paid revenue in DE was "
            "12.4k on 2026-09-07; tell me if it drops more than 10% week on week, or if "
            "organic falls while paid holds.' Empty means 'tell me if anything looks off'."
        ),
    },
    "trigger": {
        "type": "string", "enum": ["after_build", "schedule"],
        "description": (
            "after_build (default): evaluate after every successful build of the report, "
            "which is when its data changes. schedule: on the cadence fields below."
        ),
    },
    "freq": {
        "type": "string", "enum": ["hourly", "daily", "weekdays", "weekly", "monthly"],
        "description": "Cadence when trigger is schedule (default daily)",
    },
    "send_hour": {"type": "integer", "description": "Hour 0-23 for the schedule (default 8; ignored for hourly)"},
    "send_minute": {"type": "integer", "description": "Minute 0-59 for the schedule (default 0)"},
    "weekday": {"type": "integer", "description": "0 = Monday … 6 = Sunday, for weekly"},
    "month_day": {"type": "integer", "description": "1-28, for monthly"},
    "timezone": {"type": "string", "description": "IANA zone the schedule is read in, e.g. Europe/Amsterdam (default UTC)"},
    "recipient_roles": {
        "type": "array", "items": {"type": "string", "enum": ["admins", "developers", "everyone"]},
        "description": "Role chips: every current member of this studio with that role",
    },
    "recipient_emails": {
        "type": "array", "items": {"type": "string"},
        "description": "Individual recipients by email. Each must already be a member of this studio; an unknown address fails the call.",
    },
    "cooldown_hours": {
        "type": "integer",
        "description": "After the alert fires the rule stays quiet for this long (default 24)",
    },
}

TOOL_SCHEMAS = [
    {
        "name": "list_reports",
        "description": (
            "List every report available in this studio: slug, name, category, "
            "description, last run status and the link path. "
            "USUALLY UNNECESSARY: the DATA CATALOG in your instructions "
            "already contains all of this plus dataset columns and date "
            "coverage — route from there and go straight to "
            "query_report_data. Only call this if the catalog seems missing "
            "or stale. Link a report in your reply as [Name](<link>) using the "
            "absolute link the tool returns (/s/<org>/<studio>/r/<slug>/)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Optional category filter (exact match)"},
            },
        },
        "mutates": False,
        "role": roles.VIEWER,
    },
    {
        "name": "get_report_details",
        "description": (
            "Inspect one report: its sections, every chart (title, type, which "
            "dataset it reads), every dataset (id, row count, columns), "
            "validation state and last run. Use this to answer 'what does "
            "report X show' or to find which dataset_id holds the data you "
            "need before calling query_report_data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Report slug, e.g. 'payer-churn-analysis'"},
            },
            "required": ["slug"],
        },
        "mutates": False,
        "role": roles.VIEWER,
    },
    {
        "name": "query_report_data",
        "description": (
            "Read actual numbers from a report's already-built output (no "
            "database hit, instant). Loads the named dataset from the report's "
            "data.json, optionally filters rows, then aggregates. Prefer this "
            "-- the only data the assistant reads is what reports have already published. "
            "Returns a markdown table (max 50 rows). Note: for chunked "
            "datasets only the inline (most recent) months are available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Report slug"},
                "dataset_id": {
                    "type": "string",
                    "description": (
                        "Dataset id from the catalog: a DataSource id "
                        "(e.g. 'payers_weekly') or a dotted path into a "
                        "custom dashboard data block (e.g. 'pulse.hourly_rev_gop3')"
                    ),
                },
                "group_by": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Columns to group by. Omit for overall totals.",
                },
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "agg": {"type": "string",
                                    "enum": ["sum", "mean", "count", "min", "max", "nunique"]},
                        },
                        "required": ["column"],
                    },
                    "description": "Metric columns to aggregate (default agg: sum). Omit to preview raw rows.",
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "Optional equality filters: {column: [allowed values]}. "
                        "For date ranges use date_from/date_to."
                    ),
                },
                "date_column": {"type": "string", "description": "Column for date_from/date_to filtering"},
                "date_from": {"type": "string", "description": "Inclusive lower bound, YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "Inclusive upper bound, YYYY-MM-DD"},
                "sort_by": {"type": "string", "description": "Column to sort the result by (descending)"},
                "limit": {"type": "integer", "description": "Max rows returned (default 50)"},
            },
            "required": ["slug", "dataset_id"],
        },
        "mutates": False,
        "role": roles.VIEWER,
    },
    {
        "name": "read_doc",
        "description": (
            "Read a documentation or schema file from this studio's project "
            "(e.g. project_context/database_schema.md, a report's report.yaml "
            "or queries.py). Use to check table/column definitions before "
            "writing SQL, or to explain how a report computes a metric."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the studio project root"},
            },
            "required": ["path"],
        },
        "mutates": False,
        "role": roles.VIEWER,
    },
    {
        "name": "check_repo_changes",
        "description": (
            "What the portal would publish from this studio's git repository: "
            "the remote head it last saw and when, the reports and data-source "
            "declarations added, changed or removed since the last publish, "
            "the last publish and the last error. Reads what the last fetch "
            "recorded; it does not fetch. Call it before proposing "
            "publish_repo_changes, and to say how fresh that picture is."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "mutates": False,
        "role": roles.DEVELOPER,
    },
    # ── Actions: proposed, never executed here ──────────────────────────
    {
        "name": "configure_data_source",
        "description": (
            "Propose binding new credentials to a data source the studio's "
            "repository declares (one whose reports are blocked on missing or "
            "failing credentials). The user types the secrets -- user name, "
            "password, key -- into the approval card; NEVER ask for them in "
            "chat and never pass them as arguments. Nothing changes until the "
            "user approves; after approval the portal saves the credentials, "
            "tests the connection and queues the reports that were waiting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The data source name as declared (the catalog and get_report_details use it)"},
                "org_level": {
                    "type": "boolean",
                    "description": (
                        "Share the credentials with every studio in the organization "
                        "(organization admins only). Default false: this studio only."
                    ),
                },
            },
            "required": ["name"],
        },
        "mutates": True,
        "role": roles.ADMIN,
    },
    {
        "name": "test_data_source",
        "description": (
            "Propose re-running the connection test of a data source that "
            "already has credentials, and report the outcome. Useful after a "
            "warehouse outage or when a report says its source is failing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The data source name as declared"},
            },
            "required": ["name"],
        },
        "mutates": True,
        "role": roles.DEVELOPER,
    },
    {
        "name": "run_report",
        "description": (
            "Propose building a report now instead of waiting for its "
            "schedule. Spends runner time and may query the warehouse, so it "
            "always waits for approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Report slug"},
            },
            "required": ["slug"],
        },
        "mutates": True,
        "role": roles.DEVELOPER,
    },
    {
        "name": "publish_repo_changes",
        "description": (
            "Propose publishing the repository changes the studio has fetched "
            "but not yet applied (manual publish mode). Optionally pin the "
            "reviewed remote commit and say whether changed reports rebuild."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "The remote commit sha that was reviewed; the publish is dropped if the branch moved past it"},
                "rebuild": {"type": "boolean", "description": "Rebuild the changed reports after publishing (default: the studio's setting)"},
            },
        },
        "mutates": True,
        "role": roles.DEVELOPER,
    },
    {
        "name": "create_alert",
        "description": (
            "Propose an alert rule. The portal re-reads the report on the trigger, "
            "judges the instructions against the data and emails the recipients only "
            "when there is something to say. Use it when the user wants to be told "
            "about something later -- 'watch this for me', 'tell me when X moves'. "
            "Put the specifics you just discussed into instructions; the user sees "
            "the rule as it will be saved on the approval card."
        ),
        "input_schema": {
            "type": "object",
            "properties": _ALERT_PROPERTIES,
            "required": ["name", "report", "instructions"],
        },
        "mutates": True,
        "role": roles.DEVELOPER,
    },
    {
        "name": "update_alert",
        "description": (
            "Propose changing an existing alert rule -- its id comes from a "
            "create_alert result or the Alerts page. Pass only the fields to change; "
            "the rest stay as they are."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"alert_id": {"type": "integer", "description": "The rule's id"}, **_ALERT_PROPERTIES},
            "required": ["alert_id"],
        },
        "mutates": True,
        "role": roles.DEVELOPER,
    },
]

_MUTATING = {s["name"] for s in TOOL_SCHEMAS if s["mutates"]}
_BY_NAME = {s["name"]: s for s in TOOL_SCHEMAS}
#: The keys the model providers accept; ``mutates`` / ``role`` are ours.
_SCHEMA_KEYS = ("name", "description", "input_schema")


def _cell(v) -> str:
    if v is None:
        return "NULL"
    s = str(v).replace("|", "\\|").replace("\n", " ")
    if len(s) > 80:
        s = s[:77] + "…"
    return s


def format_df_markdown(df, max_rows: int) -> str:
    if len(df) == 0:
        return "(query returned 0 rows)"
    show = df.head(max_rows)
    cols = list(show.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = ["| " + " | ".join(_cell(r[c]) for c in cols) + " |" for _, r in show.iterrows()]
    body = "\n".join([header, sep] + rows)
    if len(df) > max_rows:
        body += f"\n\n…({len(df) - max_rows} more rows not shown)"
    return body


# ---------------------------------------------------------------------------
# The studio-scoped toolbox
# ---------------------------------------------------------------------------

class AssistantToolbox:
    """Read-only tool implementations bound to one studio.

    A toolbox lives for exactly one turn, which is what makes its caches safe:
    they are bounded by the request rather than the process, and they cannot
    serve a stale read into a later turn.
    """

    def __init__(
        self, studio, share_report_source: bool = True, *,
        role: str = roles.VIEWER, may_write: bool = False, session=None,
        extra_tools=(), actor=None, scope: str = "full", report=None,
    ):
        self.studio = studio
        #: OrgAssistantConfig.share_report_source -- may read_doc send report
        #: source (report.yaml, queries) to the model provider?
        self.share_report_source = share_report_source
        self.actor = actor
        self.scope = scope
        self.report_id = getattr(report, "pk", None)
        self.report_slug = getattr(report, "slug", None)
        # Tools run in a worker thread with their own DB connection. Keep only
        # immutable ids/slugs here; access is re-read immediately before each
        # dispatch on the request thread.
        #: The caller's effective role: viewer | developer | admin | org_admin
        #: (an org admin passes "org_admin"; see actions.rank).
        self.role = role
        #: OrgAssistantConfig.actions_enabled. Without it no mutating tool is
        #: listed or callable, whatever the role.
        self.may_write = may_write
        #: The conversation a proposal belongs to; None where nothing can be
        #: proposed (the suggestions probe).
        self.session = session
        #: The proposal the LAST execute() created (its card payload), else
        #: None -- the same side channel as ``provenance``.
        self.proposal: dict | None = None
        #: ``[(schema, handler), ...]`` a caller adds for one turn only, e.g.
        #: the alert evaluator's ``decide``. A handler takes the args dict and
        #: returns the result string; its result is not framed as data.
        self._extra: dict[str, tuple[dict, Callable[[dict], str]]] = {
            schema["name"]: (schema, handler) for schema, handler in extra_tools
        }
        self.base_path = f"/s/{studio.org.slug}/{studio.slug}/"
        #: Structured provenance of the LAST execute() call (contract §D.3),
        #: or None for tools that have none. ponytail: a side channel rather
        #: than changing execute()'s str return, which every caller relies on.
        self.provenance: dict | None = None
        self._reports: list[dict] | None = None
        self._parsed: OrderedDict[tuple, dict] = OrderedDict()
        self._files: dict[str, list[Path]] = {}
        self._access_prechecked = False
        self._executing_prechecked = False

    @property
    def is_report_bound(self) -> bool:
        return self.scope == "report"

    def _access_error(self, slug: str | None = None) -> str | None:
        """Re-resolve access at each data boundary so revocation takes effect."""
        if self._executing_prechecked:
            return None
        if self.actor is None:  # Trusted internal callers and existing unit tests.
            if self.is_report_bound and (not self.report_id or slug not in (None, self.report_slug)):
                return "This conversation cannot access that report."
            return None

        from django.contrib.auth import get_user_model

        if not get_user_model().objects.filter(pk=self.actor.pk, is_active=True).exists():
            return "This account can no longer access the assistant."

        from apps.core.permissions import effective_roles
        from apps.core.report_access import can_view_report, selected_report_access_block_reason

        permissions = effective_roles(self.actor, self.studio.org)
        if self.scope == "full":
            if permissions.has_full_studio_visibility(self.studio):
                return None
            return "This conversation no longer has full studio access."
        if not self.is_report_bound or not self.report_id:
            return "This conversation has no accessible report."

        from apps.reports.models import Report

        report = Report.objects.filter(
            pk=self.report_id, studio=self.studio, present_in_scan=True,
        ).first()
        if report is None or report.slug != self.report_slug:
            return "This conversation's report is no longer available."
        if slug is not None and slug != report.slug:
            return "This conversation cannot access that report."
        if (
            not permissions.has_full_studio_visibility(self.studio)
            and selected_report_access_block_reason()
        ):
            return "Selected report access is temporarily unavailable."
        if not can_view_report(self.actor, report):
            return "This conversation's report is no longer available."
        return None

    def link(self, slug: str, query: str = "") -> str:
        """Absolute portal path of a report, optionally with a filter query."""
        return f"{self.base_path}r/{slug}/" + (f"?{query}" if query else "")

    def report_name(self, slug: str) -> str:
        for r in self.reports():
            if r.get("slug") == slug:
                return r.get("name") or slug
        return slug

    def allowed(self, schema: dict) -> bool:
        """May this caller see and call the tool? Every tool needs its role
        (viewer for all reads but ``check_repo_changes``); a mutating one also
        needs actions on. Checked when the catalogue is listed and again when
        a call arrives."""
        if rank(self.role) < rank(schema.get("role", roles.VIEWER)):
            return False
        if self.is_report_bound and schema.get("name") == "check_repo_changes":
            return False
        if not schema.get("mutates"):
            return True
        if self.is_report_bound and schema.get("name") != "run_report":
            return False
        return self.may_write

    @property
    def schemas(self) -> list[dict]:
        return [
            {k: s[k] for k in _SCHEMA_KEYS}
            for s in TOOL_SCHEMAS + [schema for schema, _ in self._extra.values()]
            if self.allowed(s)
        ]

    # ── one parse per file per turn ──────────────────────────────────────
    def read_data(self, f: Path) -> dict | None:
        """Parsed ``data.json``, or None if it is unreadable or too large.

        Keyed on size and mtime as well as path, so a rebuild landing
        mid-turn is picked up rather than served from the previous read.

        **The returned dict is shared** with every other caller this turn.
        Treat it as read-only; nothing here mutates it, and the dataframes
        built from it are copies.
        """
        if self.actor is not None and self._access_error():
            return None
        if self.is_report_bound:
            try:
                f.resolve().relative_to(self._report_output(self.report_slug).resolve())
            except (ValueError, OSError, TypeError):
                return None
        try:
            st = f.stat()
        except OSError:
            return None
        if st.st_size > _MAX_DATA_BYTES:
            return None
        key = (str(f), st.st_mtime_ns, st.st_size)
        hit = self._parsed.get(key)
        if hit is not None:
            self._parsed.move_to_end(key)
            return hit
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        self._parsed[key] = data
        while len(self._parsed) > _PARSE_CACHE_ENTRIES:
            self._parsed.popitem(last=False)
        return data

    # ── paths ────────────────────────────────────────────────────────────
    @property
    def project_root(self) -> Path:
        return Path(self.studio.project_root)

    def _report_output(self, slug: str) -> Path:
        """Where this report's *served* output lives, on either backend.

        Through the storage layer, never the studio's own output directory:
        on the s3 backend that directory is the runner's write location, which
        on a web node is empty or stale — assistant must read exactly what serving
        reads.
        """
        error = self._access_error(slug)
        if error:
            raise PermissionError(error)

        from apps.core import storage

        return storage.output_root(self.studio, slug)

    def read_meta(self, slug: str) -> dict:
        from apps.core import storage

        if self._access_error(slug):
            return {}
        try:
            return storage.read_meta(self.studio, slug) or {}
        except Exception:
            return {}

    def data_files(self, slug: str) -> list[Path]:
        """data.json plus per-scope data_<scope>.json (chunk files excluded).

        Resolved once per slug per turn: the catalog, get_report_details and
        every query ask for the same slug, and on the s3 backend each ask is
        a storage lookup rather than a local stat.
        """
        if self._access_error(slug):
            return []
        cached = self._files.get(slug)
        if cached is not None:
            return cached
        out = self._report_output(slug)
        files = []
        if (out / "data.json").exists():
            files.append(out / "data.json")
        if out.is_dir():
            for p in sorted(out.glob("data_*.json")):
                if not p.name.startswith("data_chunk_"):
                    files.append(p)
        self._files[slug] = files
        return files

    # ── dispatch ─────────────────────────────────────────────────────────
    def execute(self, name: str, args: dict) -> str:
        args = args or {}
        prechecked = self._access_prechecked
        self._access_prechecked = False
        error = None if prechecked else self._access_error(
            (args.get("slug") or "").strip() or None
        )
        if error:
            self.provenance = None
            return f"Error: {error}"
        fn = {
            "list_reports": self._list_reports,
            "get_report_details": self._get_report_details,
            "query_report_data": self._query_report_data,
            "read_doc": self._read_doc,
            "check_repo_changes": self._check_repo_changes,
        }.get(name)
        if name in _MUTATING:
            fn = lambda a, _n=name: self._propose(_n, a)  # noqa: E731
        elif name in self._extra:
            fn = self._extra[name][1]
        self.provenance = None
        self.proposal = None
        if fn is None:
            return f"Error: unknown tool '{name}'."
        if name in _BY_NAME and not self.allowed(_BY_NAME[name]):
            return f"Error: '{name}' is not available to you in this studio."
        self._executing_prechecked = prechecked
        try:
            return fn(args)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            return "Error: " + (msg[:600] + "…" if len(msg) > 600 else msg)
        finally:
            self._executing_prechecked = False

    def authorize(self, args: dict) -> str | None:
        """Recheck access on the request thread immediately before dispatch."""
        args = args or {}
        error = self._access_error((args.get("slug") or "").strip() or None)
        self._access_prechecked = error is None
        return error

    def _propose(self, name: str, args: dict) -> str:
        """The gate: a mutating call becomes a ``ProposedAction`` and the
        model is told to wait. The role is re-checked here, not only when
        the catalogue was listed, and once more when the user approves."""
        from django.utils import timezone

        from apps.assistant import actions
        from apps.assistant.models import ProposedAction

        schema = next(s for s in TOOL_SCHEMAS if s["name"] == name)
        if not self.allowed(schema) or self.session is None:
            return f"Error: '{name}' is not available to you in this studio."
        spec = actions.describe(self.studio, name, args)
        if isinstance(spec, str):
            return spec
        if rank(self.role) < rank(spec["required_role"]):
            label = {"org_admin": "organization admin"}
            return (
                f"Error: this needs the {label.get(spec['required_role'], spec['required_role'])} "
                f"role; the user is a {label.get(self.role, self.role)}."
            )
        # Ids only: this runs on the tool worker thread, and the session's
        # user is not loaded -- the request thread resolved everything else.
        row = ProposedAction.objects.create(
            session_id=self.session.pk, org_id=self.studio.org_id, studio_id=self.studio.pk,
            user_id=self.session.user_id, tool=name, summary=spec["summary"],
            arguments=spec["arguments"], secret_fields=spec["secret_fields"],
            required_role=spec["required_role"],
            expires_at=timezone.now() + ProposedAction.EXPIRY,
        )
        self.proposal = row.to_event()
        return (
            f"Proposed action #{row.pk}: {row.summary}. "
            "Waiting for the user's approval in the panel."
        )

    def frame(self, name: str, args: dict, result: str) -> str:
        """Wrap a tool result in ``<data source=…>`` before it reaches the model.

        Report output and project files are author-controlled text; the tag
        marks them as data the HEADER tells the model never to take
        instructions from. Errors are ours and go through untouched.
        """
        if result.startswith("Error") or name in self._extra:
            return result
        args = args or {}
        if name == "read_doc":
            source = f"file:{args.get('path') or ''}"
        elif name == "list_reports":
            source = f"studio:{self.studio.slug}"
        elif name == "check_repo_changes":
            source = f"repo:{self.studio.slug}"
        else:
            source = f"report:{args.get('slug') or ''}"
        return f'<data source="{source}">\n{result}\n</data>'

    # ── implementations ──────────────────────────────────────────────────
    def reports(self) -> list[dict]:
        """The studio's report registry, resolved once per turn (see __init__)."""
        if self._access_error():
            return []
        if self._reports is None:
            from apps.reports.scan import build_registry_payload

            reports = build_registry_payload(self.studio).get("reports", [])
            self._reports = (
                [report for report in reports if report.get("slug") == self.report_slug]
                if self.is_report_bound else reports
            )
        return self._reports

    def _list_reports(self, args: dict) -> str:
        reports = self.reports()
        category = (args.get("category") or "").strip()
        if category:
            reports = [r for r in reports if r.get("category") == category]
        if not reports:
            return "No reports matched."

        lines = [
            "| Slug | Name | Category | Last run | Status | Link | Description |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in sorted(reports, key=lambda r: (r.get("category") or "", r.get("slug") or "")):
            desc = (r.get("description") or "").replace("|", "\\|").replace("\n", " ")
            if len(desc) > 110:
                desc = desc[:107] + "…"
            lines.append(
                f"| {r.get('slug')} | {r.get('name')} | {r.get('category') or ''} "
                f"| {str(r.get('last_run') or '')[:16]} "
                f"| {r.get('last_status') or 'not_run'} | {self.link(r.get('slug') or '')} | {desc} |"
            )
        lines.append("")
        lines.append(
            "Link a report as [Name](Link) using the Link column verbatim. Reports "
            "with status 'not_run' have no built output yet — query_report_data "
            "won't work on those."
        )
        return "\n".join(lines)

    def _check_repo_changes(self, args: dict) -> str:
        """The git-status payload as prose: what is pending, and how fresh."""
        from apps.reports.views import git_status_payload

        st = git_status_payload(self.studio)
        if not st.get("configured"):
            return "Git sync is not configured for this studio: nothing is published from a repository."
        out = [f"Repository: {st['repo']} (branch {st['branch']}), publish mode: {st['publish_mode']}"]
        head = st["remote_sha"][:12] if st["remote_sha"] else "not fetched yet"
        out.append(f"Remote head: {head}"
                   + (f", checked {st['remote_checked_at']}" if st["remote_checked_at"] else ""))
        out.append(f"Last published: {st['last_published'] or 'never'}"
                   + (f" (at {st['last_synced_sha'][:12]})" if st["last_synced_sha"] else ""))
        if st["last_error"]:
            out.append(f"Last error: {st['last_error']}")
        if st["publishing"]:
            out.append("A publish is already requested and waits for the worker.")
        pending = st["pending"]
        if not pending:
            out.append("Pending: nothing -- the portal has published everything it fetched."
                       + (" In auto mode every fetch publishes itself."
                          if st["publish_mode"] == "auto" else ""))
            return "\n".join(out)
        commits = pending.get("commits") or []
        out.append(f"Pending since the last publish ({pending.get('commits_total') or len(commits)} commit(s)):")
        for kind in ("added", "modified", "removed"):
            names = (pending.get("reports") or {}).get(kind) or []
            if names:
                out.append(f"  reports {kind}: {', '.join(names)}")
        for kind in ("added", "changed", "removed"):
            names = (pending.get("datasources") or {}).get(kind) or []
            if names:
                out.append(f"  data sources {kind}: {', '.join(names)}")
        if pending.get("root_files"):
            out.append(f"  project files: {', '.join(pending['root_files'])}")
        for c in commits[:10]:
            out.append(f"  {(c.get('sha') or '')[:8]} {c.get('message') or ''} ({c.get('author') or ''})")
        for w in pending.get("warnings") or []:
            out.append(f"  warning: {w}")
        if pending.get("initial"):
            out.append("  (first publish: everything in the repository is new)")
        if pending.get("rewritten"):
            out.append("  (history was rewritten; this lists the whole tree)")
        out.append("publish_repo_changes publishes this at that head (write scope, manual mode).")
        return "\n".join(out)

    def _get_report_details(self, args: dict) -> str:
        slug = (args.get("slug") or "").strip()
        if not _SLUG_RE.fullmatch(slug):
            return f"Error: invalid slug '{slug}'."
        meta = self.read_meta(slug)
        if not meta:
            return (
                f"Error: no metadata for report '{slug}'. Check the slug with "
                "list_reports; the report may never have been run."
            )

        details = meta.get("details") or {}
        val = (meta.get("validation") or {}).get("summary", {})
        mock = details.get("data_source") == "mock"
        self.provenance = {
            "report": slug,
            "report_name": self.report_name(slug),
            "built_at": meta.get("last_run"),
            "link": self.link(slug),
            "validation": {"fail": val.get("fail", 0), "warn": val.get("warn", 0)},
            "mock": mock,
        }
        out = [f"# Report: {self.report_name(slug)} (slug `{slug}`)"]
        out.append(
            f"Last run: {meta.get('last_run')} — status: {meta.get('last_status')}"
            + (" — DATA IS MOCK (test run), numbers are synthetic!" if mock else "")
        )
        out.append(f"Link: {self.link(slug)}")
        out.append(
            f"Validation: {val.get('fail', 0)} fail / {val.get('warn', 0)} warn "
            f"/ {val.get('suppressed', 0)} suppressed"
        )

        datasets = details.get("datasets") or []
        if datasets:
            out.append("\n## Datasets")
            out.append("| id | rows | columns | scope |")
            out.append("| --- | --- | --- | --- |")
            for d in datasets:
                out.append(
                    f"| {d.get('id')} | {d.get('rows'):,} | {d.get('columns')} "
                    f"| {d.get('scope') or ''} |"
                )

        cols_by_ds: dict[str, list[str]] = {}
        for f in self.data_files(slug):
            data = self.read_data(f)
            if data is None:
                continue
            for k, v in data.items():
                if k.startswith("_ds_") and isinstance(v, dict) and "_cols" in v:
                    cols_by_ds.setdefault(k[4:], v["_cols"])
                elif not k.startswith("_") and isinstance(v, dict):
                    for sub, rows in v.items():
                        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                            cols_by_ds.setdefault(f"{k}.{sub}", list(map(str, rows[0].keys())))
        if cols_by_ds:
            out.append("\n## Dataset columns")
            for ds_id, cols in sorted(cols_by_ds.items()):
                out.append(f"- `{ds_id}`: {', '.join(cols)}")

        matrix = details.get("filter_matrix") or []
        if matrix:
            out.append("\n## Charts")
            out.append("| Section | Chart | Type | dataset_id |")
            out.append("| --- | --- | --- | --- |")
            for m in matrix:
                title = (m.get("chart_title") or "").replace("|", "\\|")
                out.append(
                    f"| {m.get('section') or ''} | {title} | {m.get('chart_kind')} "
                    f"| {m.get('dataset_id')} |"
                )
        return "\n".join(out)

    # data.json loading ---------------------------------------------------
    @staticmethod
    def _decode_dataset(payload: dict):
        """Columnar ``_ds_*`` payload → pandas DataFrame."""
        import pandas as pd

        cols = payload.get("_cols") or []
        rows = payload.get("_data") or []
        dct = payload.get("_dict") or {}
        df = pd.DataFrame(rows, columns=cols)
        for col, values in dct.items():
            if col in df.columns:
                lookup = pd.Series(values)
                df[col] = df[col].map(
                    lambda i, lk=lookup: lk.iloc[i]
                    if isinstance(i, int) and 0 <= i < len(lk) else i
                )
        return df

    @staticmethod
    def _custom_records(data: dict, dataset_id: str):
        """Resolve a dotted path (e.g. 'pulse.hourly_rev') to a record list."""
        node = data
        for part in dataset_id.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        if isinstance(node, list) and node and isinstance(node[0], dict):
            return node
        return None

    def load_dataset(self, slug: str, dataset_id: str):
        """(DataFrame, note) or (None, error string)."""
        import pandas as pd

        if not _SLUG_RE.fullmatch(slug or ""):
            return None, f"Error: invalid slug '{slug}'."
        files = self.data_files(slug)
        if not files:
            return None, (
                f"Error: report '{slug}' has no built output (data.json missing). "
                "It may never have been run."
            )
        key = f"_ds_{dataset_id}"
        for f in files:
            try:
                size = f.stat().st_size
            except OSError as e:
                return None, f"Error reading {f.name}: {e}"
            if size > _MAX_DATA_BYTES:
                return None, (
                    f"Error: {f.name} is {size // (1024 * 1024)} MB — too "
                    "large to load server-side. Open the report itself for this one."
                )
            data = self.read_data(f)
            if data is None:
                return None, f"Error: {f.name} could not be read as report data."
            if key in data:
                note = ""
                if dataset_id in (data.get("_chunk_manifests") or {}):
                    note = (
                        "Note: this dataset is chunked — only the most recent inline "
                        "months are loaded here; older periods live in chunk files."
                    )
                return self._decode_dataset(data[key]), note
            records = self._custom_records(data, dataset_id)
            if records is not None:
                return pd.DataFrame(records), ""

        available = set()
        for f in files:
            data = self.read_data(f)
            if data is None:
                continue
            available |= {k[4:] for k in data if k.startswith("_ds_")}
            for k, v in data.items():
                if k.startswith("_") or not isinstance(v, dict):
                    continue
                for sub, rows in v.items():
                    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                        available.add(f"{k}.{sub}")
        return None, (
            f"Error: dataset '{dataset_id}' not found in report '{slug}'. "
            f"Available datasets: {sorted(available) or 'none'}."
        )

    # links with filters applied ------------------------------------------
    def filter_bars(self, slug: str) -> list[dict]:
        """The report's FilterBars as the page registers them with url_sync.

        ``data.json["components"]`` holds every FilterBar's dataset_id, its
        filters (column + type) and propagate_to -- the same config the
        browser's URL sync serialises from, so a link built from it opens
        the report with exactly those controls set.
        """
        bars: dict[str, dict] = {}
        for f in self.data_files(slug):
            data = self.read_data(f) or {}
            for comp in (data.get("components") or {}).values():
                if isinstance(comp, dict) and comp.get("type") == "filter_bar":
                    bars.setdefault(str(comp.get("dataset_id")), comp)
        return list(bars.values())

    def query_link(self, slug: str, dataset_id: str, filters: dict,
                   date_col: str | None, date_from, date_to) -> str:
        """Report link carrying the query's filters in url_sync's format.

        Only columns that are actual FilterBar controls reaching this dataset
        become parameters (``column=value``; dropdown values joined with
        commas; date ranges as ``from..to``), prefixed ``<dsId>.`` only when
        the report has more than one bar -- exactly how the page writes them.
        """
        bars = self.filter_bars(slug)
        multi = len(bars) > 1
        params: list[tuple[str, str]] = []
        for bar in bars:
            bar_ds = str(bar.get("dataset_id"))
            controls = {c.get("column"): c.get("type") or "dropdown"
                        for c in bar.get("filters") or [] if isinstance(c, dict)}
            if bar_ds == dataset_id:
                reach = {col: col for col in controls}
            else:
                reach = (bar.get("propagate_to") or {}).get(dataset_id) or {}
            for bar_col, target_col in reach.items():
                ftype = controls.get(bar_col)
                if ftype is None:
                    continue
                key = f"{bar_ds}.{bar_col}" if multi else bar_col
                if target_col in filters:
                    vals = filters[target_col]
                    vals = vals if isinstance(vals, list) else [vals]
                    if not vals:
                        continue
                    value = ",".join(map(str, vals)) if ftype == "dropdown" else str(vals[0])
                    params.append((key, value))
                elif target_col == date_col and date_from and date_to and ftype == "date_range":
                    params.append((key, f"{date_from}..{date_to}"))
        return self.link(slug, urlencode(params, safe=",.:"))

    def _query_report_data(self, args: dict) -> str:
        slug = (args.get("slug") or "").strip()
        dataset_id = (args.get("dataset_id") or "").strip()
        if not slug or not dataset_id:
            return "Error: 'slug' and 'dataset_id' are required."

        df, note = self.load_dataset(slug, dataset_id)
        if df is None:
            return note
        total_rows = len(df)
        # Filtering rebinds `df` rather than mutating it, so holding the
        # original costs nothing and saves the 0-row branch below from
        # re-reading and re-parsing the whole data.json once per filter
        # column just to report what the column contains.
        unfiltered = df

        filters = args.get("filters") or {}
        for col, allowed in filters.items():
            if col not in df.columns:
                return f"Error: filter column '{col}' not in dataset. Columns: {list(df.columns)}"
            if not isinstance(allowed, list):
                allowed = [allowed]
            df = df[df[col].astype(str).isin([str(v) for v in allowed])]

        date_col = args.get("date_column")
        date_from, date_to = args.get("date_from"), args.get("date_to")
        if date_col:
            if date_col not in df.columns:
                return f"Error: date_column '{date_col}' not in dataset."
            series = df[date_col].astype(str)
            if date_from:
                df = df[series >= str(date_from)]
                series = df[date_col].astype(str)
            if date_to:
                df = df[series <= str(date_to)]

        group_by = args.get("group_by") or []
        metrics = args.get("metrics") or []
        built_at = self.read_meta(slug).get("last_run")
        link = self.query_link(slug, dataset_id, filters, date_col, date_from, date_to)
        self.provenance = {
            "report": slug,
            "report_name": self.report_name(slug),
            "built_at": built_at,
            "dataset": dataset_id,
            "filters": {k: (v if isinstance(v, list) else [v]) for k, v in filters.items()},
            "date_range": (
                {"column": date_col, "from": date_from, "to": date_to} if date_col else None
            ),
            "group_by": list(group_by),
            "aggregate": [{"column": m.get("column"), "agg": m.get("agg", "sum")} for m in metrics],
            "rows_total": total_rows,
            "rows_after": len(df),
            "rows_returned": 0,
            "link": link,
        }

        if df.empty:
            # Self-healing hint: tell the model what WOULD have matched so it
            # can correct the filter in one retry instead of giving up.
            hints = []
            if date_col and date_col in unfiltered.columns:
                spans = unfiltered[date_col].astype(str)
                hints.append(
                    f"'{date_col}' in this dataset actually spans {spans.min()} → "
                    f"{spans.max()} — retry within that range (use the max date "
                    "for 'latest')."
                )
            for col in (filters or {}):
                if col in unfiltered.columns:
                    vals = sorted(map(str, unfiltered[col].dropna().unique()))[:15]
                    hints.append(f"available '{col}' values include: {', '.join(vals)}")
            return (
                f"0 rows after filtering (dataset has {total_rows:,} rows total). "
                + " ".join(hints)
            )

        for col in group_by:
            if col not in df.columns:
                return f"Error: group_by column '{col}' not in dataset. Columns: {list(df.columns)}"
        for m in metrics:
            if m.get("column") not in df.columns:
                return f"Error: metric column '{m.get('column')}' not in dataset."

        if metrics:
            agg_spec = {m["column"]: m.get("agg", "sum") for m in metrics}
            if group_by:
                result = df.groupby(group_by, observed=True).agg(agg_spec).reset_index()
            else:
                result = df.agg(agg_spec).to_frame().T
        elif group_by:
            result = df.groupby(group_by, observed=True).size().reset_index(name="row_count")
        else:
            result = df  # raw preview

        sort_by = args.get("sort_by")
        if sort_by and sort_by in result.columns:
            result = result.sort_values(sort_by, ascending=False)

        limit = min(int(args.get("limit") or _DATA_DISPLAY_CAP), 200)
        table = format_df_markdown(result, limit)
        self.provenance["rows_returned"] = min(len(result), limit)
        header = (
            f"Dataset '{dataset_id}' of report '{slug}' (built {built_at or 'unknown'}) — "
            f"{total_rows:,} rows total, {len(df):,} after filters, {len(result):,} result row(s)."
        )
        if note:
            header += "\n" + note
        return header + "\n\n" + table + f"\n\nLink (with these filters applied): {link}"

    # read_doc ------------------------------------------------------------
    def _resolve_read_path(self, path: str) -> Path | None:
        root = self.project_root.resolve()
        p = (root / path).resolve()
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            return None
        if self.is_report_bound:
            allowed = f"reports/{self.report_slug}"
            return p if rel == allowed or rel.startswith(allowed + "/") else None
        for prefix in READ_ALLOWLIST:
            if rel == prefix or rel.startswith(prefix + "/"):
                return p
        return None

    def _read_doc(self, args: dict) -> str:
        raw = (args.get("path") or "").strip()
        if not raw:
            return "Error: missing 'path' argument."
        p = self._resolve_read_path(raw)
        if p is None:
            readable = f"reports/{self.report_slug}" if self.is_report_bound else ", ".join(READ_ALLOWLIST)
            return (
                f"Error: '{raw}' is outside the readable part of this studio. "
                f"Readable prefixes: {readable}."
            )
        rel = p.relative_to(self.project_root.resolve()).as_posix()
        if not self.share_report_source and (rel == "reports" or rel.startswith("reports/")):
            return (
                "Error: reading report source is turned off for this organization "
                "(an org admin can enable 'Let the assistant read report source')."
            )
        if not p.exists():
            return f"Error: file not found: {raw}"
        if not p.is_file():
            return f"Error: not a file: {raw}"
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > _READ_MAX_CHARS:
            return (
                content[:_READ_MAX_CHARS]
                + f"\n\n... [truncated at {_READ_MAX_CHARS} chars, file is {len(content)}]"
            )
        return content

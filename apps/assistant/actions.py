"""What the assistant may change on the portal, and how an approved proposal runs.

The catalogue (:data:`apps.assistant.tools.TOOL_SCHEMAS`) says which tools
mutate and which role they need. This module turns a mutating call into a
proposal -- summary, the secret fields the card must collect, the role the
approver needs -- and, once approved, runs it through the same code path the
portal's own forms and endpoints use. Nothing here runs during a turn. The
MCP door (:mod:`apps.assistant.mcp`) calls the same :func:`execute` without
a proposal: the caller's harness asked them, and the audit row says ``via=mcp``.

Rationale and the catalogue's shape: internal documentation
``internal design notes`` §2-§3 (ticket #145).
"""
from __future__ import annotations

from apps.core import roles

#: One rung above the studio roles: viewer < developer < admin < org_admin.
ORG_ADMIN = "org_admin"
_RANK = {roles.VIEWER: 1, roles.DEVELOPER: 2, roles.ADMIN: 3, ORG_ADMIN: 4}


def rank(role: str | None) -> int:
    return _RANK.get(role or "", 0)


class ActionFailed(Exception):
    """The action ran and did not achieve what was asked (a connection test
    that failed). The message is the result the card shows."""


def _binding(studio, name: str):
    """The credentials a build of ``studio`` would use for ``name``: the
    studio's own row, else the organization's shared one."""
    from apps.datasources.models import DataSource

    return (
        DataSource.objects.filter(studio=studio, name=name).first()
        or DataSource.objects.filter(org=studio.org, name=name).first()
    )


def _repo(studio):
    repo = getattr(studio, "repo", None)
    return repo if repo is not None and repo.repo_url else None


# ── Alerts ──────────────────────────────────────────────────────────────────

#: What a create_alert call means when the model leaves a field out: the
#: card shows the rule exactly as it will be saved.
_ALERT_DEFAULTS = {
    "instructions": "", "trigger": "after_build", "recipient_roles": [],
    "recipient_emails": [], "cooldown_hours": 24,
}
_ALERT_INTS = ("send_hour", "send_minute", "weekday", "month_day", "cooldown_hours")
#: The fields ``AlertRule.clean`` validates (the delivery schedule's own).
_ALERT_CLEANED = ("freq", "send_hour", "send_minute", "weekday", "month_day", "timezone", "recipient_roles")


def alerts_url(studio) -> str:
    from django.urls import reverse

    return reverse("studio-alerts", args=[studio.org.slug, studio.slug])


def _alert_fields(studio, args: dict, rule=None):
    """Validate the alert fields present in ``args`` the way the Alerts form
    would. Returns ``(fields, users)`` -- the plain values a proposal stores
    and the resolved individual recipients -- or an ``"Error: …"`` string.
    With ``rule`` (an update) only the given keys are checked, against the
    rule's current values."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    from apps.alerts.models import AlertRule
    from apps.reports.models import Report
    from apps.reports.notify import intersect_with_studio

    fields: dict = {}
    users: list = []
    if "name" in args:
        fields["name"] = str(args.get("name") or "").strip()[:200]
        if not fields["name"]:
            return "Error: 'name' is required."
    if "report" in args:
        slug = str(args.get("report") or "").strip()
        if not Report.objects.filter(studio=studio, slug=slug).exists():
            return f"Error: no report '{slug}' in this studio (check the slug with list_reports)."
        fields["report"] = slug
    if "instructions" in args:
        fields["instructions"] = str(args.get("instructions") or "").strip()
    if "trigger" in args:
        fields["trigger"] = str(args.get("trigger") or AlertRule.TRIGGER_AFTER_BUILD)
        if fields["trigger"] not in dict(AlertRule.TRIGGER_CHOICES):
            return f"Error: trigger must be 'after_build' or 'schedule', not '{fields['trigger']}'."
    if "freq" in args:
        fields["freq"] = str(args.get("freq") or AlertRule.FREQ_DAILY)
        if fields["freq"] not in dict(AlertRule.FREQ_CHOICES):
            return f"Error: unknown freq '{fields['freq']}'."
    if "timezone" in args:
        fields["timezone"] = str(args.get("timezone") or "UTC")
    for key in _ALERT_INTS:
        if key in args:
            try:
                fields[key] = int(args[key])
            except (TypeError, ValueError):
                return f"Error: '{key}' must be a whole number."
    if "recipient_roles" in args:
        got = args.get("recipient_roles") or []
        fields["recipient_roles"] = [str(r) for r in (got if isinstance(got, list) else [got])]
    if "recipient_emails" in args:
        got = args.get("recipient_emails") or []
        emails = [str(e).strip() for e in (got if isinstance(got, list) else [got]) if str(e).strip()]
        found = get_user_model().objects.filter(email__in=emails, is_active=True)
        users = intersect_with_studio(found, studio)
        known = {u.email.lower() for u in users}
        unknown = [e for e in emails if e.lower() not in known]
        if unknown:
            return (
                f"Error: not members of this studio: {', '.join(unknown)}. "
                "Recipients must already have access to the studio; ask an admin to add them first."
            )
        fields["recipient_emails"] = sorted(u.email for u in users)
    # The rule as it will be saved, through the same clean() the form runs.
    probe = AlertRule() if rule is None else AlertRule(**{k: getattr(rule, k) for k in _ALERT_CLEANED})
    for key in _ALERT_CLEANED:
        if key in fields:
            setattr(probe, key, fields[key])
    try:
        probe.clean()
    except ValidationError as e:
        return "Error: " + "; ".join(f"{k}: {' '.join(v)}" for k, v in e.message_dict.items())
    if "recipient_roles" in fields:
        fields["recipient_roles"] = probe.recipient_roles  # canonical order
    return fields, users


def _alert_summary(fields: dict, report_name: str) -> str:
    when = "after every build" if fields["trigger"] == "after_build" else "on a schedule"
    to = ", ".join(fields["recipient_roles"] + fields["recipient_emails"]) or "nobody yet"
    return (
        f"Create alert “{fields['name']}” on “{report_name}”, evaluated {when}; "
        f"recipients: {to}; cooldown {fields['cooldown_hours']} h"
    )[:300]


def describe(studio, name: str, args: dict) -> dict | str:
    """What approving this call would do, or an ``"Error: …"`` string.

    Returns ``{"summary", "arguments", "secret_fields", "required_role"}``.
    ``arguments`` is the cleaned, secret-free copy the proposal stores.
    """
    if name == "configure_data_source":
        from apps.datasources.models import RepoDataSource
        from apps.datasources.views import credential_fields

        src = str(args.get("name") or "").strip()
        decl = RepoDataSource.objects.filter(studio=studio, name=src, present=True).first()
        if decl is None:
            return f"Error: no data source named '{src}' is declared in this studio's repository."
        secrets = credential_fields(decl)
        if not secrets:
            return f"Error: data source '{src}' ({decl.type}) takes no credentials."
        org_level = bool(args.get("org_level"))
        return {
            "summary": (
                f"Configure data source “{src}” ({decl.type}) with new credentials, "
                f"shared by every studio in the organization" if org_level else
                f"Configure data source “{src}” ({decl.type}) with new credentials for this studio"
            ),
            "arguments": {"name": src, "org_level": org_level},
            "secret_fields": secrets,
            "required_role": ORG_ADMIN if org_level else roles.ADMIN,
        }
    if name == "test_data_source":
        src = str(args.get("name") or "").strip()
        if _binding(studio, src) is None:
            return (
                f"Error: no credentials are bound for data source '{src}' -- "
                "propose configure_data_source first."
            )
        return {
            "summary": f"Test the connection of data source “{src}”",
            "arguments": {"name": src},
            "secret_fields": [],
            "required_role": roles.DEVELOPER,
        }
    if name == "run_report":
        from apps.reports.models import Report

        slug = str(args.get("slug") or "").strip()
        report = Report.objects.filter(
            studio=studio, slug=slug, present_in_scan=True,
        ).first()
        if report is None:
            return f"Error: no report '{slug}' in this studio (check the slug with list_reports)."
        return {
            "summary": f"Build report “{report.name or slug}” now",
            "arguments": {"slug": slug},
            "secret_fields": [],
            "required_role": roles.DEVELOPER,
        }
    if name == "publish_repo_changes":
        if _repo(studio) is None:
            return "Error: this studio is not connected to a git repository."
        to = str(args.get("to") or "").strip()[:64]
        rebuild = args.get("rebuild")
        summary = "Publish the pending repository changes"
        if to:
            summary += f" reviewed at {to[:8]}"
        if rebuild is not None:
            summary += " and rebuild the changed reports" if rebuild else " without rebuilding"
        arguments = {"to": to} if to else {}
        if rebuild is not None:
            arguments["rebuild"] = bool(rebuild)
        return {
            "summary": summary,
            "arguments": arguments,
            "secret_fields": [],
            "required_role": roles.DEVELOPER,
        }
    if name == "create_alert":
        from apps.reports.models import Report

        spec = _alert_fields(studio, {**_ALERT_DEFAULTS, "name": None, "report": None, **args})
        if isinstance(spec, str):
            return spec
        fields, _ = spec
        report = Report.objects.get(studio=studio, slug=fields["report"])
        return {
            "summary": _alert_summary(fields, report.name or report.slug),
            "arguments": fields,
            "secret_fields": [],
            "required_role": roles.DEVELOPER,
        }
    if name == "update_alert":
        from apps.alerts.models import AlertRule

        rule = AlertRule.objects.filter(studio=studio, pk=args.get("alert_id") or 0).first()
        if rule is None:
            return f"Error: no alert rule #{args.get('alert_id')} in this studio."
        spec = _alert_fields(studio, {k: v for k, v in args.items() if k != "alert_id"}, rule)
        if isinstance(spec, str):
            return spec
        fields, _ = spec
        if not fields:
            return "Error: nothing to change -- pass at least one field."
        return {
            "summary": f"Update alert “{rule.name}”: {', '.join(k.replace('_', ' ') for k in fields)}"[:300],
            "arguments": {"alert_id": rule.pk, **fields},
            "secret_fields": [],
            "required_role": roles.DEVELOPER,
        }
    return f"Error: unknown action '{name}'."


def _save_alert(request, rule, fields: dict, users: list, action: str, meta: dict) -> str:
    """Apply validated ``fields`` to ``rule`` (new or existing), save, set
    the picked recipients, audit; the one line the model relays."""
    from apps.core.audit import audit
    from apps.reports.models import Report

    if "report" in fields:
        rule.report = Report.objects.get(studio=rule.studio, slug=fields["report"])
    for key, value in fields.items():
        if key not in ("report", "recipient_emails"):
            setattr(rule, key, value)
    rule.clean()
    rule.save()
    if "recipient_emails" in fields:
        rule.recipients.set(users)
    audit(request, action, target=rule, rule_id=rule.pk, name=rule.name, changed=sorted(fields), **meta)
    verb = "created" if action == "alert.create" else "updated"
    return f"Alert “{rule.name}” {verb} -- see the Alerts page: {alerts_url(rule.studio)}"


def execute(request, tool: str, args: dict, secrets: dict, **meta) -> str:
    """Run ``tool`` with the cleaned ``args`` :func:`describe` produced, as
    ``request.user`` (the caller has re-checked the role), and return the
    sentence to relay. Raises :class:`ActionFailed` when the action ran but
    did not succeed.

    ``secrets`` are the card's values for the ``secret_fields``; they go
    straight to the encrypted column and are stored nowhere else. ``meta``
    lands on the audit row: the panel passes ``proposal_id``, MCP ``via``.
    """
    from apps.core.audit import audit

    studio = request.studio
    if tool == "configure_data_source":
        from apps.datasources.models import RepoDataSource
        from apps.datasources.views import save_credentials

        decl = RepoDataSource.objects.filter(studio=studio, name=args["name"], present=True).first()
        if decl is None:
            raise ActionFailed(f"Data source '{args['name']}' is no longer declared.")
        ok, text = save_credentials(
            request, decl, secrets, org_level=bool(args.get("org_level")), **meta
        )
        if not ok:
            raise ActionFailed(text)
        return text
    if tool == "test_data_source":
        from apps.datasources.testing import check_binding

        ds = _binding(studio, args["name"])
        if ds is None:
            raise ActionFailed(f"No credentials are bound for '{args['name']}'.")
        ok, detail = check_binding(ds, studio)
        audit(request, "datasource.test", target=ds, name=ds.name, ok=ok, **meta)
        if not ok:
            raise ActionFailed(f"Connection test failed: {detail}")
        return f"Connection OK: {detail}" if detail else "Connection OK."
    if tool == "run_report":
        from apps.reports.models import Report
        from apps.runner.services import enqueue

        report = Report.objects.filter(
            studio=studio, slug=args["slug"], present_in_scan=True,
        ).first()
        if report is None:
            raise ActionFailed(f"Report '{args['slug']}' no longer exists.")
        status = enqueue(report, trigger="manual", user=request.user)
        audit(request, "run.enqueue", target=report, slug=report.slug, **meta)
        if status == "queued":
            return f"Build of “{report.name or report.slug}” queued."
        return f"A build of “{report.name or report.slug}” is already running."
    if tool == "publish_repo_changes":
        from apps.reports.views import request_publish

        repo = _repo(studio)
        if repo is None:
            raise ActionFailed("This studio is not connected to a git repository.")
        request_publish(request, repo, rebuild=args.get("rebuild"), to=args.get("to"), **meta)
        return "Publish scheduled -- the worker publishes within seconds."
    if tool in ("create_alert", "update_alert"):
        from apps.alerts.models import AlertRule

        # Re-validated now, not when proposed: a recipient may have left the
        # studio, the report may be gone.
        if tool == "create_alert":
            rule = AlertRule(org=studio.org, studio=studio, created_by=request.user)
            spec = _alert_fields(studio, args)
        else:
            rule = AlertRule.objects.filter(studio=studio, pk=args.get("alert_id") or 0).first()
            if rule is None:
                raise ActionFailed(f"Alert rule #{args.get('alert_id')} no longer exists.")
            spec = _alert_fields(studio, {k: v for k, v in args.items() if k != "alert_id"}, rule)
        if isinstance(spec, str):
            raise ActionFailed(spec.removeprefix("Error: "))
        return _save_alert(request, rule, *spec, "alert." + tool.split("_")[0], meta)
    raise ActionFailed(f"Unknown action '{tool}'.")

"""Layer 2 of the alert design: labelled use cases on the demo warehouse,
judged by the org's real model.

    manage.py alert_scenarios [--only <id>]... [--org demo --studio demo]
                              [--out <path.md>] [--dry]

Reads ``trellum/demo/alert-scenarios.yaml``, generates the demo warehouse once
at its anchor into the studio's project root, and per scenario: builds the
report with ``FW_NOW=<before>``, creates a disabled rule, evaluates it once
(the baseline snapshot), builds with ``FW_NOW=<after>``, evaluates again and
compares the decision and ``must_mention`` to the label. Every evaluation is
``evaluate(rule, deliver=False)``: nothing is mailed, cooldown is untouched.
Spend goes through the usual budget path and shows per row.

Not CI -- it costs money and its answers are a judgment. Layer 1 (the
machinery, scripted model) is ``apps/alerts/tests/test_evaluator.py``.
Design: vault ``Architecture/design/portal-agent/00-plan`` §11.
"""
from __future__ import annotations

import time
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.alerts.evaluator import evaluate
from apps.alerts.models import AlertRule, AlertRun
from apps.assistant import llm
from apps.reports.models import Report
from apps.reports.notify import alert_recipients
from apps.runner.executor import Executor
from apps.runner.models import Run
from apps.studios.models import Studio

SCENARIOS_FILE = Path(settings.BASE_DIR) / "trellum" / "demo" / "alert-scenarios.yaml"
RULE_PREFIX = "scenario: "


def load_scenarios(path: Path = SCENARIOS_FILE) -> dict:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    spec["anchor"] = str(spec["anchor"])  # YAML parses bare dates
    for s in spec["scenarios"]:
        for key in ("before", "after", "evaluate_at"):
            if key in s:
                s[key] = str(s[key])
    return spec


def ensure_warehouse(studio, spec: dict) -> None:
    """The fixture DB at the file's anchor/days/seed, generated once."""
    from trellum.demo.tools import make_fixtures as fx

    want = {"anchor": spec["anchor"], "seed": str(spec["seed"]), "days": str(spec["days"])}
    status, _, params = fx.fixture_status(str(studio.project_root / "data-sources" / "demo.sqlite"))
    if status == "current" and {k: params.get(k) for k in want} == want:
        return
    argv = ["--project-root", str(studio.project_root)]
    for key, value in want.items():
        argv += [f"--{key}", value]
    if fx.main(argv) != 0:
        raise CommandError("fixture generation failed")


def build(report, owner, now: str) -> Run:
    """One synchronous build with the report clock pinned to ``now``."""
    run = Run.objects.create(
        report=report, studio=report.studio, slug=report.slug, pool=report.studio.pool,
        status=Run.STARTING, cache_mode="fresh", requested_by=owner,
    )
    ex = Executor("alert-scenarios", max_concurrent=1)
    started = ex.start_run(run, extra_env={"FW_NOW": now})
    while ex.running_count:
        time.sleep(1)
        ex.tick()
    run.refresh_from_db()
    if not started or run.status != Run.SUCCESS:
        raise CommandError(f"{report.slug} at {now}: build {run.status}\n{run.stderr_tail[-1500:]}")
    return run


def evaluate_alone(rule: AlertRule, today: str) -> AlertRun:
    """``evaluate(rule, deliver=False)`` on the build's own calendar, with the
    studio's other reports hidden from the model. In production an after-build
    evaluation runs right after the build, so "today" is the data's last day
    and every report sits at the same clock; here the build is pinned with
    FW_NOW and each sibling is built at its own scenario's `after`, so a
    sibling would leak the future (run 1 of #150: the launch baseline, built
    before the launch, read the ramp from `monetization` built at 08-08)."""
    from apps.assistant.tools import AssistantToolbox

    see_all = AssistantToolbox.reports
    only_this = lambda self: [r for r in see_all(self) if r["slug"] == rule.report.slug]  # noqa: E731
    now = datetime.fromisoformat(today).replace(tzinfo=timezone.utc)
    with mock.patch.object(AssistantToolbox, "reports", only_this):
        return evaluate(rule, deliver=False, now=now)


def mentions(run: AlertRun, must: list[str]) -> list[str]:
    """Case-insensitive, and a metric id's underscore reads as a space: the
    model writes "IAP revenue" for `iap_revenue`, and that is a mention."""
    norm = lambda s: s.lower().replace("_", " ")  # noqa: E731
    text = norm(" ".join([run.title, run.message, *(run.evidence.get("cited") or [])]))
    return [m for m in must if norm(m) in text]


def dry_client(scenario: dict):
    """The Layer-1 fake, answering with the scenario's own label: proves the
    loop end to end without a key or a cent."""
    from apps.alerts.tests.conftest import decide
    from apps.assistant.tests.test_message_sse import FakeClient

    response = decide(
        scenario["decision"] == "alert", title=f"Canned {scenario['decision']}",
        message="Dry run: " + (", ".join(scenario.get("must_mention") or []) or "nothing to mention"),
        evidence=[],
    )
    return FakeClient([response] * 3, [])


class Command(BaseCommand):
    help = "Run the labelled alert scenarios on the demo warehouse with the org's real model."

    def add_arguments(self, parser):
        parser.add_argument("--only", action="append", default=[], metavar="ID",
                            help="Scenario id (repeatable); a scenario's `reuses` target comes along")
        parser.add_argument("--org", default="demo")
        parser.add_argument("--studio", default="demo")
        parser.add_argument("--out", default=None, metavar="PATH",
                            help="Markdown results (default: <DATA_DIR>/alert-scenarios.md)")
        parser.add_argument("--dry", action="store_true",
                            help="Fake the model with a canned decide: no key, no spend")

    def handle(self, *, only, org, studio, out, dry, **_):
        slug = studio
        studio = Studio.objects.filter(org__slug=org, slug=slug).select_related("org").first()
        if studio is None:
            raise CommandError(f"studio {org}/{slug} not found")
        ok, why = llm.is_available(studio.org)
        if not ok:
            raise CommandError(why)

        spec = load_scenarios()
        scenarios = spec["scenarios"]
        if only:
            wanted = set(only) | {s["reuses"] for s in scenarios if s["id"] in only and s.get("reuses")}
            scenarios = [s for s in scenarios if s["id"] in wanted]
        if not scenarios:
            raise CommandError(f"no scenario matches {', '.join(only)}")
        reports = {r.slug: r for r in Report.objects.filter(studio=studio, slug__in={s["report"] for s in scenarios})}
        missing = sorted({s["report"] for s in scenarios} - set(reports))
        if missing:
            raise CommandError(f"reports not in {studio}: {', '.join(missing)} (import the demo first)")
        owners = alert_recipients(next(iter(reports.values())), "alert")
        if not owners:
            raise CommandError(f"{studio} has no admin or developer to own the rules")
        owner = owners[0]

        ensure_warehouse(studio, spec)

        self.stdout.write(
            f"{'scenario':<10} {'expected':<9} {'got':<9} {'baseline':<9} {'mentions':<9} {'cost':>8}"
        )
        rows, rules, detail = [], {}, []
        for s in scenarios:
            patch = mock.patch.object(llm, "_anthropic_client", lambda config, s=s: dry_client(s)) if dry else nullcontext()
            with patch:
                row, run = self.run_scenario(s, studio, reports[s["report"]], owner, rules)
            rows.append(row)
            detail.append((s, row, run))
            self.stdout.write(self.format_row(row))

        failed = [r for r in rows if not r["ok"] and not r["note"]]
        passed = [r for r in rows if r["ok"] and not r["note"]]
        info = [r for r in rows if r["note"]]
        total = sum((r["cost"] or 0) for r in rows)
        summary = (
            f"{len(passed)} passed, {len(failed)} failed, {len(info)} needs-knob (informational). "
            f"Cost {'unavailable' if any(r['cost'] is None for r in rows) else f'${total:.4f}'}."
        )
        self.stdout.write(summary)

        out = Path(out) if out else Path(settings.DATA_DIR) / "alert-scenarios.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.markdown(rows, detail, summary), encoding="utf-8")
        self.stdout.write(f"Results written to {out}")
        if failed:
            raise CommandError(f"{len(failed)} scenario(s) failed: {', '.join(r['id'] for r in failed)}")

    def run_scenario(self, s: dict, studio, report, owner, rules: dict) -> tuple[dict, AlertRun]:
        must = [str(m) for m in s.get("must_mention") or []]
        if s.get("reuses"):
            rule = rules[s["reuses"]]
            rule.cooldown_hours = s.get("cooldown_hours", rule.cooldown_hours)
            rule.save(update_fields=["cooldown_hours"])
            baseline, cost = None, Decimal("0")
        else:
            AlertRule.objects.filter(studio=studio, name=RULE_PREFIX + s["id"]).delete()
            build(report, owner, s["before"])
            rule = AlertRule.objects.create(
                org=studio.org, studio=studio, report=report, name=RULE_PREFIX + s["id"],
                instructions=s.get("instructions") or "", created_by=owner,
                trigger=AlertRule.TRIGGER_AFTER_BUILD, enabled=False,
                cooldown_hours=s.get("cooldown_hours", 24),
            )
            baseline = evaluate_alone(rule, s["before"])  # the snapshot the real run compares against
            cost = baseline.cost_usd
        rules[s["id"]] = rule
        build(report, owner, s["after"])
        run = evaluate_alone(rule, s.get("evaluate_at", s["after"]))
        cost = None if cost is None or run.cost_usd is None else cost + run.cost_usd
        hits = mentions(run, must)
        got = run.decision or f"{run.status}: {run.error[:60]}"
        return {
            "id": s["id"], "expected": s["decision"], "got": got,
            "hits": len(hits), "total": len(must), "cost": cost,
            "ok": got == s["decision"] and len(hits) == len(must), "note": s.get("note", ""),
            # Informational: what the model said of the `before` build, where
            # nothing has happened yet. An alert here is the false positive
            # that gets rules switched off.
            "baseline": (baseline.decision or baseline.status) if baseline else "-",
        }, run

    @staticmethod
    def format_row(r: dict) -> str:
        verdict = ("pass" if r["ok"] else "FAIL") + (" (info)" if r["note"] else "")
        return (f"{r['id']:<10} {r['expected']:<9} {r['got'][:9]:<9} {r['baseline'][:9]:<9} "
                f"{r['hits']}/{r['total']:<7} {('$' + format(r['cost'], '.4f')) if r['cost'] is not None else 'unavailable':>8}  {verdict}")

    @staticmethod
    def markdown(rows: list[dict], detail: list, summary: str) -> str:
        lines = [
            "| scenario | expected | got | baseline | mentions | cost | result |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            verdict = ("pass" if r["ok"] else "FAIL") + (" (info)" if r["note"] else "")
            cost = "unavailable" if r["cost"] is None else f"${r['cost']:.4f}"
            lines.append(f"| {r['id']} | {r['expected']} | {r['got']} | {r['baseline']} "
                         f"| {r['hits']}/{r['total']} | {cost} | {verdict} |")
        lines += ["", summary, ""]
        for s, r, run in detail:
            lines += [f"### {r['id']} — {r['got']}", "", f"**{run.title}**", "", run.message, ""]
            lines += [f"- {e}" for e in run.evidence.get("cited") or []]
            if run.error:
                lines += [f"Error: {run.error}"]
            if r["note"]:
                lines += ["", f"_Note: {r['note']}_"]
            lines.append("")
        return "\n".join(lines)

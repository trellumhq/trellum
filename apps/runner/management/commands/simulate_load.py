"""Dev-only: drive bursts of report runs through the real queue and summarize.

This command only ENQUEUES (via ``apps.runner.services.enqueue``) — a real
worker executes the builds, ideally the Docker Linux sim worker
(``docker-compose.sim.yml``) so memory limits, peak-RSS sampling, and OOM
kills behave like production. Run it from any host that reaches the same
Postgres.

    manage.py simulate_load --orgs 4 --reports-per-org 12 --setup-only
    manage.py simulate_load --orgs 4 --no-setup --bursts 5 --burst-size 40
    manage.py simulate_load --since 2026-08-15T12:00:00+00:00   # summary only
    manage.py simulate_load --cleanup

Phases: (1) idempotent setup of ``sim-NN`` orgs, each with a ``stress``
studio seeded by ``seed_stress_reports``; (2) bursts of enqueues over a
seeded-RNG shuffle of the report deck; (3) live progress from the Run table;
(4) drain until no sim run is active; (5) summary — throughput, queue-wait
and duration percentiles, status counts, peak-memory spread, and a per-org
fairness table with Jain's index.

The deck is many DISTINCT reports per org on purpose: the queue enforces one
active run per report, so depth comes from breadth.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone

from apps.orgs.models import Organization
from apps.reports.models import Report
from apps.runner import services, stats
from apps.runner.models import Run, WorkerHeartbeat
from apps.studios.models import Studio

SIM_STUDIO = "stress"
FAILURE_PROFILES = ("failing", "memhog", "oom", "timeout")


def _jain(values: list[float]) -> float | None:
    """Jain's fairness index: 1.0 = perfectly even, ->1/n = one-sided."""
    vals = [v for v in values if v is not None]
    if not vals or all(v == 0 for v in vals):
        return None
    return round(sum(vals) ** 2 / (len(vals) * sum(v * v for v in vals)), 3)


class Command(BaseCommand):
    help = "Dev-only: enqueue bursts of stress runs and summarize queue behavior."

    def add_arguments(self, parser):
        parser.add_argument("--orgs", type=int, default=4)
        parser.add_argument("--reports-per-org", type=int, default=12)
        parser.add_argument("--profile-mix", default="sleep:6,memory:3,dbscan:1,failing:1,timeout:1",
                            help="profile:weight pairs; weights shape the enqueue deck.")
        parser.add_argument("--bursts", type=int, default=5)
        parser.add_argument("--burst-size", type=int, default=40)
        parser.add_argument("--burst-interval", type=int, default=30, help="Seconds between bursts.")
        parser.add_argument("--pool", default="standard")
        parser.add_argument("--cache", default="fresh", dest="cache_mode")
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--db-source", default="synthetic")
        parser.add_argument("--db-database", default="trellum_synth")
        parser.add_argument("--setup-only", action="store_true")
        parser.add_argument("--no-setup", action="store_true")
        parser.add_argument("--no-wait", action="store_true",
                            help="Skip the drain phase; summarize whatever has finished.")
        parser.add_argument("--drain-timeout", type=int, default=1800)
        parser.add_argument("--since", default=None,
                            help="ISO timestamp: skip driving, just summarize that window.")
        parser.add_argument("--cleanup", action="store_true",
                            help="Delete the sim-NN orgs, their runs, and their data dirs.")

    def handle(self, *args, **opts):  # noqa: ARG002
        if not settings.DEBUG:
            raise CommandError("simulate_load is a development helper; DEBUG is off.")

        if opts["cleanup"]:
            self._cleanup(opts)
            return

        if opts["since"]:
            window_start = self._parse_since(opts["since"])
            self._summary(window_start, enqueued=None, elapsed_s=None)
            return

        if not opts["no_setup"]:
            self._setup(opts)
        if opts["setup_only"]:
            return

        deck = self._build_deck(opts)
        if not deck:
            raise CommandError(
                "no sim reports found -- run with --setup-only first (or drop --no-setup)"
            )

        window_start = timezone.now()
        t0 = time.monotonic()
        enqueued = self._drive(deck, opts)
        if not opts["no_wait"]:
            self._drain(window_start, opts["drain_timeout"])
        self._summary(window_start, enqueued=enqueued, elapsed_s=time.monotonic() - t0)

    # ── phase 1: setup ──────────────────────────────────────────────────────

    def _setup(self, opts: dict) -> None:
        mix = self._parse_mix(opts["profile_mix"])
        scaled = [p for p in mix if p not in FAILURE_PROFILES]
        n_failure = sum(1 for p in mix if p in FAILURE_PROFILES)
        count = max(1, (opts["reports_per_org"] - n_failure) // max(1, len(scaled)))

        for i in range(1, opts["orgs"] + 1):
            slug = f"sim-{i:02d}"
            call_command(
                "seed_stress_reports",
                org=slug,
                studio=SIM_STUDIO,
                profiles=",".join(mix),
                count_per_profile=count,
                db_source=opts["db_source"],
                db_database=opts["db_database"],
                seed=opts["seed"] + i,
                stdout=self.stdout,
                stderr=self.stderr,
            )
            Studio.objects.filter(org__slug=slug, slug=SIM_STUDIO).update(pool=opts["pool"])
        self.stdout.write(self.style.SUCCESS(f"setup complete: {opts['orgs']} sim org(s)"))

    def _parse_mix(self, raw: str) -> dict[str, int]:
        mix: dict[str, int] = {}
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            name, _, weight = part.partition(":")
            try:
                mix[name.strip()] = max(1, int(weight or "1"))
            except ValueError:
                raise CommandError(f"bad --profile-mix entry: {part!r}") from None
        if not mix:
            raise CommandError("--profile-mix is empty")
        return mix

    # ── phase 2: drive ──────────────────────────────────────────────────────

    def _build_deck(self, opts: dict) -> list[tuple[int, int]]:
        """(report_id, weight) for every present stress report in sim orgs."""
        mix = self._parse_mix(opts["profile_mix"])
        deck = []
        rows = Report.objects.filter(
            studio__org__slug__startswith="sim-",
            studio__slug=SIM_STUDIO,
            present_in_scan=True,
            disabled=False,
        ).values_list("id", "slug")
        for report_id, slug in rows:
            # stress-<profile>-NN; showcase slugs default to weight 1
            profile = slug.split("-")[1] if slug.startswith("stress-") else ""
            deck.append((report_id, mix.get(profile, 1)))
        return deck

    def _drive(self, deck: list[tuple[int, int]], opts: dict) -> int:
        rng = random.Random(opts["seed"])
        enqueued = skipped = 0
        for burst in range(1, opts["bursts"] + 1):
            # Weighted shuffle, then dedup: a heavier profile is more likely
            # to land in the burst, but each report appears at most once.
            weighted = [rid for rid, w in deck for _ in range(w)]
            rng.shuffle(weighted)
            picks: list[int] = []
            seen: set[int] = set()
            for rid in weighted:
                if rid not in seen:
                    seen.add(rid)
                    picks.append(rid)
                if len(picks) >= opts["burst_size"]:
                    break

            burst_ok = burst_skip = 0
            for report in Report.objects.filter(id__in=picks):
                result = services.enqueue(
                    report, cache_mode=opts["cache_mode"], trigger="manual"
                )
                if result == "queued":
                    burst_ok += 1
                else:
                    burst_skip += 1
            enqueued += burst_ok
            skipped += burst_skip
            self.stdout.write(
                f"burst {burst}/{opts['bursts']}: enqueued {burst_ok}, "
                f"skipped {burst_skip} (already running/queued)"
            )
            self._progress_line()
            if burst < opts["bursts"]:
                self._sleep_with_progress(opts["burst_interval"])
        self.stdout.write(f"driving done: {enqueued} enqueued, {skipped} skipped")
        return enqueued

    # ── phase 3+4: progress + drain ─────────────────────────────────────────

    def _sim_runs(self, window_start):
        return Run.objects.filter(
            studio__org__slug__startswith="sim-", created_at__gte=window_start
        )

    def _progress_line(self, window_start=None) -> int:
        qs = self._sim_runs(window_start) if window_start else Run.objects.filter(
            studio__org__slug__startswith="sim-"
        )
        by = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
        active = sum(by.get(s, 0) for s in Run.ACTIVE_STATUSES)
        done = {
            "S": by.get(Run.SUCCESS, 0), "E": by.get(Run.ERROR, 0),
            "T": by.get(Run.TIMEOUT, 0), "O": by.get(Run.OOM_KILLED, 0),
            "X": by.get(Run.STOPPED, 0),
        }
        workers = list(WorkerHeartbeat.alive())
        wtxt = ", ".join(
            f"{w.worker_id}[{w.running_count}/{w.max_concurrent} "
            f"{w.reserved_memory_mb}/{w.memory_budget_mb}MB]"
            for w in workers
        ) or "NONE"
        self.stdout.write(
            f"  queued {by.get(Run.QUEUED, 0)} | starting/running "
            f"{active - by.get(Run.QUEUED, 0)} | done "
            + " ".join(f"{k}:{v}" for k, v in done.items())
            + f" | workers: {wtxt}"
        )
        if not workers:
            self.stderr.write(self.style.WARNING(
                "  !! no live worker heartbeat -- nothing will execute. Start one, e.g.: "
                "docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d worker"
            ))
        return active

    def _sleep_with_progress(self, seconds: int) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(min(5, max(0.1, end - time.monotonic())))
            self._progress_line()

    def _drain(self, window_start, drain_timeout: int) -> None:
        self.stdout.write("draining (Ctrl+C to skip straight to the summary)...")
        deadline = time.monotonic() + drain_timeout
        try:
            while time.monotonic() < deadline:
                active = self._sim_runs(window_start).filter(
                    status__in=Run.ACTIVE_STATUSES
                ).count()
                if active == 0:
                    self.stdout.write("drained: no active sim runs left")
                    return
                self._progress_line(window_start)
                time.sleep(5)
            self.stderr.write(self.style.WARNING(
                f"drain timeout ({drain_timeout}s) hit with runs still active"
            ))
        except KeyboardInterrupt:
            self.stdout.write("interrupted -- summarizing what finished so far")

    # ── phase 5: summary ────────────────────────────────────────────────────

    def _summary(self, window_start, *, enqueued: int | None, elapsed_s: float | None) -> None:
        qs = self._sim_runs(window_start)
        total = qs.count()
        if not total:
            self.stdout.write("no sim runs in the window -- nothing to summarize")
            return

        terminal = qs.filter(status__in=stats.TERMINAL_STATUSES)
        agg = terminal.aggregate(
            completed=Count("id"),
            p50_wait=stats.Percentile(stats.queue_wait_s(), 0.5),
            p90_wait=stats.Percentile(stats.queue_wait_s(), 0.9),
            p99_wait=stats.Percentile(stats.queue_wait_s(), 0.99),
            p50_dur=stats.Percentile(stats.duration_s(), 0.5),
            p95_dur=stats.Percentile(stats.duration_s(), 0.95),
            peak_min=Min("peak_memory_mb"),
            peak_avg=Avg("peak_memory_mb"),
            peak_max=Max("peak_memory_mb"),
            peak_null=Count("id", filter=Q(peak_memory_mb__isnull=True)),
        )
        by = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}

        w = self.stdout.write
        w("")
        w("=== simulate_load summary ===")
        w(f"window start : {window_start.isoformat()}")
        if enqueued is not None:
            w(f"enqueued     : {enqueued}")
        w(f"runs in window: {total} (completed {agg['completed']})")
        if elapsed_s and agg["completed"]:
            w(f"throughput   : {agg['completed'] / (elapsed_s / 60):.1f} completed/min "
              f"over {elapsed_s:.0f}s")
        w("status counts: " + ", ".join(f"{s}={n}" for s, n in sorted(by.items())))

        def fmt(v, suffix=""):
            return "-" if v is None else f"{v:.1f}{suffix}"

        w(f"queue wait   : p50 {fmt(agg['p50_wait'], 's')}  p90 {fmt(agg['p90_wait'], 's')}  "
          f"p99 {fmt(agg['p99_wait'], 's')}")
        w(f"duration     : p50 {fmt(agg['p50_dur'], 's')}  p95 {fmt(agg['p95_dur'], 's')}")
        w(f"peak memory  : min {fmt(agg['peak_min'], 'MB')}  avg {fmt(agg['peak_avg'], 'MB')}  "
          f"max {fmt(agg['peak_max'], 'MB')}  (no reading: {agg['peak_null']} run(s))")
        if agg["completed"] and agg["peak_null"] == agg["completed"]:
            w("  note: no peak-memory readings -- the worker platform can't measure it")

        w("")
        w("per-org fairness:")
        w(f"  {'org':<10} {'enq':>5} {'start':>5} {'done':>5} {'avg wait':>9} {'max wait':>9}")
        org_rows = list(
            qs.values("studio__org__slug").annotate(
                enq=Count("id"),
                started=Count("id", filter=Q(started_at__isnull=False)),
                done=Count("id", filter=Q(status__in=stats.TERMINAL_STATUSES)),
                avg_wait=Avg(stats.queue_wait_s()),
                max_wait=Max(stats.queue_wait_s()),
            ).order_by("studio__org__slug")
        )
        for r in org_rows:
            w(f"  {r['studio__org__slug']:<10} {r['enq']:>5} {r['started']:>5} "
              f"{r['done']:>5} {fmt(r['avg_wait'], 's'):>9} {fmt(r['max_wait'], 's'):>9}")
        w(f"Jain index (avg wait)  : {_jain([r['avg_wait'] for r in org_rows])}")
        w(f"Jain index (completions): {_jain([float(r['done']) for r in org_rows])}")

    def _parse_since(self, raw: str):
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            raise CommandError(f"--since is not an ISO timestamp: {raw!r}") from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt

    # ── cleanup ─────────────────────────────────────────────────────────────

    def _cleanup(self, opts: dict) -> None:  # noqa: ARG002
        import re
        import shutil

        orgs = [
            o for o in Organization.objects.filter(slug__startswith="sim-")
            if re.fullmatch(r"sim-\d{2}", o.slug)
        ]
        if not orgs:
            self.stdout.write("no sim-NN orgs to clean up")
            return
        for org in orgs:
            # Runs first so Report's PROTECT never has anything to protect.
            Run.objects.filter(studio__org=org).delete()
            for studio in org.studios.all():
                shutil.rmtree(studio.data_root, ignore_errors=True)
            slug = org.slug
            org.delete()
            self.stdout.write(f"removed {slug}")
        self.stdout.write(self.style.SUCCESS(f"cleaned up {len(orgs)} sim org(s)"))

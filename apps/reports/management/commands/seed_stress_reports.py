"""Dev-only: seed stress reports that make the queue worth watching.

The demo reports finish in ~2 s and use no memory, so queue depth, memory
admission, OOM kills, timeouts, and fairness are invisible locally. This
command writes parameterized report directories whose generators actually
sleep, allocate-and-touch memory, scan a real Postgres datasource, fail,
trip the RLIMIT_AS cap, or blow past a container's memory limit.

    manage.py seed_stress_reports --org sim-01 --studio stress
    manage.py seed_stress_reports --org sim-acme --studio analytics --showcase
    manage.py seed_stress_reports --org sim-01 --studio stress --remove

Profiles (each seeded ``--count-per-profile`` times with scaled parameters):

  sleep    sleeps N seconds in 1 s slices             -> SUCCESS
  memory   allocates and touches ~70% of its budget   -> SUCCESS + real peak
  dbscan   aggregates + pages through the synthetic
           Postgres datasource (see
           ``seed_synthetic_datasource``)              -> SUCCESS, DB-bound
  mixed    sleep + allocate + small scan, jittered    -> SUCCESS
  failing  raises RuntimeError                        -> ERROR
  memhog   allocates 4 GB against a small budget      -> ERROR (MemoryError
           under RLIMIT_AS; still ERROR where enforcement is off)
  oom      allocates-and-touches ~2 GB resident; on a
           worker container with mem_limit < 2 GB the
           kernel OOM killer SIGKILLs it              -> OOM_KILLED
           (on an unconstrained host it just succeeds with a big peak)
  timeout  sleeps an hour against a 10 s timeout      -> TIMEOUT

``--showcase`` instead seeds ~20 realistically named reports across
categories, all querying the synthetic datasource and rendering the results
as HTML tables — an org/studio meant for driving by hand from the dashboard.

Params are baked into the generated files at seed time. Every generated file
carries MARKER, and --remove deletes only directories that contain it.
"""
from __future__ import annotations

import random
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.orgs.models import Organization
from apps.reports.scan import ReportQuotaExceeded, sync_studio_registry
from apps.studios.models import Studio

MARKER = "seeded-by: seed_stress_reports"

PROFILES = ("sleep", "memory", "dbscan", "mixed", "failing", "memhog", "oom", "timeout")

SHOWCASE_CATEGORIES = [
    "Retention", "Acquisition", "Monetisation", "Live Ops", "Marketing",
    "Finance", "Progression", "Economy", "Cohorts", "Forecasting",
]
SHOWCASE_KINDS = ["Overview", "Deep Dive"]

YAML_TEMPLATE = """# {marker}
# Stress report ({profile}) — safe to delete, or run
# `manage.py seed_stress_reports --org {org} --studio {studio} --remove`.
name: "{name}"
slug: {slug}
description: "{description}"
version: 0.1.0

category: {category}
tags:
  - stress
  - seeded

display:
  priority: {priority}
"""

# Shared helper source, embedded verbatim into generators that need it. Each
# generator is self-contained on purpose: sandboxes copy the report dir plus
# reports/_* siblings, and no-sibling generators keep the seeder simple.
HELPERS = '''
import os
import re
import time


def _sleep_loud(total_s):
    t = 0
    while t < total_s:
        time.sleep(min(1, total_s - t))
        t += 1
        if t % 5 == 0 or t >= total_s:
            print("stress: %ds elapsed of %ds" % (t, total_s), flush=True)


def _alloc_mb(total_mb, touch=True, step_mb=8):
    """Allocate total_mb as bytearray blocks; touch every page so RSS (and
    VmHWM, and the cgroup accounting) is real, not just address space."""
    blocks = []
    n = max(1, int(total_mb // step_mb))
    for i in range(n):
        b = bytearray(step_mb * 1024 * 1024)
        if touch:
            for off in range(0, len(b), 4096):
                b[off] = 1
        blocks.append(b)
        if (i + 1) % 16 == 0:
            print("stress: allocated %d MB" % ((i + 1) * step_mb), flush=True)
    return blocks


def _connect_synth(dbname):
    """Find the synthetic datasource by scanning TRELLUM_DS_* env vars (the pk in
    the prefix is unknowable at seed time) and open a psycopg connection."""
    import psycopg

    for key, val in os.environ.items():
        m = re.fullmatch(r"(TRELLUM_DS_\\d+)_DB", key)
        if m and val == dbname:
            p = m.group(1)
            conn = psycopg.connect(
                host=os.environ.get(p + "_HOST", ""),
                port=int(os.environ.get(p + "_PORT", "") or 5432),
                dbname=val,
                user=os.environ.get(p + "_USER", ""),
                password=os.environ.get(p + "_PASS", ""),
                connect_timeout=20,
            )
            with conn.cursor() as cur:
                # The synthetic table lives in public (own-database mode) or
                # in the "synth" schema (--reuse-portal-db mode).
                cur.execute("SET search_path TO public, synth")
            return conn
    raise RuntimeError(
        "stress: no TRELLUM_DS_*_DB env var matches database %r -- register it with "
        "manage.py seed_synthetic_datasource first" % (dbname,)
    )


def _rows_table(headers, rows):
    html = "<table border=1 cellpadding=4 cellspacing=0>"
    html += "<tr>" + "".join("<th>%s</th>" % h for h in headers) + "</tr>"
    for row in rows:
        html += "<tr>" + "".join("<td>%s</td>" % c for c in row) + "</tr>"
    return html + "</table>"
'''

GENERATOR_TEMPLATE = '''"""{marker} (profile: {profile})"""

from trellum import BaseReport
from trellum.components import RawHTML

{helpers}

{constants}


class StressReport(BaseReport):
    def generate(self, ctx):
{body}
'''

PROFILE_BODIES = {
    "sleep": '''\
        print("stress-sleep: sleeping %ds" % SLEEP_SECONDS, flush=True)
        _sleep_loud(SLEEP_SECONDS)
        ctx.add_section("Stress: sleep", [RawHTML(
            "<p>Slept <strong>%ds</strong> to simulate a long-running build.</p>"
            % SLEEP_SECONDS
        )])
''',
    "memory": '''\
        print("stress-memory: allocating %d MB" % ALLOC_MB, flush=True)
        blocks = _alloc_mb(ALLOC_MB)
        _sleep_loud(HOLD_SECONDS)
        n = len(blocks)
        del blocks
        ctx.add_section("Stress: memory", [RawHTML(
            "<p>Held <strong>%d MB</strong> resident (%d blocks, every page "
            "touched) for %ds.</p>" % (ALLOC_MB, n, HOLD_SECONDS)
        )])
''',
    "dbscan": '''\
        print("stress-dbscan: querying %s" % SYNTH_DB, flush=True)
        conn = _connect_synth(SYNTH_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT category, count(*) AS n, round(sum(amount), 2) AS total"
                    " FROM events GROUP BY category ORDER BY n DESC"
                )
                cats = cur.fetchall()
                print("stress-dbscan: %d categories aggregated" % len(cats), flush=True)
                scanned = 0
                checksum = 0
                last_id = 0
                while scanned < SCAN_ROWS:
                    cur.execute(
                        "SELECT id, user_id FROM events"
                        " WHERE id > %s ORDER BY id LIMIT 100000",
                        (last_id,),
                    )
                    rows = cur.fetchall()
                    if not rows:
                        break
                    scanned += len(rows)
                    last_id = rows[-1][0]
                    checksum ^= sum(r[1] for r in rows)
                    print("stress-dbscan: scanned %d rows" % scanned, flush=True)
        finally:
            conn.close()
        ctx.add_section("Stress: database scan", [RawHTML(
            "<p>Scanned <strong>%d</strong> rows (checksum %d).</p>"
            % (scanned, checksum)
            + _rows_table(("Category", "Rows", "Amount"), cats)
        )])
''',
    "mixed": '''\
        rng_sleep = SLEEP_SECONDS
        print("stress-mixed: sleep %ds, alloc %d MB" % (rng_sleep, ALLOC_MB), flush=True)
        blocks = _alloc_mb(ALLOC_MB)
        _sleep_loud(rng_sleep)
        n = len(blocks)
        del blocks
        parts = ["<p>Slept %ds while holding %d MB.</p>" % (rng_sleep, ALLOC_MB)]
        if SCAN_ROWS:
            conn = _connect_synth(SYNTH_DB)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM events WHERE id <= %s", (SCAN_ROWS,))
                    counted = cur.fetchone()[0]
            finally:
                conn.close()
            parts.append("<p>Counted %d synthetic rows.</p>" % counted)
        ctx.add_section("Stress: mixed", [RawHTML("".join(parts))])
''',
    "failing": '''\
        print("stress-failing: about to raise", flush=True)
        raise RuntimeError("stress-failing: intentional failure for load testing")
''',
    "memhog": '''\
        # Allocates far past the build memory limit. Under
        # TRELLUM_JOB_MEMORY_ENFORCE the RLIMIT_AS cap turns this into a
        # MemoryError; where enforcement is off we fail explicitly so the
        # run is an ERROR either way.
        print("stress-memhog: allocating %d MB against a small budget" % ALLOC_MB, flush=True)
        _alloc_mb(ALLOC_MB, touch=False)
        raise RuntimeError(
            "stress-memhog: allocation of %d MB unexpectedly succeeded -- "
            "memory enforcement is off on this worker" % ALLOC_MB
        )
''',
    "oom": '''\
        # Touches pages far past the worker container's mem_limit, in steps,
        # so the kernel OOM killer SIGKILLs this process (status OOM_KILLED).
        # Needs a build memory limit above ALLOC_MB (or enforcement off) so the
        # RLIMIT_AS cap does not fire first. On an unconstrained host this succeeds
        # with a very large peak_memory_mb.
        print("stress-oom: touching %d MB resident" % ALLOC_MB, flush=True)
        blocks = _alloc_mb(ALLOC_MB, step_mb=64)
        n = len(blocks)
        del blocks
        ctx.add_section("Stress: oom", [RawHTML(
            "<p>Touched <strong>%d MB</strong> without being OOM-killed -- "
            "this worker has no effective memory limit.</p>" % ALLOC_MB
        )])
''',
    "timeout": '''\
        print("stress-timeout: sleeping past the configured timeout", flush=True)
        _sleep_loud(3600)
''',
    "showcase": '''\
        print("showcase: building from %s" % SYNTH_DB, flush=True)
        if WORK_ALLOC_MB:
            blocks = _alloc_mb(WORK_ALLOC_MB)
        if WORK_SLEEP_SECONDS:
            _sleep_loud(WORK_SLEEP_SECONDS)
        conn = _connect_synth(SYNTH_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT category, count(*) AS n, round(avg(amount), 2) AS avg_amount,"
                    " round(sum(amount), 2) AS total FROM events"
                    " WHERE id <= %s GROUP BY category ORDER BY total DESC",
                    (SCAN_ROWS,),
                )
                cats = cur.fetchall()
                cur.execute(
                    "SELECT date_trunc('day', ts)::date AS day, count(*) AS n,"
                    " round(sum(amount), 2) AS total FROM events"
                    " WHERE id <= %s GROUP BY day ORDER BY day DESC LIMIT 14",
                    (SCAN_ROWS,),
                )
                days = cur.fetchall()
                cur.execute("SELECT count(*) FROM events")
                total_rows = cur.fetchone()[0]
        finally:
            conn.close()
        if WORK_ALLOC_MB:
            del blocks
        ctx.add_section("Category performance", [RawHTML(
            "<p>Aggregated over %d of %d synthetic events.</p>" % (SCAN_ROWS, total_rows)
            + _rows_table(("Category", "Events", "Avg amount", "Total"), cats)
        )])
        ctx.add_section("Daily totals (last 14 days in range)", [RawHTML(
            _rows_table(("Day", "Events", "Total"), days)
        )])
''',
}


def _scaled(base: int, variant: int, count: int, floor: int) -> int:
    return max(floor, round(base * variant / count))


class Command(BaseCommand):
    help = "Dev-only: seed heavy stress reports for load-testing the runner."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="sim-01")
        parser.add_argument("--studio", default="stress")
        parser.add_argument("--profiles", default=",".join(PROFILES))
        parser.add_argument("--count-per-profile", type=int, default=3)
        parser.add_argument("--sleep-seconds", type=int, default=20)
        parser.add_argument("--memory-mb", type=int, default=512)
        parser.add_argument("--db-source", default="synthetic",
                            help="Name of the DataSource created by seed_synthetic_datasource.")
        parser.add_argument("--db-database", default="trellum_synth",
                            help="Postgres database name the generators look up in TRELLUM_DS_* env.")
        parser.add_argument("--db-rows", type=int, default=2_000_000)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--showcase", action="store_true",
                            help="Seed ~20 realistic dbscan-backed reports for manual use.")
        parser.add_argument("--remove", action="store_true")

    def handle(self, *args, **opts):  # noqa: ARG002
        if not settings.DEBUG:
            raise CommandError("seed_stress_reports is a development helper; DEBUG is off.")

        studio = self._get_or_create_studio(opts["org"], opts["studio"])
        reports_dir = Path(str(studio.reports_dir))

        if opts["remove"]:
            count = self._remove(reports_dir)
            verb = "removed"
        else:
            reports_dir.mkdir(parents=True, exist_ok=True)
            self._warn_if_source_missing(studio, opts["db_source"])
            if opts["showcase"]:
                count = self._add_showcase(reports_dir, opts)
            else:
                count = self._add_profiles(reports_dir, opts)
            verb = "wrote"

        try:
            total = sync_studio_registry(studio)
        except ReportQuotaExceeded as exc:
            total = studio.reports.filter(present_in_scan=True).count()
            self.stderr.write(self.style.WARNING(str(exc)))
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {count} stress report(s); {studio} now scans {total} reports"
        ))

    # ── setup ───────────────────────────────────────────────────────────────

    def _get_or_create_studio(self, org_slug: str, studio_slug: str) -> Studio:
        org, org_created = Organization.objects.get_or_create(
            slug=org_slug, defaults={"name": org_slug}
        )
        studio, studio_created = Studio.objects.get_or_create(
            org=org, slug=studio_slug, defaults={"name": studio_slug.title()}
        )
        studio.ensure_dirs()
        if org_created or studio_created:
            self.stdout.write(f"created {org.slug}/{studio.slug}")
        return studio

    def _warn_if_source_missing(self, studio: Studio, name: str) -> None:
        from apps.datasources.models import sources_for_studio

        names = {s.name for s in sources_for_studio(studio)}
        if name not in names:
            self.stderr.write(self.style.WARNING(
                f"datasource {name!r} is not visible to {studio} -- dbscan/mixed/"
                f"showcase reports will fail until you run seed_synthetic_datasource "
                f"--org {studio.org.slug} --name {name}"
            ))

    # ── profile mode ────────────────────────────────────────────────────────

    def _add_profiles(self, reports_dir: Path, opts: dict) -> int:
        profiles = [p.strip() for p in opts["profiles"].split(",") if p.strip()]
        unknown = [p for p in profiles if p not in PROFILES]
        if unknown:
            raise CommandError(f"unknown profile(s): {', '.join(unknown)}")

        count = opts["count_per_profile"]
        written = 0
        for profile in profiles:
            # Failure-mode profiles are one-of-a-kind; scaling them adds noise.
            variants = 1 if profile in ("failing", "memhog", "oom", "timeout") else count
            for v in range(1, variants + 1):
                slug = f"stress-{profile}-{v:02d}"
                params = self._profile_params(profile, v, variants, opts)
                self._write_report(reports_dir, slug, profile, params, opts)
                written += 1
        return written

    def _profile_params(self, profile: str, v: int, count: int, opts: dict) -> dict:
        sleep_s = _scaled(opts["sleep_seconds"], v, count, floor=5)
        mem_mb = _scaled(opts["memory_mb"], v, count, floor=128)
        rows = _scaled(opts["db_rows"], v, count, floor=100_000)
        if profile == "sleep":
            return {
                "constants": f"SLEEP_SECONDS = {sleep_s}",
                "priority": (v * 3) % 10,
                "description": f"Sleeps {sleep_s}s to simulate a slow build.",
            }
        if profile == "memory":
            alloc = max(64, round(mem_mb * 0.7))
            return {
                "constants": f"ALLOC_MB = {alloc}\nHOLD_SECONDS = 5",
                "priority": (v * 3) % 10,
                "description": f"Holds {alloc} MB resident.",
            }
        if profile == "dbscan":
            return {
                "constants": f'SYNTH_DB = "{opts["db_database"]}"\nSCAN_ROWS = {rows}',
                "priority": (v * 3) % 10,
                "description": f"Aggregates and scans {rows:,} synthetic Postgres rows.",
            }
        if profile == "mixed":
            return {
                "constants": (
                    f"SLEEP_SECONDS = {max(5, sleep_s // 2)}\n"
                    f"ALLOC_MB = {max(64, mem_mb // 4)}\n"
                    f'SYNTH_DB = "{opts["db_database"]}"\n'
                    f"SCAN_ROWS = {rows // 4}"
                ),
                "priority": (v * 3) % 10,
                "description": "Sleep + memory + a small database scan.",
            }
        if profile == "failing":
            return {
                "constants": "", "priority": 0,
                "description": "Always fails (intentional).",
            }
        if profile == "memhog":
            return {
                "constants": "ALLOC_MB = 4096", "priority": 0,
                "description": "Allocates 4 GB against the build memory limit (expects MemoryError).",
            }
        if profile == "oom":
            return {
                "constants": "ALLOC_MB = 2048", "priority": 0,
                "description": "Touches 2 GB resident; OOM-killed on a mem-limited worker.",
            }
        # timeout
        return {
            "constants": "", "priority": 0,
            "description": "Sleeps an hour, past the runner's timeout.",
        }

    # ── showcase mode ───────────────────────────────────────────────────────

    def _add_showcase(self, reports_dir: Path, opts: dict) -> int:
        rng = random.Random(opts["seed"])
        written = 0
        for category in SHOWCASE_CATEGORIES:
            for kind in SHOWCASE_KINDS:
                slug = f"{category.lower().replace(' ', '-')}-{kind.lower().replace(' ', '-')}"
                heavy = kind == "Deep Dive"
                alloc = rng.choice([0, 64, 128, 256]) + (256 if heavy else 0)
                sleep_s = rng.randint(2, 8) + (rng.randint(10, 25) if heavy else 0)
                rows = rng.choice([100_000, 250_000, 500_000]) * (4 if heavy else 1)
                params = {
                    "constants": (
                        f'SYNTH_DB = "{opts["db_database"]}"\n'
                        f"SCAN_ROWS = {rows}\n"
                        f"WORK_SLEEP_SECONDS = {sleep_s}\n"
                        f"WORK_ALLOC_MB = {alloc}"
                    ),
                    "priority": rng.randint(0, 9),
                    "description": (
                        f"{category} {kind.lower()} over {rows:,} synthetic events"
                        + (f", ~{sleep_s}s of work" if sleep_s else "")
                    ),
                    "category": category,
                    "name": f"{category} {kind}",
                }
                self._write_report(reports_dir, slug, "showcase", params, opts)
                written += 1
        return written

    # ── file writing ────────────────────────────────────────────────────────

    def _write_report(
        self, reports_dir: Path, slug: str, profile: str, params: dict, opts: dict
    ) -> None:
        target = reports_dir / slug
        target.mkdir(parents=True, exist_ok=True)
        (target / "report.yaml").write_text(
            YAML_TEMPLATE.format(
                marker=MARKER,
                profile=profile,
                org=opts["org"],
                studio=opts["studio"],
                name=params.get("name", slug.replace("-", " ").title()),
                slug=slug,
                description=params["description"],
                category=params.get("category", "Stress"),
                priority=params["priority"],
            ),
            encoding="utf-8",
        )
        (target / "generator.py").write_text(
            GENERATOR_TEMPLATE.format(
                marker=MARKER,
                profile=profile,
                helpers=HELPERS,
                constants=params["constants"],
                body=PROFILE_BODIES[profile],
            ),
            encoding="utf-8",
        )
        (target / "__init__.py").write_text("", encoding="utf-8")

    def _remove(self, reports_dir: Path) -> int:
        import shutil

        count = 0
        if not reports_dir.is_dir():
            return 0
        for child in sorted(reports_dir.iterdir()):
            config = child / "report.yaml"
            if not config.is_file():
                continue
            # Only ever delete what this command wrote.
            if MARKER in config.read_text(encoding="utf-8"):
                shutil.rmtree(child)
                count += 1
        return count

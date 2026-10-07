"""Building a report: generate, validate, render, publish."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from trellum.project import get_project_root
from trellum.rendering.html_builder import render_report
from trellum.report import BaseReport, ReportContext, _validate_output_filename
from trellum.runner.console import _ts
from trellum.runner.discovery import (
    _discover_all_reports,
    _discover_project_components,
    _discover_project_themes,
    discover_report,
)
from trellum.runner.events import (
    _load_events,
    _weekday_highlight_config,
    local_annotation_events,
)


def _validate_report_slug(slug: object) -> str:
    """Reject manifest slugs that could select an output path."""
    if (
        not isinstance(slug, str)
        or slug in ("", ".", "..")
        or slug != slug.rstrip(" .")
        or os.path.isabs(slug)
        or ":" in slug
        or "/" in slug
        or "\\" in slug
    ):
        raise ValueError(
            f"Report slug {slug!r} is invalid: use a single path-safe name"
        )
    try:
        _validate_output_filename(slug)
    except ValueError as exc:
        raise ValueError(f"Report slug {slug!r} is invalid: use a single path-safe name") from exc
    return slug


def _standard_output_dir(slug: str) -> str:
    """Return the default output path unless it resolves outside its root."""
    output_root = os.path.join(get_project_root(), "output")
    output_dir = os.path.join(output_root, slug)
    resolved_root = os.path.realpath(output_root)
    resolved_output = os.path.realpath(output_dir)
    try:
        contained = os.path.normcase(
            os.path.commonpath((resolved_root, resolved_output))
        ) == os.path.normcase(resolved_root)
    except ValueError:
        contained = False
    if not contained:
        raise ValueError(
            f"Report slug {slug!r} resolves outside the standard output directory"
        )
    return output_dir


def _output_is_fresh(output_dir: str, max_age: int) -> bool:
    """Check if a report's output is recent enough to skip regeneration."""
    meta_path = os.path.join(output_dir, "_meta.json")
    if not os.path.exists(meta_path):
        return False
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        last_run = meta.get("last_run")
        if not last_run or meta.get("last_status") != "success":
            return False
        from datetime import datetime, timezone
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return age < max_age
    except Exception as exc:
        print(f"  WARNING: Could not check output freshness: {exc}", flush=True)
        return False

def run_report(
    report_dir: str,
    output_dir: str | None = None,
    production: bool = False,
    auto_refresh: bool = True,
    max_age: int = 0,
    fail_on_validation: bool | None = None,
    debug: bool = False,
) -> str:
    """Build a report; analysis artifacts are staged until the entire build succeeds."""
    config = BaseReport.load_config(os.path.abspath(report_dir))
    slug = _validate_report_slug(config.get("slug", os.path.basename(os.path.abspath(report_dir))))
    out = output_dir if output_dir is not None else _standard_output_dir(slug)
    options = dict(production=production, auto_refresh=auto_refresh, max_age=max_age,
                   fail_on_validation=fail_on_validation, debug=debug)
    if (config.get("kind", "report") == "analysis" and not config.get("disabled")
            and not (max_age > 0 and _output_is_fresh(out, max_age))):
        from trellum.runner.analysis_build import staged_analysis_build
        return staged_analysis_build(report_dir, out, **options)
    return _run_report(report_dir, output_dir, **options)


def _prepare_report(report_dir: str, config: dict):
    report = discover_report(report_dir)()
    if config.get("kind", "report") == "analysis":
        from trellum.themes import BUILTIN_THEMES, DEFAULT_THEME_NAME, effective_theme_name
        if effective_theme_name(config.get("theme")) not in BUILTIN_THEMES:
            print("  Warning: unknown analysis theme, using framework default", flush=True)
            config["theme"] = DEFAULT_THEME_NAME
    else:
        _discover_project_themes(get_project_root())
        _discover_project_components(get_project_root())
    return report


def _resolve_report_theme(config: dict):
    from trellum.themes import (
        BUILTIN_THEMES,
        DEFAULT_THEME_NAME,
        effective_theme_name,
        resolve_theme,
    )
    if config.get("kind") == "analysis":
        return BUILTIN_THEMES.get(effective_theme_name(config.get("theme")), BUILTIN_THEMES[DEFAULT_THEME_NAME])
    return resolve_theme(config.get("theme"))


def _run_report(
    report_dir: str,
    output_dir: str | None = None,
    production: bool = False,
    auto_refresh: bool = True,
    max_age: int = 0,
    fail_on_validation: bool | None = None,
    debug: bool = False,
) -> str:
    """Run a single report and write output.

    Args:
        auto_refresh: When False, the generated HTML will not include
            client-side ``setInterval`` polling for data.json updates.
        max_age: If > 0, skip regeneration when the existing output is
            younger than this many seconds.  0 = always regenerate.
        fail_on_validation: If True, raise an error when validation has
            FAIL-level findings (prevents rendering broken reports).
            Defaults to True when ``production=True``, False otherwise.

    Returns the output directory path.
    """
    report_dir = os.path.abspath(report_dir)
    config = BaseReport.load_config(report_dir)
    slug = _validate_report_slug(config.get("slug", os.path.basename(report_dir)))
    if output_dir is None:
        output_dir = _standard_output_dir(slug)

    if debug:
        os.environ['FRAMEWORK_DEBUG'] = '1'

    if config.get("disabled"):
        print(f"Report {slug} is disabled in report.yaml — skipping.", flush=True)
        return output_dir

    if max_age > 0 and _output_is_fresh(output_dir, max_age):
        print(f"[{_ts()}] Skipping {slug} (output is fresh)", flush=True)
        return output_dir

    report = _prepare_report(report_dir, config)

    from trellum.rendering.cdn import register_cdn

    # Register extra CDNs from report.yaml
    for cdn_name, cdn_url in config.get("extra_cdn", {}).items():
        register_cdn(cdn_name, cdn_url)

    theme = None
    theme_name = config.get("theme")
    try:
        theme = _resolve_report_theme(config)
    except ValueError:
        print(f"  Warning: unknown theme '{theme_name}', using default")

    ctx = ReportContext(
        config=config,
        slug=slug,
        output_dir=output_dir,
        theme=theme,
    )
    ctx.debug = debug

    # Events are loaded AFTER generate() so we know the scopes the
    # report declared; initialized empty here for debug banner safety.
    events: list[dict] = []

    # ── Phase: Banner ──────────────────────────────────────
    print(f"[{_ts()}] -- {slug} {'--' * max(1, (40 - len(slug)) // 2)}", flush=True)

    if debug:
        studio = config.get("studio", "unknown")
        category = config.get("category", "unknown")
        theme_label = theme_name or "default"
        print(f"[{_ts()}] [DEBUG] Report config: studio={studio}, category={category}, theme={theme_label}", flush=True)
        sources = config.get("data_sources", [])
        if sources:
            # data_sources entries may be bare strings referencing
            # data-sources/config.yaml -- normalize before reading fields.
            from trellum.data.connections import _normalize_sources
            for src in _normalize_sources(sources):
                src_name = src.get("name", "unknown")
                src_type = src.get("type", "unknown")
                print(f"[{_ts()}] [DEBUG] Data source: {src_name} (type={src_type})", flush=True)

    # ── Phase: Fetching data ───────────────────────────────
    print(f"[{_ts()}]", flush=True)
    print(f"[{_ts()}] Fetching data:", flush=True)

    start = time.time()

    try:
        t0 = time.time()
        report.generate(ctx)
        ctx.mark("generate", time.time() - t0)

        # Scopes are declared inside generate() via ctx.set_scope. Load
        # events now so multi-scope reports also pick up per-scope events
        # (see _load_events docstring for the studio filter semantics).
        if config.get("kind", "report") != "analysis":
            events = _load_events(config, ctx)
            events.extend(local_annotation_events(config))
        if debug and events:
            event_types: dict[str, int] = {}
            for ev in events:
                event_types[ev.get("type", "unknown")] = event_types.get(ev.get("type", "unknown"), 0) + 1
            type_summary = ", ".join(f"{v} {k}" for k, v in event_types.items())
            dates = [ev.get("date", "") for ev in events if ev.get("date")]
            date_range = f", date range {min(dates)}..{max(dates)}" if dates else ""
            print(f"[{_ts()}] [DEBUG] Events: {len(events)} loaded ({type_summary}){date_range}", flush=True)

        # ── Phase: Diagnostics ─────────────────────────────
        from trellum.reporting.diagnostics import (
            compute_details,
            enrich_details_with_disk,
            write_details,
        )
        details = compute_details(ctx)

        # ── Phase: Validation ──────────────────────────────
        from trellum.validation import validate_report
        validation = validate_report(ctx, events=events, details=details)

        print(f"[{_ts()}]", flush=True)
        v_summary = validation.summary
        sup_n = v_summary.get("suppressed", 0)
        sup_suffix = f" ({sup_n} suppressed)" if sup_n else ""
        # State how much was examined, not only what was found. "all checks
        # passed" beside an empty checks list reads exactly like a validator
        # that never ran, and a reader with no way to tell the difference has
        # to go and find out -- one measured run did so by copying the report,
        # breaking it on purpose and re-running.
        groups = v_summary.get("groups_run", 0)
        ran = f"{groups} check group{'' if groups == 1 else 's'} evaluated"
        if v_summary.get("fail", 0) == 0 and v_summary.get("warn", 0) == 0:
            print(f"[{_ts()}] Validation: {ran}, nothing to report{sup_suffix}",
                  flush=True)
        else:
            print(f"[{_ts()}] Validation: {ran} -- "
                  f"{v_summary.get('pass', 0)} pass, {v_summary.get('warn', 0)} warn, "
                  f"{v_summary.get('fail', 0)} fail{sup_suffix}", flush=True)
        # Surface every non-pass check so the terminal log names
        # which rule fired (not just the counts). Without this the
        # only way to find the offender was the _validation.json.
        # Suppressed checks print at the bottom with a SUPRESS tag so
        # readers see what was deliberately silenced.
        for c in validation.checks:
            if c.suppressed:
                continue
            if c.level in ("fail", "warn", "info"):
                tag = c.level.upper().rjust(7)
                where = f" [{c.component}{' / ' + c.section if c.section else ''}]" if c.component else ""
                print(f"[{_ts()}]   {tag}  {c.id}: {c.message}{where}", flush=True)
        # Name the way out, at the point of failure. Otherwise the id is a
        # string to go and grep for -- which is measurably what happens.
        if v_summary.get("fail", 0) or v_summary.get("warn", 0):
            offenders = sorted({c.id for c in validation.checks
                                if not c.suppressed and c.level in ("fail", "warn")})
            print(f"[{_ts()}]", flush=True)
            print(f"[{_ts()}]   python -m trellum checks "
                  f"{' '.join(offenders[:4])}", flush=True)
            print(f"[{_ts()}]        what each of these means, verbatim from the "
                  f"validator", flush=True)
            print(f"[{_ts()}]   python -m trellum guide validation", flush=True)
            print(f"[{_ts()}]        the protocol, and how to suppress a WARN with "
                  f"a rationale", flush=True)

        if sup_n:
            print(f"[{_ts()}]", flush=True)
            print(f"[{_ts()}] Suppressed (deliberately silenced via report.yaml):", flush=True)
            for c in validation.checks:
                if not c.suppressed:
                    continue
                ds = f" on `{c.dataset_id}`" if c.dataset_id else ""
                print(f"[{_ts()}]   SUPPRESS  {c.id}{ds}: {c.message[:140]}", flush=True)

        if debug:
            total_checks = v_summary.get("pass", 0) + v_summary.get("warn", 0) + v_summary.get("fail", 0)
            print(f"[{_ts()}]   [DEBUG] Ran {total_checks} checks", flush=True)

        should_fail = fail_on_validation if fail_on_validation is not None else production
        if should_fail and validation.summary["fail"] > 0:
            # Persist the validation result BEFORE raising so a host's
            # details view reflects the failure instead of the previous green
            # run. Create the output dir first — it may not exist yet if
            # this is the report's first run.
            os.makedirs(output_dir, exist_ok=True)
            validation.write(output_dir)
            write_details(output_dir, details)
            n = validation.summary["fail"]
            raise RuntimeError(
                f"Validation has {n} FAIL(s) — aborting. "
                f"Fix the issues above or pass fail_on_validation=False to override."
            )

        # ── Phase: Building report ─────────────────────────
        stats = ctx.stats
        print(f"[{_ts()}]", flush=True)
        print(f"[{_ts()}] Building report:", flush=True)
        print(f"[{_ts()}]   -> {stats['sections']} sections, {stats['components']} components", flush=True)

        if debug:
            all_sections = list(ctx._sections)
            for scope_sections in ctx._scopes.values():
                all_sections.extend(scope_sections)
            for section in all_sections:
                title = section.get("title", "(untitled)")
                comps = section.get("components", [])
                comp_names = ", ".join(type(c).__name__ for c in comps) if comps else "none"
                print(f"[{_ts()}]   [DEBUG] Section \"{title}\": {comp_names}", flush=True)

        extra_data: dict = {}
        if events:
            extra_data["_events"] = events
        weekday_highlight = _weekday_highlight_config(config)
        if weekday_highlight:
            extra_data["_weekday_highlight"] = weekday_highlight
        if details.get("coverage_by_dataset"):
            extra_data["_filter_health"] = details["coverage_by_dataset"]
        if details.get("data_source") == "mock":
            extra_data["_is_mock_data"] = True
        t0 = time.time()
        html_path, json_path = render_report(
            ctx, output_dir, data=extra_data or None, auto_refresh=auto_refresh,
            validation=validation, production=production, details=details,
        )
        render_elapsed = time.time() - t0
        ctx.mark("render_total", render_elapsed)
        validation.write(output_dir)
        enrich_details_with_disk(details, output_dir)
        write_details(output_dir, details)
        # Re-write _meta.json so the embedded details block reflects disk reality.
        from trellum.meta import write_meta
        write_meta(output_dir, {"details": details})

        print(f"[{_ts()}]   -> Rendering HTML ............ {render_elapsed:.2f}s", flush=True)

        # Report data.json size if it exists
        data_json_path = os.path.join(output_dir, 'data.json')
        if os.path.exists(data_json_path):
            data_size = os.path.getsize(data_json_path)
            print(f"[{_ts()}]   -> Writing data.json ......... {data_size // 1024} KB", flush=True)

        elapsed = time.time() - start

        if debug:
            import tracemalloc as _tracemalloc
            try:
                # Only report memory if tracemalloc was started elsewhere;
                # avoid starting it here as it adds overhead.
                current, peak = _tracemalloc.get_traced_memory()
                print(f"[{_ts()}] [DEBUG] Memory: peak {peak // (1024 * 1024)} MB, current {current // (1024 * 1024)} MB", flush=True)
            except RuntimeError:
                pass
            parts = [f"{k}={v:.2f}s" for k, v in ctx._timings.items()]
            print(f"[{_ts()}] [DEBUG] Generation timings: {', '.join(parts)}", flush=True)

        print(f"[{_ts()}]", flush=True)
        print(f"[{_ts()}] Done in {elapsed:.1f}s -> {output_dir}", flush=True)
    except Exception as e:
        print(f"[ERROR] Report generation failed: {e}", flush=True)
        raise
    finally:
        ctx.close_connections()

    if production:
        from trellum.output_backends.backends import get_output_backend
        backend = get_output_backend()
        if backend is None:
            raise RuntimeError(
                "--production requires an output backend. Either set "
                "BI_STORAGE_BACKEND=local (output/ is a durable volume, so "
                "there is nothing to upload), or register your own with "
                "set_output_backend() during project bootstrap."
            )
        backend_name = type(backend).__name__
        try:
            backend.publish(output_dir, slug)
        except Exception as exc:
            print(
                f"[{_ts()}] [ERROR] {backend_name}.publish() failed for slug "
                f"'{slug}': {exc}",
                flush=True,
            )
            raise
        print(f"[{_ts()}] Published to {backend_name}", flush=True)

    return output_dir

def run_all_reports(
    max_concurrent: int = 3,
    studio: str | None = None,
    category: str | None = None,
    production: bool = False,
    max_age: int = 0,
    fail_on_validation: bool | None = None,
) -> int:
    """Run all matching reports with bounded concurrency.

    Args:
        max_age: If > 0, skip reports whose output is younger than this
            many seconds.  0 = always regenerate all.

    Returns 0 if all succeeded, 1 if any failed.
    """
    reports = _discover_all_reports(studio=studio, category=category)

    if not reports:
        filters = []
        if studio:
            filters.append(f"studio={studio}")
        if category:
            filters.append(f"category={category}")
        filter_str = f" (filters: {', '.join(filters)})" if filters else ""
        print(f"No reports found{filter_str}.", flush=True)
        return 0

    mode = f", skip if fresh (<{max_age}s)" if max_age else ""
    print(f"Running {len(reports)} report(s), max {max_concurrent} concurrent{mode}\n", flush=True)

    results: list[dict] = []

    def _run_one(entry: dict) -> dict:
        slug = entry["slug"]
        start = time.time()
        try:
            run_report(entry["dir"], production=production, max_age=max_age,
                       fail_on_validation=fail_on_validation)
            return {"slug": slug, "status": "success", "elapsed": time.time() - start}
        except Exception as e:
            return {"slug": slug, "status": "error", "elapsed": time.time() - start, "error": str(e)}

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {pool.submit(_run_one, entry): entry for entry in reports}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r["slug"])

    print("\n" + "=" * 64, flush=True)
    print("  BATCH SUMMARY", flush=True)
    print("=" * 64, flush=True)

    ok = sum(1 for r in results if r["status"] == "success")
    fail = sum(1 for r in results if r["status"] == "error")

    slug_width = max((len(r["slug"]) for r in results), default=20)
    for r in results:
        icon = "OK" if r["status"] == "success" else "FAIL"
        line = f"  [{icon:>4}]  {r['slug']:<{slug_width}}  {r['elapsed']:>6.1f}s"
        if r.get("error"):
            line += f"  ({r['error'][:60]})"
        print(line, flush=True)

    print("-" * 64, flush=True)
    print(f"  {ok} succeeded, {fail} failed, {len(results)} total", flush=True)
    print("=" * 64, flush=True)

    return 1 if fail > 0 else 0

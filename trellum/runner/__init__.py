"""Report runner CLI.

Usage:
    python -m trellum.run reports/daily_revenue              # run + serve on :8050
    python -m trellum.run reports/daily_revenue --port 8070   # custom port
    python -m trellum.run reports/daily_revenue --no-serve    # generate only
    python -m trellum.run reports/daily_revenue --auto-refresh
    python -m trellum.run reports/daily_revenue --production
    python -m trellum.run --all
    python -m trellum.run --all --max-concurrent 3
    python -m trellum.run --all --studio my-studio
    python -m trellum.run --all --category Revenue

Testing (no DB needed):
    python -m trellum.run reports/daily_revenue --test       # mock data test
    python -m trellum.run --all --test                       # test all reports
    python -m trellum.run --all --test --test-screenshot     # with screenshots
    python -m trellum.run --all --test --fail-fast           # stop on first failure

Visual regression:
    python -m trellum.run --all --test --test-screenshot --save-baseline     # save baselines
    python -m trellum.run --all --test --test-screenshot --check-baseline    # compare vs baselines
    python -m trellum.run reports/dau-mau-trends --test --save-baseline      # single baseline
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

from trellum import update_check
from trellum.project import get_project_root
from trellum.report import BaseReport
from trellum.runner.console import _closing_move, _force_utf8_console, _ts
from trellum.runner.discovery import (
    _discover_all_reports,
    _discover_project_components,
    _discover_project_themes,
    discover_report,
    scan_report_configs,
)
from trellum.runner.events import (
    _load_events,
    _weekday_highlight_config,
    local_annotation_events,
)
from trellum.runner.execute import run_all_reports, run_report
from trellum.runner.ports import (
    DEFAULT_PORT,
    PORT_SEARCH_SPAN,
    _answer_identity,
    _claim_port,
    _holder_description,
    _is_our_server,
    _is_port_in_use,
    _listening_pids,
    _norm_dir,
    _process_command,
    _served_dir_at,
    resolve_port,
)
from trellum.runner.serve import (
    _render_output_index,
    _schedule_refreshes,
    _serve,
    _serve_all,
)

# Everything below was importable from `trellum.runner` when this was one
# 1,599-line module, and several of these are reached for by name: the host
# contract promises scan_report_configs (docs/COMPATIBILITY.md), the CLI drives
# the port helpers, and testing/runner.py builds a report's events the same way
# a real run does. Splitting the module must not move anything anyone already
# imports, so the names stay here whatever file now holds the body.
__all__ = [
    "main",
    "run_report",
    "run_all_reports",
    "discover_report",
    "scan_report_configs",
    "resolve_port",
    "DEFAULT_PORT",
    "PORT_SEARCH_SPAN",
    "_answer_identity",
    "_claim_port",
    "_discover_all_reports",
    "_discover_project_components",
    "_discover_project_themes",
    "_force_utf8_console",
    "_holder_description",
    "_is_our_server",
    "_is_port_in_use",
    "_listening_pids",
    "_load_events",
    "local_annotation_events",
    "_norm_dir",
    "_process_command",
    "_render_output_index",
    "_schedule_refreshes",
    "_serve",
    "_serve_all",
    "_served_dir_at",
    "_ts",
    "_weekday_highlight_config",
]

def _load_project_bootstrap(args) -> None:
    if (not args.all and args.report_dir
            and BaseReport.load_config(os.path.abspath(args.report_dir)).get("kind", "report") == "analysis"):
        return
    # Ordinary reports and batch builds can register project/cloud plugins.
    bootstrap_path = os.path.join(get_project_root(), "bootstrap.py")
    if os.path.isfile(bootstrap_path):
        spec = importlib.util.spec_from_file_location("bootstrap", bootstrap_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:
                print(f"  Warning: bootstrap.py failed: {exc}", flush=True)


def main():
    _force_utf8_console()

    parser = argparse.ArgumentParser(description="Run a BI report")
    parser.add_argument("report_dir", nargs="?", default=None,
                        help="Path to the report directory (e.g. reports/daily_revenue)")
    parser.add_argument("--all", action="store_true",
                        help="Run all reports with bounded concurrency")
    parser.add_argument("--max-concurrent", type=int, default=3,
                        help="Max concurrent reports when using --all (default: 3)")
    parser.add_argument("--studio", help="Filter by studio/tenant when using --all")
    parser.add_argument("--category", help="Filter by category when using --all (e.g. Revenue)")
    parser.add_argument("--serve", action="store_true",
                        help="Start HTTP server after generating. Default for a "
                             "single report; with --all it serves the whole "
                             "output directory with an index page.")
    parser.add_argument("--no-serve", action="store_true",
                        help="Skip HTTP server, just generate output files")
    parser.add_argument("--auto-refresh", action="store_true",
                        help="Re-run report on its cron schedule while serving (off by default)")
    parser.add_argument("--port", type=int, default=None,
                        help=f"HTTP server port. Default: {DEFAULT_PORT}, or the "
                             f"next free port if it is taken. An explicit value "
                             f"is honoured or the run fails.")
    parser.add_argument("--production", action="store_true",
                        help="Publish output via the registered output backend")
    parser.add_argument("--output", "-o", help="Override output directory")
    parser.add_argument("--portable", action="store_true",
                        help="Build output that can be served from any path: copy the "
                             "vendor assets next to the reports and reference them "
                             "relatively, instead of requiring the host to serve "
                             "/_vendor/ at its domain root")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass query cache (always hit the database)")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete all cached query results before running")
    parser.add_argument("--force-cache", action="store_true",
                        help="Use cached results regardless of TTL (only re-query on SQL change)")
    parser.add_argument("--skip-fresh", type=int, nargs="?", const=300, default=0,
                        metavar="SECONDS",
                        help="Skip reports whose output is younger than SECONDS "
                             "(default: 300). Useful for local rebuilds.")
    parser.add_argument("--test", action="store_true",
                        help="Test report(s) with mock data (no DB connection needed)")
    parser.add_argument("--test-screenshot", action="store_true",
                        help="Include Playwright screenshots in test output")
    parser.add_argument("--test-output", type=str, default=None,
                        help="Directory for test output (default: temp dir)")
    parser.add_argument("--fail-fast", action="store_true",
                        help="Stop testing on first failure (with --test --all)")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save screenshots as visual regression baselines (with --test)")
    parser.add_argument("--check-baseline", action="store_true",
                        help="Compare screenshots against saved baselines (with --test)")
    parser.add_argument("--baseline-threshold", type=float, default=5.0,
                        help="Max %% of changed pixels before failing visual regression (default: 5.0)")
    parser.add_argument("--debug", action="store_true",
                        help="Debug output: SQL queries, config details, memory usage")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--fail-on-validation", action="store_true", default=None,
                        help="Abort if validation finds FAIL-level issues "
                             "(automatic with --production)")
    parser.add_argument("--no-fail-on-validation", dest="fail_on_validation",
                        action="store_false",
                        help="Allow generation even with validation FAILs")
    args = parser.parse_args()

    _load_project_bootstrap(args)

    # Must happen before anything renders: the vendor URL base is baked into the
    # HTML at build time, so switching it afterwards would leave already-written
    # reports pointing at the old prefix.
    if args.portable:
        from trellum.rendering.cdn import set_vendor_url_base
        set_vendor_url_base("../_vendor/")

    if args.debug:
        os.environ['FRAMEWORK_DEBUG'] = '1'
        import tracemalloc
        tracemalloc.start()

    from trellum.data.query import clear_cache, disable_cache, set_default_cache_ttl

    if args.clear_cache:
        clear_cache()

    if args.force_cache:
        set_default_cache_ttl(365 * 24 * 3600)

    if args.no_cache or args.production:
        disable_cache()

    if args.test:
        from trellum.testing.runner import test_all_reports, test_report

        if args.all:
            results = test_all_reports(
                screenshot=args.test_screenshot,
                output_base=args.test_output,
                verbose=args.verbose,
                fail_fast=args.fail_fast,
                save_baseline=args.save_baseline,
                check_baseline=args.check_baseline,
                baseline_threshold=args.baseline_threshold,
            )
            failed = sum(1 for r in results if not r.passed)
            sys.exit(1 if failed > 0 else 0)

        if not args.report_dir:
            parser.error("report_dir is required when not using --all")

        result = test_report(
            args.report_dir,
            output_dir=args.test_output,
            screenshot=args.test_screenshot,
            verbose=args.verbose,
            save_baseline=args.save_baseline,
            check_baseline=args.check_baseline,
            baseline_threshold=args.baseline_threshold,
        )
        print(f"\n{result.summary_line()}", flush=True)
        for d in result.details:
            print(f"  {d}", flush=True)
        sys.exit(0 if result.passed else 1)

    update_handle = update_check.start()   # runs beside the build; read at its end

    if args.all:
        # --all builds and exits by default (it is the batch/CI path).
        # Pass --serve to browse the whole output directory afterwards.
        serve_all = args.serve and not args.production and not args.no_serve
        # Claim the port before the batch, for the reason given on the
        # single-report path below -- and more so here, where the build is long.
        output_base = os.path.abspath(
            args.output or os.path.join(get_project_root(), "output"))
        port = resolve_port(args.port, output_base) if serve_all else None
        exit_code = run_all_reports(
            max_concurrent=args.max_concurrent,
            studio=args.studio,
            category=args.category,
            production=args.production,
            max_age=args.skip_fresh,
            fail_on_validation=args.fail_on_validation,
        )
        # Reports reference the vendor assets relatively in portable mode, so a
        # copy has to sit beside them -- the originals live inside the installed
        # package and are served at runtime, which a published static tree has
        # nobody to do.
        if args.portable:
            from trellum.rendering.cdn import copy_vendor_tree
            dest = copy_vendor_tree(output_base)
            print(f"[{_ts()}] Portable build -> vendor assets copied to {dest}", flush=True)
        update_check.finish(update_handle)

        if serve_all:
            # output_base and port were resolved BEFORE the batch, so the
            # claim happened before the long build rather than after it.
            _serve_all(output_base, port)
        elif exit_code == 0 and not args.production:
            _closing_move()
        sys.exit(exit_code)

    if not args.report_dir:
        parser.error("report_dir is required when not using --all")

    # Always emit the client-side polling JS when the report has a cron
    # schedule (refresh_seconds is derived from the cron inside html_builder
    # -- 0 cron = no polling). The --auto-refresh CLI flag below only
    # gates the *local* dev loop that re-generates the report on disk;
    # in production a host's scheduler does that, and the browser
    # still needs the polling JS to notice new data.json versions.
    should_serve = not args.no_serve and not args.production

    # Claim the port BEFORE building, not after.
    #
    # --serve rebuilds first and binds last, so for the length of a rebuild the
    # URL we are about to print is still answered by whatever held the port --
    # and because we deliberately reuse the port to keep the URL stable across
    # runs, that is usually a previous server of ours serving a DIFFERENT
    # report. A measured run navigated during that window, got a real-looking
    # page for the wrong report, and spent fourteen tool calls -- including
    # reading this file out of site-packages -- establishing that the server was
    # fine all along. Refused beats plausible-and-wrong: with the old server
    # stopped up front the window answers nothing, which is unambiguous.
    port = None
    if should_serve:
        # The directory this serve will own, derived the same way run_report
        # derives it -- the identity comparison needs the answer before the
        # build starts.
        try:
            cfg = BaseReport.load_config(os.path.abspath(args.report_dir))
            slug = cfg.get("slug", os.path.basename(os.path.abspath(args.report_dir)))
        except Exception:
            slug = os.path.basename(os.path.abspath(args.report_dir))
        expected_dir = args.output or os.path.join(
            get_project_root(), "output", slug)
        port = resolve_port(args.port, expected_dir)

    output_dir = run_report(
        args.report_dir, args.output, args.production,
        auto_refresh=True,
        max_age=args.skip_fresh,
        fail_on_validation=args.fail_on_validation,
        debug=args.debug,
    )

    # The relative prefix is "../_vendor/", so the copy belongs one level up
    # from the report directory -- beside it, not inside it, which is what keeps
    # a single copy shared by every report in the tree.
    if args.portable:
        from trellum.rendering.cdn import copy_vendor_tree
        dest = copy_vendor_tree(os.path.dirname(os.path.abspath(output_dir)))
        print(f"[{_ts()}] Portable build -> vendor assets copied to {dest}", flush=True)
    update_check.finish(update_handle)

    if should_serve:
        if args.auto_refresh:
            config = BaseReport.load_config(os.path.abspath(args.report_dir))
            cron = config.get("schedule", {}).get("cron", "")
            from trellum.rendering.html_builder import _cron_to_seconds
            interval = _cron_to_seconds(cron)

            if interval > 0:
                print(f"Auto-refresh every {interval}s", flush=True)
                _schedule_refreshes(os.path.abspath(args.report_dir), output_dir, interval)

        _serve(output_dir, port)
    elif not args.production:
        _closing_move()

"""CI-only pytest plugin recording numeric framework scaling measurements."""

import json
import os
from pathlib import Path


_skipped_tests = []


def measurements(results):
    # Explicit allowlist: omit report errors, browser state and captured output.
    return [
        {"report": report.slug, "rows": report.rows,
         "metrics": [{"name": metric.name, "duration_ms": metric.duration_ms,
                      "extra": {name: value for name, value in metric.extra.items()
                                if isinstance(value, (int, float))}}
                     for metric in report.metrics]}
        for report in results
    ]


def validate_scaling(results, slug, row_counts):
    actual = [(report.slug, report.rows) for report in results]
    expected = [(slug, rows) for rows in row_counts]
    if actual != expected:
        raise AssertionError(f"Incomplete scaling measurements: expected {expected}, got {actual}")
    if any(not any(metric.name == "page_load" for metric in report.metrics) for report in results):
        raise AssertionError("Scaling measurements must include browser page_load for every row count")


def pytest_configure(config):
    config._trellum_benchmark_metrics = []
    _skipped_tests.clear()
    config._trellum_benchmark_skips = _skipped_tests


def pytest_runtest_setup(item):
    module = item.module
    original = getattr(module, "_print_perf_table", None)
    if original is None:
        return

    def capture(results):
        item.config._trellum_benchmark_metrics.extend(measurements(results))
        validate_scaling(results, item.callspec.params["slug"], module.ROW_COUNTS)
        return original(results)

    item._trellum_original_perf_table = original
    module._print_perf_table = capture


def pytest_runtest_teardown(item):
    if hasattr(item, "_trellum_original_perf_table"):
        item.module._print_perf_table = item._trellum_original_perf_table


def pytest_runtest_logreport(report):
    # Inspect the final outcome so decorator-based xfails are rejected too.
    # Benchmarks run serially; this buffer belongs to the current pytest session.
    if report.skipped:
        _skipped_tests.append(report.nodeid)


def pytest_sessionfinish(session):
    skips = session.config._trellum_benchmark_skips
    if skips:
        print("\nERROR: benchmark tests skipped: " + ", ".join(skips))
        session.exitstatus = 1
    results = session.config._trellum_benchmark_metrics
    if not results:
        return
    directory = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / ".test-results/ci/benchmarks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metrics.json").write_text(json.dumps(results, indent=2) + "\n")
    lines = ["## Scaling measurements", "", "| Report | Rows | Metric | Milliseconds |", "|---|---:|---|---:|"]
    for result in results:
        for metric in result["metrics"]:
            if metric["duration_ms"]:
                lines.append(f"| {result['report']} | {result['rows']:,} | {metric['name']} | {metric['duration_ms']:.2f} |")
    summary = "\n".join(lines) + "\n"
    (directory / "metrics.md").write_text(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
            output.write(summary)

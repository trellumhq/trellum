"""Dependency-free change classification and fail-closed aggregate checks."""

import argparse
import json
import os
import subprocess
from pathlib import Path


PORTAL_JOBS = (
    "django-tests", "sandbox", "sandbox-compatibility", "migration-safety", "django-e2e",
)
# Opt-in (workflow_dispatch `heavy` input or the `heavy-tests` PR label): being
# skipped never blocks, but a run that was requested must pass.
OPTIONAL_JOBS = ("django-heavy-tests",)
CI_HELPERS = {
    ".github/workflows/ci_checks.py", ".github/workflows/test_ci_checks.py",
    ".github/workflows/test_summary.py", ".github/workflows/test_test_summary.py",
}
BENCHMARK_PREFIXES = (
    "trellum/components/", "trellum/rendering/", "trellum/static/", "trellum/data/",
    "trellum/reporting/", "trellum/runner/", "trellum/themes/",
    "trellum/demo/reports/player-overview/", "trellum/demo/reports/cart-funnel/",
)
BENCHMARK_FILES = {
    "trellum/report.py", "trellum/assets.py", "trellum/datasets.py",
    "trellum/requirements.txt", "trellum/testing/test_performance.py", "trellum/testing/mock_data.py",
    "trellum/testing/runner.py", "trellum/testing/conftest.py",
    ".github/workflows/benchmarks.yml", ".github/workflows/benchmark_metrics.py",
    ".github/workflows/test_benchmark_metrics.py",
}


def classify(paths):
    """Docs skip expensive jobs; unknown runtime paths conservatively run portal."""
    paths = set(paths)
    portal = any(not (p.startswith("docs/") or p.endswith(".md")) for p in paths)
    framework_paths = {
        p for p in paths if p.startswith("trellum/")
        or p == ".github/workflows/framework.yml" or p in CI_HELPERS or p in BENCHMARK_FILES
    }
    code = any(not (p.startswith("trellum/docs/") or p.endswith(".md"))
               for p in framework_paths)
    data = any(p.startswith(("trellum/data/", "trellum/itest/", "trellum/requirements"))
               or p == ".github/workflows/framework.yml" or p in CI_HELPERS
               for p in framework_paths)
    benchmark = any(not p.endswith(".md") and (p.startswith(BENCHMARK_PREFIXES) or p in BENCHMARK_FILES)
                    for p in paths)
    return {"portal": "yes" if portal else "no", "code": "yes" if code else "no",
            "data": "yes" if data else "no", "benchmark": "yes" if benchmark else "no"}


def changed_paths(event, base):
    if event not in {"pull_request", "push"} or not base or set(base) == {"0"}:
        return None  # Dispatch/new branch: validate everything.
    revision = f"{base}...HEAD" if event == "pull_request" else base
    command = ["git", "diff", "--name-only", "--no-renames", "-z", revision]
    if event == "push":
        command.append("HEAD")
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE)
    return result.stdout.decode().rstrip("\0").split("\0") if result.stdout else []


def portal_failures(needs):
    """Skipped is valid only after a successful docs-only classification."""
    failures = []
    for job in ("changes", "fast-checks"):
        if needs.get(job, {}).get("result") != "success":
            failures.append(f"{job}: expected success")
    required = needs.get("changes", {}).get("outputs", {}).get("portal")
    if required not in {"yes", "no"}:
        failures.append("changes: missing or invalid portal classification")
    expected = "success" if required == "yes" else "skipped"
    for job in PORTAL_JOBS:
        result = needs.get(job, {}).get("result", "missing")
        if result != expected:
            failures.append(f"{job}: {result} (expected {expected})")
    for job in OPTIONAL_JOBS:
        result = needs.get(job, {}).get("result", "missing")
        if result not in {"success", "skipped"}:
            failures.append(f"{job}: {result} (expected success or skipped)")
    return failures


def framework_failures(needs):
    failures = []
    for job in ("changes", "lint"):
        if needs.get(job, {}).get("result") != "success":
            failures.append(f"{job}: expected success")
    outputs = needs.get("changes", {}).get("outputs", {})
    for key in ("code", "data", "benchmark"):
        if outputs.get(key) not in {"yes", "no"}:
            failures.append(f"changes: missing or invalid {key} classification")
    for job, key in (("tests", "code"), ("package", "code"), ("integration", "data"), ("benchmarks", "benchmark")):
        expected = "success" if outputs.get(key) == "yes" else "skipped"
        result = needs.get(job, {}).get("result", "missing")
        if result != expected:
            failures.append(f"{job}: {result} (expected {expected})")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    changes = sub.add_parser("changes")
    changes.add_argument("--event", required=True)
    changes.add_argument("--base", default="")
    sub.add_parser("portal-gate")
    sub.add_parser("framework-gate")
    args = parser.parse_args()
    if args.command == "changes":
        paths = changed_paths(args.event, args.base)
        outputs = classify(paths) if paths is not None else dict.fromkeys(("portal", "code", "data", "benchmark"), "yes")
        lines = "".join(f"{name}={value}\n" for name, value in outputs.items())
        print(lines, end="")
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write(lines)
    else:
        needs = json.loads(os.environ["CI_NEEDS"])
        portal = args.command == "portal-gate"
        failures = portal_failures(needs) if portal else framework_failures(needs)
        jobs = ("changes", "fast-checks", *PORTAL_JOBS, *OPTIONAL_JOBS) if portal else ("changes", "lint", "tests", "package", "integration", "benchmarks")
        lines = [f"## {'Portal' if portal else 'Framework'} checks", "", "| Job | Result |", "|---|---|"]
        lines.extend(f"| {job} | {needs.get(job, {}).get('result', 'missing')} |"
                     for job in jobs)
        lines.append("\n" + ("Failed: " + "; ".join(failures) if failures else "All applicable checks passed."))
        summary = "\n".join(lines) + "\n"
        print(summary)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
                output.write(summary)
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()

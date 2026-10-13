"""Protect change selection and aggregate behavior without CI dependencies."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_checks import PORTAL_JOBS, changed_paths, classify, framework_failures, portal_failures


class ClassificationTests(unittest.TestCase):
    def test_docs_only_and_empty(self):
        for paths in ([], ["docs/install.md", "README.md", "trellum/docs/guide.md"]):
            self.assertEqual(classify(paths), {"portal": "no", "code": "no", "data": "no", "benchmark": "no"})

    def test_portal_and_unknown_paths_do_not_trigger_framework(self):
        for path in ("apps/core/views.py", "requirements-test.txt", "Dockerfile", "new-runtime.cfg"):
            self.assertEqual(classify([path]), {"portal": "yes", "code": "no", "data": "no", "benchmark": "no"})

    def test_rendering_preserves_normal_tests_without_container_matrix(self):
        self.assertEqual(classify(["trellum/components/filterable.py"]),
                         {"portal": "yes", "code": "yes", "data": "no", "benchmark": "yes"})

    def test_drivers_workflow_and_helpers_validate_all_lanes(self):
        for path in ("trellum/data/query.py", "trellum/itest/run.py", "trellum/requirements.txt",
                     ".github/workflows/framework.yml", ".github/workflows/ci_checks.py"):
            self.assertEqual({key: value for key, value in classify([path]).items() if key != "benchmark"}, {"portal": "yes", "code": "yes", "data": "yes"})

    def test_manual_and_new_branch_validate_everything(self):
        self.assertIsNone(changed_paths("workflow_dispatch", ""))
        self.assertIsNone(changed_paths("push", "0" * 40))

    def test_benchmarks_only_for_runtime_performance_changes(self):
        for path in ("trellum/components/filterable.py", "trellum/rendering/renderer.py",
                     "trellum/static/js/filter-engine.js", "trellum/data/query.py",
                     "trellum/testing/test_performance.py", "trellum/testing/runner.py",
                     "trellum/testing/conftest.py", ".github/workflows/benchmarks.yml"):
            self.assertEqual(classify([path])["benchmark"], "yes", path)
        for path in ("apps/core/views.py", "README.md", "trellum/components/README.md",
                     "trellum/cli/help.py", "trellum/testing/test_components.py",
                     ".github/workflows/ci_checks.py", ".github/workflows/framework.yml"):
            self.assertEqual(classify([path])["benchmark"], "no", path)

    def test_deletions_renames_and_newline_names_remain_visible(self):
        # Real git selection, including both sides of a runtime-to-docs rename.
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init")
            git("config", "user.name", "CI test")
            git("config", "user.email", "ci@example.invalid")
            Path(directory, "runtime.py").write_text("original")
            git("add", ".")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("mv", "runtime.py", "notes.md")
            Path(directory, "new\nmodule.py").write_text("new")
            git("add", ".")
            git("commit", "-m", "rename")
            from unittest.mock import patch
            import ci_checks
            original_run = subprocess.run
            with patch.object(ci_checks.subprocess, "run", side_effect=lambda command, **kw: original_run(command, cwd=directory, **kw)):
                paths = changed_paths("pull_request", base)
            self.assertEqual(set(paths), {"runtime.py", "notes.md", "new\nmodule.py"})
            self.assertEqual(classify(paths)["portal"], "yes")


class AggregateTests(unittest.TestCase):
    def needs(self, docs=False):
        return {"changes": {"result": "success", "outputs": {"portal": "no" if docs else "yes"}},
                "fast-checks": {"result": "success"},
                **{job: {"result": "skipped" if docs else "success"} for job in PORTAL_JOBS}}

    def test_success_and_docs_only(self):
        self.assertEqual(portal_failures(self.needs()), [])
        self.assertEqual(portal_failures(self.needs(docs=True)), [])

    def test_every_required_job_fails_closed(self):
        for job in ("changes", "fast-checks", *PORTAL_JOBS):
            for result in ("failure", "cancelled", "skipped", "missing"):
                needs = self.needs()
                if result == "missing":
                    del needs[job]
                else:
                    needs[job]["result"] = result
                with self.subTest(job=job, result=result):
                    self.assertTrue(portal_failures(needs))

    def test_docs_failure_and_unclassified_skip_are_not_success(self):
        needs = self.needs(docs=True)
        needs["sandbox"]["result"] = "failure"
        self.assertTrue(portal_failures(needs))
        needs = self.needs(docs=True)
        needs["changes"]["outputs"] = {}
        self.assertTrue(portal_failures(needs))


class FrameworkAggregateTests(unittest.TestCase):
    def needs(self, code="yes", data="yes", benchmark="yes"):
        return {"changes": {"result": "success", "outputs": {"code": code, "data": data, "benchmark": benchmark}},
                "lint": {"result": "success"},
                "tests": {"result": "success" if code == "yes" else "skipped"},
                "package": {"result": "success" if code == "yes" else "skipped"},
                "integration": {"result": "success" if data == "yes" else "skipped"},
                "benchmarks": {"result": "success" if benchmark == "yes" else "skipped"}}

    def test_all_applicable_lanes(self):
        for code, data, benchmark in (("yes", "yes", "yes"), ("yes", "no", "yes"),
                                      ("yes", "no", "no"), ("no", "no", "no")):
            self.assertEqual(framework_failures(self.needs(code, data, benchmark)), [])

    def test_failures_cancellations_and_missing_outputs_fail_closed(self):
        for job in ("changes", "lint", "tests", "package", "integration", "benchmarks"):
            for result in ("failure", "cancelled", "skipped", "missing"):
                needs = self.needs()
                if result == "missing":
                    del needs[job]
                else:
                    needs[job]["result"] = result
                self.assertTrue(framework_failures(needs), (job, result))
        needs = self.needs("no", "no")
        del needs["changes"]["outputs"]["data"]
        self.assertTrue(framework_failures(needs))


if __name__ == "__main__":
    unittest.main()

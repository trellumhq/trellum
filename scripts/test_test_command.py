"""Check command boundaries without requiring Django, PostgreSQL, or browsers."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test as runner


class TestCommandTests(unittest.TestCase):
    def parse(self, *args):
        return runner.parse_args(list(args))

    def test_local_requires_an_explicit_selection(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.parse("local")
        self.assertEqual(caught.exception.code, 2)

    def test_local_preserves_paths_node_ids_and_optional_keyword(self):
        selection = ["apps/core/tests/test_forms_browser.py::test_save", "apps/accounts/tests"]
        args = self.parse("local", *selection, "-k", "save or login")
        command = runner.pytest_command(args)
        self.assertEqual(command[-len(selection):], selection)
        self.assertEqual(command[command.index("-n") + 1], "4")
        self.assertEqual(command[command.index("-k") + 1], "save or login")

    def test_portal_selects_all_apps_except_docker_e2e_and_heavy(self):
        command = runner.pytest_command(self.parse("portal"))
        self.assertEqual(command[-1], "apps")
        self.assertEqual(command[command.index("-m", 3) + 1], "not docker_sandbox and not portal_e2e and not heavy")
        self.assertNotIn("-k", command)
        for filtering in (("-k", "browser"), ("apps/core/tests",), ("--reuse-db",)):
            with self.subTest(filtering=filtering), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parse("portal", *filtering)

    def test_heavy_is_opt_in_everywhere(self):
        marker = lambda command: command[command.index("-m", 3) + 1]
        self.assertEqual(marker(runner.pytest_command(self.parse("portal", "--heavy"))), "not docker_sandbox and not portal_e2e")
        command = runner.pytest_command(self.parse("heavy"))
        self.assertEqual(command[-1], "apps")
        self.assertEqual(marker(command), "heavy and not docker_sandbox and not portal_e2e")
        self.assertEqual(self.parse("heavy").junitxml.name, "heavy.xml")
        local = self.parse("local", "apps/core/tests")
        self.assertIn("not heavy", marker(runner.pytest_command(local)))
        local = self.parse("local", "apps/core/tests", "--heavy")
        self.assertNotIn("heavy", marker(runner.pytest_command(local)))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse("heavy", "--heavy")

    def test_explicit_worker_count_can_run_serially_or_in_parallel(self):
        for requested, actual in ((1, "0"), (2, "2"), (16, "16")):
            with self.subTest(workers=requested):
                command = runner.pytest_command(self.parse("portal", "--workers", str(requested)))
                self.assertEqual(command[command.index("-n") + 1], actual)
        for invalid in ("0", "-1", "17", "auto", "logical"):
            with self.subTest(workers=invalid), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parse("portal", "--workers", invalid)

    def test_schema_rebuild_is_explicit_and_requires_reuse(self):
        command = runner.pytest_command(self.parse("local", "apps", "--reuse-db", "--create-db"))
        self.assertIn("--reuse-db", command)
        self.assertIn("--create-db", command)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse("local", "apps", "--create-db")

    def test_pytest_receives_root_reports_and_clean_options_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "nested" / "results.xml"
            with mock.patch.dict(os.environ, {"PYTEST_ADDOPTS": "-k omitted -n auto", "EXAMPLE_TEST_ENV": "preserved"}), mock.patch.object(runner.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
                run.return_value.returncode = 5
                status = runner.main(["portal", "--junitxml", str(report), "--durations", "7"])
            self.assertEqual(status, 5)
            self.assertTrue(report.parent.is_dir())
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--junitxml") + 1], str(report))
            self.assertEqual(command[command.index("--durations") + 1], "7")
            self.assertEqual(run.call_args.kwargs["cwd"], runner.ROOT)
            self.assertNotIn("PYTEST_ADDOPTS", run.call_args.kwargs["env"])
            self.assertEqual(run.call_args.kwargs["env"]["EXAMPLE_TEST_ENV"], "preserved")
            self.assertIn("Test command elapsed:", output.getvalue())

    def test_dry_run_has_no_report_or_subprocess_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "unused" / "results.xml"
            with mock.patch.object(runner.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(["portal", "--junitxml", str(report), "--dry-run"]), 0)
            run.assert_not_called()
            self.assertFalse(report.parent.exists())

    def test_collect_only_keeps_the_ordinary_suite_selection(self):
        command = runner.pytest_command(self.parse("portal", "--collect-only"))
        self.assertIn("--collect-only", command)
        self.assertEqual(command[-1], "apps")


if __name__ == "__main__":
    unittest.main()

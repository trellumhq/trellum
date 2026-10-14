"""Workspace identity proof; PostgreSQL races require the actual worker gate."""
import tempfile
from pathlib import Path
from django.test import SimpleTestCase, override_settings
from apps.reports.annotation_reconciliation import WorkspaceUnavailable, workspace_identity


class WorkspaceIdentityTests(SimpleTestCase):
    def test_restart_retains_identity(self):
        with tempfile.TemporaryDirectory() as root, override_settings(DATA_DIR=root):
            first = workspace_identity()
            self.assertEqual(workspace_identity(), first)
            self.assertEqual((Path(root) / ".annotation-workspace").stat().st_mode & 0o777, 0o600)

    def test_corrupt_marker_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as root, override_settings(DATA_DIR=root):
            marker = Path(root) / ".annotation-workspace"
            marker.write_text("corrupt")
            with self.assertRaises(WorkspaceUnavailable):
                workspace_identity()
            self.assertEqual(marker.read_text(), "corrupt")

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("gallery_select", Path(__file__).with_name("gallery_select.py"))
gallery_select = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gallery_select)


class GallerySelectionTest(unittest.TestCase):
    def test_workflow_uses_the_packaged_gallery_module(self):
        workflow = Path(__file__).with_name("pages.yml").read_text(encoding="utf-8")
        self.assertIn("python -m trellum.reporting.gallery output", workflow)

    def test_numeric_latest_and_guards(self):
        releases = [[
            {"tag_name": "v2.0.0", "draft": False, "prerelease": False},
            {"tag_name": "v10.1.0", "draft": False, "prerelease": False},
            {"tag_name": "v99.0.0-rc1", "draft": False, "prerelease": True},
        ]]
        self.assertEqual(gallery_select.select("schedule", "trellumhq/trellum", "main", "HEAD", False, releases), ("v10.1.0", False, True))
        self.assertEqual(gallery_select.select("push", "trellumhq/trellum", "main", "HEAD", False, releases), ("v10.1.0", False, True))
        self.assertEqual(gallery_select.select("push", "trellumhq/trellum", "main", "HEAD", False, []), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("pull_request", "trellumhq/trellum", "main", "HEAD", False, releases), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("push", "fork/example", "main", "HEAD", False, releases), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("push", "fork/example", "v1.2.3", "HEAD", False, []), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("push", "trellumhq/trellum", "v1.2.3", "HEAD", False, releases), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("workflow_dispatch", "trellumhq/trellum", "feature", "HEAD", True, releases), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("workflow_dispatch", "trellumhq/trellum", "main", "HEAD", True, releases), ("v10.1.0", False, True))
        self.assertEqual(gallery_select.select("workflow_dispatch", "trellumhq/trellum", "main", "HEAD", False, releases), ("HEAD", True, False))
        self.assertEqual(gallery_select.select("schedule", "trellumhq/trellum", "main", "HEAD", False, []), ("HEAD", True, False))
        for tag in ("v01.2.3", "v1.2.3-rc1", "v1.2", "v1.2.3\nother"):
            self.assertEqual(gallery_select.select("push", "trellumhq/trellum", tag, "HEAD", False, []), ("HEAD", True, False))


if __name__ == "__main__":
    unittest.main()

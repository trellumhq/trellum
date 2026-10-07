import tempfile
import unittest
from pathlib import Path

from check_install_version_pins import INSTALL_GUIDES, check


class InstallVersionPinCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for package in ("trellum_portal", "trellum"):
            path = self.root / package / "__init__.py"
            path.parent.mkdir(parents=True)
            path.write_text('__version__ = "0.3.0"\n', encoding="utf-8")
        for guide in INSTALL_GUIDES:
            path = self.root / guide
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("TRELLUM_VERSION=v0.3.0\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_pins_pass(self):
        self.assertEqual(check(self.root), [])

    def test_powershell_assignment_passes(self):
        path = self.root / INSTALL_GUIDES[0]
        path.write_text("$env:TRELLUM_VERSION = 'v0.3.0'\n", encoding="utf-8")
        self.assertEqual(check(self.root), [])

    def test_stale_install_pin_fails(self):
        path = self.root / INSTALL_GUIDES[0]
        path.write_text("TRELLUM_VERSION=v0.2.9\n", encoding="utf-8")
        self.assertTrue(any(INSTALL_GUIDES[0] in error for error in check(self.root)))

    def test_missing_guide_pin_fails(self):
        path = self.root / INSTALL_GUIDES[0]
        path.write_text("No release pin here.\n", encoding="utf-8")
        self.assertTrue(any(INSTALL_GUIDES[0] in error for error in check(self.root)))

    def test_package_version_mismatch_fails(self):
        path = self.root / "trellum" / "__init__.py"
        path.write_text('__version__ = "0.3.1"\n', encoding="utf-8")
        self.assertTrue(any("package versions disagree" in error for error in check(self.root)))


if __name__ == "__main__":
    unittest.main()

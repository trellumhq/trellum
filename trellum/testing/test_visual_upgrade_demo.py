"""The installed upgrade guide builds offline with portable native evidence."""

import base64
import fnmatch
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

import trellum
from trellum import project
from trellum.demo import __main__ as demo
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.runner.discovery import discover_report
from trellum.validation import validate_report

PACKAGE = Path(trellum.__file__).resolve().parent


@pytest.fixture
def upgrade_demo(tmp_path, monkeypatch):
    destination = tmp_path / "consumer"
    assert demo.main(["--dest", str(destination), "--no-data"]) == 0
    monkeypatch.setattr(project, "_project_root_override", None)
    monkeypatch.setenv("FW_PROJECT_ROOT", str(destination))
    source = destination / "reports/report-upgrade"
    report = discover_report(str(source))
    config = report.load_config(str(source))
    output = destination / "output/report-upgrade"
    ctx = ReportContext(config, "report-upgrade", str(output))
    report().generate(ctx)
    details = compute_details(ctx)
    validation = validate_report(ctx, details=details)
    assert validation.summary["warn"] == validation.summary["fail"] == 0, validation.to_dict()
    render_report(ctx, str(output), auto_refresh=False, validation=validation, details=details)
    return output


def test_installed_guide_embeds_genuine_evidence_and_declares_package_assets(upgrade_demo):
    html = (upgrade_demo / "index.html").read_text()
    sources = re.findall(r'<img src="data:image/png;base64,([^"]+)"', html)
    expected = list((PACKAGE / "docs/images/report-upgrade").glob("*.png"))
    assert len(sources) == len(expected) == 27
    assert {base64.b64decode(source) for source in sources} == {path.read_bytes() for path in expected}
    assert "Real v0.9.5" in html and "Current defaults after rebuild" in html
    assert "Explicit additions" in html and "until you rebuild" in html
    assert "unsplit" in html and "category mapping precedence" in html
    assert 'theme cycle enabled' in html and 'readable events' in html
    assert 'linear fit enabled' in html and 'trend_line=' in html
    assert "/home/" not in html
    # Catch the silent wheel failure: a source checkout has images even when
    # package-data would omit them from a fresh installation.
    config = tomllib.loads((PACKAGE / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["trellum"]
    for path in expected:
        assert any(fnmatch.fnmatch(path.relative_to(PACKAGE).as_posix(), pattern) for pattern in patterns)


def test_native_guide_desktop_tabs_images_and_section_links(upgrade_demo):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def fulfill(route):
            path = unquote(urlsplit(route.request.url).path).lstrip("/")
            file = (PACKAGE / "static/vendor" / path[len("_vendor/"):]
                    if path.startswith("_vendor/") else upgrade_demo / path)
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404, body="not found")

        page.route("**/*", fulfill)
        page.goto("http://upgrade.test/index.html", wait_until="networkidle")
        assert page.locator("img").evaluate_all("els=>els.every(el=>el.complete && el.naturalWidth>0)")
        for href in page.locator('a[href^="#sec-"]').evaluate_all("els=>els.map(el=>el.getAttribute('href'))"):
            assert page.locator(href).count() == 1
        table = page.locator("#sec-tables")
        table.get_by_role("tab", name="Explicit additions").click()
        assert table.get_by_text("Current with authored options", exact=True).is_visible()
        assert table.locator("pre").is_visible()
        assert "totals=" in table.locator("pre").inner_text()
        table.get_by_role("tab", name="Before and after rebuild").click()
        assert table.get_by_text("Real v0.9.5", exact=True).is_visible()
        assert not table.locator("pre").is_visible()
        assert page.locator("html").evaluate("el=>el.scrollWidth<=innerWidth")
        assert errors == []
        browser.close()

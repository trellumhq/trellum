"""Real-browser evidence capture, CLI import and standalone article reading."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from trellum.cli import main
from trellum.rendering.cdn import serve_vendor_request
from trellum.review import handle_review_request, maybe_serve_injected_html
from trellum.review.state import ReviewState
from trellum.runner.execute import run_report

_GENERATOR = '''from trellum import BaseReport
from trellum.components import BarChart, DataSource, FilterBar
import pandas as pd

class SourceReport(BaseReport):
    def generate(self, ctx):
        df = pd.DataFrame({"channel": ["Web", "Mobile", "Web", "Mobile"],
                           "region": ["NL", "NL", "DE", "DE"],
                           "conversion": [12, 8, 11, 9]})
        ctx.add_section("", [DataSource("conversions", df),
            FilterBar("conversions", df, filters=[{"column": "region", "label": "Region"}])])
        ctx.add_section("September conversion", [BarChart(df, x="channel",
            y="conversion", title="Conversion by channel", dataset_id="conversions")])
'''


def test_capture_import_publish_and_read_frozen_evidence(tmp_path, monkeypatch, capsys):
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr("trellum.project._project_root_override", None)
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("FW_UPDATE_CHECK", "0")
    source = tmp_path / "reports" / "conversion-source"
    source.mkdir(parents=True)
    (source / "report.yaml").write_text(json.dumps({
        "slug": "conversion-source", "name": "Conversion overview",
        "theme": "trellum light", "studio": "Example", "category": "Conversion",
        "description": "Synthetic data for an evidence capture.", "annotations": False,
    }), encoding="utf-8")
    (source / "generator.py").write_text(_GENERATOR, encoding="utf-8")
    run_report(str(source))
    review = ReviewState()
    monkeypatch.setattr("trellum.review.http.STATE", review)
    monkeypatch.setattr("trellum.review.inject.STATE", review)
    output_root = str(tmp_path / "output")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=output_root, **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if (handle_review_request(self, output_root)
                    or serve_vendor_request(self)
                    or maybe_serve_injected_html(self, output_root)):
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1360, "height": 960})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/conversion-source/", wait_until="networkidle")
                page.wait_for_function("window._chartInstances && Object.keys(window._chartInstances).length > 0")
                page.evaluate("fw.filterEngine.setFilter('conversions', 'evidence-region', 'region', 'equals', 'NL')")
                page.wait_for_function("Object.values(window._chartInstances)[0].data.datasets[0].data.reduce((a,b) => a+b, 0) === 20")
                with page.expect_download() as download_info:
                    page.evaluate("void fw.captureForAnalysis()")
                    page.locator(".fw-chart-container.fw-analysis-selectable").click()
                capture = tmp_path / "evidence.trellum-capture.json"
                download_info.value.save_as(capture)
                payload = json.loads(capture.read_text(encoding="utf-8"))
                assert payload["source"]["filters"]["conversions"]["evidence-region"]["value"] == "NL"
                assert payload["source"]["source_built_at"]
                assert payload["source"]["url"] == origin + "/conversion-source/"
                image_bytes = base64.b64decode(payload["image"]["data_base64"])
                assert len(image_bytes) > 2000  # Real rendered chart, not a stub PNG.

                assert main(["analysis", "new", "september-findings"]) == 0
                article = tmp_path / "reports" / "september-findings"
                capsys.readouterr()
                assert main(["analysis", "import", str(article), str(capture), "--name", "conversion"]) == 0
                imported_output = capsys.readouterr().out
                reference = next(line for line in imported_output.splitlines() if line.startswith("!["))
                (article / "report.yaml").write_text(json.dumps({
                    "kind": "analysis", "slug": "september-findings",
                    "name": "Why conversion fell in September", "author": "Analytics team",
                    "description": "A synthetic example of a published finding.",
                    "theme": "trellum light", "studio": "Example", "category": "Conversion",
                }), encoding="utf-8")
                (article / "content.md").write_text(
                    "## Finding\n\nMobile conversion needs investigation.\n\n"
                    + reference + "\n\n## Assumptions\n\nThis is synthetic September data for NL.\n\n"
                    "## Recommendation\n\nReview the mobile checkout flow.\n",
                    encoding="utf-8",
                )
                def no_connection(*args, **kwargs):
                    raise AssertionError("Article queried a warehouse")

                monkeypatch.setattr("trellum.report.ReportContext.get_connection", no_connection)
                run_report(str(article))
                original_evidence = hashlib.sha256((article / "evidence" / "conversion.png").read_bytes()).hexdigest()
                assert (article / "evidence" / "conversion.png").read_bytes() == image_bytes
                with Image.open(article / "evidence" / "conversion.png") as image:
                    assert image.width > 100 and image.height > 100

                page.goto(origin + "/september-findings/", wait_until="networkidle")
                assert page.locator("article h1").inner_text() == "Why conversion fell in September"
                assert page.locator("article").inner_text().find("Analytics team") >= 0
                caption = page.locator(".fw-evidence-caption").inner_text()
                assert "region equals NL" in caption and "Source built:" in caption
                assert "flagValue" not in caption
                page.locator('nav[aria-label="Contents"] a', has_text="Recommendation").click()
                assert page.url.endswith("#article-recommendation")
                assert page.locator("article img").evaluate("el => el.complete && el.naturalWidth > 100")
                assert page.locator('a[title="Enlarge image"]').get_attribute("target") == "_blank"
                assert page.locator('[data-export="analysis"]').is_hidden()
                page.screenshot(path=str(tmp_path / "analysis-desktop.png"), full_page=True)
                for command, suffix, signature in (("exportPNG", "png", b"\x89PNG"),
                                                    ("exportPDF", "pdf", b"%PDF")):
                    with page.expect_download() as exported:
                        page.evaluate(f"fw.{command}()")
                    export_path = tmp_path / f"article.{suffix}"
                    exported.value.save_as(export_path)
                    assert export_path.read_bytes().startswith(signature)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(tmp_path / "analysis-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

                # Refreshing or removing the source cannot alter the committed image.
                (source / "generator.py").write_text(_GENERATOR.replace("[12, 8, 11, 9]", "[120, 80, 110, 90]"), encoding="utf-8")
                run_report(str(source))
                assert hashlib.sha256((article / "evidence" / "conversion.png").read_bytes()).hexdigest() == original_evidence
                source.rename(tmp_path / "removed-source")
                (tmp_path / "output" / "conversion-source").rename(tmp_path / "removed-output")
                run_report(str(article))
                page.reload(wait_until="networkidle")
                assert page.locator("article img").evaluate("el => el.complete && el.naturalWidth > 100")
                assert hashlib.sha256((article / "evidence" / "conversion.png").read_bytes()).hexdigest() == original_evidence

                # Local review notices a Markdown rebuild and reloads the article.
                review.start()
                page.reload(wait_until="networkidle")
                page.wait_for_function("window._fwReviewInit === true")
                # Wait for the first status response to seed the reload watch.
                with page.expect_response("**/_fw/review/status*"):
                    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                with (article / "content.md").open("a", encoding="utf-8") as stream:
                    stream.write("\n\n## Update\n\nPublished revision two.\n")
                run_report(str(article))
                page.get_by_text("Published revision two.", exact=True).wait_for(state="visible")
                assert page.locator('nav[aria-label="Contents"] a', has_text="Update").count() == 1
                review.end()
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

"""Opt-in slide view in the built standalone shell, using local evidence only."""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

from trellum.cli.commands.analysis import new_analysis
from trellum.rendering.cdn import serve_vendor_request
from trellum.runner.execute import run_report


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def deck_site(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    source = new_analysis("meeting", format="presentation")
    config = yaml.safe_load((source / "report.yaml").read_text())
    config.update(name="September checkout review", author="Studio analytics",
                  presentation_view="slides", theme="trellum light")
    (source / "report.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for name, size in (("comparison", (1100, 640)), ("scenario", (1200, 520))):
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 70, size[0] - 50, size[1] - 70), outline="teal", width=4)
        draw.text((75, 100), "Synthetic local evidence", fill="black")
        image.save(source / "evidence" / f"{name}.png")
    # A validated local sidecar exercises the visible provenance caption too.
    (source / "evidence" / "comparison.json").write_text(json.dumps({
        "report_slug": "checkout", "report_name": "Checkout funnel",
        "component_id": "comparison", "component_title": "Checkout by device",
        "url": "https://example.test/reports/checkout", "captured_at": "2026-10-07T10:00:00Z",
        "source_built_at": "2026-10-06T09:00:00Z", "filters": {"region": "All regions"},
    }), encoding="utf-8")
    slides = [
        {"id": "question", "layout": "title", "title": "A smaller checkout. A clearer path to purchase.",
         "text": "SEPTEMBER · CHECKOUT REVIEW\n\n### 43%\n\n"
                 "of checkout starters complete a purchase. Focus the next experiment on mobile.\n\n"
                 "| Checkout starters | Completed orders | Opportunity |\n| --- | --- | --- |\n"
                 "| 12,400 | 5,300 | +2.1 pp |"},
        {"id": "finding", "layout": "image-text", "title": "Mobile is the clearest place to start",
         "text": "**The gap is concentrated.** Mobile customers complete fewer purchases than desktop customers.\n\n"
                 "The delivery step has the largest drop.\n\n> Test one shorter step before expanding the change.",
         "image": "evidence/comparison.png", "alt": "Synthetic comparison of checkout by device"},
        {"id": "scenario", "layout": "image", "title": "A modest improvement has a measurable effect",
         "text": "**Scenario, not a forecast.** Hold traffic and order value constant; compare completion rates.",
         "image": "evidence/scenario.png", "alt": "Synthetic comparison of completion scenarios"},
        {"id": "actions", "layout": "text", "title": "One focused test, then a measured decision",
         "text": "1. **Simplify the delivery step.** Remove optional fields and keep the address summary visible.\n"
                 "2. **Measure completed orders.** Compare conversion with the unchanged checkout.\n"
                 "3. **Decide after two weeks.** Review the outcome before rolling out the change."},
    ]
    (source / "slides.yaml").write_text(yaml.safe_dump({"version": 1, "slides": slides}), encoding="utf-8")
    output = Path(run_report(str(source), fail_on_validation=True))
    # Preserve an independently built legacy default document for regression checks.
    slide_markup = (output / "index.html").read_text(encoding="utf-8")
    del config["presentation_view"]
    (source / "report.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    run_report(str(source), fail_on_validation=True)
    (output / "document.html").write_text((output / "index.html").read_text(), encoding="utf-8")
    (output / "index.html").write_text(slide_markup, encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if not serve_vendor_request(self):
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(output)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_inline_navigation_reading_history_and_keyboard_scope(chromium, deck_site):
    origin, _artifacts = deck_site
    page = chromium.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(origin + "/#slide-finding", wait_until="load")
        expect(page.locator("#fwLoading")).to_be_hidden()
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
        expect(page.locator("#slide-finding")).to_be_visible()
        expect(page.locator(".fw-presentation-toc a[aria-current]")).to_have_attribute("href", "#slide-finding")
        # A deep link must leave the top controls below the sticky report bar.
        assert page.locator(".fw-presentation-controls").bounding_box()["y"] >= 45
        assert not page.evaluate("!!document.fullscreenElement")
        page.locator("#slide-finding .fw-slide-text > p").first.click()
        page.keyboard.press("ArrowRight")
        expect(page.locator("#slide-scenario")).to_be_visible()
        page.keyboard.press("ArrowLeft")
        expect(page.locator("#slide-finding")).to_be_visible()
        page.locator('.fw-presentation-toc a[href="#slide-actions"]').click()
        expect(page.locator("#slide-actions-title")).to_be_focused()
        page.go_back()
        expect(page.locator("#slide-finding")).to_be_visible()
        page.go_forward()
        expect(page.locator("#slide-actions")).to_be_visible()
        page.keyboard.press("Home")
        expect(page.locator("#slide-question")).to_be_visible()
        page.keyboard.press("End")
        expect(page.locator("#slide-actions")).to_be_visible()
        page.keyboard.press("ArrowLeft")
        expect(page.locator("#slide-scenario")).to_be_visible()
        page.get_by_role("button", name="Read all slides", exact=True).click()
        expect(page.locator(".fw-slide:visible")).to_have_count(4)
        page.locator('.fw-presentation-toc a[href="#slide-finding"]').click()
        page.get_by_role("button", name="Slide view", exact=True).click()
        expect(page.locator("#slide-finding")).to_be_visible()
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
        # Stable review/evidence anchors also select their enclosing slide.
        page.evaluate("location.hash = 'slide-scenario-passage-1'")
        expect(page.locator("#slide-scenario")).to_be_visible()
        page.evaluate("location.hash = 'slide-finding-evidence-1'")
        expect(page.locator("#slide-finding")).to_be_visible()
        page.locator('.fw-presentation-toc a[href="#slide-actions"]').click()
        page.evaluate("""() => {
            const slide = document.querySelector('#slide-actions');
            slide.insertAdjacentHTML('beforeend', '<input><textarea></textarea><select><option>A</option></select>' +
                '<div contenteditable><span>Editable</span></div><button>Authored action</button><a href="#local">Authored link</a>');
            document.body.insertAdjacentHTML('beforeend', '<button id="outside">Outside the deck</button>');
        }""")
        for selector in ("input", "textarea", "select", "[contenteditable]", "button", "a"):
            page.locator("#slide-actions " + selector).last.focus()
            page.keyboard.press("Home")
            expect(page.locator("#slide-actions")).to_be_visible()
        page.locator("#outside").focus()
        page.keyboard.press("Home")
        expect(page.locator("#slide-actions")).to_be_visible()
        # Deep links and history clearing do not leave every slide hidden.
        page.evaluate("location.hash = ''")
        expect(page.locator("#slide-question")).to_be_visible()
        assert not errors
    finally:
        page.close()


def test_fullscreen_exit_and_print_keep_inline_slides(chromium, deck_site):
    origin, _artifacts = deck_site
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(origin, wait_until="load")
        expect(page.locator("#fwLoading")).to_be_hidden()
        # Exercise native fullscreen before testing a denied request.
        page.get_by_role("button", name="Present", exact=True).click()
        page.wait_for_function("!!document.fullscreenElement")
        page.evaluate("document.exitFullscreen()")
        expect(page.get_by_role("button", name="Present", exact=True)).to_be_focused()
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
        page.evaluate("() => { document.querySelector('.fw-presentation').requestFullscreen = () => Promise.reject(new Error('denied')); }")
        page.get_by_role("button", name="Read all slides", exact=True).click()
        page.get_by_role("button", name="Present", exact=True).click()
        expect(page.locator(".fw-presentation-notice")).to_contain_text("Fullscreen unavailable")
        page.keyboard.press("End")
        expect(page.locator("#slide-actions")).to_be_visible()
        page.emulate_media(media="print")
        expect(page.locator(".fw-slide:visible")).to_have_count(4)
        expect(page.locator(".fw-presentation-controls")).to_be_hidden()
        expect(page.locator(".fw-presentation")).to_have_css("display", "block")
        expect(page.locator("#slide-actions")).to_have_css("aspect-ratio", "auto")
        expect(page.locator("#slide-scenario img")).to_have_css("max-height", "none")
        page.emulate_media(media="screen")
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.get_by_role("button", name="Present", exact=True)).to_be_focused()
        expect(page.get_by_role("button", name="Read all slides", exact=True)).to_be_visible()
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
        page.emulate_media(media="print")
        expect(page.locator(".fw-slide:visible")).to_have_count(4)
        expect(page.locator(".fw-presentation")).to_have_css("display", "block")
        expect(page.locator("#slide-actions")).to_have_css("aspect-ratio", "auto")
        page.emulate_media(media="screen")
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
    finally:
        page.close()


def test_default_document_view_stays_readable(chromium, deck_site):
    origin, _artifacts = deck_site
    page = chromium.new_page(viewport={"width": 1280, "height": 800})
    try:
        page.goto(origin + "/document.html#slide-finding", wait_until="load")
        expect(page.locator("#fwLoading")).to_be_hidden()
        expect(page.locator(".fw-slide:visible")).to_have_count(4)
        expect(page.get_by_role("button", name="Read all slides", exact=True)).to_have_count(0)
        page.locator("#slide-finding h2").focus()
        page.keyboard.press("End")
        assert page.url.endswith("#slide-finding")
        page.evaluate("() => { document.querySelector('.fw-presentation').requestFullscreen = () => Promise.reject(new Error('denied')); }")
        page.get_by_role("button", name="Present", exact=True).click()
        expect(page.locator(".fw-slide:visible")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.locator(".fw-slide:visible")).to_have_count(4)
    finally:
        page.close()


def test_desktop_slides_fit_shell_without_cropping(chromium, deck_site):
    origin, artifacts = deck_site
    page = chromium.new_page()
    try:
        page.goto(origin, wait_until="load")
        expect(page.locator("#fwLoading")).to_be_hidden()
        for width, height in ((1280, 800), (1440, 900)):
            page.set_viewport_size({"width": width, "height": height})
            for slide_id in ("question", "finding", "scenario", "actions"):
                page.locator(f'.fw-presentation-toc a[href="#slide-{slide_id}"]').click()
                slide = page.locator(".fw-slide:visible")
                bounds = slide.bounding_box()
                assert bounds["y"] + bounds["height"] <= height, (width, slide_id, bounds)
                assert 1.6 < bounds["width"] / bounds["height"] < 1.9
                assert slide.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
                page.screenshot(path=str(artifacts / f"slides-{width}-{slide_id}.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # The aspect ratio is a preferred size: lengthy content must grow, never clip.
        page.locator(".fw-slide:visible .fw-slide-text").evaluate("el => el.append(document.createTextNode('Additional context. '.repeat(500)))")
        assert page.locator(".fw-slide:visible").evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    finally:
        page.close()


@pytest.mark.heavy
def test_mobile_and_small_landscape_slides_do_not_overflow(chromium, deck_site):
    origin, artifacts = deck_site
    page = chromium.new_page()
    try:
        page.goto(origin, wait_until="load")
        expect(page.locator("#fwLoading")).to_be_hidden()
        for width, height in ((390, 844), (812, 375)):
            page.set_viewport_size({"width": width, "height": height})
            for slide_id in ("question", "finding", "scenario", "actions"):
                page.locator(f'.fw-presentation-toc a[href="#slide-{slide_id}"]').click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator(".fw-slide:visible").evaluate("el => el.scrollHeight <= el.clientHeight + 1")
                page.screenshot(path=str(artifacts / f"slides-{width}-{slide_id}.png"), full_page=True)
            page.emulate_media(media="print")
            expect(page.locator(".fw-slide:visible")).to_have_count(4)
            page.emulate_media(media="screen")
    finally:
        page.close()

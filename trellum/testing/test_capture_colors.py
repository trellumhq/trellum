"""Native vendored captures retain computed report paint in a private clone."""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

import pandas as pd
from PIL import Image
import pytest

from trellum.components import ComparisonTable, DataTable, KpiCard, PivotTable, RawHTML
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "bfb651ffee5823dda2aeb7a8f6f1f0ca2e0c9ce3"
EXPORT = ROOT / "trellum/static/js/runtime/export.js"
HIDE = ".fw-filter-bar,.fw-anno-bar,.fw-table-search,.fw-table-count,.fw-csv-btn,.fw-chart-dl,.fw-export-wrap,.fw-analysis-capture-ui"


@pytest.fixture(scope="module")
def native_capture(tmp_path_factory):
    from playwright.sync_api import sync_playwright

    root = tmp_path_factory.mktemp("capture-colors")
    evidence = Path(os.environ.get("TRELLUM_CAPTURE_EVIDENCE_DIR", root / "evidence"))
    evidence.mkdir(parents=True, exist_ok=True)
    baseline = subprocess.run(["git", "show", f"{BASELINE}:trellum/static/js/runtime/export.js"],
                              cwd=ROOT, text=True, capture_output=True, timeout=10)
    baseline_export = baseline.stdout if baseline.returncode == 0 else None
    for enabled in (False, True):
        output = root / f"after-{enabled}"
        frame = pd.DataFrame({"Campaign": ["Measured zero", "Launch", "Ongoing"],
                              "Spend": [0, 10, 90], "Revenue": [0, 30, 90]})
        ctx = ReportContext({"name": "Native capture colors", "studio": "test", "category": "Components",
                             "description": "Three independent synthetic campaigns show supplied spend and revenue, raw-unit targets and a pivot of the same observations."},
                            "capture-colors", str(output))
        ctx.add_section("Campaign observations", [
            KpiCard(label="Supplied revenue", value=120, format="currency", comparison={"measure": "relative_percent", "value": 2}),
            DataTable(frame, "Campaigns", static=True, searchable=True, sortable=True, max_rows=3,
                      totals={"Spend": {"agg": "sum", "format": "currency"}, "Revenue": {"agg": "sum", "format": "currency"}} if enabled else None),
        ])
        target = {"Revenue": {"target": 30, "direction": "higher"}} if enabled else None
        ctx.add_section("Supplied targets", [
            DataTable(frame, "Thresholds", static=True, cell_shading=target, row_limit=10),
            ComparisonTable(frame, "Comparison", "Revenue", ["Spend"], static=True,
                            conditional_formats={}, cell_shading=target),
        ])
        ctx.add_section("Campaign pivot", [
            PivotTable(frame, ["Campaign"], values="Revenue", title="Revenue by campaign", static=True, freeze=enabled),
        ])
        ctx.add_section("Computed paint", [RawHTML(html='''
            <style>
            .capture-paint {color:color-mix(in srgb,var(--text-main) 75%,transparent);padding:12px;border:3px solid currentColor}
            .capture-paint span {display:block;padding:12px;background-image:linear-gradient(90deg,color(srgb .2 .4 .6 / .4) 0%,color(srgb .2 .4 .6 / .4) 50%,color(srgb .2 .4 .6 / .4) 100%);border:2px solid currentColor}
            .capture-paint::before {content:"Inherited pseudo paint";display:block;padding:12px;color:inherit;background:color-mix(in srgb,currentColor 20%,transparent)}
            </style><div class="capture-paint"><span>Computed gradient stops</span></div>''')])
        result = validate_report(ctx, details=compute_details(ctx))
        assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()
        render_report(ctx, auto_refresh=False)
        (evidence / f"validation-{enabled}.json").write_text(json.dumps(result.to_dict(), indent=2))
        if baseline_export is not None:
            before = root / f"before-{enabled}"
            shutil.copytree(output, before)
            html = (before / "index.html").read_text()
            assert EXPORT.read_text() in html
            (before / "index.html").write_text(html.replace(EXPORT.read_text(), baseline_export))
    shutil.copytree(root / "after-True", evidence / "native-report", dirs_exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser, root, evidence
        browser.close()


def _open(native_capture, width=1280, *, before=False, enabled=True):
    browser, root, _ = native_capture
    page = browser.new_page(viewport={"width": width, "height": 1000})
    def fulfill(route):
        url = urlsplit(route.request.url)
        assert url.hostname == "capture.test", route.request.url
        path = unquote(url.path).lstrip("/")
        file = ROOT / "trellum/static/vendor" / path[len("_vendor/"):] if path.startswith("_vendor/") else root / path
        route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404)
    page.route("**/*", fulfill)
    page.goto(f"http://capture.test/{'before' if before else 'after'}-{enabled}/index.html", wait_until="networkidle")
    for section in page.locator(".fw-section").all():
        section.scroll_into_view_if_needed()
        if section.locator(".fw-table-scroll").count():
            section.locator("tbody tr").first.wait_for()
    page.evaluate("document.fonts.ready")
    page.mouse.move(0, 0)
    page.evaluate("scrollTo(0,0)")
    return page


def _theme(page, theme):
    page.locator("#fwThemeSelect").select_option("trellum " + theme)
    page.mouse.move(0, 0)
    page.evaluate("document.fonts.ready")
    # Header controls have a color transition; compare settled paint.
    page.wait_for_function("!document.getAnimations().some(a=>a.playState==='running' && a.effect?.target?.closest('.fw-header,.fw-container'))")


def _state(page):
    return page.locator(".fw-container").evaluate("""root => Array.from(root.querySelectorAll('*')).map(e=>{
      const s=getComputedStyle(e), r=e.getBoundingClientRect();return [e.tagName,e.tagName==='OPTION'?'':e.id,e.textContent,
        e.style.cssText,s.color,s.backgroundColor,s.backgroundImage,s.borderTopColor,
        r.x,r.y+scrollY,r.width,r.height,e.scrollLeft,e.value || ''];})""")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_dependency_screen_unchanged_and_baseline_failure(native_capture, width):
    _, root, evidence = native_capture
    if not (root / "before-True").exists():
        pytest.skip("Historical dependency unavailable; native capture tests still run on current assets")
    for enabled in (False, True):
        before = _open(native_capture, width, before=True, enabled=enabled)
        after = _open(native_capture, width, enabled=enabled)
        try:
            for theme in ("light", "dark"):
                _theme(before, theme)
                _theme(after, theme)
                assert _state(before) == _state(after)
                for index in range(4):
                    a = before.locator(".fw-section").nth(index)
                    b = after.locator(".fw-section").nth(index)
                    first = a.screenshot(animations="disabled")
                    second = b.screenshot(animations="disabled")
                    assert first == second
                    for label, data in (("before", first), ("after", second)):
                        (evidence / f"screen-{width}-{theme}-{enabled}-{index}-{label}.png").write_bytes(data)
                assert after.evaluate("document.documentElement.scrollWidth<=innerWidth")
            # Real native multi-row failure at the exact dependency, without a stub.
            failure = before.locator(".fw-section").first.evaluate("el=>fw.captureElementSnapshotForAnalysis(el).then(()=>null,e=>e.message)")
            assert failure == 'Attempting to parse an unsupported color function "color"'
            (evidence / f"baseline-{width}-{enabled}-failure.txt").write_text(failure)
        finally:
            before.close()
            after.close()


def _reference(page, target, path):
    """Screen paint in the same existing capture hide context, without a redraw."""
    page.evaluate("""selector=>{window.hiddenForReference=Array.from(document.querySelectorAll(selector)).map(e=>[e,e.style.display]);
        hiddenForReference.forEach(([e])=>e.style.display='none');}""", HIDE)
    try:
        page.evaluate("scrollTo(0,0)")
        box = target.bounding_box()
        image = Image.open(BytesIO(page.screenshot(full_page=True, animations="disabled"))).convert("RGB")
        image = image.crop((round(box["x"]), round(box["y"]), round(box["x"] + box["width"]), round(box["y"] + box["height"])))
        image.save(path)
        points = target.evaluate("""root=>{const box=root.getBoundingClientRect();return Array.from(root.querySelectorAll('tbody td,tfoot td,.capture-paint,.capture-paint span,.fw-kpi-change')).flatMap(e=>{
            const r=e.getBoundingClientRect(),x=r.x-box.x+4,y=r.y-box.y+r.height/2;
            if(e.matches('.capture-paint span'))return [[x+10,r.y-box.y+6],[x+r.width/2,r.y-box.y+6],[x+r.width-15,r.y-box.y+6]];
            if(e.matches('.capture-paint'))return [[r.x-box.x+1,r.y-box.y+r.height/2],[r.x-box.x+20,r.y-box.y+20]];
            if(e.matches('.fw-kpi-change'))return [[r.x-box.x+1,y]];
            return x>0&&x<box.width-4&&y>0&&y<box.height-4 ? [[x,y]] : [];});}""")
        size = target.bounding_box()
        return image, points, size
    finally:
        page.evaluate("hiddenForReference.forEach(([e,value])=>e.style.display=value);delete window.hiddenForReference")


def _pixels_match(reference, captured, points, *, scale=2):
    assert len(points) >= 3
    for x, y in points:
        expected = reference.getpixel((int(x), int(y)))[:3]
        actual = captured.getpixel((int(x * scale), int(y * scale)))[:3]
        assert max(abs(a - b) for a, b in zip(expected, actual)) <= 2, (x, y, expected, actual)


def _pdf_rgb(path):
    # The bundled jsPDF's default addImage embeds uncompressed RGB plus a
    # separate alpha mask. Inspect the actual download without a PDF dependency.
    pdf = path.read_bytes()
    start = pdf.index(b"/Subtype /Image")
    stream = pdf.index(b"stream\n", start)
    header = pdf[start:stream]
    assert b"/ColorSpace /DeviceRGB" in header and b"/Filter" not in header
    width = int(re.search(rb"/Width (\d+)", header)[1])
    height = int(re.search(rb"/Height (\d+)", header)[1])
    length = int(re.search(rb"/Length (\d+)", header)[1])
    assert length == width * height * 3
    assert b"/Count 1" in pdf[:1000]
    media = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf[:1000])
    assert media and abs(float(media[1]) - width * 72 / 192) < .01
    assert abs(float(media[2]) - height * 72 / 192) < .01
    return Image.frombytes("RGB", (width, height), pdf[stream + 7:stream + 7 + length])


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_real_analysis_png_pdf_colors_scope_and_restoration(native_capture, width):
    _, _, evidence = native_capture
    page = _open(native_capture, width)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        for theme in ("light", "dark"):
            _theme(page, theme)
            for index, section in enumerate(page.locator(".fw-section").all()):
                section.scroll_into_view_if_needed()
                if index < 3:
                    assert section.locator("tbody tr").count() >= 3
                if index == 0:
                    search = section.locator(".fw-table-search")
                    search.focus()
                    assert section.locator("tfoot").inner_text().split("\t")[1:] == ["$100.00", "$120.00"]
                    assert "3 matching rows" in section.locator(".fw-table-totals-note").inner_text()
                prefix = evidence / f"native-{width}-{theme}-{index}"
                screen, points, size = _reference(page, section, prefix.with_suffix(".screen.png"))
                state = _state(page)
                focus = page.evaluate("document.activeElement.outerHTML")
                payload = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
                assert payload["format"] == "trellum-analysis-capture"
                assert payload["source"]["report_slug"] == "capture-colors"
                assert payload["source"]["filters"] == {}
                prefix.with_suffix(".source.json").write_text(json.dumps(payload["source"], indent=2))
                png = base64.b64decode(payload["image"]["data_base64"])
                prefix.with_suffix(".png").write_bytes(png)
                native = Image.open(BytesIO(png)).convert("RGB")
                assert abs(native.width - size["width"] * 2) <= 2
                assert abs(native.height - size["height"] * 2) <= 2
                _pixels_match(screen, native, points)
                assert _state(page) == state
                assert page.evaluate("document.activeElement.outerHTML") == focus
                assert page.locator("iframe.html2canvas-container,html2canvaspseudoelement,.fw-analysis-capture-ui").count() == 0
            container = page.locator(".fw-container")
            screen, points, size = _reference(page, container, evidence / f"report-{width}-{theme}.screen.png")
            before = _state(page)
            paths = {}
            for fmt in ("PNG", "PDF"):
                with page.expect_download() as download:
                    page.evaluate("fmt=>fw['export'+fmt]()", fmt)
                path = evidence / f"report-{width}-{theme}.{fmt.lower()}"
                download.value.save_as(path)
                paths[fmt] = path
                assert not page.locator("#fwExportBtn.running,iframe.html2canvas-container").count()
                assert _state(page) == before
            native = Image.open(paths["PNG"]).convert("RGB")
            assert abs(native.height - size["height"] * 2) <= 2
            _pixels_match(screen, native, points)
            assert paths["PDF"].read_bytes().startswith(b"%PDF-")
            pdf_rgb = _pdf_rgb(paths["PDF"])
            assert pdf_rgb.size == native.size
            _pixels_match(screen, pdf_rgb, points)
            # jsPDF embeds its 2x capture on a single, custom-sized page.
            # Rasterize the real downloaded PDF at 192 dpi to inspect its paint.
            if shutil.which("pdftoppm"):
                subprocess.run(["pdftoppm", "-singlefile", "-scale-to-x", str(native.width), "-scale-to-y", str(native.height),
                                "-png", str(paths["PDF"]), str(paths["PDF"].with_suffix(".pdf-render"))], check=True, capture_output=True, timeout=30)
                pdf_image = Image.open(paths["PDF"].with_suffix(".pdf-render.png")).convert("RGB")
                assert pdf_image.size == native.size
                _pixels_match(screen, pdf_image, points)
        assert not errors
    finally:
        page.close()


def test_numeric_browser_conversion_and_computed_paint_tokens(native_capture):
    page = _open(native_capture)
    try:
        result = page.evaluate("""source=>{
            const factory=new Function(source+';return [_fwCaptureColorConverter, _fwCapturePaint];');
            const [make,paint]=factory(),convert=make();
            const inputs=['color(srgb .2 .4 .6 / .12345)','color(srgb 1.4 -.2 .5 / .35)',
                'oklch(70% .2 30 / .6)','color(display-p3 1 0 0 / .35)',
                'color(srgb none .3 .2 / none)'];
            const ordinary=['rgb(1, 2, 3)','rgba(1, 2, 3, 0.4)','#123abc','transparent','red'];
            const url=`url("data:image/svg+xml,<svg data-color='color(srgb .1 .2 .3)'/>")`;
            const gradient='linear-gradient(45deg, color(srgb .2 .4 .6 / .4) 20%, color(srgb .2 .4 .6 / .4) 80%), '+
                url;
            let unknown='';try{convert('unknown-color(1 2 3)')}catch(e){unknown=e.message}
            let unresolved='';try{convert('currentColor')}catch(e){unresolved=e.message}
            let multicolor='';try{paint('linear-gradient(color(srgb 1 0 0),rgb(0,0,255))',convert)}catch(e){multicolor=e.message}
            return {converted:inputs.map(convert),ordinary:ordinary.map(convert),paint:paint(gradient,convert),url,unknown,unresolved,multicolor};
        }""", EXPORT.read_text())
        assert result["converted"] == ["rgba(51, 102, 153, 0.12345)", "rgba(255, 0, 128, 0.35)",
                                        "rgba(255, 97, 77, 0.6)", "rgba(255, 0, 0, 0.35)", "rgba(0, 77, 51, 0)"]
        assert result["ordinary"] == ["rgb(1, 2, 3)", "rgba(1, 2, 3, 0.4)", "#123abc", "transparent", "red"]
        assert result["paint"].startswith("linear-gradient(45deg, rgba(51, 102, 153, 0.4) 20%, rgba(51, 102, 153, 0.4) 80%)")
        assert result["paint"].endswith(result["url"])
        assert "cannot convert CSS color: unknown-color" in result["unknown"]
        assert result["unresolved"] == "Image capture cannot convert CSS color: currentColor"
        assert result["multicolor"] == "Image capture cannot preserve modern multicolor gradient interpolation."
    finally:
        page.close()


def test_native_hover_shading(native_capture):
    _, _, evidence = native_capture
    page = _open(native_capture)
    try:
        # Freeze the existing hide context so the pointer stays over the same
        # native row while comparing the real screen and clone, in both themes.
        section = page.locator(".fw-section").nth(1)
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate("""selector=>document.querySelectorAll(selector).forEach(e=>e.style.display='none')""", HIDE)
            row = section.locator("tbody tr").nth(1)
            row.hover()
            screen, points, _ = _reference(page, section, evidence / f"hover-{theme}.screen.png")
            state = _state(page)
            payload = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
            path = evidence / f"hover-{theme}.png"
            path.write_bytes(base64.b64decode(payload["image"]["data_base64"]))
            _pixels_match(screen, Image.open(path).convert("RGB"), points)
            assert _state(page) == state
            assert row.evaluate("e=>e.matches(':hover')")
    finally:
        page.close()


@pytest.mark.heavy
def test_native_phone_current_horizontal_view(native_capture):
    _, _, evidence = native_capture
    page = _open(native_capture, 390)
    try:
        # Capturing a horizontally scrolled frozen pivot retains its viewport,
        # even when the hidden numeric column would make a wider export useful.
        _theme(page, "dark")
        pivot = page.locator(".fw-pivot-scroll")
        pivot.evaluate("e=>e.scrollLeft=e.scrollWidth")
        assert pivot.evaluate("e=>e.scrollLeft>0")
        section = page.locator(".fw-section").nth(2)
        screen, points, _ = _reference(page, section, evidence / "pivot-scroll.screen.png")
        state = _state(page)
        payload = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
        path = evidence / "pivot-scroll.png"
        path.write_bytes(base64.b64decode(payload["image"]["data_base64"]))
        _pixels_match(screen, Image.open(path).convert("RGB"), points)
        assert _state(page) == state
        assert not page.locator("iframe.html2canvas-container").count()
    finally:
        page.close()


def test_clone_failure_and_renderer_failure_restore_native_controls(native_capture):
    page = _open(native_capture)
    try:
        # Load real vendored libs through an ordinary successful native capture.
        page.locator(".fw-section").first.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
        before = _state(page)
        for failure in ("clone", "renderer", "synchronous"):
            error = page.locator(".fw-section").first.evaluate("""async (el,mode)=>{
                const real=window.html2canvas;
                window.html2canvas=(target,options)=>{
                    if(mode==='synchronous')throw new Error('forced renderer error');
                    if(mode==='renderer')return Promise.reject(new Error('forced renderer error'));
                    const onclone=options.onclone;
                    options.onclone=async(doc,root)=>{await onclone(doc,root);throw new Error('forced clone error')};
                    return real(target,options);
                };
                try{await fw.captureElementSnapshotForAnalysis(el);return null}
                catch(e){return e.message}finally{window.html2canvas=real}
            }""", failure)
            assert error == f"forced {'clone' if failure == 'clone' else 'renderer'} error"
            assert _state(page) == before
            assert not page.locator("iframe.html2canvas-container,html2canvaspseudoelement").count()
        # An unsupported interpolation fails in the real compatibility hook,
        # retaining the authored style rather than substituting another color.
        paint = page.locator(".capture-paint span")
        paint.evaluate("e=>e.style.backgroundImage='linear-gradient(color(srgb 1 0 0),color(srgb 0 0 1))'")
        authored = paint.get_attribute("style")
        error = page.locator(".fw-section").last.evaluate("el=>fw.captureElementSnapshotForAnalysis(el).then(()=>null,e=>e.message)")
        assert error == "Image capture cannot preserve modern multicolor gradient interpolation."
        assert paint.get_attribute("style") == authored
        assert not page.locator("iframe.html2canvas-container").count()
        paint.evaluate("e=>e.style.backgroundImage=''")
        assert _state(page) == before
        # Exercise the full-report rejection path too, with a real clone.
        page.evaluate("""()=>{window.realCapture=window.html2canvas;window.html2canvas=(el,options)=>{
            const onclone=options.onclone;options.onclone=async(doc,root)=>{await onclone(doc,root);throw new Error('forced clone error')};
            return realCapture(el,options)};fw.exportPNG();}""")
        page.locator(".fw-analysis-error").wait_for()
        assert "forced clone error" in page.locator(".fw-analysis-error").inner_text()
        assert not page.locator("#fwExportBtn.running,iframe.html2canvas-container").count()
        assert _state(page) == before
    finally:
        page.close()

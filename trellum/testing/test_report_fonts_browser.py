"""Report metadata and canvas labels share typography, including theme changes."""

from pathlib import Path

import pytest

from trellum.assets import load_css, load_js
from trellum.components.base import RenderContext
from trellum.components.report_metadata import ReportMetadata
from trellum.themes import DEFAULT_THEME


@pytest.mark.parametrize("width", [1440, 390])
def test_metadata_and_chart_fonts_follow_report_theme(width, tmp_path):
    from playwright.sync_api import sync_playwright

    vendor = Path(__file__).resolve().parents[1] / "static" / "vendor"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.route("http://fonts.test/**", lambda route: route.fulfill(
            path=str(vendor / route.request.url.removeprefix("http://fonts.test/"))))
        css = DEFAULT_THEME.to_css_vars()
        page.set_content(
            '<link rel="stylesheet" href="http://fonts.test/inter.css">'
            f'<style>{css}' + load_css("base.css")
            + load_css("components/header.css") + ReportMetadata.css()
            + 'html[data-theme="custom"]{--font-family:monospace;}</style>'
            '<div class="fw-report-chrome"><header class="fw-header">'
            '<div class="fw-header-left"><h1>Report font check</h1></div>'
            '</header>' + ReportMetadata(values={"Source": "Daily report"}).render_html(RenderContext(theme=DEFAULT_THEME))
            + '</div><div class="fw-container"><h2>Revenue by day</h2>'
            '<div style="height:300px"><canvas id="fontChart"></canvas></div></div>'
        )
        page.add_script_tag(path=str(vendor / "chart.umd.min.js"))
        page.evaluate("""() => {
            window._fwConfig = {defaultTheme:'default', themes:{
                default:{tick_color:'#ddd', grid_color:'#444', chart_colors:['#36a89e']},
                custom:{tick_color:'#ddd', grid_color:'#444', chart_colors:['#36a89e']}
            }};
        }""")
        page.add_script_tag(content="\n".join(load_js("runtime/" + name + ".js")
            for name in ("storage", "theme", "chart_defaults")))
        page.evaluate("""() => {
            window._chartInstances = {};
            window._chartInstances = {fontChart: new Chart(document.getElementById('fontChart'), {
                type:'bar', data:{labels:['Monday','Tuesday'], datasets:[{
                    label:'Revenue',data:[12,18],backgroundColor:'#36a89e'
                }]},
                options:{animation:false, plugins:{legend:{display:true}}}
            })};
        }""")
        page.evaluate("document.fonts.ready")

        def fonts():
            return page.evaluate("""() => {
                var chart = window._chartInstances.fontChart;
                return {
                    metadata:getComputedStyle(document.querySelector('.fw-report-metadata')).fontFamily,
                    body:getComputedStyle(document.body).fontFamily,
                    axis:chart.scales.x._resolveTickFontOptions(0).family,
                    legend:Chart.helpers.toFont(chart.legend.options.labels.font).family
                };
            }""")

        initial = fonts()
        assert "Inter" in initial["body"]
        assert len(set(initial.values())) == 1, initial
        assert page.evaluate("document.fonts.check('12px Inter')")
        page.screenshot(path=str(tmp_path / f"report-font-{width}.png"))
        page.evaluate("switchTheme('custom')")
        page.wait_for_function("window._chartInstances.fontChart.scales.x._resolveTickFontOptions(0).family === 'monospace'")
        assert set(fonts().values()) == {"monospace"}
        browser.close()

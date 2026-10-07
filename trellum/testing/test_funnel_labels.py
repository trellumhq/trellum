"""Narrow funnel labels move outside their bars on small screens."""

import pytest

from trellum.assets import STATIC_ROOT, load_js


@pytest.fixture
def mobile_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 360, "height": 480})
        yield page
        browser.close()


def test_short_funnel_labels_stay_clear_of_axis_on_mobile(mobile_page):
    page = mobile_page
    page.set_content(
        '<div style="width:340px;height:300px"><canvas id="funnel"></canvas></div>'
    )
    page.add_script_tag(content=(STATIC_ROOT / "vendor/chart.umd.min.js").read_text())
    page.add_script_tag(
        content=(STATIC_ROOT / "vendor/chartjs-plugin-datalabels.min.js").read_text()
    )
    page.evaluate("""() => {
        Chart.register(ChartDataLabels);
        window._chartInstances = {};
        window._fwRenderers = {};
        window._fwFontPx = () => 12;
        window.getThemeColors = () => ({
            chart_colors: ['#477a9b'], grid_color: '#ddd', tick_color: '#222'
        });
        window.fmtCompact = value => String(value);
    }""")
    page.add_script_tag(content=load_js("components/funnel.js"))
    page.evaluate("""() => window._fwRenderers.funnel('funnel', {
        labels: ['Opened product page', 'Added item to cart', 'Started checkout',
                 'Entered delivery details', 'Submitted payment'],
        values: [10000, 5000, 800, 100, 10], show_pct: true
    })""")

    result = page.evaluate("""() => {
        const chart = window._chartInstances.funnel;
        const options = chart.config.options.plugins.datalabels;
        const bars = chart.getDatasetMeta(0).data.map((bar, dataIndex) => {
            const ctx = {chart, datasetIndex: 0, dataIndex};
            return {
                width: bar.getProps(['width'], true).width,
                anchor: options.anchor(ctx), align: options.align(ctx),
                clamp: options.clamp, clip: options.clip
            };
        });
        const labels = chart.$datalabels._datasets[0].map(label => ({
            anchor: label._model.anchor, align: label._model.align,
            rect: label.$layout._box._rect
        }));
        return {bars, labels, chartAreaLeft: chart.chartArea.left,
                canvasWidth: chart.width};
    }""")

    assert result["bars"][0]["anchor"] == "center"
    assert result["bars"][0]["align"] == "center"
    assert all(row["anchor"] == row["align"] == "end" for row in result["bars"][2:])
    assert all(row["clamp"] and row["clip"] is False for row in result["bars"])
    assert all(row["anchor"] == row["align"] == "end" for row in result["labels"][2:])
    assert all(row["rect"]["x"] >= result["chartAreaLeft"]
               and row["rect"]["x"] + row["rect"]["w"] <= result["canvasWidth"]
               for row in result["labels"])

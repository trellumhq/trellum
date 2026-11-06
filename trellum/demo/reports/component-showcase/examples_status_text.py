"""Small status-text roles on native report cards and comparison tables."""

import pandas as pd

from trellum.components import ComparisonTable, DataSource, FilterBar, KpiCard, KpiRow, RawHTML


def status_fixture():
    """Distinct supplied metric observations; no comparison math runs in the UI."""
    frame = pd.DataFrame([
        ("Revenue", 24800, .12, "", "Performance"),
        ("Cost per install", 2.35, -.08, "", "Performance"),
        ("Refund rate", .07, 2.0, "", "Performance"),
        ("Retention", .31, 0, "", "Context"),
        ("New market", 735, None, "No prior period", "Context"),
    ], columns=["Metric", "Current", "Change", "Reason", "Focus"])
    context = {
        "metric_column": "Metric", "period_labels": {"Current": "Current week"},
        "change_labels": {"Change": "Change vs prior week"},
        "reason_columns": {"Change": "Reason"},
        "metrics": {
            "Revenue": {"direction": "higher", "format": "currency", "change_measure": "relative_percent"},
            "Cost per install": {"direction": "lower", "format": "currency", "change_measure": "relative_percent"},
            "Refund rate": {"direction": "lower", "format": "percent", "change_measure": "percentage_points"},
            "Retention": {"direction": "higher", "format": "percent", "change_measure": "percentage_points"},
            "New market": {"direction": "higher", "format": "currency", "change_measure": "relative_percent"},
        },
    }
    return frame, context


def add_sections(ctx):
    ctx.add_section("Readable small status text", [
        RawHTML(html="<p>Theme-specific outcome, forecast and context tones keep the current "
                "card sizes. Chart palettes and accents are separate. Direction and outcome "
                "words carry meaning alongside color. These are synthetic supplied values.</p>"),
        KpiRow([
            KpiCard(label="Revenue", value=24800, format="currency", delta="+12% · Improved",
                    delta_direction="up", sub="Higher is better"),
            KpiCard(label="Cost per install", value=2.35, format="currency", delta="−8% · Improved",
                    delta_direction="up", sub="Lower is better"),
            KpiCard(label="Refund rate", value=.07, format="percent", delta="+2 pp · Worsened",
                    delta_direction="down", sub="Lower is better"),
            KpiCard(label="Retention", value=.31, format="percent", delta="0 pp · Unchanged",
                    sub="Same weekly population"),
            KpiCard(label="Supplied forecast", value=9600, format="currency", forecast=10500,
                    sub="Author estimate · next week"),
            KpiCard(label="Prior period", value="—", format="plain", delta="Unknown change",
                    sub="No prior period supplied"),
        ], static=True),
    ])
    frame, context = status_fixture()
    ctx.add_section("Status text with declared metric directions", [
        DataSource("showcase_status_text", frame),
        FilterBar("showcase_status_text", frame, filters=[
            {"column": "Focus", "label": "Status examples: focus", "type": "toggle"},
        ]),
        RawHTML(html="<p>Supplied changes use each metric's declared direction. Outcome and "
                "unavailable-reason words retain a neutral context tone.</p>"),
        ComparisonTable(frame, "Declared status outcomes", "Current", ["Change"],
                        dataset_id="showcase_status_text", columns=["Metric", "Current", "Change"],
                        comparison_context=context),
        RawHTML(html="<p class='fw-table-note'>Both weekly scopes contain seven days; the new "
                "market has no prior-period comparison.</p>"),
    ])
    legacy = pd.DataFrame({"Metric": ["Orders", "Returns"], "Current": [410, 18],
                           "Change": [35, -4]})
    ctx.add_section("Status text in legacy comparison rules", [
        RawHTML(html="<p>Legacy comparison columns retain sign-based auto colors and supplied "
                "values. Explicit author colors take precedence over semantic text roles.</p>"),
        ComparisonTable(legacy, "Legacy sign colors", "Current", ["Change"], static=True),
        ComparisonTable(legacy, "Explicit accent and hex colors", "Current", ["Change"], static=True,
                        conditional_formats={"Change": [
                            {"op": ">", "value": 0, "color": "var(--accent-green)"},
                            {"op": "<", "value": 0, "color": "#b1457b"},
                        ]}),
    ])

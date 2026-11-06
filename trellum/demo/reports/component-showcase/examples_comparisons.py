"""Supplied comparison values with explicit periods, units and outcomes."""

import pandas as pd

from trellum.components import ComparisonTable, DataSource, FilterBar, RawHTML


def comparison_fixture():
    """Compute fixture deltas upstream from unrounded, matching weekly scopes."""
    cpi = 26680 / 10180
    rows = [
        ("Revenue", 30000, 33240, (33240 - 30000) / 30000, "", "", "Performance"),
        ("Cost per install", 2.8, cpi, (cpi - 2.8) / 2.8, "", "", "Performance"),
        ("Refund rate", .05, .08, (.08 - .05) * 100, "", "", "Performance"),
        ("Day 1 retention", .3, .3, 0.0, "", "", "Performance"),
        ("New-channel revenue", 0, 480, None, "", "Zero baseline", "Availability"),
        ("Day 7 retention", .32, None, None, "Not mature", "Awaiting day 7", "Availability"),
        ("Average order value", 4.5, None, None, "No current data", "No current data", "Availability"),
        ("Sessions per player", 2.15, 2.4, (2.4 - 2.15) / 2.15, "", "", "Performance"),
    ]
    frame = pd.DataFrame(rows, columns=[
        "Metric", "Previous", "Current", "Change", "Current reason", "Change reason", "Focus",
    ])
    metrics = {
        "Revenue": {"direction": "higher", "format": "currency", "precision": 0},
        "Cost per install": {"direction": "lower", "format": "currency", "precision": 2},
        "Refund rate": {"direction": "lower", "format": "percent", "precision": 1,
                        "change_measure": "percentage_points"},
        "Day 1 retention": {"direction": "higher", "format": "percent", "precision": 1,
                            "change_measure": "percentage_points"},
        "New-channel revenue": {"direction": "higher", "format": "currency", "precision": 0},
        "Day 7 retention": {"direction": "higher", "format": "percent", "precision": 1,
                            "change_measure": "percentage_points"},
        "Average order value": {"direction": "higher", "format": "currency", "precision": 2},
        # The author deliberately supplies no favorable direction for this metric.
        "Sessions per player": {"format": "ratio", "precision": 2},
    }
    for spec in metrics.values():
        spec.setdefault("change_measure", "relative_percent")
        spec["change_precision"] = 1
    context = {
        "metric_column": "Metric",
        "period_labels": {"Previous": "Previous · 21–27 Oct", "Current": "Current · 28 Oct–3 Nov"},
        "change_labels": {"Change": "Change · vs previous"},
        "metrics": metrics,
        "reason_columns": {"Current": "Current reason", "Change": "Change reason"},
    }
    return frame, context


def add_sections(ctx):
    frame, context = comparison_fixture()
    columns = ["Metric", "Previous", "Current", "Change"]
    ctx.add_section("Explicit weekly comparisons", [
        RawHTML(html="<p>Synthetic weekly observations use the same population and seven-day "
                "scope in both periods. Deltas are supplied from unrounded inputs; "
                "undefined changes and cohort maturity are author-declared.</p>"),
        ComparisonTable(frame, "Weekly metric outcomes", "Current", ["Change"],
                        static=True, columns=columns, comparison_context=context),
    ])
    ctx.add_section("Filterable comparison context", [
        DataSource("showcase_comparisons", frame),
        FilterBar("showcase_comparisons", frame, filters=[
            {"column": "Focus", "label": "Comparison examples: focus", "type": "toggle"},
        ]),
        ComparisonTable(frame, "Supplied comparisons by focus", "Current", ["Change"],
                        dataset_id="showcase_comparisons", columns=columns,
                        comparison_context=context),
    ])
    ctx.add_section("Explicit comparison color rules", [
        RawHTML(html="<p>This author rule colors negative supplied changes. Other values "
                "remain neutral; declared outcome text still explains their meaning.</p>"),
        ComparisonTable(frame.iloc[:4], "Author color precedence", "Current", ["Change"],
                        static=True, columns=columns, comparison_context=context,
                        conditional_formats={"Change": [
                            {"op": "<", "value": 0, "color": "var(--accent-red)"},
                        ]}),
    ])
    ctx.add_section("Legacy absolute reference columns", [
        RawHTML(html="<p>Without comparison_context, comparison columns retain their supplied "
                "values and legacy sign colors. Budget is an absolute reference.</p>"),
        ComparisonTable(pd.DataFrame({"Campaign": ["Launch", "Returning players"],
                                      "Actual": [18420.125, 6250.875], "Budget": [20000, 6000]}),
                        "Actual and budget", "Actual", ["Budget"], static=True),
    ])

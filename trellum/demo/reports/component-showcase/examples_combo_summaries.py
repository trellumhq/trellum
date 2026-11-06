"""Native axis-specific summaries over four complete additive synthetic facts."""
import pandas as pd

from trellum.components import ComboChart, DataSource, FilterBar, Grid, RawHTML

TITLES = ("Combo summaries — static off/on", "Combo summaries — axis identity and reducers",
          "Combo summaries — context and independent legends", "Combo summaries — eligibility and legacy formats")


def facts():
    return pd.DataFrame([{"day": f"2026-08-0{day}", "platform": platform,
        "spend": 10 * day * multiplier, "fees": 2 * day * multiplier,
        "orders": 3 * day * multiplier, "visits": 20 * day * multiplier}
        for day in (1, 2) for platform, multiplier in (("iOS", 1), ("Android", 2))])


def summary(stat="sum", axis="left", series="Daily total", **options):
    return {"stat": stat, "scope": "axis_series", "axis": axis, "series": series, **options}


def filters(dataset, frame, **options):
    return FilterBar(dataset, frame, filters=[{"column": "day", "type": "date_range"},
        {"column": "platform", "type": "toggle"}], **options)


def live(frame, dataset, title, **options):
    return ComboChart(frame, "day", ["spend", "fees"], ["orders", "visits"], title,
        dataset_id=dataset, bar_labels=["Daily total", "Fees"], line_labels=["Daily total", "Visits"],
        **options)


def add_sections(ctx):
    frame = facts()
    dataset = "combo_summary_static"
    static = frame.groupby("day", as_index=False)[["spend", "fees", "orders", "visits"]].sum().iloc[::-1]
    ctx.add_section(TITLES[0], [DataSource(dataset, frame), filters(dataset, frame),
        RawHTML(html="<p>Four complete additive facts. Static supplied order is Aug 2 then Aug 1: spend [60, 30], last 30. "
            "Declared static percent formats remain inactive: the plot uses compact numbers left and currency right. "
            "The bound companion responds to this section's filters.</p>"),
        Grid(columns=2, children=[ComboChart(static, "day", ["spend"], ["orders"], "Static " + mode,
            static=True, bar_labels=["Daily total"], line_labels=["Daily total"],
            bar_format="percent", line_format="percent",
            **({"header_summary": summary("last")} if mode == "on" else {})) for mode in ("off", "on")]),
        live(frame, dataset, "Static section live companion", header_summary=summary())])
    dataset = "combo_summary_reducers"
    ctx.add_section(TITLES[1], [DataSource(dataset, frame), filters(dataset, frame),
        RawHTML(html="<p>Spend [30, 60]: total 90, mean 45, min 30, max 60, last 60. Orders [9, 18]: total 27, mean 13.5. "
            "Both captions are Daily total, selected independently by axis. Fees total 18; visits total 180. "
            "iOS + Aug 1: spend 10 and orders 3. Quantities are additive delivered facts, not unique people.</p>"),
        Grid(columns=2, children=[
            *[live(frame, dataset, "Spend " + stat, header_summary=summary(stat)) for stat in ("sum", "mean", "min", "max", "last")],
            *[live(frame, dataset, "Orders " + stat, header_summary=summary(stat, "right")) for stat in ("sum", "mean")],
            live(frame, dataset, "Fees sum", header_summary=summary(series="Fees")),
            live(frame, dataset, "Visits sum", header_summary=summary(axis="right", series="Visits")),
        ])])
    dataset = "combo_summary_context"
    ctx.add_section(TITLES[2], [DataSource(dataset, frame), filters(dataset, frame),
        RawHTML(html="<p>Stacked bars still summarize the selected individual dataset. Legends and zoom change the view, "
            "not the summary population. Context, month context and the explicit 220px plot height remain independent.</p>"),
        Grid(columns=2, children=[live(frame, dataset, "Context " + axis, stacked_bars=True, height=220,
            month_context=True, header_context="Delivered observations\nIndependent chart context.",
            left_axis_label="Spend and fees · USD", right_axis_label="Orders and visits · count",
            header_summary=summary(axis=axis)) for axis in ("left", "right")])])
    dataset = "combo_summary_eligibility"
    variants = {}
    for name, value in (("missing", None), ("string", "10"), ("bool", True), ("unsafe", 2 ** 53)):
        current = frame.copy()
        current["spend"] = current.spend.astype(object)
        current.loc[0, "spend"] = value
        variants[name] = current
    zero = frame.iloc[::2].copy()
    zero["spend"] = 0
    variants["zero"] = zero
    signed = frame.copy()
    signed["spend"] = [-10, 20, -20, 40]
    variants["signed"] = signed
    fraction = frame.copy()
    fraction["orders"] = fraction.orders / 100
    variants["fraction"] = fraction
    sources = {dataset + "_" + name: data for name, data in variants.items()}
    ctx.add_section(TITLES[3], [DataSource(dataset, frame), *[DataSource(key, data) for key, data in sources.items()],
        filters(dataset, frame, propagate_to={key: {"day": "day", "platform": "platform"} for key in sources}),
        RawHTML(html="<p>Selected missing, string, boolean and unsafe integer inputs withhold the statistic; unselected invalid values do not. "
            "Filtering away invalid facts restores eligibility. Zero and signed quantities count. "
            "Repeated source columns across axes preserve legacy doubled aggregation (spend [60, 120]). "
            "Duplicate same-axis captions are ambiguous; opposite-axis matches are independent.</p>"),
        Grid(columns=2, children=[
            *[live(data, dataset + "_" + name, "Eligibility " + name, header_summary=summary())
                for name, data in variants.items() if name != "fraction"],
            live(variants["missing"], dataset + "_missing", "Unselected invalid", header_summary=summary(axis="right")),
            live(variants["fraction"], dataset + "_fraction", "Fractional orders", line_format="percent", header_summary=summary("mean", "right")),
            ComboChart(frame, "day", ["spend", "fees"], ["orders"], "Same-axis duplicate", dataset_id=dataset,
                bar_labels=["Daily total", "Daily total"], line_labels=["Daily total"], header_summary=summary()),
            live(frame, dataset, "Missing caption", header_summary=summary(series="Absent")),
            ComboChart(frame, "day", ["spend"], ["spend"], "Reused source", dataset_id=dataset,
                bar_labels=["Daily total"], line_labels=["Daily total"], header_summary=summary()),
            ComboChart(static, "day", ["spend"], ["orders"], "Static right actual currency", static=True,
                line_labels=["Daily total"], line_format="percent", header_summary=summary("mean", "right")),
        ])])

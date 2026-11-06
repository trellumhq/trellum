"""Native original-unit summaries over complete synthetic delivered events."""
import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, RawHTML, StackedBar, Toggle

BREAKDOWNS = {"Channel": "channel", "Paid/Owned": "group"}
TITLES = ("Stacked summaries — static off/on", "Stacked summaries — selected reducers",
          "Stacked summaries — local and shared controls", "Stacked summaries — eligibility and identity")


def facts():
    """12 complete additive facts; UA 90, RT 180, Organic 270, total 540."""
    return pd.DataFrame([{"day": f"2026-08-0{day}", "platform": platform, "channel": channel,
        "group": "Owned" if channel == "Organic" else "Paid", "events": base * day * multiplier,
        "line": .01 * day * multiplier}
        for day in (1, 2) for platform, multiplier in (("iOS", 1), ("Android", 2))
        for channel, base in (("UA", 10), ("RT", 20), ("Organic", 30))])


def summary(stat="sum", series="UA", **options):
    return {"stat": stat, "scope": "absolute_series", "series": series, **options}


def filters(dataset, frame, **options):
    return FilterBar(dataset, frame, filters=[{"column": "day", "type": "date_range"},
        {"column": "platform", "type": "toggle"}], **options)


def live(frame, dataset, title, **options):
    return StackedBar(frame, "day", ["events"], title, dataset_id=dataset, stack_by="channel",
        value_format="number", **options)


def add_sections(ctx):
    frame = facts()
    dataset = "stacked_summary_static"
    static = pd.DataFrame({"day": ["2026-08-01", "2026-08-02"], "UA": [30, 60]})
    ctx.add_section(TITLES[0], [DataSource(dataset, frame), filters(dataset, frame),
        RawHTML(html="<p>Static supplied snapshot: UA [30, 60], total 90. Its legacy title stays omitted. "
            "The live companion responds to this section's independent filters. Counts are delivered additive events, not people.</p>"),
        Grid(columns=2, children=[StackedBar(static, "day", ["UA"], "Static off", static=True, value_format="number"),
            StackedBar(static, "day", ["UA"], "Static on", static=True, value_format="number", header_summary=summary())]),
        live(frame, dataset, "Static section live companion", header_summary=summary())])
    dataset = "stacked_summary_reducers"
    ctx.add_section(TITLES[1], [DataSource(dataset, frame), filters(dataset, frame),
        RawHTML(html="<p>Full UA displayed entries [30, 60]: sum 90, unweighted mean 45, min 30, max 60, last 60. "
            "iOS + Aug 1 gives 10. Each statistic uses the final displayed entries; it asserts no warehouse completeness.</p>"),
        Grid(columns=2, children=[live(frame, dataset, "UA " + stat, header_summary=summary(stat),
            line_cols=["line"], line_labels=["UA"]) for stat in ("sum", "mean", "min", "max", "last")])])
    dataset = "stacked_summary_controls"
    ctx.add_section(TITLES[2], [DataSource(dataset, frame), filters(dataset, frame),
        RawHTML(html="<p>Local and shared native controls preserve original summary units in Percentage mode. "
            "Paid/Owned removes the exact UA caption. Legends are independent. Shared plots are fixed at 220px.</p>"),
        live(frame, dataset, "Local UA", header_summary=summary(), stack_by_options=BREAKDOWNS,
            normalize_toggle=True, cross_filter=True),
        Toggle(dataset + "_split", list(BREAKDOWNS)), Toggle(dataset + "_mode", ["Absolute", "Percentage"]),
        Grid(columns=2, children=[live(frame, dataset, "Shared " + series, header_summary=summary(series=series),
            show_controls=False, height=220, stack_by_options=BREAKDOWNS, normalize_toggle=True,
            stack_toggle_id=dataset + "_split", normalize_toggle_id=dataset + "_mode",
            header_context="Delivered events; original units in the summary.") for series in ("UA", "RT")])])
    dataset = "stacked_summary_eligibility"
    invalid = frame.copy(); invalid["events"] = invalid.events.astype(object); invalid.loc[0, "events"] = None
    string = frame.copy(); string["events"] = string.events.astype(object); string.loc[0, "events"] = "10"
    zero = frame.copy(); zero.loc[zero.channel == "UA", "events"] = 0
    signed = frame.copy(); signed.loc[signed.channel == "RT", "events"] *= -1
    rate = frame.copy(); rate["events"] = rate.events / 1000
    ambiguous = frame.copy(); ambiguous["second"] = ambiguous.events * -1
    variants = {"null": invalid, "string": string, "zero": zero, "signed": signed, "rate": rate, "ambiguous": ambiguous}
    sources = {dataset + "_" + key: value for key, value in variants.items()}
    ctx.add_section(TITLES[3], [DataSource(dataset, frame),
        *[DataSource(key, value) for key, value in sources.items()],
        filters(dataset, frame, propagate_to={key: {"day": "day", "platform": "platform"} for key in sources}),
        RawHTML(html="<p>max_stacks=2 groups UA into Other (1), exactly 90. UA is then unavailable. "
            "Null/string input withholds summaries without changing legacy plots; filtered-away invalid facts no longer veto. "
            "Measured zero stays zero. Signed quantities remain allowed. Fractional declared rates use an unweighted mean. "
            "Multiple diverging measures with the same final caption are ambiguous.</p>"),
        Grid(columns=2, children=[
            live(frame, dataset, "UA grouped away", max_stacks=2, header_summary=summary()),
            live(frame, dataset, "Exact Other", max_stacks=2, header_summary=summary(series="Other (1)")),
            *[live(value, dataset + "_" + key, "Eligibility " + key, header_summary=summary())
                for key, value in (("null", invalid), ("string", string), ("zero", zero), ("signed", signed))],
            StackedBar(rate, "day", ["events"], "Fractional rate", dataset_id=dataset + "_rate", stack_by="channel",
                value_format="percent", header_summary=summary("mean")),
            StackedBar(ambiguous, "day", ["events", "second"], "Ambiguous UA", dataset_id=dataset + "_ambiguous",
                stack_by="channel", diverging=True, value_format="number", header_summary=summary()),
        ])])

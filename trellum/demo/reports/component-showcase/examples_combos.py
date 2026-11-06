"""Deterministic ComboChart examples, with daily observations by channel."""
import pandas as pd

from trellum.components import ComboChart, DataSource, FilterBar, Grid, RawHTML


def add_sections(ctx):
    frame = pd.DataFrame([
        {"day": f"2026-10-{i + 1:02d}", "channel": channel,
         "spend": (900 + i * 70) * scale, "fees": (80 + i * 11) * scale,
         "orders": (25 + i * 3) * scale, "visits": (180 + i * 23) * scale}
        for i in range(8) for channel, scale in (("Direct", 1), ("Partner", 2))
    ])
    ctx.add_section("Combo charts — daily channel observations", [
        DataSource("showcase_combos", frame),
        FilterBar("showcase_combos", frame, filters=[
            {"column": "channel", "label": "Combo examples: channel", "type": "toggle"}]),
        RawHTML(html="<p>Bars use the left axis and lines use the right. Each row is one "
                "day and channel; filters sum the matching observations. Legend controls "
                "change visibility, while CSV includes every plotted series.</p>"),
        ComboChart(frame, "day", ["spend", "fees"], ["orders", "visits"],
            "Currency bars and count lines · live", dataset_id="showcase_combos",
            bar_labels=["Spend", "Fees"], line_labels=["Orders", "Visits"],
            left_axis_label="Spend and fees · USD", right_axis_label="Orders and visits · count",
            bar_format="currency", line_format="number"),
        ComboChart(frame, "day", ["spend", "fees"], ["orders"],
            "Same caption on opposite axes · independent controls", dataset_id="showcase_combos",
            bar_labels=["Daily total", "Fees"], line_labels=["Daily total"],
            left_axis_label="Daily spend and fees · USD", right_axis_label="Daily orders · count"),
    ])
    fixed = frame[frame.channel == "Direct"].reset_index(drop=True)
    ctx.add_section("Combo charts — fixed representations and axis context", [
        RawHTML(html="<p>These independent static charts retain supplied rows. The current "
                "static path uses compact-number left ticks and currency right ticks, "
                "regardless of bar_format/line_format; y_max, y1_max and show_labels "
                "are not forwarded. Names below identify actual series without implying "
                "a formatter. Explicit height and line_colors are honored.</p>"),
        ComboChart(fixed, "day", ["spend"], ["orders"],
            "Static defaults · explicit line color and 220px plot", static=True,
            bar_labels=["Spend"], line_labels=["Orders"], line_colors=["#ab3478"],
            height=220, bar_format="currency", line_format="percent",
            y_max=9999, y1_max=9999, show_labels=True),
        ComboChart(fixed, "day", ["spend", "fees"], ["orders", "visits"],
            "Long series captions · wrapping context", static=True,
            bar_labels=["Daily spend for the international storefront", "Processing fees for completed purchases"],
            line_labels=["Completed orders across the supplied daily observations", "Storefront visits from the direct channel"]),
        Grid(children=[
            ComboChart(fixed, "day", ["spend"], [], "Left axis only · supplied spend", static=True),
            ComboChart(fixed, "day", [], ["orders"], "Right series only · supplied orders", static=True),
        ]),
        ComboChart(fixed.iloc[:0], "day", ["spend"], ["orders"],
            "No supplied observations · named series retained", static=True),
        ComboChart(fixed.iloc[:0], "day", [], [], "No configured series · no legend groups", static=True),
    ])

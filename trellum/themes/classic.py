from trellum.themes.theme import Theme

# The palettes that were the default light/dark before the trellum identity
# (pre-v0.6.0). Kept in the registry as "light" / "dark" so nobody loses a
# look they had chosen; the trellum pair carries the defaults now.

# Both classics share one chart palette (they always did) -- keep it in one
# place so an edit cannot fork the light and dark variants silently.
_CLASSIC_CHART_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
]

ClassicLightTheme = Theme(
    bg_main="#f0f2f5",
    bg_card="#ffffff",
    bg_card_hover="#f8f9fc",
    bg_header="#E84855",

    text_main="#1e2028",
    text_secondary="#6b7280",
    text_muted="#9ca3af",

    accent_green="#10b981",
    accent_red="#ef4444",
    accent_blue="#3b82f6",
    accent_yellow="#f59e0b",

    border_color="#e2e5ea",
    border_radius="10px",
    shadow="0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)",

    chart_colors=list(_CLASSIC_CHART_COLORS),

    color_today="#ef4444",
    color_yesterday="#f59e0b",
    color_2d_ago="#f97316",
    color_7d_ago="#3b82f6",
    color_forecast="#9ca3af",

    grid_color="#e5e7eb",
    tick_color="#6b7280",
)

ClassicDarkTheme = Theme(
    bg_main="#1A1A2E",
    bg_card="#2A2A3E",
    bg_card_hover="#33334D",
    bg_header="#E84855",

    text_main="#e4e6eb",
    text_secondary="#9a9bb0",
    text_muted="#6b6d82",

    accent_green="#00c48c",
    accent_red="#FF4081",
    accent_blue="#4C8BF5",
    accent_yellow="#FFD166",

    border_color="#3A3A50",
    border_radius="10px",
    shadow="0 2px 8px rgba(0,0,0,0.3)",

    chart_colors=list(_CLASSIC_CHART_COLORS),

    color_today="#FF4081",
    color_yesterday="#FFD166",
    color_2d_ago="#FF8C42",
    color_7d_ago="#4C8BF5",
    color_forecast="#ffffff",

    grid_color="#2E2E45",
    tick_color="#6b6d82",
)

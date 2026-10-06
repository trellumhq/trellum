from trellum.themes.theme import Theme

DarkTheme = Theme(
    bg_main="#111315",
    bg_card="#181b1f",
    bg_card_hover="#23272d",
    bg_header="#0F766E",

    text_main="#f3f4f6",
    text_secondary="#a7adb8",
    text_muted="#8f96a3",

    accent_green="#00c48c",
    accent_red="#F87171",
    accent_blue="#4C8BF5",
    accent_yellow="#FFD166",

    border_color="#30363d",
    border_radius="10px",
    shadow="0 2px 8px rgba(0,0,0,0.3)",

    # Dark-surface chart palette; leading trio is CVD/contrast-validated
    chart_colors=[
        "#11a398", "#7e88e6", "#be7f0a", "#5fa362", "#b58bc9",
        "#4c8bf5", "#a98467", "#6fbab5", "#d4638f", "#8b8ba7",
    ],

    color_today="#2DD4BF",
    color_yesterday="#FFD166",
    color_2d_ago="#FF8C42",
    color_7d_ago="#7e88e6",
    color_forecast="#ffffff",

    grid_color="#2b3037",
    tick_color="#8f96a3",
)

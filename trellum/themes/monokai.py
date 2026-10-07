from trellum.themes.theme import Theme

MonokaiTheme = Theme(
    bg_main="#272822",
    bg_card="#3e3d32",
    bg_card_hover="#49483e",
    bg_header="#a6e22e",
    on_accent="#172126",

    text_main="#f8f8f2",
    text_secondary="#c0b8a8",
    text_muted="#75715e",

    accent_green="#a6e22e",
    accent_red="#f92672",
    accent_blue="#66d9ef",
    accent_yellow="#e6db74",

    border_color="#49483e",
    border_radius="10px",
    shadow="0 2px 8px rgba(0,0,0,0.35)",

    chart_colors=[
        "#f92672", "#a6e22e", "#66d9ef", "#e6db74", "#ae81ff",
        "#fd971f", "#f44747", "#a1efe4", "#f8f8f2", "#cc6633",
    ],

    color_today="#f92672",
    color_yesterday="#e6db74",
    color_2d_ago="#fd971f",
    color_7d_ago="#66d9ef",
    color_forecast="#75715e",

    grid_color="#3e3d32",
    tick_color="#75715e",
)

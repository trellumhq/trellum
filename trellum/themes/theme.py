from dataclasses import dataclass, field
from typing import List


@dataclass
class Theme:
    """Visual theme applied to all report components.

    Every field becomes a CSS variable (e.g. bg_main -> --bg-main).
    """

    # Backgrounds
    bg_main: str = "#f6f7f8"
    bg_card: str = "#ffffff"
    bg_card_hover: str = "#eef1f3"
    bg_header: str = "#0F766E"

    # Text
    text_main: str = "#172126"
    text_secondary: str = "#475569"
    text_muted: str = "#64748b"

    # Accents
    accent_green: str = "#10b981"
    accent_red: str = "#ef4444"
    accent_blue: str = "#3b82f6"
    accent_yellow: str = "#f59e0b"

    # Borders
    border_color: str = "#e2e6ea"
    border_radius: str = "10px"
    shadow: str = "0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)"

    # Chart palette (10-color; leading trio is CVD/contrast-validated)
    chart_colors: List[str] = field(default_factory=lambda: [
        "#0d9488", "#6c7fd8", "#c9861b", "#59a14f", "#b07aa1",
        "#4e79a7", "#9c755f", "#76b7b2", "#d4638f", "#8a8aa8",
    ])

    # Day-comparison line colors (today wears the accent, never danger red)
    color_today: str = "#0f766e"
    color_yesterday: str = "#f59e0b"
    color_2d_ago: str = "#f97316"
    color_7d_ago: str = "#6c7fd8"
    color_forecast: str = "#9ca3af"

    # Chart grid/tick colors
    grid_color: str = "#e2e6ea"
    tick_color: str = "#64748b"

    # Typography
    font_family: str = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    font_size_base: str = "14px"
    font_size_small: str = "12px"
    font_size_kpi: str = "24px"
    # Semantic interface sizes.  Components consume these instead of
    # embedding tiny values, while existing public typography knobs remain
    # the source of truth for report authors.
    font_size_interface: str = "var(--font-size-base)"
    font_size_table: str = "13px"
    font_size_label: str = "var(--font-size-small)"
    font_size_meta: str = "var(--font-size-small)"
    font_size_axis: str = "var(--font-size-small)"
    font_size_tooltip: str = "var(--font-size-small)"
    font_size_section: str = "16px"
    font_size_heading: str = "24px"
    radius_control: str = "8px"
    radius_card: str = "var(--border-radius)"
    on_accent: str = "#ffffff"
    primary_fill: str = "var(--bg-header)"

    # Spacing
    spacing_sm: str = "8px"
    spacing_md: str = "16px"
    spacing_lg: str = "24px"

    def to_css_vars(self, selector: str = ":root") -> str:
        """Convert theme fields to CSS custom properties.

        Args:
            selector: CSS selector to scope the variables under.
                Defaults to ``:root``.  Use ``[data-theme="dark"]`` for
                attribute-scoped theme blocks.
        """
        lines = []
        for key, val in self.__dict__.items():
            if isinstance(val, list):
                continue
            css_name = key.replace("_", "-")
            lines.append(f"    --{css_name}: {val};")
        return f"{selector} {{\n" + "\n".join(lines) + "\n}"

    def to_dict(self) -> dict:
        """Serialize all theme fields to a plain dict (including lists)."""
        return {k: v for k, v in self.__dict__.items()}

    def chart_colors_js(self) -> str:
        """Return chart_colors as a JS array literal."""
        items = ", ".join(f"'{c}'" for c in self.chart_colors)
        return f"[{items}]"

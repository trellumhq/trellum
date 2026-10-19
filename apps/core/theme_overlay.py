"""Portal-owned theme tokens: the layer the framework does not own.

The 13 themes belong to the framework (``trellum.themes.THEME_REGISTRY``) so
that a project using the framework alone can pick one. The portal derives its
CSS theme table from that registry — see
``apps/core/management/commands/sync_themes.py``.

Two kinds of token cannot come from the framework:

1. **Tokens it has no field for** — ``--focus`` and ``--shadow-hover``. The
   framework's ``Theme`` has ``shadow`` but no hover elevation, and no notion
   of a focus ring at all.
2. **Corrections.** A theme is free to choose any palette, but the portal has
   guarantees a theme may not break: danger must never look like the brand,
   and a warning must be readable against the theme's own background. Where a
   framework value would break one, the correction lives here with its reason.

Everything else is derived from the framework and must NOT be listed here.
``sync_themes`` fails if an entry names a theme or token that no longer exists,
so this file cannot rot silently.
"""
from __future__ import annotations

# Per theme, the tokens the portal overrides or supplies, each with the reason
# it is not simply taken from the framework.
OVERLAY: dict[str, dict[str, str]] = {
    "trellum light": {
        # accent_yellow #f59e0b is too light for text on #ffffff cards.
        "--warn": "#d97706",
        "--focus": "#2563eb",
        "--shadow-hover": "0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04)",
    },
    "trellum dark": {
        "--warn": "#fbbf24",  # accent_yellow #FFD166 skews orange next to --red.
        "--focus": "#4C8BF5",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.45), 0 2px 6px rgba(0,0,0,0.3)",
    },
    "light": {
        # The classic palette: accent_red #ef4444 is the same red family as
        # bg_header #E84855 — danger must stand apart from the brand.
        "--red": "#d92d20",
        # accent_yellow #f59e0b is too light for text on #ffffff cards.
        "--warn": "#d97706",
        "--focus": "#3b82f6",
        "--shadow-hover": "0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04)",
    },
    "dark": {
        "--warn": "#fbbf24",  # accent_yellow #FFD166 skews orange next to --red.
        "--focus": "#4C8BF5",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.45), 0 2px 6px rgba(0,0,0,0.3)",
    },
    "money": {
        "--warn": "#d4a017",  # accent_yellow #f1c40f is indistinguishable from the gold accent.
        "--focus": "#1abc9c",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.35)",
    },
    "blossom": {
        # accent_red #e8668b is the same pink as bg_header #d4638f.
        "--red": "#c0392b",
        "--warn": "#c9822f",  # accent_yellow #e8b86d is too pale on the light ground.
        "--focus": "#5b8def",  # the theme's own --blue is a muted purple, too close to the accent.
        "--shadow-hover": "0 4px 14px rgba(150,100,180,0.16), 0 2px 4px rgba(150,100,180,0.08)",
    },
    "midnight": {
        "--warn": "#f59e0b",  # accent_yellow #fbbf24 is too close to the theme's --green glow.
        "--focus": "#60a5fa",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.4)",
    },
    "sunset": {
        # accent_red #e85d4a is the same orange-red family as bg_header #e06c3a.
        "--red": "#e0405a",
        "--warn": "#f5a623",  # accent_yellow #f5b942 sits between the accent and --red.
        "--focus": "#6aafd2",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.35)",
    },
    "nord": {
        "--focus": "#88c0d0",  # nord's frost cyan; --blue is too near the accent.
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.45), 0 2px 6px rgba(0,0,0,0.3)",
    },
    "dracula": {
        # bg_header #bd93f9 is bright enough that white text on it fails contrast.
        "--on-accent": "#201f33",
        "--focus": "#8be9fd",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.35)",
    },
    "solarized": {
        "--focus": "#2aa198",  # solarized cyan; --blue IS the accent in this theme.
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.35)",
    },
    "ocean": {
        "--focus": "#38bdf8",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.4)",
    },
    "monokai": {
        # bg_header #a6e22e is a bright green; white text on it is unreadable.
        "--on-accent": "#14150f",
        "--focus": "#66d9ef",
        "--shadow-hover": "0 6px 18px rgba(0,0,0,0.5), 0 2px 6px rgba(0,0,0,0.35)",
    },
}

# Tokens the overlay is allowed to set. Anything else means the framework
# should have been the source, or a new token needs adding to the contract.
ALLOWED_TOKENS = frozenset(
    {"--red", "--green", "--blue", "--warn", "--focus", "--on-accent", "--shadow-hover"}
)

# A theme with no field for "which mode is this" (the framework's Theme
# dataclass has none) is classified from its background's relative luminance
# at generation time (apps.core.themes.theme_mode). This map exists so a
# future ambiguous theme (a mid-grey background, say) is a declared fact
# instead of a guess -- empty today because all 13 themes classify cleanly.
MODE_OVERRIDE: dict[str, str] = {}

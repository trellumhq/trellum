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

# The management-surface ink corrections that a mechanical ramp toward the
# theme's own --text (apps.core.themes.ink) gets technically right but
# aesthetically wrong -- the "add colors for some themes" case the owner
# asked for. Every other theme, and every other token of these four, is
# ramped mechanically at generation time; nothing here duplicates that.
#
# Method: hold the source color's hue and saturation and move only its HSL
# lightness until it clears the same 4.5:1 floor the mechanical ramp targets
# (apps.core.tests.test_theme_sync verifies these values still pass). Mixing
# straight toward a theme's near-white/near-black --text (what the mechanical
# ramp does) desaturates a vivid accent into grey or pink; holding hue/
# saturation keeps the theme's own identity instead.
MGMT_OVERLAY: dict[str, dict[str, str]] = {
    "blossom": {
        # The mechanical ramp lands text2/text3 within a few percent of each
        # other (both drift toward the same neutral-plum blend), flattening
        # the hierarchy between them; HSL-lightened, hue held, to keep them
        # distinguishable and keep blossom's own dusty-plum character.
        "--text2": "#816b8d",
        "--text3": "#85689b",
        # The report green (#5cba9f, already a muted teal) turns muddy
        # grey-green under the mechanical ramp; HSL-lightened instead so it
        # still reads as green next to the red/warn dots beside it. Held a
        # touch darker than a bare 4.5 floor would allow: it also has to
        # clear 4.5:1 against its own 12% badge tint (validate_mgmt's
        # composite check), which is the tighter constraint here.
        "--green": "#317360",
    },
    "nord": {
        # Nord's palette is deliberately desaturated already; mixing further
        # toward near-white text turns the frost/aurora hues into flat grey.
        # HSL-lightened (hue/saturation held) keeps them recognizably "nord".
        "--text3": "#a6aebf",
        # Mixing aurora red toward white washes it into dusty rose -- still
        # technically 4.5:1, but no longer reads as danger at a glance.
        # Lightened enough to also clear the 12%-badge-tint composite check.
        "--red": "#e3babe",
        "--accent-ink": "#9ab0cb",
    },
    "dracula": {
        # #6272a4 is dracula's own "comment" tone -- correct at a glance, but
        # under ~9px syntax highlighting, not at 13px table text. HSL-
        # lightened rather than mixed toward white so it stays in the same
        # blue-slate family editors recognize as dracula's muted color.
        "--text3": "#aeb6d0",
        # Mixing dracula's red toward white washes it into pale pink; HSL-
        # lightened (clearing the 12%-badge-tint composite check too) to
        # stay legibly red.
        "--red": "#ffbdbd",
    },
    "monokai": {
        # #75715e is monokai's own olive "comment" tone, unusable at 13px;
        # HSL-lightened to keep the olive hue instead of greying out.
        "--text3": "#aba796",
        # The signature monokai magenta (#f92672) washes into pale pink
        # under the mechanical ramp; HSL-lightened (clearing the
        # 12%-badge-tint composite check too) to stay a clear, saturated
        # pink-red instead.
        "--red": "#fca0c1",
    },
}

# Tokens MGMT_OVERLAY may set. Everything else in the management block comes
# from the theme (pass-through) or the mechanical ink ramp -- see
# apps.core.themes.build_mgmt_tokens.
MGMT_ALLOWED_TOKENS = frozenset(
    {"--text2", "--text3", "--green", "--red", "--blue", "--warn", "--accent-ink",
     "--scope-org", "--scope-studio"}
)

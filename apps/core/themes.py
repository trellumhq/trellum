"""Build the portal's CSS theme table from the framework's theme registry.

The framework owns the themes (``trellum.themes.THEME_REGISTRY``, a stable
public API — see ``trellum/docs/PORTAL_SPLIT.md``) so that a project using
the framework alone can select one. The portal renders the same names as CSS
custom properties for its dashboard chrome.

The two vocabularies differ: the framework's ``Theme`` names fields for report
content (``bg_main``, ``text_main``, ``accent_red``), the portal's tokens name
surfaces (``--bg``, ``--text``, ``--red``). ``FIELD_MAP`` is that translation.
Tokens the framework has no field for, and corrections the portal insists on,
come from ``apps.core.theme_overlay``.

``manage.py sync_themes`` writes the result to ``static/themes.generated.css``.
A test regenerates and compares, so the checked-in file cannot go stale.
"""
from __future__ import annotations

from apps.core.theme_overlay import (
    ALLOWED_TOKENS,
    MGMT_ALLOWED_TOKENS,
    MGMT_OVERLAY,
    MODE_OVERRIDE,
    OVERLAY,
)

GENERATED_PATH = "static/themes.generated.css"

# Portal token -> framework Theme field.
FIELD_MAP: dict[str, str] = {
    "--bg": "bg_main",
    "--bg-card": "bg_card",
    "--bg-hover": "bg_card_hover",
    "--text": "text_main",
    "--text2": "text_secondary",
    "--text3": "text_muted",
    "--accent": "bg_header",
    "--green": "accent_green",
    "--red": "accent_red",
    "--blue": "accent_blue",
    "--warn": "accent_yellow",
    "--border": "border_color",
    "--shadow": "shadow",
}

# Emission order — matches how the blocks read in the old hand-written table.
TOKEN_ORDER = [
    "--bg", "--bg-card", "--bg-hover",
    "--text", "--text2", "--text3",
    "--accent", "--accent-soft", "--on-accent",
    "--green", "--red", "--blue", "--warn", "--focus",
    "--border", "--shadow", "--shadow-hover",
]

# The management-surface overlay adds one token (--accent-ink: accent-as-text,
# split from --accent so fills and links can be corrected independently) plus
# the four brand scope tokens (--scope-org/--scope-studio and their soft
# tints), which every studio theme now carries so the settings rail and
# scope banner stay legible under it. See build_mgmt_tokens / docs/design-
# system.md "Theme scoping".
MGMT_TOKEN_ORDER = [
    "--bg", "--bg-card", "--bg-hover",
    "--text", "--text2", "--text3",
    "--accent", "--accent-soft", "--accent-ink", "--on-accent",
    "--green", "--red", "--blue", "--warn", "--focus",
    "--border", "--shadow", "--shadow-hover",
    "--scope-org", "--scope-org-soft", "--scope-studio", "--scope-studio-soft",
]

# Brand scope colors are constant across every theme (docs/design-system.md
# "the lattice glyph and the org/studio chips must look identical ... on
# every theme"); only the mode (which of the pair) and the ink correction (to
# clear 4.5:1 on that theme's own card) vary. Matches ui.css's own
# light/dark body.mgmt refinements (ui.css:31-32,43-44) exactly.
SCOPE_BRAND = {
    "light": {"--scope-org": "#0D9488", "--scope-studio": "#7C6FE0"},
    "dark": {"--scope-org": "#2DD4BF", "--scope-studio": "#9D8FF2"},
}

MGMT_HEADER = """
/* ============================================================================
   Per-viewer studio theming — the management-surface overlay.

   For each theme above, the block below corrects the same palette for the
   dense 13px management surface: `--text`/`--border`/`--shadow`/`--accent`/
   `--on-accent`/`--focus` pass through unchanged (already checked, see
   validate()); `--text2`/`--text3`/the semantic colors/the new `--accent-ink`
   are ink-ramped toward the theme's own --text until they clear the floors
   in validate_mgmt() below; `--scope-org`/`--scope-studio` pick the brand
   ramp for the theme's mode and are ink-ramped the same way. A handful of
   tokens on the most saturated themes are hand-tuned in
   apps/core/theme_overlay.py MGMT_OVERLAY instead of mechanically ramped —
   see that file for which and why.

   Selected on a SECOND attribute, data-studio-theme, not data-theme: two
   registry themes are literally named "light"/"dark", the same strings the
   management surface's own mode boot writes into data-theme, so reusing it
   here would collide. Portal Neutral (ui.css) is demoted with :where() so
   this always wins when both are present. See docs/design-system.md.
   ========================================================================= */
"""

HEADER = """/* ============================================================================
   GENERATED FILE — DO NOT EDIT.

   Written by `manage.py sync_themes` from trellum.themes.THEME_REGISTRY
   plus apps/core/theme_overlay.py. Edit those, then regenerate. A test
   (apps/core/tests/test_theme_sync.py) fails if this file is stale.

   The framework owns the themes so a project using the framework alone can
   pick one; the portal renders them for its dashboard chrome. The management
   surface does NOT use this table — it has its own neutral palette in ui.css
   and stays visually stable when a theme changes.
   ========================================================================= */
"""


class ThemeContractError(Exception):
    """A generated theme would break a guarantee the portal makes."""


def _rgb(value: str) -> tuple[int, int, int]:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


def _relative_luminance(value: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _mix(a: str, b: str, t: float) -> str:
    """Linear per-channel sRGB interpolation from `a` (t=0) to `b` (t=1)."""
    ar, ag, ab = _rgb(a)
    br, bg, bb = _rgb(b)
    return "#{:02x}{:02x}{:02x}".format(
        round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t)
    )


def theme_mode(name: str, tokens: dict[str, str]) -> str:
    """"light" or "dark" — which brand scope ramp and --accent-soft alpha a
    theme uses. The framework's Theme dataclass has no mode field, so this is
    classified from the background's own relative luminance; MODE_OVERRIDE
    exists so a future ambiguous background is a declared fact, not a guess.
    """
    if name in MODE_OVERRIDE:
        return MODE_OVERRIDE[name]
    return "light" if _relative_luminance(tokens["--bg"]) > 0.5 else "dark"


def theme_mode_for(name: str) -> str:
    """"light" or "dark" for a registered theme name, without the caller
    having to build its full token set first.

    Used by the management-surface boot script (``_theme_boot_mgmt.html``)
    to pick its pre-paint default: org/product surfaces default to the
    organization's resolved default theme's mode (dark, since an unset
    ``Organization.default_theme`` resolves to "trellum dark") rather than
    the OS preference, while the existing light/dark/auto viewer preference
    still overrides it. An unknown name (a theme deleted from the registry
    after an org picked it) falls back to "dark" — the same direction an
    empty ``default_theme`` resolves to — rather than raising mid-render.
    """
    from trellum.themes import THEME_REGISTRY

    theme = THEME_REGISTRY.get(name)
    if theme is None:
        return "dark"
    return theme_mode(name, build_tokens(name, theme))


def has_custom_repo_theme(studio) -> bool:
    """True when ``Studio.repo_theme`` names a theme NOT in
    ``trellum.themes.THEME_REGISTRY`` -- a repo-custom theme
    (``trellum.themes.register_theme``) the portal's own generated
    stylesheet (``static/themes.generated.css``, built from the
    FRAMEWORK's registry at `sync_themes` time) has no CSS for, because that
    registration only happens transiently inside the repo's own report-build
    process.

    Used by :func:`apps.reports.views._inject_report_chrome` to decide
    whether the served report's BAKED ``data-theme`` may be safely
    overwritten with the resolved chrome theme: for a registry name it may
    (both surfaces render the same look); for a custom name it must not --
    the report was built with real CSS for it, the chrome (falling back to
    Trellum per :func:`explicit_studio_theme` rung 0 below) was not.
    """
    from trellum.themes import THEME_REGISTRY

    return bool(studio.repo_theme) and studio.repo_theme not in THEME_REGISTRY


def explicit_studio_theme(user, studio) -> str:
    """Rungs 0-2 of the studio-theme resolution chain, WITHOUT the bare
    "trellum dark" fallback — "" means nothing anywhere (repo, viewer
    override, studio) explicitly names a theme for this studio.

    This is the piece :func:`resolve_studio_theme` builds on, split out
    because chrome and report content need different answers to "nothing
    was picked": report content always needs a concrete ``data-theme`` to
    paint with (:func:`resolve_studio_theme`), but the management surface's
    ``data-studio-theme`` attribute must stay ABSENT in that case so the
    surface renders as plain Trellum, mode-governed by the separate
    light/dark/auto preference (``ui-redesign-proposals.html`` §1: "any
    surface resolved to 'Trellum'... the mode row never applies to it" is
    about the *bare, nothing-picked* case — an explicit pick of "trellum
    dark" itself is a pin like any other and does carry
    ``data-studio-theme``, same as the picker offering it as a normal
    option). Both ``apps.core.context_processors.shell`` (chrome) and
    ``apps.reports.views._inject_report_chrome`` (report content) call this
    to decide whether to emit ``data-studio-theme`` at all.

    Chain, first rung that resolves wins:

      0. ``Studio.repo_theme`` — the studio's own repository declares a
         theme in its project-root ``config.yaml`` (``theme:``), synced by
         ``apps.reports.scan.sync_studio_registry``. When set, EVERY other
         rung is moot, whatever a viewer or the studio admin has picked —
         only the repo's own file controls it now. A registry name resolves
         to itself here (rung 0 IS the chrome's answer). A CUSTOM name (not
         in ``THEME_REGISTRY`` — a theme the repo registers itself,
         :func:`has_custom_repo_theme`) answers "" here — the same "nothing
         resolvable for chrome" signal as if nothing were picked at all,
         which is exactly right: the portal's stylesheet has no CSS for it,
         so the chrome falls through to Trellum. Report CONTENT, which
         built its own CSS for that custom name, is handled separately —
         see :func:`apps.reports.views._inject_report_chrome`.
      1. ``StudioMembership.theme`` for (user, studio) — the viewer's
         personal per-studio override — unless ``studio.org.lock_studio_theme``
         is on, in which case overrides are ignored entirely (even a value
         already stored from before the lock was turned on), or the stored
         value no longer names a registered theme.
      2. ``Studio.theme`` — the studio's own default, if it still names a
         registered theme.

    There used to be a further rung, ``Organization.default_theme`` — retired
    (``.lavish/theming-controls-design.html`` decision ②) once the org
    admin's own default-palette control moved off the account page and no UI
    anywhere wrote it any more, orphaning it as a rung nothing could aim at.
    The column stays on :class:`apps.orgs.models.Organization`, unused, same
    "keep the field, skip the migration" rule as ``User.theme`` below.

    Every rung is guarded against a name that no longer exists in the
    registry (a theme a project used to register and stopped), so a stale
    stored value falls through to the next rung instead of the caller
    rendering an unmatched selector. ``user`` may be ``None`` or
    unauthenticated (public share links) — rung 1 is simply skipped, same
    as a viewer with no override on file.
    """
    from trellum.themes import THEME_REGISTRY

    if studio.repo_theme:
        return studio.repo_theme if studio.repo_theme in THEME_REGISTRY else ""

    org = studio.org

    if user is not None and getattr(user, "is_authenticated", False) and not org.lock_studio_theme:
        from apps.studios.models import StudioMembership

        override = (
            StudioMembership.objects.filter(user=user, studio=studio)
            .values_list("theme", flat=True)
            .first()
        )
        if override and override in THEME_REGISTRY:
            return override

    if studio.theme and studio.theme in THEME_REGISTRY:
        return studio.theme

    return ""


def resolve_studio_theme(user, studio) -> str:
    """The single source of truth for "what theme does the CHROME render in
    this studio" — a ``trellum.themes.THEME_REGISTRY`` key, never empty.

    Consulted by both consumers that render the management chrome and the
    served report content identically -- with exactly one deliberate
    carve-out: when ``Studio.repo_theme`` names a CUSTOM (non-registry)
    theme, this resolves to Trellum for the chrome (the portal has no CSS
    for it), but ``apps.reports.views._inject_report_chrome`` leaves the
    report's own BAKED ``data-theme`` alone instead of overwriting it with
    this value, because the report's build-time CSS for that custom name is
    real and this function's answer is not (see
    :func:`has_custom_repo_theme`). Every other case, both surfaces agree.

    ``explicit_studio_theme``'s rungs 0-2, falling back to
    ``trellum.themes.DEFAULT_THEME_NAME`` ("trellum dark") when nothing
    anywhere names one — see that function's docstring for the full chain
    and for why chrome additionally consults it directly (to know whether
    to emit ``data-studio-theme`` at all).
    """
    from trellum.themes import DEFAULT_THEME_NAME

    return explicit_studio_theme(user, studio) or DEFAULT_THEME_NAME


def viewer_theme_override(user, studio) -> str:
    """The raw ``StudioMembership.theme`` stored for (user, studio), or ""
    -- unlike :func:`resolve_studio_theme`, this does NOT walk the
    studio/org fallback chain and does NOT apply the org lock. It exists
    for UI reflection only: the studio header's picker must show what this
    viewer actually chose (so "Studio default" stays selected when they
    have not overridden anything), not the resolved value every OTHER
    viewer might currently be seeing via the studio/org default.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    from apps.studios.models import StudioMembership

    return (
        StudioMembership.objects.filter(user=user, studio=studio)
        .values_list("theme", flat=True)
        .first()
        or ""
    )


def ink(color: str, ground: str, pole: str, floor: float, *, tint: bool = False, steps: int = 300) -> str:
    """The smallest mix of `color` toward `pole` that clears `floor` contrast
    against `ground`.

    `pole` is normally the theme's own --text: mixing toward the theme's ink
    (not plain black/white) keeps the correction inside the theme's
    temperature. When `tint=True`, also requires the same floor against
    `ground` tinted 12% with the candidate itself -- the badge/chip recipe
    (`color: var(--x); background: color-mix(var(--x) 12%, transparent)`)
    composites the tint over the card, which can be slightly harder to read
    than the flat color alone.
    """

    def passes(candidate: str) -> bool:
        if _contrast(candidate, ground) < floor:
            return False
        if tint and _contrast(candidate, _mix(ground, candidate, 0.12)) < floor:
            return False
        return True

    if passes(color):
        return color
    for i in range(1, steps + 1):
        candidate = _mix(color, pole, i / steps)
        if passes(candidate):
            return candidate
    # Every theme's own --text clears every floor here with headroom (the
    # weakest, dracula, is 8.59:1 vs a 4.5 floor) -- this line is defensive,
    # never actually the return path for any of the 13 registered themes.
    return pole


def build_tokens(name: str, theme) -> dict[str, str]:
    """The full token set for one theme: framework values + overlay + derived."""
    tokens = {token: getattr(theme, field) for token, field in FIELD_MAP.items()}

    accent = tokens["--accent"]
    r, g, b = _rgb(accent)
    # Every theme's accent-soft is the accent at 8% — derived, never hand-set.
    tokens["--accent-soft"] = f"rgba({r},{g},{b},0.08)"
    # White on the accent unless the accent is bright enough that it fails;
    # a theme with a bright accent supplies its own tint in the overlay.
    tokens["--on-accent"] = "#ffffff" if _relative_luminance(accent) < 0.4 else "#1a1a1a"

    overlay = OVERLAY.get(name, {})
    unknown = set(overlay) - ALLOWED_TOKENS
    if unknown:
        raise ThemeContractError(
            f"theme '{name}': overlay sets {sorted(unknown)}, which the framework owns"
        )
    tokens.update(overlay)

    missing = [t for t in TOKEN_ORDER if t not in tokens]
    if missing:
        raise ThemeContractError(f"theme '{name}' is missing {missing}")
    return tokens


def validate(name: str, tokens: dict[str, str]) -> None:
    """Guarantees a theme may not break, whatever the framework does."""
    # Danger must never be mistakable for the brand. This is the collision the
    # UI pass removed; enforcing it here stops a framework bump reviving it.
    distance = _distance(tokens["--accent"], tokens["--red"])
    if distance <= 40:
        raise ThemeContractError(
            f"theme '{name}': --accent {tokens['--accent']} and --red {tokens['--red']} "
            f"are too close (distance {distance:.0f}). Correct --red in "
            f"apps/core/theme_overlay.py."
        )
    # Text on the accent must be readable — primary buttons and the active nav
    # depend on it.
    ratio = _contrast(tokens["--accent"], tokens["--on-accent"])
    if ratio < 3.0:
        raise ThemeContractError(
            f"theme '{name}': --on-accent {tokens['--on-accent']} on --accent "
            f"{tokens['--accent']} is {ratio:.1f}:1. Set --on-accent in the overlay."
        )
    # A dark theme that reuses the light hover shadow loses elevation on hover.
    _LIGHT_GROUND = ("trellum light", "light", "blossom")
    if "rgba(0,0,0,0.08)" in tokens["--shadow-hover"] and name not in _LIGHT_GROUND:
        raise ThemeContractError(f"theme '{name}' still has the light hover shadow")


def build_mgmt_tokens(name: str, tokens: dict[str, str]) -> dict[str, str]:
    """The management-surface overlay for one theme: `tokens` (the report
    palette) passed through where already checked, ink-ramped where they were
    tuned for report chrome only. See MGMT_TOKEN_ORDER and
    docs/design-system.md "Theme scoping"."""
    mode = theme_mode(name, tokens)
    ground = tokens["--bg-card"]
    pole = tokens["--text"]
    accent_r, accent_g, accent_b = _rgb(tokens["--accent"])
    accent_soft_alpha = 0.10 if mode == "light" else 0.12

    overlay = MGMT_OVERLAY.get(name, {})
    unknown = set(overlay) - MGMT_ALLOWED_TOKENS
    if unknown:
        raise ThemeContractError(
            f"theme '{name}': MGMT_OVERLAY sets {sorted(unknown)}, outside the mgmt contract"
        )

    mgmt: dict[str, str] = {
        # Pass-through: already checked by validate() against the report
        # surface, and (per the §5 audit) every theme's own --text/--focus
        # already clear the mgmt floors too.
        "--bg": tokens["--bg"],
        "--bg-card": tokens["--bg-card"],
        "--bg-hover": tokens["--bg-hover"],
        "--border": tokens["--border"],
        "--shadow": tokens["--shadow"],
        "--shadow-hover": tokens["--shadow-hover"],
        "--accent": tokens["--accent"],
        "--on-accent": tokens["--on-accent"],
        "--focus": tokens["--focus"],
        "--text": tokens["--text"],
        # Re-derived for the denser mgmt surface (10%/12% by mode, matching
        # ui.css's own body.mgmt refinements) rather than the report's flat 8%.
        "--accent-soft": f"rgba({accent_r},{accent_g},{accent_b},{accent_soft_alpha:.2f})",
    }

    for tok in ("--text2", "--text3"):
        mgmt[tok] = overlay.get(tok) or ink(tokens[tok], ground, pole, 4.5)
    for tok in ("--green", "--red", "--blue", "--warn"):
        mgmt[tok] = overlay.get(tok) or ink(tokens[tok], ground, pole, 4.5, tint=True)
    # Split from --accent (a fill) so foreground uses (links, active-tab text,
    # chip text) can be corrected without dulling the brand fill/button pair.
    mgmt["--accent-ink"] = overlay.get("--accent-ink") or ink(tokens["--accent"], ground, pole, 4.5)

    brand = SCOPE_BRAND[mode]
    for base, soft in (("--scope-org", "--scope-org-soft"), ("--scope-studio", "--scope-studio-soft")):
        inked = overlay.get(base) or ink(brand[base], ground, pole, 4.5)
        mgmt[base] = inked
        sr, sg, sb = _rgb(inked)
        mgmt[soft] = f"rgba({sr},{sg},{sb},{accent_soft_alpha:.2f})"

    missing = [t for t in MGMT_TOKEN_ORDER if t not in mgmt]
    if missing:
        raise ThemeContractError(f"theme '{name}' mgmt block is missing {missing}")
    return mgmt


def validate_mgmt(name: str, mgmt: dict[str, str]) -> None:
    """The management-surface contrast guarantees (docs/design-system.md
    §5) -- CI fails if any theme's mgmt block ever breaks a floor, whatever
    the framework registry does. Checked against the mgmt block's own
    --bg-card, the ground for tables, panels and forms."""
    ground = mgmt["--bg-card"]

    text_ratio = _contrast(mgmt["--text"], ground)
    if text_ratio < 7.0:
        raise ThemeContractError(
            f"theme '{name}': mgmt --text {mgmt['--text']} is {text_ratio:.2f}:1 on "
            f"--bg-card {ground} (floor 7.0)"
        )

    for tok in ("--text2", "--text3", "--accent-ink", "--scope-org", "--scope-studio"):
        ratio = _contrast(mgmt[tok], ground)
        if ratio < 4.5:
            raise ThemeContractError(
                f"theme '{name}': mgmt {tok} {mgmt[tok]} is {ratio:.2f}:1 on --bg-card "
                f"{ground} (floor 4.5). Add a correction in theme_overlay.py MGMT_OVERLAY."
            )

    for tok in ("--green", "--red", "--blue", "--warn"):
        ratio = _contrast(mgmt[tok], ground)
        if ratio < 4.5:
            raise ThemeContractError(
                f"theme '{name}': mgmt {tok} {mgmt[tok]} is {ratio:.2f}:1 on --bg-card "
                f"{ground} (floor 4.5). Add a correction in theme_overlay.py MGMT_OVERLAY."
            )
        tint_ground = _mix(ground, mgmt[tok], 0.12)
        tint_ratio = _contrast(mgmt[tok], tint_ground)
        if tint_ratio < 4.5:
            raise ThemeContractError(
                f"theme '{name}': mgmt {tok} {mgmt[tok]} is {tint_ratio:.2f}:1 against its "
                f"own 12% badge tint on --bg-card (floor 4.5)."
            )

    focus_ratio = _contrast(mgmt["--focus"], ground)
    if focus_ratio < 3.0:
        raise ThemeContractError(
            f"theme '{name}': mgmt --focus {mgmt['--focus']} is {focus_ratio:.2f}:1 on "
            f"--bg-card {ground} (floor 3.0)."
        )

    on_accent_ratio = _contrast(mgmt["--on-accent"], mgmt["--accent"])
    if on_accent_ratio < 3.0:
        raise ThemeContractError(
            f"theme '{name}': mgmt --on-accent {mgmt['--on-accent']} on --accent "
            f"{mgmt['--accent']} is {on_accent_ratio:.2f}:1 (floor 3.0)."
        )

    # Danger must never look like the brand, re-checked with the mgmt --red
    # (which may itself have moved under the ink ramp or an overlay).
    distance = _distance(mgmt["--accent"], mgmt["--red"])
    if distance <= 40:
        raise ThemeContractError(
            f"theme '{name}': mgmt --accent {mgmt['--accent']} and --red {mgmt['--red']} "
            f"are too close (distance {distance:.0f}) after correction."
        )


def render(registry) -> str:
    """The complete generated stylesheet."""
    unknown_themes = set(OVERLAY) - set(registry)
    if unknown_themes:
        raise ThemeContractError(
            f"theme_overlay.py has entries for themes that no longer exist: "
            f"{sorted(unknown_themes)}"
        )
    unknown_mgmt_themes = set(MGMT_OVERLAY) - set(registry)
    if unknown_mgmt_themes:
        raise ThemeContractError(
            f"theme_overlay.py MGMT_OVERLAY has entries for themes that no longer exist: "
            f"{sorted(unknown_mgmt_themes)}"
        )

    parts = [HEADER]
    mgmt_parts = [MGMT_HEADER]
    for name, theme in registry.items():
        tokens = build_tokens(name, theme)
        validate(name, tokens)
        lines = "\n".join(f"    {t}: {tokens[t]};" for t in TOKEN_ORDER)
        parts.append(f'[data-theme="{name}"] {{\n{lines}\n}}\n')

        mgmt = build_mgmt_tokens(name, tokens)
        validate_mgmt(name, mgmt)
        mgmt_lines = "\n".join(f"    {t}: {mgmt[t]};" for t in MGMT_TOKEN_ORDER)
        mgmt_parts.append(f'html[data-studio-theme="{name}"] body.mgmt {{\n{mgmt_lines}\n}}\n')

    return "\n".join(parts + mgmt_parts)

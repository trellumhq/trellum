from __future__ import annotations

from trellum.themes.blossom import BlossomTheme
from trellum.themes.classic import ClassicDarkTheme, ClassicLightTheme
from trellum.themes.dark import DarkTheme
from trellum.themes.default import DefaultTheme
from trellum.themes.dracula import DraculaTheme
from trellum.themes.midnight import MidnightTheme
from trellum.themes.money import MoneyTheme
from trellum.themes.monokai import MonokaiTheme
from trellum.themes.nord import NordTheme
from trellum.themes.ocean import OceanTheme
from trellum.themes.solarized import SolarizedTheme
from trellum.themes.sunset import SunsetTheme
from trellum.themes.theme import Theme

THEME_REGISTRY: dict[str, Theme] = {
    # The trellum pair is the default look. Dark is the default of the two --
    # see DEFAULT_THEME_NAME.
    "trellum light": DefaultTheme,
    "trellum dark": DarkTheme,
    # The pre-trellum defaults, kept so an already-chosen look survives.
    "light": ClassicLightTheme,
    "dark": ClassicDarkTheme,
    "money": MoneyTheme,
    "blossom": BlossomTheme,
    "midnight": MidnightTheme,
    "sunset": SunsetTheme,
    "nord": NordTheme,
    "dracula": DraculaTheme,
    "solarized": SolarizedTheme,
    "ocean": OceanTheme,
    "monokai": MonokaiTheme,
}


# The look a report gets when it does not ask for one. Kept as a single named
# constant because three separate places used to decide this independently --
# resolve_theme(), BaseReport, and the HTML builder's data-theme attribute --
# and nothing stopped them drifting apart.
DEFAULT_THEME_NAME = "trellum dark"
DEFAULT_THEME: Theme = THEME_REGISTRY[DEFAULT_THEME_NAME]


def register_theme(name: str, theme: Theme) -> None:
    """Register a custom theme by name.

    Projects can call this to add themes without modifying framework code.
    """
    THEME_REGISTRY[name] = theme


def effective_theme_name(name: str | None) -> str:
    """The theme NAME a build should use when nothing more specific is given.

    An explicit ``name`` (typically a report's own ``report.yaml`` ``theme:``)
    always wins and is returned unchanged. Otherwise the project's own
    ``config.yaml`` ``theme:`` -- so a project (or a portal-managed studio
    whose repo declares one) sets its default palette ONCE and every report
    in it picks that up without repeating ``theme:`` per report.yaml -- and
    finally :data:`DEFAULT_THEME_NAME` if neither says anything.

    Returns the bare name, unvalidated -- callers look it up in
    :data:`THEME_REGISTRY` themselves, same as any other name (an unknown
    project-config name is just as recoverable as an unknown report.yaml
    one).
    """
    if name:
        return name
    from trellum.project import load_project_config

    project_name = load_project_config().get("theme")
    return project_name or DEFAULT_THEME_NAME


def resolve_theme(name: str | None) -> Theme:
    """Look up a theme by name, falling back to the project's own
    ``config.yaml`` ``theme:`` (:func:`effective_theme_name`) and then
    :data:`DEFAULT_THEME`.

    Raises ``ValueError`` only for an EXPLICIT unknown ``name`` -- an
    unknown project-config default instead falls back to
    :data:`DEFAULT_THEME` quietly, the same way an unknown report.yaml
    value already degrades at the call site in
    ``trellum.runner.execute.run_report`` (a single project-level typo must
    not fail every report in the project).
    """
    resolved = effective_theme_name(name)
    theme = THEME_REGISTRY.get(resolved)
    if theme is not None:
        return theme
    if name is None:
        return DEFAULT_THEME
    raise ValueError(
        f"Unknown theme '{name}'. Available: {list(THEME_REGISTRY)}"
    )


__all__ = [
    "Theme", "DefaultTheme", "DarkTheme",
    "ClassicLightTheme", "ClassicDarkTheme",
    "MoneyTheme", "BlossomTheme",
    "MidnightTheme", "SunsetTheme", "NordTheme", "DraculaTheme",
    "SolarizedTheme", "OceanTheme", "MonokaiTheme",
    "THEME_REGISTRY", "register_theme", "resolve_theme", "effective_theme_name",
    "DEFAULT_THEME", "DEFAULT_THEME_NAME",
]

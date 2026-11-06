# Report status text

`Theme` separates small status text from decorative accents and chart colors.
Rebuilt reports use these roles on native components:

| Keyword-only Theme field | CSS variable | Consumers | Legacy custom-theme default |
| --- | --- | --- | --- |
| `text_positive` | `--text-positive` | KpiCard `up`; favorable ComparisonTable auto colors | `var(--accent-green)` |
| `text_negative` | `--text-negative` | KpiCard `down`; unfavorable ComparisonTable auto colors | `var(--accent-red)` |
| `text_warning` | `--text-warning` | KpiCard supplied forecast | `var(--accent-yellow)` |
| `text_context` | `--text-context` | KpiCard subtitle and no-direction delta; table notes and comparison outcome/reason text | `var(--text-muted)` |

All thirteen built-in themes explicitly author these text colors. The default
remains `trellum dark`. Existing accents, chart palettes, primary/secondary/muted
text tokens, backgrounds, font sizes, card heights, row arrangement and table
geometry are unchanged. No automatic contrast adjustment runs in the browser.

```python
from trellum.themes import Theme, register_theme

register_theme("studio", Theme(
    bg_main="#111315", bg_card="#181b1f", bg_card_hover="#23272d",
    text_main="#f3f4f6", text_muted="#a7adb8",
    text_positive="#72e0b2", text_negative="#ffacac",
    text_warning="#fce192", text_context="#c6cbd3",
))
```

New fields are keyword-only; existing positional constructors and dataclass
subclasses retain their argument positions. `Theme()` and custom themes that
omit roles use CSS aliases to their own legacy accent/muted tokens. Explicit
role values, including authored CSS variable references, take precedence and
serialize unchanged through `to_dict()` and `to_css_vars()`. Consumers also
include nested legacy fallbacks for hosts whose CSS lacks the new properties.
Custom authored colors remain the author's responsibility.

KpiCard continues to display supplied delta strings and direction. `up` means
the author requested favorable styling; a cost decrease can therefore use `up`.
No sign is parsed to choose direction. No-direction or unknown change remains
without favorable/unfavorable meaning. A supplied forecast gets its own tone;
it does not become an observed value or an inferred prediction. A plain authored
em dash is display text, not a missing-metric calculation API.

ComparisonTable retains both modes. Legacy auto rules color positive/negative
supplied values by sign. With `comparison_context`, automatic numeric delta
colors follow the declared metric direction. Outcome words and unavailable
reasons keep neutral context styling. Explicit `conditional_formats` dictionaries
are preserved, including `var(--accent-green)` references and hex colors;
`{}` still disables automatic colors. Values, calculation/formatting semantics,
CSV units, search, sorting, selection and filter behavior are unchanged.

Built-in role colors are measured at an unrounded contrast ratio of at least
4.5:1, with a 4.6:1 test margin, on their consumed normal/hover KPI surfaces,
actual composited 12% delta-pill backgrounds, normal/striped/hover comparison
rows and report-panel notes. This is a scoped small-text measurement. It does
not establish contrast for every theme surface, disabled control or explicit
author override, nor does it establish color-vision accessibility. Existing
outcome words and directions remain necessary alongside color.

`component-showcase/examples_status_text.py` provides deterministic native KPI
and comparison examples, including both improvement directions, worsening,
neutral/unknown change, supplied forecast, unavailable reasons, legacy rules
and explicit color overrides. Its `add_sections(ctx)` uses the report's native
theme picker and a dataset-bound focus filter; the independent cards and legacy
tables explicitly use `static=True` without a dataset id.

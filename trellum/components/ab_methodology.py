"""ABMethodologyNote — collapsible in-report explainer for Winsor / CUPED / CI.

Drop one instance into any A/B test report so readers understand what they're
seeing without leaving the page. **The explanation text lives only here** —
edit this module to update every A/B report in one place.

Usage in a report's ``generate``:

    from trellum.components import ABMethodologyNote
    ctx.add_section("", [ABMethodologyNote()])
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trellum.assets import load_css
from trellum.components.base import Component, RenderContext

_METHODOLOGY_HTML = """\
<div class="fw-ab-method-body">
  <p>This report shows the same A/B test through three lenses. The default \
view (<em>CUPED</em>) is the most trustworthy — the others are sanity checks.</p>

  <h4>The three modes</h4>
  <ul class="fw-ab-method-list">
    <li><strong>Raw</strong> — observed averages. Dominated by whale variance: a \
handful of high-spending users can swing the headline by tens of percent based \
on which variant they happened to land in. Useful as a backstop, not a \
headline.</li>
    <li><strong>Winsor p99</strong> — caps each user's revenue at the 99th \
percentile of the <em>pooled</em> Control + Test population. Same cap on both \
arms (unlike per-variant "Exclude top X%" which biases the comparison). The \
whale is still counted as a payer; their contribution to the average is just \
capped.</li>
    <li><strong>CUPED</strong> (<em>default</em>) — subtracts each user's \
revenue baseline measured in the 14 days <em>before</em> the test started. \
Removes the predictable "this person was always going to spend a lot" \
component, leaving only the part the test could plausibly have caused. \
Typically 30–50% variance reduction on revenue metrics.</li>
  </ul>

  <h4>Reading the deltas</h4>
  <p>Every KPI row shows an observed delta (<code>▲ +5.2%</code>) and a \
<strong>95% confidence interval</strong> (<code>95% CI [-2.1, +12.3]</code>) \
below it. The CI is the range of plausible true effects given the data:</p>

  <table class="fw-ab-method-ci-table">
    <thead><tr><th>CI looks like</th><th>Interpretation</th><th>Bar colour</th></tr></thead>
    <tbody>
      <tr><td><code>[+2, +8]</code> all positive</td>
          <td>Test is reliably <strong>better</strong></td>
          <td><span class="fw-ab-method-swatch up"></span> green</td></tr>
      <tr><td><code>[-9, -1]</code> all negative</td>
          <td>Test is reliably <strong>worse</strong></td>
          <td><span class="fw-ab-method-swatch down"></span> red</td></tr>
      <tr><td><code>[-5, +12]</code> crosses 0</td>
          <td><strong>Can't tell — treat as noise</strong></td>
          <td><span class="fw-ab-method-swatch neutral"></span> grey</td></tr>
    </tbody>
  </table>

  <p>A grey bar means the test <em>might</em> be moving the metric, but we \
don't yet have enough data to say so with confidence. Wide CIs come from \
heavy-tailed revenue + short test windows + small effect sizes — not from a \
bug. To halve a CI typically takes <strong>~4× more data</strong>.</p>

  <h4>Two-stage decomposition</h4>
  <p><strong>Conversion</strong> (payers / DAU) × <strong>ARPPU</strong> \
(revenue / payer) = ARPDAU. Showing them as separate rows makes it obvious \
whether a revenue lift comes from <em>more payers</em>, <em>bigger \
baskets</em>, or both.</p>

  <p style="margin-top: 14px; opacity: 0.8;">\
See the <em>A/B Testing</em> section of the framework README for the full \
technique reference.</p>
</div>"""


@dataclass
class ABMethodologyNote(Component):
    """Collapsible in-report explainer for Winsor / CUPED / CI methodology.

    A single source of truth for the methodology text across all A/B reports.
    Renders as a closed-by-default ``<details>`` block so it doesn't clutter
    the page but is one click away for any reader who wants to understand
    what they're looking at.

    No parameters — drop it in as ``ABMethodologyNote()``.
    """

    _component_type: str = field(default="ab_methodology", init=False, repr=False)

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        ctx.register(cid, {"type": "ab_methodology"})
        return (
            f'<details class="fw-ab-method" id="{cid}">'
            f'<summary class="fw-ab-method-summary">'
            f'<span class="fw-ab-method-icon">ℹ</span> '
            f'How to read this report — Winsor, CUPED, and 95% CI explained'
            f'</summary>'
            f'{_METHODOLOGY_HTML}'
            f'</details>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/ab_methodology.css")

    @classmethod
    def client_js(cls) -> str:
        return ""

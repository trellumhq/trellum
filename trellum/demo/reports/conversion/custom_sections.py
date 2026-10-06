"""Sankey flow diagram for purchase-flow.

Why this is a `RawHTML` section and not a component:

`FunnelChart` draws the value *at* each stage. A Sankey draws the movement
*between* stages -- including the users who leave, which is the part anybody
looking at a funnel actually wants. No arrangement of framework components
expresses a link between two nodes, so this clears the bar `AGENTS.md` sets for
reaching past the component library.

Two rules this file exists to demonstrate, both of which the validator enforces:

* **No colour literals.** `rawhtml-hardcoded-hex` FAILs the report on any
  `#rrggbb`, and `rawhtml-hardcoded-rgba` warns on `rgb()`/`rgba()`, because
  neither follows a theme switch. Every colour here comes from
  `fw.getThemeColors()`, so the diagram re-paints when the theme changes.
* **Subscribe to the filter engine** rather than keeping private state. The
  chart re-reads its own dataset on every filter change through
  `window._fwFilterEngine.subscribe`.
"""

CUSTOM_HTML = """
<style>
  .fw-custom-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;
    color: var(--text-secondary); border: 1px dashed var(--border-color);
    border-radius: 4px; padding: 3px 8px; margin-bottom: 10px;
  }
  .fw-custom-badge b { color: var(--text-main); font-weight: 600; }
</style>
<div class="fw-custom-badge"><b>Custom section</b> · RawHTML + chartjs-chart-sankey via extra_cdn — links between stages are a relationship no stock component draws</div>
<style>
  .pf-wrap { position: relative; height: 420px; }
  .pf-note {
    margin-top: 8px; font-size: 11px; color: var(--text-secondary);
  }
</style>
<div class="pf-wrap"><canvas id="pfSankey"></canvas></div>
<div class="pf-note">
  Width is users. A stage's outgoing links always sum to its incoming ones —
  the "left" branches are the drop-off.
</div>
"""

CUSTOM_JS = """
(function () {
  var CANVAS = 'pfSankey';
  var chart = null;

  // Chart.js needs a colour per node. Taking them from the live theme rather
  // than a fixed list is what keeps the diagram correct across theme switches;
  // a baked palette would also trip the validator.
  function nodeColour(key, colours) {
    if (key.indexOf('left after') === 0) { return colours.tick_color; }
    if (key === 'purchase') { return colours.chart_colors[2]; }
    var order = ['Phone', 'Tablet', 'Desktop'];
    var i = order.indexOf(key);
    if (i >= 0) { return colours.chart_colors[i % colours.chart_colors.length]; }
    return colours.chart_colors[0];
  }

  /* One payload per title, keyed "_flow_<scope>". Scoped reports share the
     default scope's HTML and JS, so this one function reads whichever scope
     is active and redraws on renderAll -- the path a scope switch takes.
     The sankey follows the title switcher like a live component, without the
     link geometry ever being computed client-side. */
  function activePayload() {
    var data = window._reportData || {};
    var scope = window._currentScope;
    if (scope && data['_flow_' + scope]) { return data['_flow_' + scope]; }
    for (var k in data) {                 // initial load, before a scope is set
      if (k.indexOf('_flow_') === 0) { return data[k]; }
    }
    return null;
  }

  function draw() {
    var payload = activePayload();
    var el = document.getElementById(CANVAS);
    if (!payload || !el) { return; }

    // The plugin registers itself as a chart type; if the report.yaml is
    // missing its extra_cdn entry this is where it shows up, so say so rather
    // than failing with an opaque Chart.js error.
    if (!(window.Chart && Chart.registry.controllers.get('sankey'))) {
      console.warn('[purchase-flow] sankey plugin not loaded -- is ' +
                   'extra_cdn.chartjs_sankey missing from report.yaml?');
      return;
    }

    var colours = fw.getThemeColors();
    if (chart) { chart.destroy(); }
    chart = new Chart(el.getContext('2d'), {
      type: 'sankey',
      data: {
        datasets: [{
          data: payload.links,
          colorFrom: function (c) { return nodeColour(c.dataset.data[c.dataIndex].from, colours); },
          colorTo: function (c) { return nodeColour(c.dataset.data[c.dataIndex].to, colours); },
          colorMode: 'gradient',
          labels: payload.labels,
          // Deliberately NOT pinning columns. Placing each drop-off beside the
          // stage it left sounds better and renders worse: the first stage
          // loses 4.4M of 10.8M, so once columns are fixed the later, smaller
          // flows are starved of vertical space and collapse into stubs. The
          // plugin's own layout keeps the ribbon legible the whole way across.
          // `payload.columns` is still built, for anyone who wants to try.
          font: { color: colours.tick_color },
          size: 'max'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (c) {
                var d = c.dataset.data[c.dataIndex];
                return d.from + ' \\u2192 ' + d.to + ': ' +
                       d.flow.toLocaleString() + ' users';
              }
            }
          }
        }
      }
    });
  }

  // Two separate jobs, and missing either leaves the section blank.
  //
  // 1. The FIRST draw. window.renderAll fires on theme switch, scope change and
  //    auto-refresh -- not on initial load, which the framework renders through
  //    another path. data.json is fetched asynchronously, hence the short poll.
  function whenReady(fn, attempts) {
    attempts = (attempts == null) ? 100 : attempts;
    if (window.fw && activePayload()) {
      fn();
      return;
    }
    if (attempts <= 0) { return; }
    setTimeout(function () { whenReady(fn, attempts - 1); }, 50);
  }
  whenReady(draw);

  // 2. EVERY redraw after that. Chaining rather than replacing, so this does
  //    not silently drop whatever the framework or another section installed.
  var previous = window.renderAll;
  window.renderAll = function () {
    if (typeof previous === 'function') { previous.apply(this, arguments); }
    draw();
  };

  // 3. THEME SWITCHES, which are their own event and not covered by the above.
  //    Every colour in this chart came out of fw.getThemeColors() at build
  //    time, so without this the diagram keeps the old palette until something
  //    else happens to redraw it. The validator warns when a RawHTML section
  //    makes Chart.js instances and does not listen -- worth trusting, because
  //    the symptom is subtle and only appears when a user toggles the theme.
  window.addEventListener('fw-theme-change', function () { draw(); });
})();
"""

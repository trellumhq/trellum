"""The leak, animated -- a pure-canvas particle flow for cart-funnel.

Why this is `RawHTML`, and the components ruled out first: `FunnelChart`
(already on the page) states the magnitudes but not the *experience* -- a
funnel bar is a number wearing a shape, and the thing this report is named
for is attrition happening. An `AreaChart` draws totals over time, not
attrition through stages; the sankey pattern (see purchase-flow) draws
static volume between many nodes, not a rate you can watch. None of them can
show seven in ten carts failing to become orders as something that visibly
*happens*. This section can: sessions flow in from the left as particles,
each gate passes the live measured share, and everyone else drips out the
bottom. Ten seconds of watching it teaches the abandonment number better
than the KPI card above it does.

What it demonstrates that the other three custom sections do not: a custom
section with **no vendor library at all**. purchase-flow, revenue-map and
player-segments each load a Chart.js plugin through extra_cdn; this is plain
canvas and requestAnimationFrame. If the visual is genuinely novel, the
platform primitives are often enough -- reach for a library when it earns
its payload, not by reflex.

The rules it still plays by, because every custom section must:

* **Live under the filters.** It subscribes to the same DataSource the KPI
  row uses; narrow to Mobile and the gates re-tighten to Mobile's real
  keep-rates, smoothly.
* **Theme-aware.** Every colour comes from `fw.getThemeColors()` or a
  computed CSS variable at draw time; fades use `globalAlpha`, never
  hardcoded rgba literals (the validator FAILs hex and WARNs rgba).
* **Respectful of motion preferences.** Under `prefers-reduced-motion` the
  particles are dropped and the same geometry renders as a static tapering
  pipe with leak wedges -- the information survives, the motion goes.
* **Cheap when unwatched.** The animation pauses when the tab is hidden or
  the canvas is scrolled out of view.
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
<div class="fw-custom-badge"><b>Custom section</b> · RawHTML, pure canvas — no vendor library, live under the filters</div>
<style>
  .lk-wrap { position: relative; height: 400px; }
  .lk-wrap canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
  .lk-note { margin-top: 8px; font-size: 11px; color: var(--text-secondary); }
</style>
<div class="lk-wrap"><canvas id="lkCanvas"></canvas></div>
<div class="lk-note">Each dot is a share of sessions; the gates pass exactly
the measured rate for the current filter selection. Reduced-motion systems see
the same pipe without the particles.</div>
"""

CUSTOM_JS = """
(function () {
  var DS = 'traffic';       // the same DataSource the KPI row filters
  var canvas, ctx2d, wrap;
  var stages = [];          // [{col, label}] from the server
  var counts = [];          // live sums, eased toward targets
  var targets = [];
  var particles = [];
  var running = false, raf = 0;
  var inView = true, tabVisible = !document.hidden;
  function visible() { return inView && tabVisible; }
  var reduced = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function themeColours() {
    var fwc = (window.fw && fw.getThemeColors) ? fw.getThemeColors() : {};
    var css = getComputedStyle(document.documentElement);
    return {
      flow: (fwc.chart_colors || [])[0] || css.getPropertyValue('--accent-blue').trim(),
      done: (fwc.chart_colors || [])[2] || css.getPropertyValue('--accent-green').trim(),
      lost: css.getPropertyValue('--accent-red').trim(),
      text: css.getPropertyValue('--text-main').trim(),
      dim:  fwc.tick_color || css.getPropertyValue('--text-secondary').trim(),
      grid: fwc.grid_color || css.getPropertyValue('--border-color').trim(),
    };
  }

  function fmt(n) {
    if (n >= 1e6) { return (n / 1e6).toFixed(1) + 'M'; }
    if (n >= 1e3) { return (n / 1e3).toFixed(1) + 'k'; }
    return String(Math.round(n));
  }

  /* Geometry: gate x-positions and the pipe's half-thickness at each gate,
     linearly proportional to the live counts. The collapse from a fat pipe
     to a sliver IS the message, so no log scale -- only a 3px floor so the
     survivors stay visible. */
  function geometry(w, h) {
    var padX = 70, top = 46, bandH = h * 0.52;
    var xs = [], half = [];
    var base = Math.max(counts[0] || 0, 1);
    for (var i = 0; i < stages.length; i++) {
      xs.push(padX + (w - 2 * padX) * (i / (stages.length - 1)));
      half.push(Math.max(3, (bandH / 2) * ((counts[i] || 0) / base)));
    }
    return { xs: xs, half: half, midY: top + bandH / 2 };
  }

  function halfAt(g, x) {
    var i = 0;
    while (i < g.xs.length - 2 && x > g.xs[i + 1]) { i++; }
    var t = (x - g.xs[i]) / Math.max(1, g.xs[i + 1] - g.xs[i]);
    t = Math.min(1, Math.max(0, t));
    return g.half[i] + (g.half[i + 1] - g.half[i]) * t;
  }

  function drawStatic(c) {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    var g = geometry(w, h);
    ctx2d.clearRect(0, 0, w, h);
    if (!counts[0]) {
      ctx2d.fillStyle = c.dim;
      ctx2d.font = '13px ' + getComputedStyle(document.body).fontFamily;
      ctx2d.fillText('No sessions match the current filters.', 24, 40);
      return g;
    }

    // The pipe: one filled band whose edges step down at each gate.
    ctx2d.beginPath();
    ctx2d.moveTo(g.xs[0], g.midY - g.half[0]);
    var i;
    for (i = 1; i < g.xs.length; i++) { ctx2d.lineTo(g.xs[i], g.midY - g.half[i]); }
    for (i = g.xs.length - 1; i >= 0; i--) { ctx2d.lineTo(g.xs[i], g.midY + g.half[i]); }
    ctx2d.closePath();
    ctx2d.globalAlpha = reduced ? 0.35 : 0.16;
    ctx2d.fillStyle = c.flow;
    ctx2d.fill();
    ctx2d.globalAlpha = 1;

    var fontFam = getComputedStyle(document.body).fontFamily;

    for (i = 0; i < g.xs.length; i++) {
      // Gate line
      ctx2d.strokeStyle = c.grid;
      ctx2d.beginPath();
      ctx2d.moveTo(g.xs[i], g.midY - g.half[0] - 6);
      ctx2d.lineTo(g.xs[i], g.midY + g.half[0] + 6);
      ctx2d.stroke();
      // Label + live count above
      ctx2d.textAlign = i === 0 ? 'left' : (i === g.xs.length - 1 ? 'right' : 'center');
      ctx2d.fillStyle = c.dim;
      ctx2d.font = '11px ' + fontFam;
      ctx2d.fillText(stages[i].label, g.xs[i], 18);
      ctx2d.fillStyle = c.text;
      ctx2d.font = '600 13px ' + fontFam;
      ctx2d.fillText(fmt(counts[i]), g.xs[i], 36);

      // The loss in each gap, said plainly underneath.
      if (i > 0) {
        var lost = (counts[i - 1] || 0) - (counts[i] || 0);
        var pct = counts[i - 1] ? Math.round(lost / counts[i - 1] * 100) : 0;
        var mx = (g.xs[i - 1] + g.xs[i]) / 2;
        ctx2d.textAlign = 'center';
        ctx2d.fillStyle = c.lost;
        ctx2d.font = '600 12px ' + fontFam;
        ctx2d.fillText('-' + fmt(lost), mx, g.midY + g.half[0] + 34);
        ctx2d.fillStyle = c.dim;
        ctx2d.font = '10px ' + fontFam;
        ctx2d.fillText(pct + '% leak here', mx, g.midY + g.half[0] + 48);
        if (reduced) {
          // Static leak wedge: the particles' story without the motion.
          ctx2d.globalAlpha = 0.25;
          ctx2d.fillStyle = c.lost;
          ctx2d.beginPath();
          ctx2d.moveTo(g.xs[i], g.midY + halfAt(g, g.xs[i] - 1));
          ctx2d.lineTo(g.xs[i] + Math.min(26, 8 + pct * 0.3), g.midY + g.half[0] + 20);
          ctx2d.lineTo(g.xs[i] - 6, g.midY + g.half[0] + 20);
          ctx2d.closePath();
          ctx2d.fill();
          ctx2d.globalAlpha = 1;
        }
      }
    }
    return g;
  }

  function step() {
    raf = 0;
    if (!running || !visible()) { return; }
    var c = themeColours();
    // Ease displayed counts toward the filtered targets so a filter click
    // tightens the gates smoothly instead of snapping.
    var moving = false;
    for (var i = 0; i < targets.length; i++) {
      var d = (targets[i] || 0) - (counts[i] || 0);
      if (Math.abs(d) > 0.5) { counts[i] = (counts[i] || 0) + d * 0.12; moving = true; }
      else { counts[i] = targets[i] || 0; }
    }
    var g = drawStatic(c);
    if (counts[0]) {
      // Spawn at a constant visual rate: the rates through the gates are the
      // data; absolute volume is already written on the labels.
      for (var s = 0; s < 3; s++) {
        particles.push({ x: g.xs[0] - 24 - Math.random() * 20,
                         j: Math.random() * 2 - 1,
                         vy: 0, leak: false, a: 1, gate: 1 });
      }
      var keep = [];
      for (var p = 0; p < particles.length; p++) {
        var pt = particles[p];
        if (!pt.leak) {
          pt.x += 2.1;
          var gi = pt.gate;
          if (gi < g.xs.length && pt.x >= g.xs[gi]) {
            var prob = counts[gi - 1] ? (counts[gi] || 0) / counts[gi - 1] : 0;
            if (Math.random() < prob) { pt.gate++; }
            else { pt.leak = true; pt.vy = 0.4 + Math.random() * 0.7; }
          }
          pt.y = g.midY + pt.j * (halfAt(g, pt.x) - 2);
        } else {
          pt.x += 0.5;
          pt.vy += 0.09;
          pt.y += pt.vy;
          pt.a -= 0.016;
        }
        var out = pt.x > g.xs[g.xs.length - 1] + 26 || pt.a <= 0 ||
                  pt.y > canvas.clientHeight - 4;
        if (!out) {
          ctx2d.globalAlpha = Math.max(0, pt.a) * 0.9;
          ctx2d.fillStyle = pt.leak ? c.lost
                          : (pt.gate >= g.xs.length ? c.done : c.flow);
          ctx2d.beginPath();
          ctx2d.arc(pt.x, pt.y, 2.1, 0, 6.2832);
          ctx2d.fill();
          ctx2d.globalAlpha = 1;
          keep.push(pt);
        }
      }
      particles = keep;
      if (particles.length > 900) { particles.splice(0, particles.length - 900); }
    }
    raf = requestAnimationFrame(step);
    void moving;
  }

  function redraw() {
    if (reduced) { drawStatic(themeColours()); return; }
    if (!raf && running && visible()) { raf = requestAnimationFrame(step); }
  }

  function setTargets(rows) {
    targets = stages.map(function (s) {
      var total = 0;
      for (var r = 0; r < rows.length; r++) { total += rows[r][s.col] || 0; }
      return total;
    });
    if (!counts.length || reduced) { counts = targets.slice(); }
    redraw();
  }

  function fitCanvas() {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(canvas.clientWidth * dpr);
    canvas.height = Math.round(canvas.clientHeight * dpr);
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    redraw();
  }

  function boot() {
    var payload = (window._reportData || {})._leak;
    canvas = document.getElementById('lkCanvas');
    if (!payload || !canvas) { return; }
    wrap = canvas.parentElement;
    ctx2d = canvas.getContext('2d');
    stages = payload.stages || [];
    running = true;

    fitCanvas();
    if (window.ResizeObserver) {
      new ResizeObserver(fitCanvas).observe(wrap);
    }
    // Don't burn frames nobody is watching: two independent gates, tab
    // visibility and viewport intersection, each owning its own flag.
    document.addEventListener('visibilitychange', function () {
      tabVisible = !document.hidden;
      redraw();
    });
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        inView = entries[0].isIntersecting;
        redraw();
      }, { threshold: 0.05 }).observe(wrap);
    }

    fw.filterEngine.subscribe(DS, 'cart-funnel-leak', setTargets);
    setTargets(fw.filterEngine.getFiltered(DS) || []);
  }

  // onReady, not a hand-rolled poll -- the engine tells us when the
  // DataSource exists and the data has arrived.
  function whenReady(attempts) {
    attempts = (attempts == null) ? 120 : attempts;
    if (window.fw && fw.filterEngine && window._reportData &&
        window._reportData._leak) {
      fw.filterEngine.onReady
        ? fw.filterEngine.onReady(DS, boot)
        : boot();
      return;
    }
    if (attempts <= 0) {
      console.warn('[cart-funnel] leak section gave up waiting for fw');
      return;
    }
    setTimeout(function () { whenReady(attempts - 1); }, 50);
  }
  whenReady();

  // Chained, so nothing already installed is dropped; theme changes redraw
  // with the new palette (colours are read fresh every frame anyway, but a
  // reduced-motion static render needs the nudge).
  var previous = window.renderAll;
  window.renderAll = function () {
    if (typeof previous === 'function') { previous.apply(this, arguments); }
    redraw();
  };
  window.addEventListener('fw-theme-change', function () { redraw(); });
})();
"""

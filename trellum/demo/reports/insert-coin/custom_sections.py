"""Ride the Metric: the report that becomes a game when you press Start.

v2, after the owner's design review of v1's pixel cabinet: no retro, no
sprites, no CRT. The game now speaks the report's own visual language -- the
level IS a line chart. Terrain is the daily-active-users curve drawn with
grid, axis labels and an area fill; revenue is orbs of light whose size is
their dollar value; the outage is a literal gap in the line (the metric
flatlined, so there is nothing to stand on); events.yaml entries stand in
the world as the same dashed annotations every other chart uses.

The rules of engagement, per the owner:

* **Nothing moves until Start is pressed.** At rest the section is a static
  panorama of the whole filtered window -- a chart. Press Start and the
  camera drops to street level and the run begins. Blur or hide the tab and
  it pauses. The run ends on a stats screen whose window-IAP line is the
  same sum as the KPI row above -- stated so the agreement is visible.
* **Live under the filters.** The level is rebuilt from the filtered rows on
  every filter change (and the run resets to the panorama, because the world
  changed). Selecting a single spender tier changes the comet -- the whale
  is heavy with a wide magnetic pull, the non-spender is the fastest thing
  in the game with nothing to collect.
* **Adaptive revenue orbs.** A fixed denomination starves the small tiers,
  so the orb value recalibrates to the filtered rows (the legend chip shows
  the live rate) and each orb's size encodes its value.
* **Theme-native.** Every color is read from the theme tokens or
  ``fw.getThemeColors()`` at draw time; flip the theme and the game reskins
  on the next frame. No dark-screen exception.
"""

CABINET_HTML = """
<style>
  .fw-custom-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;
    color: var(--text-secondary); border: 1px dashed var(--border-color);
    border-radius: 4px; padding: 3px 8px; margin-bottom: 10px;
  }
  .fw-custom-badge b { color: var(--text-main); font-weight: 600; }
  .rm-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 8px; }
  .rm-chip {
    display: inline-flex; align-items: center; gap: 7px;
    background: var(--bg-card); border: 1px solid var(--border-color);
    border-radius: 999px; padding: 4px 12px;
    font-size: 12px; font-weight: 600; color: var(--text-main);
  }
  .rm-chip .lab { color: var(--text-secondary); font-weight: 500; }
  .rm-chip .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .rm-stage { position: relative; height: 680px; outline: none; }
  .rm-stage canvas {
    position: absolute; inset: 0; width: 100%; height: 100%;
    border: 1px solid var(--border-color); border-radius: 10px;
  }
  .rm-stage:focus-visible canvas { outline: 2px solid var(--accent-blue); }
  .rm-overlay {
    position: absolute; inset: 0; display: flex;
    align-items: center; justify-content: center; pointer-events: none;
  }
  .rm-panel {
    pointer-events: auto; text-align: center; max-width: 470px;
    background: var(--bg-card); border: 1px solid var(--border-color);
    border-radius: 12px; padding: 22px 30px 20px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.22);
  }
  .rm-panel h3 { margin: 0 0 8px; font-size: 17px; color: var(--text-main); }
  .rm-panel p {
    margin: 0 0 6px; font-size: 12.5px; line-height: 1.55;
    color: var(--text-secondary);
  }
  .rm-panel p b { color: var(--text-main); }
  .rm-buttons { display: flex; gap: 10px; justify-content: center; margin-top: 14px; }
  .rm-btn {
    border: 0; border-radius: 8px; padding: 10px 24px; cursor: pointer;
    font: 600 13.5px inherit; background: var(--accent-blue); color: #fff;
  }
  .rm-btn:hover { filter: brightness(1.08); }
  .rm-btn:focus-visible { outline: 2px solid var(--accent-blue); outline-offset: 2px; }
  .rm-btn.ghost {
    background: transparent; color: var(--text-main);
    border: 1px solid var(--border-color);
  }
  .rm-note { margin-top: 8px; font-size: 11px; color: var(--text-secondary); }
</style>
<div class="fw-custom-badge"><b>Custom section</b> · RawHTML minigame — pure
canvas, theme-native, rebuilt from the same filtered rows as the KPI row.
Nothing moves until you press Start.</div>
<div class="rm-chips">
  <span class="rm-chip"><span class="dot" style="background:var(--accent-blue)"></span>Line — daily active users</span>
  <span class="rm-chip"><span class="dot" style="background:var(--accent-yellow)"></span>Orbs — IAP revenue · small &asymp; <span id="rmOrbV">—</span> · big = 5&times;, floats high</span>
  <span class="rm-chip"><span class="dot" style="background:var(--accent-red)"></span>Gap — a day under 55% of the window median has no floor</span>
  <span class="rm-chip"><span class="dot" style="background:var(--accent-blue);box-shadow:0 0 8px var(--accent-blue)"></span>Comet — you</span>
</div>
<div class="rm-chips">
  <span class="rm-chip"><span class="lab">day</span>&nbsp;<span id="rmDay">—</span></span>
  <span class="rm-chip"><span class="lab">DAU</span>&nbsp;<span id="rmDau">—</span></span>
  <span class="rm-chip"><span class="lab">collected</span>&nbsp;<span id="rmScore">$0</span></span>
  <span class="rm-chip"><span class="lab">window IAP</span>&nbsp;<span id="rmTotal">—</span></span>
  <span class="rm-chip"><span class="lab">playing as</span>&nbsp;<span id="rmWho">—</span></span>
</div>
<div class="rm-stage" id="rmStage" tabindex="0" role="application"
     aria-label="Ride the Metric minigame. The level is the filtered data:
terrain is daily active users, orbs are IAP revenue, gaps are outage days.
Press the Start button to play; Space or click jumps; R restarts; Escape
returns to the chart view.">
  <canvas id="rmCanvas"></canvas>
  <div class="rm-overlay" id="rmOverlay"></div>
</div>
<div class="rm-note">At rest this is a chart of the filtered window. Press
Start and you are inside it: <b>Space / click</b> to jump, <b>R</b> to
restart, <b>Esc</b> for the chart view. Tabbing away pauses. Every filter
change rebuilds the level and resets the run.</div>
"""

CABINET_JS = """
(function () {
  var DS = 'arcade';
  var PXD = 40;                  // world px per day while playing
  var GRAV = 1250, JUMP_V = 460;

  var payload, canvas, ctx2d, stage, overlay;
  var days = [];                 // [{date, dau, iap}] filtered, per day
  var median = 0, maxDau = 0, minDau = 0, yLo = 0, yHi = 1;
  var windowIap = 0, fieldTotal = 0;
  var orbs = [], orbV = 0;
  var events = [];               // [{idx, label, type}]
  var tierName = 'default', tier = null;
  var state = 'ready';           // ready | playing | paused | over
  var runner = null, trail = [];
  var raf = 0, lastT = 0, inView = true;
  var reduced = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var fmtMoney = function (n) { return '$' + Math.round(n); };

  // ── Theme, read at draw time so theme switches reskin the game ──────
  function colors() {
    var fwc = (window.fw && fw.getThemeColors) ? fw.getThemeColors() : {};
    var css = getComputedStyle(document.documentElement);
    function v(name) { return css.getPropertyValue(name).trim(); }
    return {
      line: v('--accent-blue') || (fwc.chart_colors || [])[0],
      gold: v('--accent-yellow'),
      bad: v('--accent-red'),
      good: v('--accent-green'),
      text: v('--text-main'),
      dim: fwc.tick_color || v('--text-secondary'),
      grid: fwc.grid_color || v('--border-color'),
      card: v('--bg-card'),
    };
  }
  function isDarkTheme() {
    var bg = colors().card;
    var m = /^#([0-9a-fA-F]{6})/.exec(bg);
    if (!m) { return false; }
    var n = parseInt(m[1], 16);
    return (0.2126 * (n >> 16 & 255) + 0.7152 * (n >> 8 & 255) +
            0.0722 * (n & 255)) < 128;
  }
  // Fully transparent stop in the SAME hue, so gradients fade cleanly on
  // both themes instead of shifting through gray.
  function fadeTo(color) {
    var m = /^#([0-9a-fA-F]{6})/.exec(String(color).trim());
    if (!m) { return 'rgba(0,0,0,0)'; }
    var n = parseInt(m[1], 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' +
           (n & 255) + ',0)';
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el && el.textContent !== String(value)) { el.textContent = value; }
  }

  // ── Level building: the filtered rows become the world ──────────────
  function niceVal(x) {
    var steps = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000,
                 50000, 100000];
    for (var i = 0; i < steps.length; i++) { if (x <= steps[i]) { return steps[i]; } }
    return 250000;
  }
  function medianOf(arr) {
    if (!arr.length) { return 0; }
    var s = arr.slice().sort(function (a, b) { return a - b; });
    return s[s.length >> 1];
  }

  function rebuild(rows) {
    var byDay = {}, tiers = {};
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var rec = byDay[r.event_date] || (byDay[r.event_date] = { dau: 0, iap: 0 });
      rec.dau += r.dau || 0;
      rec.iap += r.iap_revenue || 0;
      tiers[r.spender_tier] = true;
    }
    var keys = Object.keys(byDay).sort();
    days = keys.map(function (d) {
      return { date: d, dau: byDay[d].dau, iap: byDay[d].iap };
    });
    var tierKeys = Object.keys(tiers);
    tierName = tierKeys.length === 1 ? tierKeys[0] : 'default';
    tier = payload.tiers[tierName] || payload.tiers['default'];

    median = medianOf(days.map(function (d) { return d.dau; }));
    maxDau = 0; minDau = Infinity; windowIap = 0;
    days.forEach(function (d) {
      d.pit = d.dau < median * payload.pit_fraction;
      maxDau = Math.max(maxDau, d.dau);
      if (!d.pit) { minDau = Math.min(minDau, d.dau); }
      windowIap += d.iap;
    });
    if (!isFinite(minDau)) { minDau = 0; }
    // Band-normalise the terrain to the data's own range: mapping from zero
    // squashed the whole curve into the top of the canvas and flattened the
    // hills. A non-zero baseline is normal for a line chart, and here it is
    // also the level design -- the weekly waves become real slopes.
    var span = Math.max(1, maxDau - minDau);
    yLo = Math.max(0, minDau - span * 0.30);
    yHi = maxDau + span * 0.22;

    // Adaptive orb denomination, in two sizes: small = orbV, big = 5x. The
    // big orbs float high where only a well-timed jump reaches, so the field
    // has hierarchy and the run has decisions -- not a string of identical
    // pearls.
    var iaps = days.filter(function (d) { return !d.pit; })
                   .map(function (d) { return d.iap; });
    var medIap = medianOf(iaps);
    orbV = medIap > 0 ? niceVal(medIap / 2.5) : 0;
    orbs = [];
    fieldTotal = 0;
    days.forEach(function (d, idx) {
      if (d.pit || !orbV) { return; }
      var big = d.iap >= 5 * orbV ? 1 : 0;
      var rest = d.iap - big * 5 * orbV;
      var n = Math.max(0, Math.min(4, Math.round(rest / orbV)));
      if (big) {
        orbs.push({ f: idx + 0.5, x: (idx + 0.5) * PXD,
                    h: 86 + 22 * Math.abs(Math.sin(idx * 7.3)),
                    v: 5 * orbV, r: 8, taken: false, burst: 0 });
        fieldTotal += 5 * orbV;
      }
      for (var j = 0; j < n; j++) {
        var fx = idx + (j + 0.7) / (n + 1);
        orbs.push({ f: fx, x: fx * PXD,
                    h: 34 + 38 * Math.abs(Math.sin(idx * 3.7 + j * 2.1)),
                    v: orbV, r: 4.6, taken: false, burst: 0 });
        fieldTotal += orbV;
      }
    });

    // Real calendar events inside the window, as in-world annotations.
    events = [];
    var evs = (window._reportData || {})._events || [];
    for (var e = 0; e < evs.length; e++) {
      for (var k = 0; k < days.length; k++) {
        if (days[k].date === evs[e].date) {
          events.push({ idx: k, label: evs[e].label || '',
                        type: evs[e].type || 'campaign' });
          break;
        }
      }
    }

    setText('rmOrbV', orbV ? fmtMoney(orbV) : '—');
    setText('rmTotal', fmtMoney(windowIap));
    setText('rmWho', tierName === 'default' ? 'all tiers'
                                            : tierName.replace('_', ' '));
    setState('ready');
  }

  // ── Terrain geometry ─────────────────────────────────────────────────
  function valueAt(f) {                       // catmull over daily DAU
    var n = days.length;
    var i = Math.max(0, Math.min(n - 2, Math.floor(f)));
    var t = f - i;
    function v(ix) { return days[Math.max(0, Math.min(n - 1, ix))].dau; }
    var p0 = v(i - 1), p1 = v(i), p2 = v(i + 1), p3 = v(i + 2);
    return 0.5 * ((2 * p1) + (-p0 + p2) * t +
                  (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t +
                  (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t);
  }
  function pitAt(f) {
    var i = Math.max(0, Math.min(days.length - 1, Math.round(f - 0.5)));
    return days[i].pit;
  }

  // ── Run state ────────────────────────────────────────────────────────
  function resetRun() {
    runner = { x: 1.5 * PXD, y: 0, vy: 0, ground: true,
               score: 0, coins: 0, done: 0, win: false };
    trail.length = 0;
    orbs.forEach(function (o) {
      o.taken = false; o.burst = 0; o.x = o.f * PXD;
      o.hh = o.h;
    });
  }

  function panel(html) {
    overlay.innerHTML = html;
    overlay.style.display = html ? 'flex' : 'none';
  }
  function btn(id, label, ghost) {
    return '<button class="rm-btn' + (ghost ? ' ghost' : '') +
           '" id="' + id + '">' + label + '</button>';
  }
  function wire(id, fn) {
    var el = document.getElementById(id);
    if (el) { el.addEventListener('click', fn); }
  }

  function setState(next) {
    state = next;
    if (state === 'ready') {
      resetRun();
      panel('<div class="rm-panel"><h3>This chart is playable</h3>' +
        '<p>The line is <b>daily active users</b> for the current filters. ' +
        'The orbs are <b>IAP revenue</b> — bigger orb, bigger money. A day ' +
        'below 55% of the window median has <b>no floor</b>.</p>' +
        '<p>Ride the curve, jump the gaps, collect the quarter.</p>' +
        '<div class="rm-buttons">' + btn('rmStart', 'Start') + '</div></div>');
      wire('rmStart', function () { startRun(); });
      drawPanorama();
    } else if (state === 'playing') {
      panel('');
      kick();
    } else if (state === 'paused') {
      panel('<div class="rm-panel"><h3>Paused</h3>' +
        '<p>The run held its breath while you were away.</p>' +
        '<div class="rm-buttons">' + btn('rmResume', 'Resume') +
        btn('rmQuit', 'Back to the chart', true) + '</div></div>');
      wire('rmResume', function () { setState('playing'); });
      wire('rmQuit', function () { setState('ready'); });
    } else if (state === 'over') {
      var dayIdx = runner.win ? days.length - 1
        : Math.min(days.length - 1, Math.max(0, Math.floor(runner.x / PXD)));
      panel('<div class="rm-panel"><h3>' +
        (runner.win ? 'Window complete' :
         'The metric flatlined — that gap was a real day') + '</h3>' +
        '<p>' + (runner.win ? 'rode all' : 'survived') + ' <b>' +
        (dayIdx + 1) + ' of ' + days.length +
        ' days</b> (' + days[0].date + ' → ' + days[dayIdx].date + ')</p>' +
        '<p>collected <b>' + fmtMoney(runner.score) + '</b> of ' +
        fmtMoney(fieldTotal) + ' on the field (' + runner.coins +
        ' orbs)</p>' +
        '<p>true window IAP <b>' + fmtMoney(windowIap) +
        '</b> — the same sum as the KPI row above</p>' +
        '<div class="rm-buttons">' + btn('rmAgain', 'Play again') +
        btn('rmChart', 'View the chart', true) + '</div></div>');
      wire('rmAgain', function () { startRun(); });
      wire('rmChart', function () { setState('ready'); });
    }
  }

  function startRun() {
    resetRun();
    state = 'playing';
    panel('');
    stage.focus({ preventScroll: true });
    kick();
  }

  function jump() {
    if (state !== 'playing' || !runner) { return; }
    if (runner.ground) { runner.vy = -JUMP_V; runner.ground = false; }
  }

  // ── Simulation ───────────────────────────────────────────────────────
  function update(dt, H, yOf) {
    // Slope-coupled speed: downhill runs fast, uphill drags. It makes the
    // terrain FELT, so different windows genuinely play differently.
    var f0 = runner.x / PXD;
    if (runner.ground) {
      var yB = yOf(valueAt(Math.max(0, f0 - 0.2)));
      var yA = yOf(valueAt(Math.min(days.length - 1, f0 + 0.2)));
      var grade = (yA - yB) / (0.4 * PXD);
      runner.mult = Math.max(0.72, Math.min(1.55, 1 + grade * 0.6));
    }
    var speed = 150 * tier.speed * (runner.mult || 1);
    var fPrev = runner.x / PXD;
    runner.x += speed * dt;
    var f = runner.x / PXD;
    var gy = pitAt(f) ? null : yOf(valueAt(f));
    if (runner.ground) {
      if (gy == null) { runner.ground = false; runner.vy = 0; }
      else { runner.y = gy; }
    } else {
      // Landing is a surface CROSSING, not a proximity window: you land the
      // frame your path passes from above the curve to below it -- which on
      // a steep uphill means alighting on the slope instead of tunnelling
      // through it. Already below the surface (deep in a pit, crossing
      // under the far edge)? Then there is nothing to land on, and you fall.
      var prevGy = pitAt(fPrev) ? null : yOf(valueAt(fPrev));
      var prevY = runner.y;
      runner.vy += GRAV * dt;
      runner.y += runner.vy * dt;
      var wasAbove = prevGy != null ? prevY <= prevGy + 0.5
                   : (gy != null && prevY <= gy + 0.5);
      if (gy != null && runner.y >= gy && wasAbove) {
        runner.y = gy; runner.vy = 0; runner.ground = true;
      }
    }
    // orbs: magnet pull + pickup
    for (var i = 0; i < orbs.length; i++) {
      var o = orbs[i];
      if (o.taken) { o.burst = Math.max(0, o.burst - dt * 2.2); continue; }
      if (Math.abs(o.x - runner.x) > 220) { continue; }
      if (pitAt(o.f)) { continue; }
      var oy = yOf(valueAt(o.f)) - o.hh;
      var dx = o.x - runner.x, dy = oy - (runner.y - 12);
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (tier.pull && dist < tier.pull && dist > 1) {
        o.x -= dx / dist * 280 * dt;
        o.hh += dy / dist * 280 * dt * 0.6;
      }
      if (dist < o.r + 11) {
        o.taken = true; o.burst = 1;
        runner.coins += 1; runner.score += o.v;
      }
    }
    trail.push({ x: runner.x, y: runner.y - 12 });
    if (trail.length > 24) { trail.shift(); }

    if (runner.y > H + 40) { runner.win = false; endRun(); }
    else if (f >= days.length - 1.2) { runner.win = true; endRun(); }
  }
  function endRun() { setState('over'); drawFrame(); }

  // ── Drawing ──────────────────────────────────────────────────────────
  function geometry(W, H, pxd) {
    var pad = { l: 54, r: 16, t: 30, b: 34 };
    return {
      pad: pad,
      yOf: function (v) {
        return pad.t + (1 - (v - yLo) / (yHi - yLo || 1)) * (H - pad.t - pad.b);
      },
      pxd: pxd,
    };
  }

  function drawScene(W, H, g, camX) {
    var c = colors(), dark = isDarkTheme();
    var pad = g.pad, yOf = g.yOf, pxd = g.pxd;
    var plotB = H - pad.b;
    var fontFam = getComputedStyle(document.body).fontFamily;
    ctx2d.clearRect(0, 0, W, H);
    if (!days.length) {
      ctx2d.fillStyle = c.dim;
      ctx2d.font = '13px ' + fontFam;
      ctx2d.fillText('No days match the current filters.', 20, 40);
      return;
    }

    // grid + y labels, like any LineChart on the page
    var step = niceVal((yHi - yLo) / 3.5);
    ctx2d.font = '11px ' + fontFam;
    ctx2d.textAlign = 'right';
    for (var v = Math.ceil(yLo / step) * step; v <= yHi; v += step) {
      var yy = yOf(v);
      if (yy < pad.t) { break; }
      ctx2d.strokeStyle = c.grid; ctx2d.lineWidth = 1;
      ctx2d.beginPath(); ctx2d.moveTo(pad.l, yy); ctx2d.lineTo(W - pad.r, yy);
      ctx2d.stroke();
      ctx2d.fillStyle = c.dim;
      ctx2d.fillText(window.fmtCompact ? fmtCompact(v) : String(v),
                     pad.l - 8, yy + 3.5);
    }

    // x ticks: drawn unclipped in screen space so edge labels stay whole
    var tickEvery = Math.max(1, Math.ceil(days.length / 8));
    ctx2d.textAlign = 'center';
    ctx2d.fillStyle = c.dim;
    for (var td = 0; td < days.length; td += tickEvery) {
      var sx = td * pxd - camX;
      if (sx < pad.l - 4 || sx > W - pad.r + 4) { continue; }
      ctx2d.fillText(days[td].date.slice(5),
                     Math.max(pad.l + 16, Math.min(W - pad.r - 16, sx)),
                     H - 12);
    }

    ctx2d.save();
    ctx2d.beginPath(); ctx2d.rect(pad.l, 0, W - pad.l - pad.r, H); ctx2d.clip();
    ctx2d.translate(-camX, 0);

    // pit bands
    ctx2d.fillStyle = c.bad;
    ctx2d.globalAlpha = 0.07;
    for (var pi = 0; pi < days.length; pi++) {
      if (days[pi].pit) { ctx2d.fillRect(pi * pxd - pxd / 2, pad.t, pxd, plotB - pad.t); }
    }
    ctx2d.globalAlpha = 1;

    // event annotations, framework style
    for (var ei = 0; ei < events.length; ei++) {
      var ev = events[ei];
      var ex = ev.idx * pxd;
      var ecol = ev.type === 'incident' ? c.bad
               : ev.type === 'release' ? c.good
               : ev.type === 'ab_test' ? c.line
               : c.gold;
      ctx2d.strokeStyle = ecol; ctx2d.lineWidth = 1;
      ctx2d.setLineDash([4, 4]);
      ctx2d.beginPath(); ctx2d.moveTo(ex, pad.t); ctx2d.lineTo(ex, plotB);
      ctx2d.stroke();
      ctx2d.setLineDash([]);
      ctx2d.fillStyle = ecol;
      // labels only when the line is on screen; near the right edge they
      // flip to the left side of the line
      if (ex - camX > W - 8 || ex - camX < 40) {
        // off-screen annotation: the dashed line clips away, skip the label
      } else if (ex - camX > W - 150) {
        ctx2d.textAlign = 'right';
        ctx2d.fillText(ev.label, ex - 5, pad.t + 6 + (ei % 2) * 13);
      } else {
        ctx2d.textAlign = 'left';
        ctx2d.fillText(ev.label, ex + 5, pad.t + 6 + (ei % 2) * 13);
      }
    }

    // the line + area, skipping pit days entirely
    var segs = [], cur = null;
    for (var si = 0; si < days.length; si++) {
      if (days[si].pit) { if (cur) { segs.push(cur); cur = null; } }
      else { if (!cur) { cur = [si, si]; } cur[1] = si; }
    }
    if (cur) { segs.push(cur); }
    segs.forEach(function (seg) {
      var x0 = seg[0] * pxd, x1 = seg[1] * pxd;
      if (x1 < camX - 40 || x0 > camX + W + 40) { return; }
      var from = Math.max(x0, camX - 20), to = Math.min(x1, camX + W + 20);
      ctx2d.beginPath();
      var first = true;
      for (var x = from; x <= to; x += 3) {
        var y2 = yOf(valueAt(x / pxd));
        if (first) { ctx2d.moveTo(x, y2); first = false; }
        else { ctx2d.lineTo(x, y2); }
      }
      ctx2d.strokeStyle = c.line; ctx2d.lineWidth = 2;
      ctx2d.lineJoin = 'round'; ctx2d.lineCap = 'round';
      ctx2d.stroke();
      ctx2d.lineTo(to, plotB); ctx2d.lineTo(from, plotB); ctx2d.closePath();
      var grad = ctx2d.createLinearGradient(0, pad.t, 0, plotB);
      grad.addColorStop(0, c.line);
      grad.addColorStop(1, fadeTo(c.line));
      ctx2d.globalAlpha = 0.14;
      ctx2d.fillStyle = grad;
      ctx2d.fill();
      ctx2d.globalAlpha = 1;
      // gap end caps
      [seg[0], seg[1]].forEach(function (endIdx) {
        var nb = days[endIdx - 1], na = days[endIdx + 1];
        if ((nb && nb.pit) || (na && na.pit)) {
          ctx2d.fillStyle = c.bad;
          ctx2d.beginPath();
          ctx2d.arc(endIdx * pxd, yOf(days[endIdx].dau), 3, 0, 6.2832);
          ctx2d.fill();
        }
      });
    });

    // orbs, sized by value
    var bobT = performance.now() / 420;
    for (var oi = 0; oi < orbs.length; oi++) {
      var o = orbs[oi];
      var ox = state === 'playing' ? o.x : o.f * pxd;
      if (ox < camX - 30 || ox > camX + W + 30) { continue; }
      var oy = yOf(valueAt(o.f)) - (state === 'playing' ? o.hh : o.h);
      if (o.taken) {
        if (o.burst > 0) {
          ctx2d.strokeStyle = c.gold;
          ctx2d.globalAlpha = o.burst;
          ctx2d.lineWidth = 1.5;
          ctx2d.beginPath();
          ctx2d.arc(ox, oy, o.r + 3 + (1 - o.burst) * 14, 0, 6.2832);
          ctx2d.stroke();
          ctx2d.globalAlpha = 1;
        }
        continue;
      }
      var bob = (state === 'playing' && !reduced) ? Math.sin(bobT + o.f * 3) * 2.5 : 0;
      var rr = state === 'playing' ? o.r : Math.max(2.2, o.r * 0.55);
      // Tight glow, solid core, thin ring: a coin of light, not a fuzzy blob.
      var glowR = rr * 1.7 + 2;
      var og = ctx2d.createRadialGradient(ox, oy + bob, 0, ox, oy + bob, glowR);
      og.addColorStop(0, c.gold);
      og.addColorStop(0.55, c.gold);
      og.addColorStop(1, fadeTo(c.gold));
      ctx2d.globalAlpha = dark ? 0.5 : 0.4;
      ctx2d.fillStyle = og;
      ctx2d.beginPath(); ctx2d.arc(ox, oy + bob, glowR, 0, 6.2832); ctx2d.fill();
      ctx2d.globalAlpha = 1;
      ctx2d.fillStyle = c.gold;
      ctx2d.beginPath(); ctx2d.arc(ox, oy + bob, rr, 0, 6.2832); ctx2d.fill();
      ctx2d.strokeStyle = c.gold;
      ctx2d.globalAlpha = 0.55;
      ctx2d.lineWidth = 1;
      ctx2d.beginPath(); ctx2d.arc(ox, oy + bob, rr + 2.5, 0, 6.2832);
      ctx2d.stroke();
      ctx2d.globalAlpha = 1;
    }

    // the comet, only while a run exists
    if (state === 'playing' || state === 'paused' || state === 'over') {
      for (var ti = 0; ti < trail.length; ti++) {
        var p = trail[ti], a = (ti + 1) / trail.length;
        ctx2d.globalAlpha = a * a * 0.5;
        ctx2d.fillStyle = c.line;
        ctx2d.beginPath();
        ctx2d.arc(p.x, p.y, 1 + a * (tier.size * 0.3), 0, 6.2832);
        ctx2d.fill();
      }
      ctx2d.globalAlpha = 1;
      var hx = runner.x, hy = runner.y - 12;
      var hg = ctx2d.createRadialGradient(hx, hy, 0, hx, hy, tier.size);
      hg.addColorStop(0, '#fff');
      hg.addColorStop(0.35, c.line);
      hg.addColorStop(1, fadeTo(c.line));
      ctx2d.fillStyle = hg;
      ctx2d.beginPath(); ctx2d.arc(hx, hy, tier.size, 0, 6.2832); ctx2d.fill();
      ctx2d.fillStyle = '#fff';
      ctx2d.beginPath();
      ctx2d.arc(hx, hy, Math.max(2.2, tier.size * 0.22), 0, 6.2832);
      ctx2d.fill();
    }
    ctx2d.restore();
  }

  function drawPanorama() {
    var W = canvas.clientWidth, H = canvas.clientHeight;
    if (!W || !days.length) { return; }
    var pad = 54 + 16;
    var pxd = Math.max(2, (W - pad) / Math.max(1, days.length - 1));
    drawScene(W, H, geometry(W, H, pxd), -54);
    updateHud(0);
  }

  function drawFrame() {
    var W = canvas.clientWidth, H = canvas.clientHeight;
    var g = geometry(W, H, PXD);
    var camX = Math.max(-g.pad.l,
      Math.min(runner.x - W * 0.33, days.length * PXD - W + 60));
    drawScene(W, H, g, camX);
  }

  function updateHud(f) {
    if (!days.length) { return; }
    var idx = Math.min(days.length - 1, Math.max(0, Math.floor(f)));
    setText('rmDay', days[idx].date);
    setText('rmDau', window.fmtCompact
      ? fmtCompact(Math.round(valueAt(Math.min(f, days.length - 1))))
      : Math.round(days[idx].dau));
    setText('rmScore', fmtMoney(runner ? runner.score : 0));
  }

  // ── Loop: runs only while playing ────────────────────────────────────
  function step(t) {
    raf = 0;
    if (state !== 'playing') { return; }
    if (!inView || document.hidden) { setState('paused'); return; }
    var dt = lastT ? Math.min(0.05, (t - lastT) / 1000) : 0.016;
    lastT = t;
    var W = canvas.clientWidth, H = canvas.clientHeight;
    var g = geometry(W, H, PXD);
    update(dt, H, g.yOf);
    if (state !== 'playing') { return; }
    drawFrame();
    updateHud(runner.x / PXD);
    raf = requestAnimationFrame(step);
  }
  function kick() {
    if (state === 'playing' && !raf) { lastT = 0; raf = requestAnimationFrame(step); }
  }

  // ── Wiring ───────────────────────────────────────────────────────────
  function fitCanvas() {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(canvas.clientWidth * dpr);
    canvas.height = Math.round(canvas.clientHeight * dpr);
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (state === 'playing') { drawFrame(); } else { drawPanorama(); }
  }

  function boot() {
    payload = (window._reportData || {})._arcade;
    canvas = document.getElementById('rmCanvas');
    stage = document.getElementById('rmStage');
    overlay = document.getElementById('rmOverlay');
    if (!payload || !canvas) { return; }
    ctx2d = canvas.getContext('2d');
    if (window.fw && fw['fmtCompact$']) { fmtMoney = fw['fmtCompact$']; }

    fitCanvas();
    if (window.ResizeObserver) { new ResizeObserver(fitCanvas).observe(stage); }
    document.addEventListener('visibilitychange', function () {
      if (document.hidden && state === 'playing') { setState('paused'); }
    });
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        inView = entries[0].isIntersecting;
        if (!inView && state === 'playing') { setState('paused'); }
      }, { threshold: 0.05 }).observe(stage);
    }
    stage.addEventListener('keydown', function (e) {
      if (e.code === 'Space' || e.code === 'ArrowUp') {
        if (state === 'playing') { e.preventDefault(); jump(); }
      } else if (e.code === 'KeyR') {
        if (state === 'playing' || state === 'over' || state === 'paused') {
          e.preventDefault(); startRun();
        }
      } else if (e.code === 'Escape') {
        if (state !== 'ready') { setState('ready'); }
      }
    });
    canvas.addEventListener('pointerdown', function () {
      if (state === 'playing') { stage.focus({ preventScroll: true }); jump(); }
    });
    window.addEventListener('blur', function () {
      if (state === 'playing') { setState('paused'); }
    });

    fw.filterEngine.subscribe(DS, 'ride-the-metric', rebuild);
    rebuild(fw.filterEngine.getFiltered(DS) || []);
  }

  function whenReady(attempts) {
    attempts = (attempts == null) ? 120 : attempts;
    if (window.fw && fw.filterEngine && window._reportData &&
        window._reportData._arcade) {
      fw.filterEngine.onReady
        ? fw.filterEngine.onReady(DS, boot)
        : boot();
      return;
    }
    if (attempts <= 0) {
      console.warn('[insert-coin] cabinet gave up waiting for fw');
      return;
    }
    setTimeout(function () { whenReady(attempts - 1); }, 50);
  }
  whenReady();

  var previous = window.renderAll;
  window.renderAll = function () {
    if (typeof previous === 'function') { previous.apply(this, arguments); }
    if (state !== 'playing') { drawPanorama(); }
  };
  window.addEventListener('fw-theme-change', function () {
    if (state !== 'playing') { drawPanorama(); }
  });
})();
"""

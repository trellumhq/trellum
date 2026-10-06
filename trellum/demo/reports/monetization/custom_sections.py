"""The Journey: acquisition to purchase as a living metro map.

A custom section born from the owner's sketch: campaigns at the head of the
map, players flowing top-to-bottom through the level bands, every loss a
fork (retry, quit, or head for checkout), purchases looping players back
into the game along a dashed return rail. Each light is one player-session,
colored by spender tier.

Everything on screen is measured, not scripted. The section reads the
``fact_journey`` transitions for the current filters and walks agents
through the measured Markov chain: at every station a light picks its next
edge with exactly the probabilities the warehouse recorded for its tier.
The counters are computed straight from the aggregated rows -- purchases by
tier, the share of checkout arrivals that came through a LOSS, and where
entries come from -- so the two headline stories (the big tiers buy after
losing; retargeting is how they come back) are read off the data, not the
animation.

Rendering is the metro variant the owner picked from three candidates:
strict lanes, station bars with a tick, dashed rails for everything that
moves back up the map. Colors come from theme tokens at draw time; reduced
motion renders the same graph as a static flow map with edge widths
proportional to measured volume.
"""

JOURNEY_HTML = """
<style>
  .fw-custom-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;
    color: var(--text-secondary); border: 1px dashed var(--border-color);
    border-radius: 4px; padding: 3px 8px; margin-bottom: 10px;
  }
  .fw-custom-badge b { color: var(--text-main); font-weight: 600; }
  .jn-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 8px; }
  .jn-chip {
    display: inline-flex; align-items: center; gap: 7px;
    background: var(--bg-card); border: 1px solid var(--border-color);
    border-radius: 999px; padding: 4px 12px;
    font-size: 12px; font-weight: 600; color: var(--text-main);
  }
  .jn-chip .lab { color: var(--text-secondary); font-weight: 500; }
  .jn-chip .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .jn-stage { position: relative; height: 540px; }
  .jn-stage canvas {
    position: absolute; inset: 0; width: 100%; height: 100%;
    border: 1px solid var(--border-color); border-radius: 10px;
  }
  .jn-note { margin-top: 8px; font-size: 11px; color: var(--text-secondary); }
</style>
<div class="fw-custom-badge"><b>Custom section</b> · RawHTML flow map — every
light is one player-session walking the measured transition rates; nothing
is scripted.</div>
<div class="jn-chips">
  <span class="jn-chip"><span class="dot" style="background:var(--accent-yellow)"></span>Whale</span>
  <span class="jn-chip"><span class="dot" style="background:var(--accent-blue)"></span>Dolphin</span>
  <span class="jn-chip"><span class="dot" style="background:var(--accent-green)"></span>Minnow</span>
  <span class="jn-chip"><span class="dot" style="background:var(--text-secondary)"></span>Non-spender</span>
  <span class="jn-chip"><span class="lab">each light = one player-session</span></span>
</div>
<div class="jn-chips">
  <span class="jn-chip stat"><span class="lab">purchases</span>&nbsp;<span id="jnPur">—</span></span>
  <span class="jn-chip stat"><span class="lab">checkout reached via a loss</span>&nbsp;<span id="jnLoss">—</span></span>
  <span class="jn-chip stat"><span class="lab">largest entry source</span>&nbsp;<span id="jnSrc">—</span></span>
</div>
<div class="jn-stage" id="jnStage">
  <canvas id="jnCanvas"
    aria-label="The Journey: players flow from acquisition campaigns at the
top, through game level bands, win and lose outcomes, checkout, purchase and
exit. Colors are spender tiers. All rates are measured from the filtered
data."></canvas>
</div>
<div class="jn-note">Campaigns at the top, money at the bottom. Losses fork
three ways — retry (dashed rail up), quit (toward Exit), or Checkout — and a
purchase loops the player back into the game along the left rail. Filter to
one spender tier and the river changes shape: the big tiers reach checkout
through losses; retargeting is how they come back in. Every rate is summed
from <b>fact_journey</b> under the current filters.</div>
"""

JOURNEY_JS = """
(function () {
  var DS = 'journey';
  var TIER_ORDER = ['whale', 'dolphin', 'minnow', 'non_spender'];
  var TIER_TOKEN = {
    whale: '--accent-yellow',
    dolphin: '--accent-blue',
    minnow: '--accent-green',
    non_spender: '--text-secondary',
  };
  var SRC_LABEL = { paid: 'Paid UA', organic: 'Organic', retarget: 'Retargeting' };
  var NODES = {
    paid:     { label: 'Paid UA',     fx: 0.20, fy: 0.06 },
    organic:  { label: 'Organic',     fx: 0.50, fy: 0.06 },
    retarget: { label: 'Retargeting', fx: 0.80, fy: 0.06 },
    easy:     { label: 'Levels 1–3',  fx: 0.20, fy: 0.26 },
    mid:      { label: 'Levels 4–6',  fx: 0.50, fy: 0.26 },
    hard:     { label: 'Levels 7–10', fx: 0.80, fy: 0.26 },
    win:      { label: 'Win',         fx: 0.32, fy: 0.48 },
    lose:     { label: 'Lose',        fx: 0.68, fy: 0.48 },
    checkout: { label: 'Checkout',    fx: 0.50, fy: 0.70 },
    purchase: { label: 'Purchase',    fx: 0.32, fy: 0.90 },
    exit:     { label: 'Exit',        fx: 0.68, fy: 0.90 }
  };
  var SOURCES = ['paid', 'organic', 'retarget'];

  var canvas, ctx2d, stage, payload;
  var P = {};            // P[tier][from] = [[to, cumWeight], ...]
  var edgeVol = {};      // 'from>to' -> total transitions (all tiers)
  var tierEntries = {};  // tier -> entries total
  var srcEntries = {};   // source -> entries total
  var maxVol = 1;
  var agents = [], acc = 0, flash = {};
  var raf = 0, lastT = 0, inView = true;
  var reduced = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function tok(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }
  function isDarkTheme() {
    var m = /^#([0-9a-fA-F]{6})/.exec(tok('--bg-card'));
    if (!m) { return false; }
    var n = parseInt(m[1], 16);
    return (0.2126 * (n >> 16 & 255) + 0.7152 * (n >> 8 & 255) +
            0.0722 * (n & 255)) < 128;
  }
  function setText(id, value) {
    var el = document.getElementById(id);
    if (el && el.textContent !== String(value)) { el.textContent = value; }
  }

  // ── Measured rates: the filtered rows become the Markov chain ───────
  function rebuild(rows) {
    P = {}; edgeVol = {}; tierEntries = {}; srcEntries = {};
    var raw = {};   // raw[tier][from][to] = n
    var viaWin = 0, viaLoss = 0, purchases = 0;
    var purByTier = {};
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var t = r.spender_tier, f = r.from_node, to = r.to_node;
      var n = r.transitions || 0;
      if (!NODES[f] || !NODES[to]) { continue; }
      raw[t] = raw[t] || {};
      raw[t][f] = raw[t][f] || {};
      raw[t][f][to] = (raw[t][f][to] || 0) + n;
      edgeVol[f + '>' + to] = (edgeVol[f + '>' + to] || 0) + n;
      if (SOURCES.indexOf(f) >= 0) {
        tierEntries[t] = (tierEntries[t] || 0) + n;
        srcEntries[f] = (srcEntries[f] || 0) + n;
      }
      if (to === 'checkout') {
        if (f === 'win') { viaWin += n; } else if (f === 'lose') { viaLoss += n; }
      }
      if (f === 'checkout' && to === 'purchase') {
        purchases += n;
        purByTier[t] = (purByTier[t] || 0) + n;
      }
    }
    Object.keys(raw).forEach(function (t) {
      P[t] = {};
      Object.keys(raw[t]).forEach(function (f) {
        var cum = 0, list = [];
        Object.keys(raw[t][f]).forEach(function (to) {
          cum += raw[t][f][to];
          list.push([to, cum]);
        });
        list.total = cum;
        P[t][f] = list;
      });
    });
    maxVol = 1;
    Object.keys(edgeVol).forEach(function (k) {
      maxVol = Math.max(maxVol, edgeVol[k]);
    });

    // honest counters, straight off the aggregated rows
    var fmtN = window.fmtCompact || function (x) { return String(Math.round(x)); };
    var parts = TIER_ORDER
      .filter(function (t) { return purByTier[t]; })
      .map(function (t) { return fmtN(purByTier[t]) + ' ' + t.replace('_', '-'); });
    setText('jnPur', purchases
      ? fmtN(purchases) + '  (' + parts.join(' · ') + ')' : '0');
    setText('jnLoss', (viaWin + viaLoss)
      ? Math.round(viaLoss / (viaWin + viaLoss) * 100) + '%' : '—');
    var best = null, entriesTotal = 0;
    SOURCES.forEach(function (s) {
      entriesTotal += srcEntries[s] || 0;
      if (best == null || (srcEntries[s] || 0) > (srcEntries[best] || 0)) { best = s; }
    });
    setText('jnSrc', entriesTotal
      ? SRC_LABEL[best] + ' ' +
        Math.round((srcEntries[best] || 0) / entriesTotal * 100) + '%' : '—');

    agents = [];
    flash = {};
    redraw();
  }

  function pickWeighted(list) {
    if (!list || !list.total) { return null; }
    var r = Math.random() * list.total;
    for (var i = 0; i < list.length; i++) {
      if (r < list[i][1]) { return list[i][0]; }
    }
    return list[list.length - 1][0];
  }
  function pickTier() {
    var total = 0, t;
    for (t in tierEntries) { total += tierEntries[t]; }
    if (!total) { return null; }
    var r = Math.random() * total, cum = 0;
    for (t in tierEntries) {
      cum += tierEntries[t];
      if (r < cum) { return t; }
    }
    return null;
  }
  function pickSource(tier) {
    // entries per source for this tier come from the chain itself
    var list = [], cum = 0;
    SOURCES.forEach(function (s) {
      var out = P[tier] && P[tier][s];
      if (out && out.total) { cum += out.total; list.push([s, cum]); }
    });
    list.total = cum;
    return pickWeighted(list);
  }

  // ── Metro geometry ───────────────────────────────────────────────────
  function layout(W, H) {
    var ns = {};
    Object.keys(NODES).forEach(function (k) {
      ns[k] = { x: NODES[k].fx * W, y: NODES[k].fy * H, label: NODES[k].label };
    });
    return ns;
  }
  function isUp(f, to) { return NODES[to].fy < NODES[f].fy; }
  function ctrls(f, to, ns, W) {
    var a = ns[f], b = ns[to];
    if (f === 'purchase') {                    // the return rail, far left
      return { x1: 0.05 * W, y1: a.y, x2: 0.05 * W, y2: b.y };
    }
    var my = (a.y + b.y) / 2;
    return { x1: a.x, y1: my, x2: b.x, y2: my };
  }
  function pointAt(f, to, ns, W, t) {
    var a = ns[f], b = ns[to];
    var c = ctrls(f, to, ns, W);
    var u = 1 - t;
    return {
      x: u * u * u * a.x + 3 * u * u * t * c.x1 + 3 * u * t * t * c.x2 + t * t * t * b.x,
      y: u * u * u * a.y + 3 * u * u * t * c.y1 + 3 * u * t * t * c.y2 + t * t * t * b.y
    };
  }

  // ── Agents: a random walk on the measured chain ──────────────────────
  function stepAgents(dt, ns, W) {
    acc += dt * 11;
    while (acc >= 1 && agents.length < 140) {
      acc -= 1;
      var tier = pickTier();
      if (!tier) { break; }
      var src = pickSource(tier);
      if (!src) { break; }
      var a = { tier: tier, node: src, to: null, t: 0 };
      if (!advance(a)) { continue; }
      agents.push(a);
    }
    var keep = [];
    for (var i = 0; i < agents.length; i++) {
      var ag = agents[i];
      var p0 = pointAt(ag.node, ag.to, ns, W, ag.t);
      var p1 = pointAt(ag.node, ag.to, ns, W, Math.min(1, ag.t + 0.05));
      var seglen = Math.max(4, Math.hypot(p1.x - p0.x, p1.y - p0.y)) / 0.05;
      ag.t += 118 * dt / seglen;
      if (ag.t >= 1) {
        var arrived = ag.to;
        flash[arrived] = 1;
        if (arrived === 'exit') { continue; }
        ag.node = arrived;
        if (!advance(ag)) { continue; }
      }
      keep.push(ag);
    }
    agents = keep;
    Object.keys(flash).forEach(function (k) {
      flash[k] = Math.max(0, flash[k] - dt * 2.4);
    });
  }
  function advance(a) {
    var nxt = pickWeighted(P[a.tier] && P[a.tier][a.node]);
    if (!nxt) { return false; }
    a.to = nxt;
    a.t = 0;
    return true;
  }

  // ── Drawing ──────────────────────────────────────────────────────────
  function drawMap(W, H, animated) {
    var ns = layout(W, H);
    var dark = isDarkTheme();
    var cMuted = tok('--text-secondary'), cBorder = tok('--border-color');
    var cInk = tok('--text-main'), cGold = tok('--accent-yellow');
    var cBad = tok('--accent-red');
    var fontFam = getComputedStyle(document.body).fontFamily;
    ctx2d.clearRect(0, 0, W, H);

    var keys = Object.keys(edgeVol);
    if (!keys.length) {
      ctx2d.fillStyle = cMuted;
      ctx2d.font = '13px ' + fontFam;
      ctx2d.fillText('No transitions match the current filters.', 20, 40);
      return;
    }

    // wires: dashed when they move back up the map
    keys.forEach(function (key) {
      var parts = key.split('>');
      var f = parts[0], to = parts[1];
      if (!NODES[f] || !NODES[to]) { return; }
      var a = ns[f], b = ns[to];
      var c = ctrls(f, to, ns, W);
      var toExit = to === 'exit';
      ctx2d.strokeStyle = toExit ? cBad : cMuted;
      if (animated) {
        ctx2d.globalAlpha = toExit ? 0.10 : 0.14;
        ctx2d.lineWidth = 2;
      } else {
        // reduced motion: the same graph as a static flow map
        ctx2d.globalAlpha = toExit ? 0.25 : 0.35;
        ctx2d.lineWidth = 1.5 + 11 * Math.sqrt(edgeVol[key] / maxVol);
      }
      ctx2d.lineCap = 'round';
      ctx2d.setLineDash(isUp(f, to) ? [4, 5] : []);
      ctx2d.beginPath();
      ctx2d.moveTo(a.x, a.y);
      ctx2d.bezierCurveTo(c.x1, c.y1, c.x2, c.y2, b.x, b.y);
      ctx2d.stroke();
    });
    ctx2d.setLineDash([]);
    ctx2d.globalAlpha = 1;

    // the return rail, named
    ctx2d.fillStyle = cMuted;
    ctx2d.font = '10.5px ' + fontFam;
    ctx2d.save();
    ctx2d.translate(0.05 * W - 8, 0.58 * H);
    ctx2d.rotate(-Math.PI / 2);
    ctx2d.textAlign = 'center';
    ctx2d.fillText('a purchase loops back into the game →', 0, 0);
    ctx2d.restore();

    // agents
    if (animated) {
      for (var d = 0; d < agents.length; d++) {
        var q = agents[d];
        var pos = pointAt(q.node, q.to, ns, W, q.t);
        var tail = pointAt(q.node, q.to, ns, W, Math.max(0, q.t - 0.045));
        var col = tok(TIER_TOKEN[q.tier]) || cMuted;
        ctx2d.strokeStyle = col;
        ctx2d.globalAlpha = dark ? 0.55 : 0.45;
        ctx2d.lineWidth = 2.4;
        ctx2d.lineCap = 'round';
        ctx2d.beginPath();
        ctx2d.moveTo(tail.x, tail.y); ctx2d.lineTo(pos.x, pos.y);
        ctx2d.stroke();
        ctx2d.globalAlpha = dark ? 0.95 : 0.85;
        ctx2d.fillStyle = col;
        ctx2d.beginPath(); ctx2d.arc(pos.x, pos.y, 2.6, 0, 6.2832);
        ctx2d.fill();
      }
      ctx2d.globalAlpha = 1;
    }

    // station bars
    ctx2d.font = '600 11.5px ' + fontFam;
    ctx2d.textAlign = 'center';
    ctx2d.textBaseline = 'middle';
    Object.keys(ns).forEach(function (k) {
      var nd = ns[k];
      var hot = k === 'purchase';
      var w = ctx2d.measureText(nd.label).width + 30;
      var h = 26, r = 5;
      if (flash[k] > 0) {
        ctx2d.strokeStyle = hot ? cGold : tok('--accent-blue');
        ctx2d.globalAlpha = flash[k] * 0.55;
        ctx2d.lineWidth = 2;
        ctx2d.beginPath();
        ctx2d.roundRect(nd.x - w / 2 - 3, nd.y - h / 2 - 3, w + 6, h + 6, r + 3);
        ctx2d.stroke();
        ctx2d.globalAlpha = 1;
      }
      ctx2d.fillStyle = tok('--bg-card');
      ctx2d.strokeStyle = hot ? cGold : cBorder;
      ctx2d.lineWidth = hot ? 1.5 : 1;
      ctx2d.beginPath();
      ctx2d.roundRect(nd.x - w / 2, nd.y - h / 2, w, h, r);
      ctx2d.fill(); ctx2d.stroke();
      ctx2d.fillStyle = hot ? cGold : cMuted;
      ctx2d.beginPath();
      ctx2d.arc(nd.x - w / 2 + 10, nd.y, 3, 0, 6.2832); ctx2d.fill();
      ctx2d.fillStyle = k === 'exit' ? cMuted : cInk;
      ctx2d.fillText(nd.label, nd.x + 4, nd.y + 0.5);
    });
    ctx2d.textBaseline = 'alphabetic';

    if (!animated) {
      ctx2d.textAlign = 'left';
      ctx2d.font = '11px ' + fontFam;
      ctx2d.fillStyle = cMuted;
      ctx2d.fillText('Reduced motion: edge width is measured volume.', 12, 16);
    }
  }

  // ── Loop ─────────────────────────────────────────────────────────────
  function alive() { return inView && !document.hidden; }
  function step(t) {
    raf = 0;
    if (!alive() || reduced) { return; }
    var dt = lastT ? Math.min(0.05, (t - lastT) / 1000) : 0.016;
    lastT = t;
    var W = canvas.clientWidth, H = canvas.clientHeight;
    stepAgents(dt, layout(W, H), W);
    drawMap(W, H, true);
    raf = requestAnimationFrame(step);
  }
  function redraw() {
    var W = canvas.clientWidth, H = canvas.clientHeight;
    if (reduced) { drawMap(W, H, false); return; }
    if (!raf && alive()) { lastT = 0; raf = requestAnimationFrame(step); }
  }

  function fitCanvas() {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(canvas.clientWidth * dpr);
    canvas.height = Math.round(canvas.clientHeight * dpr);
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (reduced) { drawMap(canvas.clientWidth, canvas.clientHeight, false); }
  }

  function boot() {
    payload = (window._reportData || {})._journey;
    canvas = document.getElementById('jnCanvas');
    stage = document.getElementById('jnStage');
    if (!canvas || !payload) { return; }
    ctx2d = canvas.getContext('2d');
    fitCanvas();
    if (window.ResizeObserver) { new ResizeObserver(fitCanvas).observe(stage); }
    document.addEventListener('visibilitychange', redraw);
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        inView = entries[0].isIntersecting;
        redraw();
      }, { threshold: 0.05 }).observe(stage);
    }
    fw.filterEngine.subscribe(DS, 'journey-map', rebuild);
    rebuild(fw.filterEngine.getFiltered(DS) || []);
  }

  function whenReady(attempts) {
    attempts = (attempts == null) ? 120 : attempts;
    if (window.fw && fw.filterEngine && window._reportData &&
        window._reportData._journey) {
      fw.filterEngine.onReady
        ? fw.filterEngine.onReady(DS, boot)
        : boot();
      return;
    }
    if (attempts <= 0) {
      console.warn('[monetization] journey section gave up waiting for fw');
      return;
    }
    setTimeout(function () { whenReady(attempts - 1); }, 50);
  }
  whenReady();

  var previous = window.renderAll;
  window.renderAll = function () {
    if (typeof previous === 'function') { previous.apply(this, arguments); }
    redraw();
  };
  window.addEventListener('fw-theme-change', function () { redraw(); });
})();
"""

"""The two custom sections player-overview absorbed in the consolidation.

Both arrived from standalone reports (revenue-map, player-segments) with their
rationale intact -- see each block's comments. The venn subscribes to the
`segments` DataSource and stays live under its section filters; the map is a
static payload keyed `_revenue_map`, with the ranked country bar beside it
staying live. Extra client libraries come from extra_cdn in report.yaml.
"""

# == Choropleth (from revenue-map) =======================================

MAP_HTML = """
<style>
  .fw-custom-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;
    color: var(--text-secondary); border: 1px dashed var(--border-color);
    border-radius: 4px; padding: 3px 8px; margin-bottom: 10px;
  }
  .fw-custom-badge b { color: var(--text-main); font-weight: 600; }
</style>
<div class="fw-custom-badge"><b>Custom section</b> · RawHTML + chartjs-chart-geo via extra_cdn — the component library has no map; the atlas is fetched at runtime</div>
<style>
  .rm-wrap { position: relative; height: 460px; }
  .rm-note { margin-top: 8px; font-size: 11px; color: var(--text-secondary); }
  .rm-miss { color: var(--accent-red); }
</style>
<div class="rm-wrap"><canvas id="rmMap"></canvas></div>
<div class="rm-note" id="rmNote"></div>
"""

MAP_JS = """
(function () {
  var chart = null;
  var atlas = null;          // cached across redraws; it never changes

  function paint(countries, payload) {
    var el = document.getElementById('rmMap');
    if (!el) { return; }

    var colours = fw.getThemeColors();
    var byName = payload.by_country || {};
    var max = payload.max || 1;

    // Every shape gets a value, including the ones with no data -- Chart.js
    // needs a complete dataset, and a missing country should read as "no
    // revenue" rather than vanishing.
    var points = countries.map(function (f) {
      return { feature: f, value: byName[f.properties.name] || 0 };
    });

    if (chart) { chart.destroy(); }
    chart = new Chart(el.getContext('2d'), {
      type: 'choropleth',
      data: {
        labels: countries.map(function (f) { return f.properties.name; }),
        datasets: [{
          label: payload.label || 'Revenue',
          outline: countries,
          data: points
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        showOutline: true,
        showGraticule: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (c) {
                var v = c.raw.value;
                return c.raw.feature.properties.name + ': ' +
                       (v ? payload.prefix + v.toLocaleString() : 'no data');
              }
            }
          }
        },
        scales: {
          projection: { axis: 'x', projection: 'naturalEarth1' },
          // Colours come from the live theme, so the map re-shades on a theme
          // switch. A baked palette would also trip rawhtml-hardcoded-hex.
          color: {
            axis: 'x',
            quantize: 5,
            interpolate: function (v) {
              return 'color-mix(in srgb, ' + colours.chart_colors[0] + ' ' +
                     Math.round(8 + v * 84) + '%, transparent)';
            },
            legend: { position: 'bottom-right', align: 'right' },
            max: max,
            // Raw values overflow the legend and get clipped mid-number --
            // "154,27" reads as a broken figure rather than a large one.
            ticks: {
              callback: function (v) {
                if (v >= 1e6) { return payload.prefix + (v / 1e6).toFixed(1) + 'M'; }
                if (v >= 1e3) { return payload.prefix + Math.round(v / 1e3) + 'k'; }
                return payload.prefix + Math.round(v);
              }
            }
          }
        }
      }
    });

    // Which warehouse countries the atlas could not place. Only the client can
    // tell -- it is the side holding the atlas -- and a choropleth that
    // silently drops a country is the classic way this kind of chart lies.
    var known = {};
    countries.forEach(function (f) { known[f.properties.name] = true; });
    var unmatched = Object.keys(byName).filter(function (n) { return !known[n]; });

    var note = document.getElementById('rmNote');
    if (note) {
      note.innerHTML = unmatched.length
        ? 'Not on the map: <span class="rm-miss">' + unmatched.join(', ') +
          '</span> \\u2014 the warehouse name does not match the atlas name. ' +
          'Add it to _ATLAS_NAMES in generator.py.'
        : 'Shaded by ' + (payload.label || 'value').toLowerCase() +
          ' over the selected period.';
    }
  }

  function draw() {
    var payload = (window._reportData || {})._revenue_map;
    if (!payload || !window.Chart || !window.ChartGeo || !window.topojson) {
      return;
    }
    if (atlas) { paint(atlas, payload); return; }

    // The atlas is a static asset, not report data -- fetched once from the
    // vendor route the framework already serves, then cached.
    fetch(new URL('countries-110m.json',
      document.querySelector('script[src$="topojson-client.min.js"]').src))
      .then(function (r) { return r.json(); })
      .then(function (topo) {
        atlas = ChartGeo.topojson.feature(topo, topo.objects.countries).features;
        paint(atlas, payload);
      })
      .catch(function (e) {
        console.warn('[revenue-map] could not load the world atlas', e);
      });
  }

  // First draw: renderAll does not fire on initial load, and data.json arrives
  // asynchronously, so poll briefly for both the data and the plugins.
  function whenReady(fn, attempts) {
    attempts = (attempts == null) ? 120 : attempts;
    if (window._reportData && window._reportData._revenue_map &&
        window.fw && window.ChartGeo && window.topojson) { fn(); return; }
    if (attempts <= 0) {
      console.warn('[revenue-map] gave up waiting -- are chartjs_geo and ' +
                   'topojson_client in report.yaml extra_cdn?');
      return;
    }
    setTimeout(function () { whenReady(fn, attempts - 1); }, 50);
  }
  whenReady(draw);

  // Every redraw after that, chained so nothing already installed is dropped.
  var previous = window.renderAll;
  window.renderAll = function () {
    if (typeof previous === 'function') { previous.apply(this, arguments); }
    draw();
  };

  // Theme switches are a separate event; without this the map keeps the old
  // palette until something else redraws it.
  window.addEventListener('fw-theme-change', function () { draw(); });
})();
"""


# == Venn (from player-segments) =========================================

VENN_HTML = """
<style>
  .fw-custom-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;
    color: var(--text-secondary); border: 1px dashed var(--border-color);
    border-radius: 4px; padding: 3px 8px; margin-bottom: 10px;
  }
  .fw-custom-badge b { color: var(--text-main); font-weight: 600; }
</style>
<div class="fw-custom-badge"><b>Custom section</b> · RawHTML + chartjs-chart-venn via extra_cdn — recounts every intersection live from the filter engine</div>
<style>
  .ps-wrap { position: relative; height: 460px; }
  .ps-stats {
    display: flex; flex-wrap: wrap; gap: 20px;
    margin-top: 10px; font-size: 12px; color: var(--text-secondary);
  }
  .ps-stat b { color: var(--text-main); font-variant-numeric: tabular-nums; }
  .ps-note { margin-top: 8px; font-size: 11px; color: var(--text-secondary); }
  .ps-empty { color: var(--accent-red); }
</style>
<div class="ps-wrap"><canvas id="psVenn"></canvas></div>
<div class="ps-stats" id="psStats"></div>
<div class="ps-note" id="psNote"></div>
"""

VENN_JS = """
(function () {
  var chart = null;
  var DS = 'segments';

  // The order chartjs-chart-venn's own extractSets emits for three sets, as
  // bitmasks over the ordered segment list. Matching it keeps the region
  // colours and the layout in agreement.
  var LAYOUT = [
    [0], [1], [2], [0, 1], [0, 2], [1, 2], [0, 1, 2]
  ];

  function maskOf(indices) {
    var m = 0;
    for (var i = 0; i < indices.length; i++) { m |= (1 << indices[i]); }
    return m;
  }

  /* Exclusive region counts, straight from raw membership rows.
     One pass to fold each player's behaviours into a bitmask, one pass to
     count the masks. A player with all three lands in exactly one bucket --
     which is what the plugin wants, and the opposite of the inclusive
     intersections a Venn is normally described with. */
  function countRegions(rows, order) {
    var bit = {};
    for (var i = 0; i < order.length; i++) { bit[order[i]] = 1 << i; }

    var byPlayer = {};
    for (var r = 0; r < rows.length; r++) {
      var b = bit[rows[r].segment];
      if (b === undefined) { continue; }   // a 4th behaviour, not drawn
      var u = rows[r].user_id;
      byPlayer[u] = (byPlayer[u] || 0) | b;
    }

    var counts = {};
    var players = 0;
    for (var u2 in byPlayer) {
      if (!Object.prototype.hasOwnProperty.call(byPlayer, u2)) { continue; }
      counts[byPlayer[u2]] = (counts[byPlayer[u2]] || 0) + 1;
      players += 1;
    }
    return { counts: counts, players: players };
  }

  /* Overlap colours are mixed, not stacked. The plugin fills each region as
     its own opaque slice, so the usual translucent-circles effect has to be
     built: blend the palette entries the region belongs to, then thin the
     result by degree so deeper overlaps read as denser. Mixing through
     color-mix keeps the values coming from the live theme rather than baked
     hexes, which would also trip rawhtml-hardcoded-hex. */
  function regionColour(indices, palette) {
    var mixed = palette[indices[0] % palette.length];
    for (var i = 1; i < indices.length; i++) {
      mixed = 'color-mix(in srgb, ' + mixed + ', ' +
              palette[indices[i] % palette.length] + ')';
    }
    var strength = [0, 28, 46, 62][indices.length] || 40;
    return 'color-mix(in srgb, ' + mixed + ' ' + strength + '%, transparent)';
  }

  /* The region counts are drawn *on* the fills, so they need the theme's
     primary text colour. getThemeColors().tick_color is the axis colour -- a
     muted grey that vanishes against a saturated region on a dark theme, which
     is exactly how this was first got wrong. */
  function textColour() {
    // The theme token is --text-main. A wrong custom-property name resolves to
    // the empty string rather than throwing, and Chart.js then falls back to
    // its own default grey -- which looks deliberate and is unreadable on half
    // the themes. Fall back to the page's own colour if the token ever moves.
    var token = getComputedStyle(document.documentElement)
                  .getPropertyValue('--text-main').trim();
    return token || getComputedStyle(document.body).color;
  }

  function draw(rows) {
    var payload = (window._reportData || {})._player_segments;
    var el = document.getElementById('psVenn');
    if (!el || !payload || !payload.order || payload.order.length < 2) { return; }

    // A missing plugin fails inside Chart.js with an opaque error, so name the
    // actual cause -- this is the line that gets read when extra_cdn is wrong.
    if (!(window.Chart && Chart.registry.controllers.get('venn'))) {
      console.warn('[player-segments] venn plugin not loaded -- is ' +
                   'extra_cdn.chartjs_venn missing from report.yaml?');
      return;
    }

    var order = payload.order;
    var colours = fw.getThemeColors();
    var palette = colours.chart_colors || [];
    var res = countRegions(rows, order);

    var data = [], labels = [], background = [];
    for (var i = 0; i < LAYOUT.length; i++) {
      var idx = LAYOUT[i];
      var names = idx.map(function (n) { return order[n]; });
      data.push({ sets: names, value: res.counts[maskOf(idx)] || 0 });
      labels.push(names.join(' \\u2229 '));
      background.push(regionColour(idx, palette));
    }

    if (chart) { chart.destroy(); }
    chart = new Chart(el.getContext('2d'), {
      type: 'venn',
      data: {
        labels: labels,
        datasets: [{
          label: 'Players',
          data: data,
          backgroundColor: background,
          borderColor: colours.grid_color
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // Both scales are hidden (the plugin sets display:false), but their
        // tick options are still what it draws text with: the x ticks colour
        // and format the number inside each region, the y ticks do the set
        // names around the circles. Nothing on the dataset controls this --
        // setting `color` there looks right and does nothing.
        scales: {
          x: {
            ticks: {
              color: textColour(),
              // The plugin's default callback is bare String(), so a five
              // figure region renders as "13927".
              callback: function (v) { return Number(v).toLocaleString(); }
            }
          },
          y: { ticks: { color: textColour() } }
        },
        plugins: {
          legend: { display: false },
          fwLegend: { display: false },
          // The plugin prints the value inside each region itself; the global
          // datalabels plugin would print a second copy on top of it.
          datalabels: { display: false },
          tooltip: {
            callbacks: {
              label: function (c) {
                var v = c.raw.value || 0;
                var pct = res.players ? (v / res.players * 100).toFixed(1) : '0';
                return v.toLocaleString() + ' players (' + pct + '%) -- ' +
                       'exactly this combination';
              }
            }
          }
        }
      }
    });

    renderStats(res, order, payload);
  }

  /* Set sizes are inclusive -- "spenders" means all of them, overlaps
     included -- while the diagram's regions are exclusive. Showing both, and
     saying which is which, is the point: the gap between the two is what the
     Venn exists to make visible. */
  function renderStats(res, order, payload) {
    var stats = document.getElementById('psStats');
    var note = document.getElementById('psNote');
    if (!stats) { return; }

    if (!res.players) {
      stats.innerHTML = '<span class="ps-empty">No players match ' +
                        'the current filters.</span>';
      if (note) { note.textContent = ''; }
      return;
    }

    var parts = ['<span class="ps-stat">Players in view: <b>' +
                 res.players.toLocaleString() + '</b></span>'];
    for (var i = 0; i < order.length; i++) {
      var total = 0;
      for (var m in res.counts) {
        if (m & (1 << i)) { total += res.counts[m]; }
      }
      parts.push('<span class="ps-stat">' + order[i] + ': <b>' +
                 total.toLocaleString() + '</b> (' +
                 (total / res.players * 100).toFixed(0) + '%)</span>');
    }
    var all = res.counts[(1 << order.length) - 1] || 0;
    parts.push('<span class="ps-stat">All three: <b>' +
               all.toLocaleString() + '</b> (' +
               (all / res.players * 100).toFixed(0) + '%)</span>');
    stats.innerHTML = parts.join('');

    if (note) {
      note.textContent = 'Set totals are inclusive; each region of the ' +
        'diagram counts players with exactly that combination. Base is ' +
        'players doing at least one of the three, not the whole player base.';
    }
  }

  function refresh() {
    if (!(window.fw && fw.filterEngine)) { return; }
    draw(fw.filterEngine.getFiltered(DS) || []);
  }

  // Wait for the data, the plugin and the filter engine's dataset. renderAll
  // does not fire on initial load and data.json arrives asynchronously.
  function whenReady(fn, attempts) {
    attempts = (attempts == null) ? 120 : attempts;
    if (window._reportData && window._reportData._player_segments &&
        window.fw && fw.filterEngine && window.Chart &&
        Chart.registry.controllers.get('venn') &&
        fw.filterEngine.getFiltered(DS)) { fn(); return; }
    if (attempts <= 0) {
      console.warn('[player-segments] gave up waiting -- is chartjs_venn in ' +
                   'report.yaml extra_cdn, and is the "' + DS + '" DataSource ' +
                   'declared?');
      return;
    }
    setTimeout(function () { whenReady(fn, attempts - 1); }, 50);
  }

  whenReady(function () {
    refresh();
    // The reason this section is here: the filter engine hands subscribers the
    // filtered rows, so the diagram recounts instead of going stale.
    fw.filterEngine.subscribe(DS, 'player-segments-venn', function (rows) {
      draw(rows || []);
    });
  });

  // Cold load and URL-preloaded state go through _renderComps, not renderAll,
  // so both hooks are needed. rAF because _updateToggleVis flips display
  // synchronously and the canvas would still measure 0x0 this tick.
  window._initToggleVis = (function (previous) {
    return function () {
      if (typeof previous === 'function') { previous.apply(this, arguments); }
      requestAnimationFrame(refresh);
    };
  })(window._initToggleVis);

  var previousRenderAll = window.renderAll;
  window.renderAll = function () {
    if (typeof previousRenderAll === 'function') {
      previousRenderAll.apply(this, arguments);
    }
    requestAnimationFrame(refresh);
  };

  // Theme switches are a separate event; without this the regions keep the
  // old palette until something else redraws them.
  window.addEventListener('fw-theme-change', function () { refresh(); });
})();
"""

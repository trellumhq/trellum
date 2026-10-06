    // ── Chart.js Defaults ─────────────────────────────────
    if (typeof Chart !== 'undefined') {
        var _initTheme = getThemeColors();
        // Match the actual display DPR, but floor at 2 so DevTools device
        // emulation (which can report DPR=1) still gets a crisp backing store.
        // The floor is deliberate and stays: a report is read on phones and
        // high-density screens and exported as PNG, none of which report the
        // authoring machine's ratio, so the canvas is always drawn at 2x at
        // least. On a 1x display that measures very slightly softer than
        // drawing at 1x -- that is the price, not a bug. A chart that looks
        // blown up is a chart in the wrong-sized box; fix the box.
        Chart.defaults.devicePixelRatio = Math.max(2, window.devicePixelRatio || 1);
        // Default to 'nearest' so the tooltip shows only the series the
        // user is actually hovering -- less visual noise on multi-line
        // time series. Charts that want all-series-at-x behaviour can
        // override with `interaction: { mode: 'index' }`.
        Chart.defaults.interaction = { mode: 'nearest', intersect: false };
        Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(0,0,0,0.85)';

        // ── HTML tooltip (framework-wide) ──────────────────
        // Chart.js draws its default tooltip on the canvas via fillText.
        // On high-DPI displays the rasterized text looks soft next to
        // vector-drawn chart lines, because canvas text skips the browser's
        // subpixel font hinting. Replacing it with an absolutely-positioned
        // HTML div gets the browser's native, ClearType-quality rendering
        // for free. Chart.js still computes position, labels, and colors —
        // we only swap the render layer.
        (function _installHtmlTooltip() {
            if (document.getElementById('fwChartTooltipStyle')) return;
            var style = document.createElement('style');
            style.id = 'fwChartTooltipStyle';
            style.textContent = [
                '.fw-chart-tooltip{position:absolute;pointer-events:none;z-index:9999;',
                'background:rgba(0,0,0,0.85);color:#fff;padding:8px 10px;border-radius:6px;',
                'font-size:var(--font-size-tooltip,12px);line-height:1.5;font-family:inherit;white-space:nowrap;',
                'max-width:360px;box-shadow:0 2px 8px rgba(0,0,0,0.25);',
                // Anchor is the tooltip's top-left; we position left/top at
                // cursor + small offset. No transform/flip logic needed.
                'opacity:0;transition:opacity 0.1s;}',
                '.fw-chart-tooltip .fw-tt-title{font-weight:600;margin-bottom:4px;font-size:var(--font-size-tooltip,12px);',
                'opacity:0.9;}',
                '.fw-chart-tooltip .fw-tt-line{display:flex;align-items:center;gap:6px;}',
                '.fw-chart-tooltip .fw-tt-swatch{width:10px;height:10px;border-radius:2px;',
                'flex-shrink:0;display:inline-block;}',
            ].join('');
            document.head.appendChild(style);
        })();

        var _fwTooltipEl = null;
        function _fwTooltipElGet() {
            if (_fwTooltipEl && document.body.contains(_fwTooltipEl)) return _fwTooltipEl;
            _fwTooltipEl = document.createElement('div');
            _fwTooltipEl.className = 'fw-chart-tooltip';
            document.body.appendChild(_fwTooltipEl);
            return _fwTooltipEl;
        }

        function _fwEscapeHtml(s) {
            return String(s).replace(/[&<>"']/g, function(c) {
                return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
            });
        }

        // Pick the tooltip swatch colour that actually matches what the user
        // sees on the chart. Line charts use a translucent backgroundColor
        // (e.g. "#FFD60020") for the area fill and a solid borderColor for
        // the line — the borderColor is what reads as "the colour". Treemap,
        // sankey, bar and doughnut put the solid colour in backgroundColor
        // and often leave borderColor unset or as a thin accent stroke.
        // Strategy: pick whichever side looks opaque.
        function _fwIsTransparent(c) {
            if (!c) return true;
            if (c === 'transparent') return true;
            var m = /^#[0-9a-fA-F]{8}$/.exec(c);
            if (m) return parseInt(c.slice(7, 9), 16) < 0xC0;  // alpha < 0.75
            m = /^rgba\([^)]*?,\s*([\d.]+)\s*\)$/.exec(c);
            if (m) return parseFloat(m[1]) < 0.75;
            return false;
        }
        function _fwSwatchColor(lc, dp) {
            // Primary: tooltip.labelColors[i], populated by most chart types.
            if (lc) {
                var bg = lc.backgroundColor;
                var bc = lc.borderColor;
                if (bg && !_fwIsTransparent(bg)) return bg;
                if (bc && !_fwIsTransparent(bc)) return bc;
            }
            // Fallback: read from the data point's element (sankey flows,
            // custom plots, or plugins that skip populating labelColors).
            if (dp) {
                if (dp.element && dp.element.options) {
                    var eo = dp.element.options;
                    if (eo.backgroundColor && !_fwIsTransparent(eo.backgroundColor)) return eo.backgroundColor;
                    if (eo.borderColor && !_fwIsTransparent(eo.borderColor)) return eo.borderColor;
                }
                if (dp.dataset) {
                    var dsBg = dp.dataset.backgroundColor;
                    if (Array.isArray(dsBg)) dsBg = dsBg[dp.dataIndex];
                    if (typeof dsBg === 'string' && !_fwIsTransparent(dsBg)) return dsBg;
                    var dsBc = dp.dataset.borderColor;
                    if (Array.isArray(dsBc)) dsBc = dsBc[dp.dataIndex];
                    if (typeof dsBc === 'string' && !_fwIsTransparent(dsBc)) return dsBc;
                }
            }
            return (lc && (lc.backgroundColor || lc.borderColor)) || 'transparent';
        }

        // Use the Y-axis tick formatter for tooltip values so currency,
        // percentages, compact units etc. carry over automatically. Charts
        // that want a different tooltip format can still override this via
        // `plugins.tooltip.callbacks.label` per-chart.
        Chart.defaults.plugins.tooltip.callbacks.label = function(ctx) {
            var dsLabel = ctx.dataset.label || '';
            var value = ctx.parsed.y;
            if (value === null || value === undefined) {
                return dsLabel;
            }
            var scales = ctx.chart.scales || {};
            // Pick the Y scale the dataset is actually bound to (handles
            // dual-axis combo charts where some datasets use y1).
            var yAxisId = ctx.dataset.yAxisID || 'y';
            var yScale = scales[yAxisId] || scales.y;
            var formatted = null;
            if (yScale && yScale.options && yScale.options.ticks &&
                typeof yScale.options.ticks.callback === 'function') {
                try {
                    formatted = yScale.options.ticks.callback.call(yScale, value, 0, []);
                } catch (e) { formatted = null; }
            }
            if (formatted === null || formatted === undefined) {
                try { formatted = new Intl.NumberFormat().format(value); }
                catch (e) { formatted = String(value); }
            }
            return dsLabel ? dsLabel + ': ' + formatted : String(formatted);
        };

        Chart.defaults.plugins.tooltip.enabled = false;

        // Guard: if a per-chart config sets `tooltip: {enabled: true}` (older
        // reports written before we had an HTML tooltip) without also setting
        // `external: null`, Chart.js will draw BOTH the canvas tooltip and
        // call our HTML external → two overlapping tooltips. Before every
        // update, force enabled=false on any chart that still inherits an
        // external callback so only the HTML one renders.
        Chart.register({
            id: '_fwTooltipNoDouble',
            beforeUpdate: function(chart) {
                var opts = chart.options && chart.options.plugins && chart.options.plugins.tooltip;
                if (opts && opts.external && opts.enabled !== false) {
                    opts.enabled = false;
                }
            },
        });

        Chart.defaults.plugins.tooltip.external = function(context) {
            var tooltip = context.tooltip;
            var el = _fwTooltipElGet();

            // Chart.js signals "hide" with opacity === 0; skip updating the DOM.
            if (!tooltip || tooltip.opacity === 0) {
                el.style.opacity = '0';
                return;
            }

            var titleLines = tooltip.title || [];
            var bodyItems = tooltip.body || [];
            var labelColors = tooltip.labelColors || [];

            var html = '';
            if (titleLines.length) {
                html += '<div class="fw-tt-title">' + _fwEscapeHtml(titleLines.join(' ')) + '</div>';
            }
            for (var i = 0; i < bodyItems.length; i++) {
                var lines = (bodyItems[i].lines || []).map(_fwEscapeHtml).join('<br>');
                var lc = labelColors[i] || {};
                var dp = tooltip.dataPoints && tooltip.dataPoints[i];
                var swatch = _fwSwatchColor(lc, dp);
                html += '<div class="fw-tt-line">'
                     +   '<span class="fw-tt-swatch" style="background:' + _fwEscapeHtml(swatch) + '"></span>'
                     +   '<span>' + lines + '</span>'
                     + '</div>';
            }
            el.innerHTML = html;

            // Anchor the tooltip's top-left to the data point (caret) +
            // small offset so it reads as "attached" to the point and
            // stays put while the mouse moves within the nearest region.
            var OFFSET_X = 14, OFFSET_Y = 14;
            var canvas = context.chart.canvas;
            var rect = canvas.getBoundingClientRect();
            var anchorX = rect.left + window.scrollX + tooltip.caretX;
            var anchorY = rect.top + window.scrollY + tooltip.caretY;
            var x = anchorX + OFFSET_X;
            var y = anchorY + OFFSET_Y;

            // Right-edge / bottom-edge guard: flip the tooltip to the
            // opposite side of the anchor point if it would overflow.
            var tw = el.offsetWidth || 200, th = el.offsetHeight || 80;
            var viewportRight = window.scrollX + document.documentElement.clientWidth;
            var viewportBottom = window.scrollY + document.documentElement.clientHeight;
            if (x + tw > viewportRight) x = anchorX - tw - OFFSET_X;
            if (y + th > viewportBottom) y = anchorY - th - OFFSET_Y;

            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.opacity = '1';
        };
        Chart.defaults.plugins.legend.display = false;
        Chart.defaults.elements.line.tension = 0;
        Chart.defaults.elements.line.borderWidth = 2.5;
        Chart.defaults.elements.point.radius = 1;
        Chart.defaults.color = _initTheme.tick_color;
        Chart.defaults.borderColor = _initTheme.grid_color;
        Chart.defaults.scales = Chart.defaults.scales || {};
        // Register datalabels plugin if available
        if (typeof ChartDataLabels !== 'undefined') {
            Chart.register(ChartDataLabels);
        }
        if (Chart.defaults.plugins.datalabels !== undefined) {
            Chart.defaults.plugins.datalabels.display = false;
        }

        // ── Auto-register charts in _chartInstances ──────────
        // Patches Chart.prototype.update so every chart (including
        // those created by RawHTML custom JS) is automatically
        // registered for theme switching and annotation refresh.
        var _origUpdate = Chart.prototype.update;
        Chart.prototype.update = function() {
            if (this.canvas && this.canvas.id && window._chartInstances[this.canvas.id] !== this) {
                window._chartInstances[this.canvas.id] = this;
                if (_chartObserver && !this.canvas._fwObserved) {
                    _chartObserver.observe(this.canvas);
                    this.canvas._fwObserved = true;
                }
            }
            return _origUpdate.apply(this, arguments);
        };

        // ── fwLegend: global plugin for standardized HTML legends ──
        // Automatically creates clickable HTML legend buttons above any
        // chart with >1 dataset.  Opt out per-chart with:
        //   plugins: { fwLegend: { display: false } }
        Chart.register({
            id: 'fwLegend',
            afterInit: function(chart) {
                var opts = chart.options.plugins && chart.options.plugins.fwLegend;
                if (opts && opts.display === false) return;
                if (chart.data.datasets.length < 2) return;
                // Doughnut/pie legends use position:bottom style
                var isDoughnut = chart.config.type === 'doughnut' || chart.config.type === 'pie';
                if (isDoughnut) return;

                var container = chart.canvas.parentElement;
                var legEl = document.createElement('div');
                legEl.className = 'fw-chart-legend';
                container.parentElement.insertBefore(legEl, container);
                chart._fwLegendEl = legEl;
                this._buildButtons(chart);
            },
            afterUpdate: function(chart) {
                if (!chart._fwLegendEl) return;
                this._buildButtons(chart);
            },
            _buildButtons: function(chart) {
                var legEl = chart._fwLegendEl;
                if (!legEl) return;
                legEl.innerHTML = '';
                var seenLabels = {};
                chart.data.datasets.forEach(function(ds, i) {
                    var lbl = ds.label || 'Series ' + (i+1);
                    if (seenLabels[lbl]) return;
                    seenLabels[lbl] = true;
                    var color = ds.borderColor || ds.backgroundColor;
                    if (typeof color === 'object') color = Array.isArray(color) ? color[0] : '#888';
                    var vis = chart.isDatasetVisible(i);
                    var btn = document.createElement('span');
                    btn.className = 'cl' + (vis ? ' active' : '');
                    btn.style.borderColor = vis ? color : 'var(--border-color)';
                    btn.style.background = vis ? color + '20' : 'transparent';
                    btn.innerHTML = '<span class="dot" style="background:' + color + '"></span>' + lbl;
                    btn.title = 'Click to toggle  •  Double-click (or Shift-click) to show only this';
                    // "Isolate" = hide every series whose label is not `lbl`. If
                    // already isolated to `lbl`, restore visibility on all.
                    var _isolate = function() {
                        var alreadyIsolated = chart.data.datasets.every(function(d, j) {
                            return (d.label === lbl) === chart.isDatasetVisible(j);
                        });
                        chart.data.datasets.forEach(function(d, j) {
                            chart.setDatasetVisibility(j, alreadyIsolated ? true : d.label === lbl);
                        });
                        chart.update();
                    };
                    btn.addEventListener('click', function(e) {
                        if (e.shiftKey || e.metaKey) { _isolate(); return; }
                        var nowVis = chart.isDatasetVisible(i);
                        chart.data.datasets.forEach(function(d, j) {
                            if (d.label === lbl) chart.setDatasetVisibility(j, !nowVis);
                        });
                        chart.update();
                        btn.classList.toggle('active', !nowVis);
                        btn.style.borderColor = !nowVis ? color : 'var(--border-color)';
                        btn.style.background = !nowVis ? color + '20' : 'transparent';
                    });
                    // Double-click / double-tap → isolate. CSS `touch-action:
                    // manipulation` on .cl makes mobile double-taps fire dblclick
                    // instead of the browser's zoom gesture.
                    btn.addEventListener('dblclick', function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        _isolate();
                    });
                    legEl.appendChild(btn);
                });
            },
            beforeDestroy: function(chart) {
                if (chart._fwLegendEl) {
                    chart._fwLegendEl.remove();
                    chart._fwLegendEl = null;
                }
            }
        });
    }

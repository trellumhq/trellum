window._fwRenderers['treemap'] = function renderTreemap(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    var host = canvas.closest('.fw-treemap');
    var context = host && host.querySelector('[data-treemap-context]');
    // Ordered tuples keep dotted names and repeated leaves distinct.
    var identity = JSON.stringify([cfg.dataset_id || null, cfg.group_cols]);
    if (canvas._fwTreemapIdentity !== identity) {
        canvas._fwTreemapIdentity = identity;
        canvas._fwTreemapGroups = new Map();
    }
    var groupIndex = canvas._fwTreemapGroups;
    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var valFmt = fmtMap[cfg.value_format] || fmtCompact;

    function _buildTreemap(rows) {
        rows = rows || [];
        var total = 0;
        var reason = rows.length ? '' : 'Shares unavailable: no supplied values.';
        rows.forEach(function(row) {
            var value = row[cfg.value_col];
            if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
                reason = 'Shares unavailable: values must be complete, finite and nonnegative.';
            } else total += value;
        });
        if (!Number.isFinite(total)) reason = 'Shares unavailable: nonfinite total.';
        if (!reason && total === 0) reason = 'Shares undefined: the current total is zero.';
        var supplied = new WeakMap();
        if (window._chartInstances[id]) {
            window._chartInstances[id].destroy();
            delete window._chartInstances[id];
        }
        var groupCols = cfg.group_cols;
        var valueCol = cfg.value_col;

        var tree = rows.map(function(r, index) {
            var obj = {};
            for (var g = 0; g < groupCols.length; g++) {
                obj[groupCols[g]] = r[groupCols[g]] != null ? String(r[groupCols[g]]) : '';
            }
            obj._value = r[valueCol] || 0;
            if (cfg.color_col && r[cfg.color_col] != null) obj._color = r[cfg.color_col];
            supplied.set(obj, {
                value: r[valueCol],
                text: !cfg.dataset_id && cfg.supplied_values ? cfg.supplied_values[index] : _fwTreemapRaw(r[valueCol])
            });
            if (groupCols.some(function(col) { return obj[col] === ''; })) {
                reason = 'Shares unavailable: blank hierarchy names can suppress legacy leaves.';
            }
            var key = JSON.stringify([obj[groupCols[0]]]);
            if (!groupIndex.has(key)) groupIndex.set(key, groupIndex.size);
            return obj;
        });

        var colorCol = cfg.color_col;
        var rawColorVals = colorCol ? tree.map(function(t) { return t._color; }).filter(function(v) { return v != null; }) : [];
        var colorIsNumeric = rawColorVals.length > 0 && rawColorVals.every(function(v) {
            return typeof v === 'number' || (typeof v === 'string' && v !== '' && !isNaN(Number(v)));
        });
        var colorVals = colorIsNumeric ? rawColorVals.map(Number) : rawColorVals;
        var cMin = colorIsNumeric && colorVals.length ? Math.min.apply(null, colorVals) : 0;
        var cMax = colorIsNumeric && colorVals.length ? Math.max.apply(null, colorVals) : 1;
        var cRange = cMax - cMin || 1;
        /* Legacy encounter-order palette for supplied categorical colors. */
        var catIndex = {};
        if (!colorIsNumeric) {
            var seen = [];
            rawColorVals.forEach(function(v) { if (seen.indexOf(v) === -1) seen.push(v); });
            seen.forEach(function(v, i) { catIndex[v] = i; });
        }

        function _hexToRgb(hex) {
            if (hex.length === 4) hex = '#' + hex[1]+hex[1]+hex[2]+hex[2]+hex[3]+hex[3];
            return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
        }

        function _path(raw) {
            // Group children are original tree rows in bundled plugin 2.3.1.
            // raw.path is display-only: [A.B,C] and [A,B.C] both join to A.B.C.
            var first = raw && raw._data && raw._data.children && raw._data.children[0];
            return first ? groupCols.slice(0, raw.l + 1).map(function(col) { return first[col]; }) : [];
        }
        function _ink(ctx) {
            var bg = _background(ctx), rgb, alpha;
            if (bg[0] === '#') { rgb = _hexToRgb(bg); alpha = parseInt(bg.slice(7, 9) || 'ff', 16) / 255; }
            else { var nums = bg.match(/[\d.]+/g).map(Number); rgb = nums.slice(0, 3); alpha = nums[3]; }
            var card = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#fff';
            var base = card[0] === '#' ? _hexToRgb(card) : (card.match(/[\d.]+/g) || [255,255,255]).slice(0,3).map(Number);
            var lum = rgb.map(function(v, i) {
                var c = (v * alpha + base[i] * (1 - alpha)) / 255;
                return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
            });
            var l = lum[0]*0.2126 + lum[1]*0.7152 + lum[2]*0.0722;
            return (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) ? '#000000' : '#ffffff';
        }
        function _captionFits(ctx) {
            if (!ctx.raw) return false;
            // Keep vendor font/padding/spacing: these reserve hierarchy space.
            // Scriptable display controls paint only, leaving layout unchanged.
            var font = Chart.helpers.toFont({});
            var drawing = ctx.chart.ctx;
            drawing.save(); drawing.font = font.string;
            var width = drawing.measureText(ctx.raw.g || '').width;
            drawing.restore();
            return width + 14 <= ctx.raw.w && font.lineHeight + 13 <= ctx.raw.h;
        }
        function _share(value) {
            return !reason && Number.isFinite(value) ? (value / total * 100).toFixed(1) + '%' : '—';
        }

        function _background(ctx) {
            var pal = getThemeColors().chart_colors;
            var n = pal.length;
            if (!ctx.raw || !ctx.raw._data) return pal[0] + 'dd';
            if (colorCol && ctx.raw._data.children && ctx.raw._data.children[0]) {
                var child = ctx.raw._data.children[0];
                var cv = child._color;
                if (cv == null) {
                    var giNull = ctx.dataIndex % n;
                    return pal[(giNull * 7) % n] + 'dd';
                }
                if (colorIsNumeric) {
                    var t = (Number(cv) - cMin) / cRange;
                    var lo = _hexToRgb(pal[0]);
                    var hi = _hexToRgb(pal[n - 1] || pal[0]);
                    var r = Math.round(lo[0] + (hi[0]-lo[0])*t);
                    var g = Math.round(lo[1] + (hi[1]-lo[1])*t);
                    var b = Math.round(lo[2] + (hi[2]-lo[2])*t);
                    return 'rgba('+r+','+g+','+b+',0.87)';
                }
                /* Categorical: stable color per distinct value */
                var ci = catIndex[cv];
                if (ci == null) ci = 0;
                return pal[ci % n] + 'dd';
            }
            if (!colorCol) {
                var path = _path(ctx.raw);
                return pal[(groupIndex.get(JSON.stringify(path.slice(0, 1))) || 0) % n] + 'dd';
            }
            var gi = ctx.dataIndex % n;
            var pi = (gi * 7) % n;
            return pal[pi] + 'dd';
        }

        window._chartInstances[id] = new Chart(canvas, {
            type: 'treemap',
            data: {
                datasets: [{
                    tree: tree,
                    key: '_value',
                    groups: groupCols,
                    borderWidth: 2,
                    borderColor: function() {
                        var sty = getComputedStyle(document.documentElement);
                        return sty.getPropertyValue('--bg-card').trim() || '#ffffff';
                    },
                    spacing: 2,
                    backgroundColor: _background,
                    captions: { display: _captionFits, color: _ink, hoverColor: _ink },
                    labels: {
                        display: function(ctx) {
                            var r = ctx.raw;
                            return !!r && [r.x, r.y, r.w, r.h].every(Number.isFinite) && r.w > 0 && r.h > 0;
                        },
                        align: 'left',
                        position: 'top',
                        overflow: 'hidden',
                        color: _ink,
                        font: { size: window._fwFontPx('--font-size-label', 12), weight: 700 },
                        formatter: function(ctx) {
                            if (!ctx || !ctx.raw || !ctx.raw._data) return '';
                            var lines = [ctx.raw.g || '', valFmt(ctx.raw.v)];
                            if (cfg.show_percentages && !reason) lines.push(_share(ctx.raw.v));
                            return lines;
                        },
                        hoverColor: _ink
                    }
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: { display: false },
                    fwLegend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function(items) {
                                // Point interaction may include a parent and leaf.
                                // Keep that selection and name its deepest path.
                                var item = items.reduce(function(best, current) {
                                    return !best || current.raw.l > best.raw.l ? current : best;
                                }, null);
                                var raw = item && item.raw;
                                if (!raw || !raw._data) return '';
                                return _path(raw).join(' › ') || raw.g || '';
                            },
                            label: function(ctx) {
                                var raw = ctx.raw;
                                if (!raw) return '';
                                var source = raw._data.children.map(function(row) { return supplied.get(row); });
                                var complete = source.every(function(s) { return s && typeof s.value === 'number' && Number.isFinite(s.value); });
                                var value = complete ? valFmt(raw.v) + ' (exact: ' + String(raw.v) + ')' :
                                    'Supplied: ' + source.map(function(s) { return s ? s.text : 'unknown'; }).join(', ') + ' (legacy size value: ' + String(raw.v) + ')';
                                return (raw.g || '') + ': ' + value +
                                    (!reason ? ' · ' + _share(raw.v) + ' of ' + (cfg.dataset_id ? 'filtered' : 'supplied') + ' total' : ' · ' + reason);
                            }
                        }
                    }
                }
            }
        });
        var chart = window._chartInstances[id];
        chart._fwThemeManagedFonts = true;
        if (!context) return; // Older markup safely keeps its canvas renderer.
        var previous = context.querySelector('details');
        var wasOpen = previous && previous.open;
        var nodes = chart.data.datasets[0].data;
        var leaves = nodes.filter(function(raw) { return raw.l === groupCols.length - 1; });
        var leafTotal = leaves.reduce(function(sum, raw) { return sum + raw.v; }, 0);
        if (!reason && Math.abs(leafTotal - total) > Math.max(1, total) * 1e-12) {
            reason = 'Shares unavailable: legacy leaves do not cover the supplied population.';
        }
        context.replaceChildren();
        function text(tag, value, parent, className) {
            var el = document.createElement(tag); el.textContent = value;
            if (className) el.className = className;
            parent.appendChild(el); return el;
        }
        function pathText(path, parent) {
            path.forEach(function(value, i) {
                if (i) text('span', ' › ', parent);
                text('span', value, parent, 'fw-treemap-path');
            });
        }
        text('p', 'Rectangle area = ' + valueCol + '. Color = ' +
            (colorCol ? 'supplied ' + colorCol + ' field.' : 'first-level ' + groupCols[0] + ' hierarchy group.'), context);
        if (colorCol) {
            text('p', (colorIsNumeric ? 'Numeric' : 'Categorical') + ' color uses the first source child of each rectangle, not an aggregate. ' +
                'Source row order can change colors.' + (colorIsNumeric ? ' No quantitative color legend is implied.' : ' Category palette indices follow encounter order.'), context);
        } else {
            var key = text('ul', '', context, 'fw-treemap-key');
            key.setAttribute('aria-label', 'Hierarchy color key');
            var seenGroups = new Set();
            tree.forEach(function(row) {
                var path = [row[groupCols[0]]], identity = JSON.stringify(path);
                if (seenGroups.has(identity)) return;
                seenGroups.add(identity);
                var item = text('li', '', key);
                item.dataset.path = identity;
                var swatch = text('span', '', item, 'fw-treemap-swatch');
                swatch.setAttribute('aria-hidden', 'true');
                swatch.style.backgroundColor = getThemeColors().chart_colors[groupIndex.get(identity) % getThemeColors().chart_colors.length] + 'dd';
                var name = text('span', '', item, 'fw-treemap-name');
                pathText(path, name);
            });
        }
        text('p', reason || 'Shares include every value in the current ' + (cfg.dataset_id ? 'filtered' : 'supplied') + ' result, including tiny and zero categories; values must be additive.', context);
        var details = text('details', '', context);
        details.open = !!wasOpen;
        text('summary', 'Full-path details (' + leaves.length + ' leaves)', details);
        var scroll = text('div', '', details, 'fw-treemap-details-scroll');
        var table = text('table', '', scroll);
        var head = text('tr', '', text('thead', '', table));
        ['Hierarchy path', 'Value', 'Share of ' + (cfg.dataset_id ? 'filtered' : 'supplied') + ' total'].forEach(function(label) {
            text('th', label, head).scope = 'col';
        });
        var body = text('tbody', '', table);
        leaves.forEach(function(raw) {
            var path = _path(raw), tr = text('tr', '', body);
            tr.dataset.path = JSON.stringify(path);
            tr.dataset.value = String(raw.v);
            var name = text('td', '', tr); pathText(path, name);
            var source = raw._data.children.map(function(row) { return supplied.get(row); });
            var complete = source.every(function(s) { return s && typeof s.value === 'number' && Number.isFinite(s.value); });
            var cell = text('td', complete ? valFmt(raw.v) : 'Unavailable', tr);
            text('span', (complete ? 'Exact: ' + String(raw.v) : 'Supplied: ' + source.map(function(s) { return s ? s.text : 'unknown'; }).join(', ') + '; legacy size value: ' + String(raw.v)), cell, 'fw-treemap-raw');
            text('td', _share(raw.v), tr).title = reason || String(raw.v / total * 100) + '%';
        });
        if (!leaves.length) text('p', 'No hierarchy leaves are available in this result.', details);
        text('p', 'Legacy sizing is unsupported for signed or all-zero values; blank parents can suppress leaves. CSV does not export populated hierarchy leaves.', details);
    }

    var bindings = window._fwTreemapBindings || (window._fwTreemapBindings = {});
    if (bindings[id]) { bindings[id](); delete bindings[id]; }
    if (cfg.dataset_id && window._fwMetricsWorkspace) {
        window._fwLiveWrap(id, cfg, _buildTreemap);
        return;
    }
    var unsubscribe = cfg.dataset_id ? window._fwFilterEngine.subscribe(cfg.dataset_id, id, _buildTreemap) : function() {};
    function drawCurrent() {
        if (document.getElementById(id) !== canvas) {
            bindings[id](); delete bindings[id]; return;
        }
        _buildTreemap(cfg.dataset_id ? window._fwFilterEngine.getFiltered(cfg.dataset_id) : cfg.rows);
    }
    window.addEventListener('fw-theme-change', drawCurrent);
    bindings[id] = function() { unsubscribe(); window.removeEventListener('fw-theme-change', drawCurrent); };
    drawCurrent();
};

function _fwTreemapRaw(value) {
    return value == null ? 'null' : String(value);
}

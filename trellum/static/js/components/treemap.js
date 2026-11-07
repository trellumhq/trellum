/* Reduce completed plugin leaves only. Source membership verifies that legacy
 * grouping retained the supplied input; it never certifies warehouse coverage. */
function _fwTreemapSummaryResult(leaves, tree, rows, cfg) {
    var summary = cfg.header_summary;
    if (!leaves) return {value: null, reason: 'No completed hierarchy result.'};
    if (summary.stat === 'count') return {value: leaves.length, reason: ''};
    var safe = function(v) {
        return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= Number.MAX_SAFE_INTEGER;
    };
    if ((!cfg.dataset_id && cfg.summary_guard && !cfg.summary_guard.values_valid) ||
            rows.some(function(row) { return !safe(row[cfg.value_col]); })) {
        return {value: null, reason: 'Every supplied value must be finite, numeric, nonnegative and safe.'};
    }
    if (!cfg.group_cols.length || tree.some(function(row) {
        return cfg.group_cols.some(function(col) { return !row[col].trim(); });
    })) return {value: null, reason: 'Blank or suppressed hierarchy names.'};
    if (!leaves.length) return {value: null, reason: 'No final leaf entries in current result.'};
    var covered = new Set(), inputs = new Set(tree), complete = true;
    leaves.forEach(function(raw) {
        var children = raw._data && raw._data.children;
        if (!children || !children.length) { complete = false; return; }
        children.forEach(function(child) {
            if (!inputs.has(child) || covered.has(child)) complete = false;
            covered.add(child);
        });
    });
    if (!complete || covered.size !== inputs.size) {
        return {value: null, reason: 'Legacy leaves do not cover supplied hierarchy rows.'};
    }
    if (leaves.some(function(raw) { return !safe(raw.v); })) {
        return {value: null, reason: 'Unsafe aggregate leaf value.'};
    }
    var value, correction = 0;
    if (summary.stat === 'min' || summary.stat === 'max') {
        value = leaves.reduce(function(best, raw) {
            return summary.stat === 'min' ? Math.min(best, raw.v) : Math.max(best, raw.v);
        }, leaves[0].v);
    } else {
        // The unweighted mean needs no potentially overflowing raw sum.
        // Compensated addition in leaf/n units also preserves small zeros.
        value = 0;
        leaves.forEach(function(raw) {
            var term = (summary.stat === 'mean' ? raw.v / leaves.length : raw.v) - correction;
            var next = value + term; correction = (next - value) - term; value = next;
        });
    }
    return safe(value) ? {value: value, reason: ''}
        : {value: null, reason: 'Unsafe aggregate summary result.'};
}

function _fwTreemapSummary(canvas, cfg, result) {
    var host = canvas.closest('.fw-treemap') || canvas._fwTreemapSummaryHost ||
        (canvas.parentElement && canvas.parentElement.parentElement);
    if (!host) return;
    canvas._fwTreemapSummaryHost = host;
    var box = host.querySelector('[data-chart-summary="' + canvas.id + '"]');
    var scopeId = canvas.id + '-treemap-summary-scope', reasonId = canvas.id + '-treemap-summary-reason';
    var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(t) {
        return t && t !== scopeId && t !== reasonId;
    });
    if (cfg.header_summary) {
        if (!box) {
            var container = canvas.parentElement, before = container.previousElementSibling;
            if (before && before.classList.contains('fw-chart-header-context')) before = before.previousElementSibling;
            var head = document.createElement('div'); head.className = 'fw-chart-head';
            if (before && before.classList.contains('fw-chart-title')) {
                before.before(head); head.appendChild(before); before.style.marginBottom = '';
            } else if (container.previousElementSibling && container.previousElementSibling.classList.contains('fw-chart-header-context')) {
                container.previousElementSibling.before(head);
            } else container.before(head);
            box = document.createElement('div'); box.dataset.chartSummary = canvas.id; head.appendChild(box);
        }
        function span(cls, text) {
            var node = document.createElement('span'); node.className = cls; node.textContent = text; return node;
        }
        result = result || {value: null, reason: 'No completed hierarchy result.'};
        var summary = cfg.header_summary;
        var value = result.value == null ? '—' : summary.stat === 'count' ? String(result.value)
            : window.getFormatter(summary.format)(result.value);
        box.className = 'fw-chart-summary fw-treemap-summary';
        var scope = span('fw-treemap-summary-scope', 'Current final leaf entries' +
            (summary.stat === 'count' ? ' · Count independent of numeric eligibility' : ''));
        scope.id = scopeId;
        var description = span('fw-treemap-summary-description',
            'Current supplied or report-filtered final plugin leaf entries after existing grouping. ' +
            'Includes leaves outside the view and without fitting labels; repeated full paths aggregate, identical names under different parents remain separate. ' +
            'Not source facts, visible labels, matrix areas, paths or unique people. ' +
            (summary.stat === 'count' ? 'Count is independent of numeric and geometry validity; it does not claim all source categories survived blank-parent suppression. ' :
                'All raw values and final leaves must be finite, numeric, nonnegative and safe; all supplied hierarchy rows must be covered. ' +
                (summary.stat === 'mean' ? 'Unweighted arithmetic mean of aggregate leaf entries. ' : '')) +
            'No warehouse completeness or precision beyond the supplied finite snapshot is established.');
        var children = [span('fw-chart-summary-label', summary.label), span('fw-chart-summary-value', value), scope, description];
        tokens.push(scopeId);
        if (result.reason) {
            var reason = span('fw-treemap-summary-reason', result.reason); reason.id = reasonId;
            children.push(reason); tokens.push(reasonId);
        }
        box.replaceChildren.apply(box, children); box.title = description.textContent;
        scope.setAttribute('aria-label', scope.textContent + '. ' + description.textContent);
    } else if (box) {
        var parent = box.parentElement; box.remove();
        if (parent.classList.contains('fw-chart-head')) {
            var title = parent.querySelector('.fw-chart-title');
            if (title) { title.style.marginBottom = '4px'; parent.replaceWith(title); }
            else if (!parent.children.length) parent.remove();
        }
    }
    if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
    else canvas.removeAttribute('aria-describedby');
}

window._fwRenderers['treemap'] = function renderTreemap(id, cfg) {
    var bindings = window._fwTreemapBindings || (window._fwTreemapBindings = {});
    if (bindings[id]) bindings[id]();
    if (window._fwMetricDrawLeases && window._fwMetricDrawLeases[id]) {
        window._fwMetricDrawLeases[id](); delete window._fwMetricDrawLeases[id];
    }
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
    var active = true, unsubscribe = function() {}, lifeOff = function() {}, lease = null;
    var engine = window._fwFilterEngine;
    function dispose() {
        if (!active) return;
        active = false; unsubscribe(); lifeOff(); observer.disconnect();
        window.removeEventListener('fw-theme-change', drawCurrent);
        if (lease) {
            lease();
            if (window._fwMetricDrawLeases[id] === lease) delete window._fwMetricDrawLeases[id];
        }
        if (bindings[id] === dispose) delete bindings[id];
        if (document.getElementById(id) !== canvas) {
            _fwTreemapSummary(canvas, {}, null);
            var chart = window._chartInstances[id];
            if (chart && chart.canvas === canvas) { chart.destroy(); delete window._chartInstances[id]; }
        }
    }
    var observer = new MutationObserver(function() {
        if (document.getElementById(id) !== canvas) dispose();
    });
    observer.observe(document.body, {childList: true, subtree: true});
    bindings[id] = dispose;
    _fwTreemapSummary(canvas, cfg, null);

    function _buildTreemap(rows) {
        if (!active || document.getElementById(id) !== canvas) return;
        if (cfg.header_summary) _fwTreemapSummary(canvas, cfg, null);
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
        var nodes = chart.data.datasets[0].data;
        var leaves = nodes.filter(function(raw) { return raw.l === groupCols.length - 1; });
        if (cfg.header_summary) _fwTreemapSummary(canvas, cfg, _fwTreemapSummaryResult(leaves, tree, rows, cfg));
        var leafTotal = leaves.reduce(function(sum, raw) { return sum + raw.v; }, 0);
        if (!reason && Math.abs(leafTotal - total) > Math.max(1, total) * 1e-12) {
            reason = 'Shares unavailable: legacy leaves do not cover the supplied population.';
            // The plugin exposes its leaves only after construction. Remove
            // any shares painted before that population could be checked.
            chart.update('none');
        }
        if (!context) return; // Older markup safely keeps its canvas renderer.
        var previous = context.querySelector('details');
        var wasOpen = previous && previous.open;
        var hadFocus = previous && previous.contains(document.activeElement);
        var oldScroll = previous && previous.querySelector('.fw-treemap-details-scroll');
        var scrollLeft = oldScroll ? oldScroll.scrollLeft : 0;
        var scrollTop = oldScroll ? oldScroll.scrollTop : 0;
        var pageSize = 200;
        var page = Math.min(Math.max(0, Math.floor((leaves.length - 1) / pageSize)),
            previous ? Number(previous.dataset.page || 0) : 0);
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
                if (seenGroups.size > pageSize) return;
                var item = text('li', '', key);
                item.dataset.path = identity;
                var swatch = text('span', '', item, 'fw-treemap-swatch');
                swatch.setAttribute('aria-hidden', 'true');
                swatch.style.backgroundColor = getThemeColors().chart_colors[groupIndex.get(identity) % getThemeColors().chart_colors.length] + 'dd';
                var name = text('span', '', item, 'fw-treemap-name');
                pathText(path, name);
            });
            if (seenGroups.size > pageSize) text('p', 'Color key shows the first ' + pageSize +
                ' of ' + seenGroups.size + ' groups. Every hierarchy path is available in the paged details.', context);
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
        function appendLeaf(raw) {
            var path = _path(raw), tr = text('tr', '', body);
            tr.dataset.path = JSON.stringify(path);
            tr.dataset.value = String(raw.v);
            var name = text('td', '', tr); pathText(path, name);
            var source = raw._data.children.map(function(row) { return supplied.get(row); });
            var complete = source.every(function(s) { return s && typeof s.value === 'number' && Number.isFinite(s.value); });
            var cell = text('td', complete ? valFmt(raw.v) : 'Unavailable', tr);
            text('span', (complete ? 'Exact: ' + String(raw.v) : 'Supplied: ' + source.map(function(s) { return s ? s.text : 'unknown'; }).join(', ') + '; legacy size value: ' + String(raw.v)), cell, 'fw-treemap-raw');
            text('td', _share(raw.v), tr).title = reason || String(raw.v / total * 100) + '%';
        }
        var previousButton, nextButton, pageStatus;
        if (leaves.length > pageSize) {
            var pages = text('div', '', details, 'fw-treemap-pages');
            previousButton = text('button', 'Previous', pages); previousButton.type = 'button';
            pageStatus = text('span', '', pages); pageStatus.setAttribute('aria-live', 'polite');
            nextButton = text('button', 'Next', pages); nextButton.type = 'button';
            previousButton.addEventListener('click', function() { page--; renderPage(); });
            nextButton.addEventListener('click', function() { page++; renderPage(); });
        }
        function renderPage() {
            details.dataset.page = String(page);
            body.replaceChildren();
            leaves.slice(page * pageSize, (page + 1) * pageSize).forEach(appendLeaf);
            if (pageStatus) {
                var pageFocus = document.activeElement;
                pageStatus.textContent = 'Showing ' + (page * pageSize + 1) + '–' +
                    Math.min((page + 1) * pageSize, leaves.length) + ' of ' + leaves.length + ' leaves';
                previousButton.disabled = page === 0;
                nextButton.disabled = (page + 1) * pageSize >= leaves.length;
                if (pageFocus === previousButton && previousButton.disabled) nextButton.focus({preventScroll: true});
                else if (pageFocus === nextButton && nextButton.disabled) previousButton.focus({preventScroll: true});
            }
        }
        renderPage();
        scroll.scrollLeft = scrollLeft; scroll.scrollTop = scrollTop;
        if (!leaves.length) text('p', 'No hierarchy leaves are available in this result.', details);
        text('p', 'Legacy sizing is unsupported for signed or all-zero values; blank parents can suppress leaves. CSV does not export populated hierarchy leaves.', details);
        if (hadFocus) details.querySelector('summary').focus({preventScroll: true});
    }

    function drawCurrent() {
        if (document.getElementById(id) !== canvas) {
            dispose(); return;
        }
        if (cfg.dataset_id && !engine.isReady(cfg.dataset_id)) {
            _fwTreemapSummary(canvas, cfg, null); return;
        }
        _buildTreemap(cfg.dataset_id ? currentRows() : cfg.rows);
    }
    function currentRows() {
        // Structural parent replacement does not invalidate a scoped child's
        // row cache in the legacy engine. Its existing read API applies all
        // current filters without consulting that child cache.
        return engine._scopes[cfg.dataset_id] ? engine.getFilteredExcluding(cfg.dataset_id, [])
            : engine.getFiltered(cfg.dataset_id);
    }
    function bind() {
        unsubscribe();
        if (lease) { lease(); lease = null; }
        if (cfg.dataset_id && window._fwMetricsWorkspace) {
            window._fwLiveWrap(id, cfg, function(rows) {
                if (engine.isReady(cfg.dataset_id)) _buildTreemap(engine._scopes[cfg.dataset_id] ? currentRows() : rows);
                else _fwTreemapSummary(canvas, cfg, null);
            });
            lease = window._fwMetricDrawLeases[id];
        } else {
            unsubscribe = cfg.dataset_id ? engine.subscribe(cfg.dataset_id, id, drawCurrent) : function() {};
            drawCurrent();
        }
    }
    if (cfg.dataset_id && engine.subscribeLifecycle) lifeOff = engine.subscribeLifecycle(function(event) {
        // Re-entry after a removed source or scoped-parent replacement needs
        // a fresh row subscription. Existing filter state remains engine-owned.
        var source = cfg.dataset_id, seen = new Set();
        while (source && !seen.has(source)) {
            seen.add(source);
            if (source === event.dataset_id) { bind(); return; }
            source = engine._scopes[source] && engine._scopes[source].parent;
        }
    });
    if (!cfg.dataset_id || !window._fwMetricsWorkspace) window.addEventListener('fw-theme-change', drawCurrent);
    bind();
};

function _fwTreemapRaw(value) {
    return value == null ? 'null' : String(value);
}

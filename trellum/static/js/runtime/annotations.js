    // ── Annotation Builder ─────────────────────────────────
    // Shared helper that converts _events from events.yaml into
    // Chart.js annotation plugin objects.  Used by framework charts
    // automatically and available to custom RawHTML dashboards.
    var _ANNO_STYLES = {
        campaign: {color:'#FFD166', dash:[6,3], width:2, label:'Campaigns'},
        ab_test:  {color:'rgba(76,139,245,0.18)', border:'#4C8BF5', label:'A/B Tests'},
        release:  {color:'#00c48c', dash:[], width:2, label:'Releases'},
        incident: {color:'#FF4081', dash:[], width:2, label:'Incidents'},
        event:    {color:'rgba(176,122,161,0.14)', border:'#c47db8', label:'Events'},
        power_bet: {color:'rgba(99,110,114,0.05)', border:'#636e72', dash:[5,4], width:1, label:'Power Bet Events'},
        weekday:  {color:'#00cec9', dash:[4,4], width:1, label:'Weekdays'}
    };

    // Box annotations fill an area, so their colour must be translucent or
    // the band hides the data underneath. _ANNO_STYLES mixes conventions:
    // some types carry a solid hex (correct for a line's borderColor) and
    // others an rgba() already sized for a fill. Normalise here so a ranged
    // event of ANY type renders as a wash rather than a slab.
    function _annoFill(color, alpha) {
        var a = (alpha == null) ? 0.18 : alpha;
        if (!color) return 'rgba(76,139,245,' + a + ')';
        var c = String(color).trim();
        if (c.indexOf('rgba') === 0) return c;                 // already has alpha
        var rgb = /^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/i.exec(c);
        if (rgb) return 'rgba(' + rgb[1] + ',' + rgb[2] + ',' + rgb[3] + ',' + a + ')';
        var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
        if (!m) return c;                                      // named colour etc.
        var h = m[1];
        if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
        var n = parseInt(h, 16);
        return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ','
             + (n & 255) + ',' + a + ')';
    }

    // Annotation type visibility state.  Keys are type names, values are booleans.
    // Initialized from events default_visible, then localStorage overrides.
    var _annoVisible = {};
    var _annoStorageKey = 'fw-anno-visible';

    function _loadAnnoState(events) {
        var typeDefaults = {};
        if (events && events.length) {
            events.forEach(function(ev) {
                var t = ev.type || 'release';
                if (typeDefaults[t] === undefined) {
                    typeDefaults[t] = ev.default_visible !== false;
                } else {
                    if (ev.default_visible !== false) typeDefaults[t] = true;
                }
            });
        }
        // Recurring weekday highlight participates as a pseudo type so it
        // gets its own toggle in the annotation bar alongside real events.
        var _wh = window._reportData && window._reportData._weekday_highlight;
        if (_wh) {
            typeDefaults['weekday'] = _wh.default_visible !== false;
        }
        var urlParams = window._fwUrlSync ? window._fwUrlSync.parse() : {};
        var urlAnno = urlParams['anno'];
        var slug = document.querySelector('[data-report-slug]');
        var storageKey = _annoStorageKey + (slug ? '-' + slug.getAttribute('data-report-slug') : '');
        // Clear existing object in-place so window._annoVisible reference stays valid
        for (var k in _annoVisible) { if (_annoVisible.hasOwnProperty(k)) delete _annoVisible[k]; }
        if (urlAnno !== undefined) {
            var visSet = {};
            if (urlAnno) urlAnno.split(',').forEach(function(t) { visSet[t.trim()] = true; });
            for (var t in typeDefaults) {
                _annoVisible[t] = !!visSet[t];
            }
        } else {
            var saved = {};
            try { saved = JSON.parse(_lsGet(storageKey) || '{}'); } catch(e) {}
            for (var t in typeDefaults) {
                _annoVisible[t] = saved[t] !== undefined ? !!saved[t] : typeDefaults[t];
            }
        }
        _annoVisible._storageKey = storageKey;
        if (window._fwUrlSync && !window._fwUrlSync._annoDefaults) {
            var defs = {};
            for (var dt in typeDefaults) defs[dt] = typeDefaults[dt];
            window._fwUrlSync._annoDefaults = defs;
        }
    }

    function _saveAnnoState() {
        var toSave = {};
        for (var t in _annoVisible) {
            if (t === '_storageKey') continue;
            toSave[t] = _annoVisible[t];
        }
        _lsSet(_annoVisible._storageKey || _annoStorageKey, JSON.stringify(toSave));
    }

    var _annoStateLoaded = false;

    // Snap an event date to the nearest preceding chart label. Required
    // on weekly/monthly-grain charts where labels are e.g. Mondays only
    // — Chart.js category axes can only render annotations at exact
    // label values, so a Wednesday event on a Monday-anchored chart
    // would be silently dropped without this snap. On daily charts the
    // event date already matches a label and the function is a no-op.
    function _snapToLabel(d, labels) {
        if (labels.indexOf(d) >= 0) return d;
        var snapped = labels[0];
        for (var i = 0; i < labels.length; i++) {
            if (labels[i] <= d) snapped = labels[i];
            else break;
        }
        return snapped;
    }

    // WCAG-based text colour for a coloured chip background.
    // Returns '#111' for light backgrounds and '#fff' for dark ones.
    function _pickTextColor(hexColor) {
        var hex = (hexColor || '#000').replace('#', '');
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        if (hex.length !== 6) return '#fff';
        var r = parseInt(hex.substr(0,2),16)/255;
        var g = parseInt(hex.substr(2,2),16)/255;
        var b = parseInt(hex.substr(4,2),16)/255;
        r = r < 0.04045 ? r/12.92 : Math.pow((r+0.055)/1.055, 2.4);
        g = g < 0.04045 ? g/12.92 : Math.pow((g+0.055)/1.055, 2.4);
        b = b < 0.04045 ? b/12.92 : Math.pow((b+0.055)/1.055, 2.4);
        var lum = 0.2126*r + 0.7152*g + 0.0722*b;
        return lum > 0.35 ? '#111' : '#fff';
    }

    // An optional collector is used ONLY by grouped LineCharts. Associate the
    // original event with the annotation here, at the one eligibility/snap path;
    // never recover dates or identities from positioned category labels.
    function _buildAnnotations(events, labels, dated) {
        var _wh = window._reportData && window._reportData._weekday_highlight;
        if ((!events || !events.length) && !_wh) return {};
        if (!labels || !labels.length) return {};
        if (!/^\d{4}-\d{2}-\d{2}/.test(labels[0])) return {};
        if (!_annoStateLoaded) { _loadAnnoState(events); _annoStateLoaded = true; }
        // Box-annotation labels DON'T render label.backgroundColor in the
        // plugin (the text sits on the translucent box fill over the chart
        // background), so their text must contrast with the chart bg. Use
        // the theme's primary text colour (dark on light themes, light on
        // dark) — tick_color is a muted axis-tick grey and reads poorly.
        var boxLabelText = '#888';
        try {
            var _tm = getComputedStyle(document.documentElement)
                .getPropertyValue('--text-main').trim();
            if (_tm) boxLabelText = _tm;
        } catch (e) {}
        // Scope filter: for multi-scope reports, the server tags each
        // event with its originating studio. Only show events that
        // match the active scope (or are studio: shared). Non-scope
        // reports leave window._currentScope unset, so everything the
        // server sent is shown verbatim.
        var activeScope = window._currentScope;
        var minD = labels[0], maxD = labels[labels.length - 1];
        var annos = {};
        (events || []).forEach(function(ev, i) {
            var evType = ev.type || 'release';
            if (_annoVisible[evType] === false) return;
            if (activeScope && ev.studio && ev.studio !== 'shared' && ev.studio !== activeScope) return;
            if (ev.date > maxD || (ev.end_date || ev.date) < minD) return;
            var st = _ANNO_STYLES[evType] || _ANNO_STYLES.release;
            // Solid colour for line/border — fall back to st.color when no border.
            var solidColor = st.border || st.color;
            // Line labels DO render a solid chip → contrast against it.
            var labelText = _pickTextColor(solidColor);
            var clippedMin = ev.date < minD ? minD : ev.date;
            var clippedMax = (ev.end_date && ev.end_date > maxD) ? maxD : (ev.end_date || ev.date);
            clippedMin = _snapToLabel(clippedMin, labels);
            clippedMax = _snapToLabel(clippedMax, labels);
            if (ev.end_date && ev.end_date !== ev.date && clippedMin < clippedMax) {
                annos['ev_box_' + i] = {
                    type: 'box', xMin: clippedMin, xMax: clippedMax,
                    backgroundColor: _annoFill(st.color),
                    borderColor: solidColor, borderWidth: 1,
                    // Drawn on top (the plugin default) rather than behind the
                    // data: the fill is translucent, so the series stays
                    // readable, and a band behind an opaque dataset -- stacked
                    // areas, bars -- would simply vanish.
                    label: {display: true, content: ev.label, position: 'start',
                        font: {size: window._fwFontPx('--font-size-label', 12), weight: 'bold'}, color: boxLabelText,
                        backgroundColor: 'transparent',
                        padding: {x: 4, y: 2}, borderRadius: 3}
                };
            } else {
                annos['ev_line_' + i] = {
                    type: 'line', xMin: clippedMin, xMax: clippedMin,
                    borderColor: solidColor, borderWidth: st.width || 2,
                    borderDash: st.dash || [],
                    label: {display: true, content: ev.label, position: 'start',
                        font: {size: window._fwFontPx('--font-size-label', 12), weight: 'bold'}, color: labelText,
                        backgroundColor: solidColor, padding: {x: 4, y: 2}, borderRadius: 3}
                };
            }
            if (dated) {
                var key = 'ev_' + (annos['ev_box_' + i] ? 'box_' : 'line_') + i;
                annos[key].label.display = false;
                dated.push({key: key, index: i, event: ev, annotation: annos[key]});
            }
        });
        // Recurring weekday markers — thin vertical lines drawn on every
        // chart label whose weekday matches the report's config. Computed
        // from the chart's own labels so they always track the visible
        // date range (no 90-day cutoff, no per-date maintenance).
        if (_wh && _annoVisible['weekday'] !== false && _wh.days && _wh.days.length) {
            var _wdset = {};
            for (var _di = 0; _di < _wh.days.length; _di++) _wdset[_wh.days[_di]] = true;
            var _wst = _ANNO_STYLES.weekday;
            var _wcolor = _wst.border || _wst.color;
            for (var _li = 0; _li < labels.length; _li++) {
                var _lab = labels[_li];
                if (!/^\d{4}-\d{2}-\d{2}/.test(_lab)) continue;
                var _p = _lab.split('-');
                var _dt = new Date(Date.UTC(+_p[0], +_p[1] - 1, parseInt(_p[2], 10)));
                if (!_wdset[_dt.getUTCDay()]) continue;
                annos['wd_' + _li] = {
                    type: 'line', xMin: _lab, xMax: _lab,
                    borderColor: _wcolor, borderWidth: _wst.width || 1,
                    borderDash: _wst.dash || [4, 4]
                };
            }
        }
        return annos;
    }

    // Per-chart native canvas plugin. It adds no layout padding, datasets or
    // annotation geometry. Canvas drawing is included in existing PNG/PDF paths.
    function _lineEventLabelsPlugin(previous, dated) {
        var root = previous && previous.root, signature = '', handedOff = false;
        var pendingFocus = previous && previous.focusKey;
        var restoreFocus = pendingFocus && document.activeElement === document.body;
        var listeners = new AbortController();
        var groups = [], pressed = null, beganOutside = false;
        function focusKey() {
            var active = document.activeElement;
            return root && root.contains(active) ? active.dataset.eventFocus || 'summary' : null;
        }
        function describe(chart, add) {
            var id = chart.canvas.id + '-event-details';
            var tokens = (chart.canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
            tokens = tokens.filter(function(t) { return t !== id; });
            if (add) tokens.push(id);
            if (tokens.length) chart.canvas.setAttribute('aria-describedby', tokens.join(' '));
            else chart.canvas.removeAttribute('aria-describedby');
        }
        function summary() { return root && root.querySelector('summary'); }
        function reconcile(chart) {
            if (!groups.length) {
                var returnToChart = focusKey() || (restoreFocus && pendingFocus);
                pendingFocus = null;
                if (root) {
                    // Keep open preference for a subsequent nonempty selection,
                    // but reserve no row and leave no stale event descriptions.
                    root.hidden = true;
                    root.querySelector('.fw-event-list').replaceChildren();
                }
                describe(chart, false);
                if (returnToChart) {
                    // The summary is hidden too. Keep focus on the owning chart,
                    // without adding a sequential tab stop or taking focus from
                    // an unrelated filter. Preserve any authored canvas name.
                    var canvas = chart.canvas;
                    if (!canvas.hasAttribute('tabindex')) canvas.tabIndex = -1;
                    if (!canvas.hasAttribute('aria-label') && !canvas.hasAttribute('aria-labelledby'))
                        canvas.setAttribute('aria-label', canvas.dataset.fwTitle || 'Report chart');
                    canvas.focus({preventScroll: true});
                }
                signature = '';
                return;
            }
            if (!root) {
                root = document.createElement('details');
                root.className = 'fw-event-details';
                root.id = chart.canvas.id + '-event-details';
                root.open = !!(previous && previous.open);
                var heading = document.createElement('summary');
                heading.dataset.eventFocus = 'summary';
                var note = document.createElement('p');
                note.className = 'fw-event-note';
                note.textContent = 'Events in displayed range. A marker such as “2 · 3” means group 2 contains 3 events, not a metric value. Numbers may change with width or selection. Dates below are original dates; marks use the chart’s existing date mapping.';
                var list = document.createElement('div');
                list.className = 'fw-event-list';
                root.append(heading, note, list);
            }
            var slot = chart.canvas.parentElement;
            if (slot.nextElementSibling && slot.nextElementSibling.id === chart.canvas.id + '-month-context')
                slot = slot.nextElementSibling;
            if (slot.nextElementSibling !== root) slot.after(root);
            root.hidden = false;
            describe(chart, true);
            var count = groups.reduce(function(n, g) { return n + g.events.length; }, 0);
            summary().textContent = 'Events in displayed range · ' + count + (count === 1 ? ' event' : ' events') +
                ' · ' + groups.length + (groups.length === 1 ? ' group' : ' groups');
            var next = JSON.stringify(groups.map(function(g) {
                return g.events.map(function(d) { return [d.index, d.event.label, d.event.date, d.event.end_date, d.event.type]; });
            }));
            if (next === signature) return;
            var focused = focusKey() || (restoreFocus && pendingFocus);
            pendingFocus = null;
            var content = root.querySelector('.fw-event-list');
            // Reuse per-event nodes when membership/width changes. Stable keys
            // come from source identity, never the local marker number.
            var items = new Map(Array.from(content.querySelectorAll('[data-event-item]')).map(function(item) {
                return [item.dataset.eventFocus, item];
            }));
            var fragment = document.createDocumentFragment();
            groups.forEach(function(g, i) {
                var section = document.createElement('section');
                section.dataset.eventGroup = String(i + 1);
                var heading = document.createElement('h4');
                heading.tabIndex = -1;
                heading.dataset.eventFocus = 'group:' + g.events[0].index;
                heading.textContent = 'Group ' + (i + 1) + ' · ' + g.events.length + (g.events.length === 1 ? ' event' : ' events');
                var list = document.createElement('ul');
                g.events.forEach(function(d) {
                    var key = 'event:' + d.index;
                    var item = items.get(key) || document.createElement('li');
                    item.dataset.eventFocus = key;
                    item.dataset.eventItem = String(d.index);
                    item.tabIndex = -1;
                    // Literal text only, including hostile report-authored names.
                    item.textContent = String(d.event.label == null ? '' : d.event.label) + ' — ' + String(d.event.date) +
                        (d.event.end_date ? ' – ' + String(d.event.end_date) : '') + ' · ' + String(d.event.type || 'release');
                    list.appendChild(item);
                });
                section.append(heading, list);
                fragment.appendChild(section);
            });
            content.replaceChildren(fragment);
            signature = next;
            if (focused) {
                var target = Array.from(root.querySelectorAll('[data-event-focus]')).find(function(el) {
                    return el.dataset.eventFocus === focused;
                }) || summary();
                target.focus({preventScroll: true});
            }
        }
        function layout(chart) {
            var area = chart.chartArea, x = chart.scales.x;
            groups = [];
            chart._fwEventMarkers = groups;
            if (!area || !x || area.width <= 0) return;
            dated.forEach(function(d) {
                var a = d.annotation, labels = chart.data.labels;
                var start = x.getPixelForValue(labels.indexOf(a.xMin));
                var end = x.getPixelForValue(labels.indexOf(a.xMax));
                if (!Number.isFinite(start) || !Number.isFinite(end) ||
                        Math.max(start, end) < area.left || Math.min(start, end) > area.right) return;
                groups.push({anchor: Math.max(area.left, Math.min(area.right, start)), events: [d]});
            });
            groups.sort(function(a, b) { return a.anchor - b.anchor || a.events[0].index - b.events[0].index; });
            var clustered = [];
            groups.forEach(function(g) {
                var last = clustered[clustered.length - 1];
                if (last && g.anchor - last.anchor <= 52) last.events.push(g.events[0]);
                else clustered.push(g);
            });
            groups = clustered;
            var ctx = chart.ctx;
            ctx.save();
            var size = window._fwFontPx('--font-size-label', 12);
            ctx.font = '600 ' + size + 'px ' + Chart.defaults.font.family;
            // Clamping can collide even when starts were far apart. Merge and
            // renumber until all final measured boxes fit. Each pass removes a
            // group; even arbitrarily dense plots retain every event in a group.
            var merged;
            do {
                merged = false;
                groups.forEach(function(g, i) {
                    ctx.font = '600 ' + size + 'px ' + Chart.defaults.font.family;
                    g.text = String(i + 1) + (g.events.length > 1 ? ' · ' + g.events.length : '');
                    var textWidth = ctx.measureText(g.text).width;
                    var fittedSize = Math.min(size, size * Math.max(1, area.width - 12) / textWidth);
                    g.font = '600 ' + fittedSize + 'px ' + Chart.defaults.font.family;
                    ctx.font = g.font;
                    g.width = Math.min(area.width, ctx.measureText(g.text).width + 12);
                    g.height = fittedSize + 10;
                    g.left = Math.max(area.left, Math.min(area.right - g.width, g.anchor - g.width / 2));
                    g.top = area.top + 3;
                });
                for (var i = 1; i < groups.length; i++) {
                    if (groups[i - 1].left + groups[i - 1].width + 4 > groups[i].left) {
                        groups[i - 1].events = groups[i - 1].events.concat(groups[i].events);
                        groups.splice(i, 1);
                        merged = true;
                        break;
                    }
                }
            } while (merged);
            ctx.restore();
            chart._fwEventMarkers = groups;
        }
        function hit(chart, e) {
            var point = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || e;
            var box = chart.canvas.getBoundingClientRect();
            var x = (point.clientX - box.left) * chart.width / box.width;
            var y = (point.clientY - box.top) * chart.height / box.height;
            return groups.find(function(g) {
                return x >= g.left && x <= g.left + g.width && y >= g.top && y <= g.top + g.height;
            });
        }
        return {
            id: 'fwLineEventLabels',
            beforeInit: function(chart) {
                chart._fwDatedEvents = dated;
                chart._fwTakeEventDetails = function() { handedOff = true; return {root: root}; };
                // Capture before Chart.js and drag-zoom's native listeners. Only
                // marker hits are consumed; normal plotting interactions pass.
                ['pointerdown', 'pointerup', 'pointercancel', 'mousedown', 'mouseup', 'touchstart', 'touchend', 'click', 'dblclick'].forEach(function(type) {
                    chart.canvas.addEventListener(type, function(e) {
                        if (type === 'pointercancel') { pressed = null; beganOutside = false; return; }
                        var group = hit(chart, e);
                        var down = type === 'pointerdown' || type === 'mousedown' || type === 'touchstart';
                        if (down && !group) { pressed = null; beganOutside = true; return; }
                        if (type === 'click' && beganOutside) { beganOutside = false; return; }
                        if ((type === 'pointerup' || type === 'mouseup' || type === 'touchend') && !pressed) return;
                        if (!group && !pressed) return;
                        if (down) {
                            pressed = group;
                            beganOutside = false;
                            if (type === 'pointerdown') chart.canvas.setPointerCapture(e.pointerId);
                        }
                        e.preventDefault(); e.stopImmediatePropagation();
                        if (type !== 'click' && type !== 'touchend') return;
                        pressed = null;
                        if (!group) return;
                        if (!root) return;
                        root.open = true;
                        var i = groups.indexOf(group);
                        var heading = root.querySelector('[data-event-group="' + (i + 1) + '"] h4');
                        if (heading) {
                            heading.scrollIntoView({block: 'nearest'});
                            heading.focus({preventScroll: true});
                        }
                    }, {capture: true, passive: false, signal: listeners.signal});
                });
            },
            afterUpdate: function(chart) { layout(chart); reconcile(chart); },
            afterDraw: function(chart) {
                var ctx = chart.ctx;
                ctx.save();
                ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                var css = getComputedStyle(document.documentElement);
                groups.forEach(function(g) {
                    ctx.font = g.font;
                    ctx.fillStyle = css.getPropertyValue('--bg-card').trim() || '#fff';
                    ctx.fillRect(g.left, g.top, g.width, g.height);
                    ctx.strokeStyle = css.getPropertyValue('--text-secondary').trim() || '#666';
                    ctx.lineWidth = 1;
                    ctx.strokeRect(g.left + .5, g.top + .5, g.width - 1, g.height - 1);
                    ctx.fillStyle = css.getPropertyValue('--text-main').trim() || '#111';
                    ctx.fillText(g.text, g.left + g.width / 2, g.top + g.height / 2);
                });
                ctx.restore();
            },
            beforeDestroy: function(chart) {
                listeners.abort();
                // renderAll destroys charts before invoking the renderer. Keep
                // only small UI state on the surviving canvas, never listeners
                // or detached rows. The factory's direct handoff can reuse DOM.
                chart.canvas._fwEventDetailsState = {
                    open: !!(root && root.open), focusKey: focusKey()
                };
                describe(chart, false);
                if (root && !handedOff) root.remove();
                delete chart._fwEventMarkers;
                delete chart._fwTakeEventDetails;
                delete chart._fwDatedEvents;
            }
        };
    }
    window._fwLineEventLabelsPlugin = _lineEventLabelsPlugin;

    function _refreshAllAnnotations() {
        var instances = window._chartInstances || {};
        var events = window._reportData && window._reportData._events;
        if (!events) return;
        Object.keys(instances).forEach(function(id) {
            var chart = instances[id];
            if (!chart || !chart.options || !chart.options.plugins) return;
            if (!chart.options.plugins.annotation) return;
            var labels = chart.data && chart.data.labels;
            if (chart._fwDatedEvents) {
                chart._fwDatedEvents.length = 0;
                chart.config.options.plugins.annotation.annotations = _buildAnnotations(events, labels, chart._fwDatedEvents);
            } else chart.options.plugins.annotation.annotations = _buildAnnotations(events, labels);
            chart.update('none');
        });
    }

    function _buildAnnoToggleBar() {
        var events = window._reportData && window._reportData._events;
        var _whBar = window._reportData && window._reportData._weekday_highlight;
        if ((!events || !events.length) && !_whBar) return;
        _loadAnnoState(events);

        var types = Object.keys(_annoVisible).filter(function(k) { return k !== '_storageKey'; });
        if (!types.length) return;

        var bar = document.getElementById('fwAnnoBar');
        if (!bar) {
            var anchor = document.querySelector('.fw-report-chrome')
                || document.getElementById('fwReportMetadata')
                || document.querySelector('.fw-header');
            if (!anchor) return;
            bar = document.createElement('div');
            bar.id = 'fwAnnoBar';
            bar.className = 'fw-anno-bar';
            anchor.parentElement.insertBefore(bar, anchor.nextSibling);
        }

        bar.innerHTML = '<span class="fw-anno-label">Annotations:</span>';
        types.forEach(function(t) {
            var st = _ANNO_STYLES[t] || _ANNO_STYLES.release;
            var dotColor = st.border || st.color;
            var tLabel = st.label || t;
            if (t === 'weekday') {
                var _wh3 = window._reportData && window._reportData._weekday_highlight;
                if (_wh3 && _wh3.label) tLabel = _wh3.label;
            }
            var vis = _annoVisible[t];
            var btn = document.createElement('span');
            btn.className = 'fw-anno-toggle' + (vis ? ' active' : '');
            btn.dataset.annoType = t;
            btn.style.borderColor = vis ? dotColor : 'var(--border-color, #ccc)';
            btn.style.background = vis ? dotColor + '40' : 'transparent';
            btn.innerHTML = '<span class="fw-anno-dot" style="background:' + dotColor + '"></span>'
                + tLabel;
            btn.addEventListener('click', function() {
                var nowVis = _annoVisible[t];
                _annoVisible[t] = !nowVis;
                btn.classList.toggle('active', !nowVis);
                btn.style.borderColor = !nowVis ? dotColor : 'var(--border-color, #ccc)';
                btn.style.background = !nowVis ? dotColor + '20' : 'transparent';
                _saveAnnoState();
                _refreshAllAnnotations();
                if (window._fwUrlSync) window._fwUrlSync.write();
            });
            bar.appendChild(btn);
        });
    }

    window._buildAnnotations = _buildAnnotations;
    window._ANNO_STYLES = _ANNO_STYLES;
    window._annoVisible = _annoVisible;
    window._buildAnnoToggleBar = _buildAnnoToggleBar;
    window._refreshAllAnnotations = _refreshAllAnnotations;

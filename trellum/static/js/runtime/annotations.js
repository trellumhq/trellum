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

    // An optional collector is used by owned LineChart event modes. Associate the
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
        var annos = {}, identities = new Map();
        (events || []).forEach(function(ev, i) {
            var evType = ev.type || 'release';
            // Source metadata + occurrence distinguishes duplicate declarations
            // without changing identity when a filter removes earlier events.
            if (dated) {
                var source = JSON.stringify([ev.date, ev.end_date || '', ev.label, evType, ev.studio || '']);
                var occurrence = identities.get(source) || 0;
                identities.set(source, occurrence + 1);
            }
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
            var key = (annos['ev_box_' + i] ? 'ev_box_' : 'ev_line_') + i;
            annos[key]._fwEvent = ev;
            if (dated) {
                annos[key].label.display = false;
                dated.push({key: key, index: i, identity: source + ':' + occurrence, event: ev, annotation: annos[key]});
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

    // Original dates for display; snapped plot positions are never used here.
    function _eventDateText(d) {
        return String(d.event.date) + (d.event.end_date ? ' – ' + d.event.end_date : '');
    }
    function _eventIdentity(d) {
        return d.identity || 'source:' + d.index;
    }
    function _eventName(d) {
        return String(d.event.label == null ? '' : d.event.label);
    }
    function _eventColor(d) {
        var style = _ANNO_STYLES[d.event.type] || _ANNO_STYLES.release;
        return style.border || style.color;
    }
    function _eventMapping(d) {
        var a = d.annotation;
        if (a.xMin === d.event.date && a.xMax === (d.event.end_date || d.event.date)) return '';
        return 'Plotted at ' + a.xMin + (a.xMin !== a.xMax ? ' – ' + a.xMax : '') +
            ' (existing date buckets). Original dates shown.';
    }
    // Plain, non-interactive row: the export key and the selected detail.
    function _eventRow(d) {
        var row = document.createElement('li'), name = document.createElement('strong');
        row.style.setProperty('--event-color', _eventColor(d));
        name.textContent = _eventName(d);
        var date = document.createElement('time');
        date.dateTime = String(d.event.date);
        date.textContent = _eventDateText(d);
        row.append(date, name);
        var note = _eventMapping(d);
        if (note) {
            var mapping = document.createElement('span');
            mapping.className = 'fw-event-mapping';
            mapping.textContent = note;
            row.append(mapping);
        }
        return row;
    }
    // DOM owner for adaptive events. Names that fit beside their in-plot markers
    // are drawn on the canvas; crowded names collapse to the markers alone. The
    // single compact row below the plot holds the selected event and the entry to
    // every event. Nothing here changes canvas height, datasets or drag handlers.
    function _adaptiveEventLabelsPlugin(previous, dated) {
        var root, bar, detail, selectedBox, clearButton, allButton, panel, panelTitle, list, exportKey, chartRef;
        var items = [], eligible = [], layoutKey = '';
        var selected = previous && previous.selected || [], popup = previous && previous.popup || null;
        var opener = previous && previous.opener || 'all', pendingFocus = previous && previous.focusKey;
        var restoreFocus = pendingFocus && document.activeElement === document.body;
        var scroll = previous && previous.scroll || 0, signature = '', panelKey = '';
        var listeners = new AbortController(), removal, pressed = null, beganOutside = false;
        var HALF = 4.5, ROW = 9, MAX_ROWS = 5, NAME_GAP = 3;
        function focusKey() {
            var active = document.activeElement;
            return root && root.contains(active) ? active.dataset.eventFocus || null : null;
        }
        function focusChart(chart) {
            if (!chart.canvas.hasAttribute('tabindex')) chart.canvas.tabIndex = -1;
            if (!chart.canvas.hasAttribute('aria-label') && !chart.canvas.hasAttribute('aria-labelledby'))
                chart.canvas.setAttribute('aria-label', chart.canvas.dataset.fwTitle || 'Report chart');
            chart.canvas.focus({preventScroll: true});
        }
        function describe(chart, add) {
            var id = chart.canvas.id + '-adaptive-events';
            var tokens = (chart.canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(t) { return t && t !== id; });
            if (add) tokens.push(id);
            if (tokens.length) chart.canvas.setAttribute('aria-describedby', tokens.join(' '));
            else chart.canvas.removeAttribute('aria-describedby');
        }
        // Event lines are drawn quietly; a selected event is emphasised. Only the
        // owned annotation options change, never positions or the source events.
        function styleFor(d, on) {
            var base = d.base || (d.base = {color: d.annotation.borderColor, width: d.annotation.borderWidth,
                dash: d.annotation.borderDash});
            if (d.key.indexOf('ev_line_') === 0) return on ?
                {borderColor: base.color, borderWidth: 2, borderDash: [4, 3]} :
                {borderColor: _annoFill(base.color, 0.55), borderWidth: 1, borderDash: [3, 4]};
            return {borderWidth: on ? base.width + 2 : base.width};
        }
        function highlight(chart) {
            var selectedSet = new Set(selected), byKey = new Map();
            dated.forEach(function(d) {
                byKey.set(d.key, d);
                Object.assign(d.annotation, styleFor(d, selectedSet.has(_eventIdentity(d))));
            });
            // Resolve the annotation plugin's element options too, without a
            // recursive update or adding another source annotation.
            var plugin = window['chartjs-plugin-annotation'];
            if (plugin) plugin.getAnnotations(chart).forEach(function(el) {
                var d = byKey.get(el.options.id);
                if (d) Object.assign(el.options, styleFor(d, selectedSet.has(_eventIdentity(d))));
            });
        }
        function renderSelection() {
            var rows = eligible.filter(function(d) { return selected.indexOf(_eventIdentity(d)) >= 0; });
            detail.hidden = !rows.length;
            selectedBox.replaceChildren();
            rows.slice(0, 1).forEach(function(d) {
                var row = _eventRow(d);
                selectedBox.style.setProperty('--event-color', _eventColor(d));
                while (row.firstChild) selectedBox.append(row.firstChild);
            });
        }
        function renderPanel(force) {
            var open = !!popup;
            var scope = popup === 'all' ? eligible : popup ? eligible.filter(function(d) { return popup.indexOf(_eventIdentity(d)) >= 0; }) : [];
            if (open && !scope.length) { popup = null; open = false; }
            panel.hidden = !open;
            allButton.setAttribute('aria-expanded', String(open && popup === 'all'));
            if (!open) { panelKey = ''; return; }
            var key = JSON.stringify([popup === 'all' ? 'all' : scope.map(_eventIdentity), signature]);
            if (!force && key === panelKey) return;
            var previousScroll = panelKey ? list.scrollTop : scroll;
            panelKey = key;
            panel.dataset.scope = popup === 'all' ? 'all' : 'local';
            panelTitle.textContent = popup === 'all' ? 'All ' + scope.length + ' events' : scope.length + (scope.length === 1 ? ' event' : ' events');
            var ol = document.createElement('ol');
            scope.forEach(function(d) {
                var li = document.createElement('li'), button = document.createElement('button');
                var id = _eventIdentity(d), date = document.createElement('time'), name = document.createElement('span');
                li.dataset.eventItem = id;
                li.style.setProperty('--event-color', _eventColor(d));
                button.type = 'button'; button.dataset.eventFocus = id; button.dataset.eventPick = id;
                date.dateTime = String(d.event.date); date.textContent = _eventDateText(d);
                name.textContent = _eventName(d);
                button.append(date, name); li.append(button); ol.append(li);
            });
            list.replaceChildren(ol);
            list.scrollTop = previousScroll;
        }
        function refresh(chart) {
            renderSelection(); renderPanel(); highlight(chart); chart.draw();
        }
        function returnFocus(chart) {
            if (opener === 'canvas' || !eligible.length) focusChart(chart); else allButton.focus({preventScroll: true});
        }
        function closePanel(chart, focus) {
            popup = null; refresh(chart);
            if (focus) returnFocus(chart);
        }
        function clearSelection(chart) {
            selected = []; popup = null; refresh(chart);
            if (eligible.length) allButton.focus({preventScroll: true}); else focusChart(chart);
        }
        function choose(chart, ids, from) {
            if (ids.length === 1) {
                selected = ids; popup = null; refresh(chart);
                selectedBox.focus({preventScroll: true});
            } else {
                popup = ids; opener = from; refresh(chart);
                var first = list.querySelector('button');
                if (first) first.focus({preventScroll: true});
            }
        }
        function build(chart) {
            root = document.createElement('div'); root.className = 'fw-adaptive-events';
            root.id = chart.canvas.id + '-adaptive-events';
            bar = document.createElement('div'); bar.className = 'fw-event-bar';
            detail = document.createElement('div'); detail.className = 'fw-event-detail'; detail.hidden = true;
            detail.setAttribute('aria-live', 'polite');
            selectedBox = document.createElement('div'); selectedBox.className = 'fw-event-selected';
            selectedBox.tabIndex = -1; selectedBox.dataset.eventFocus = 'details';
            clearButton = document.createElement('button'); clearButton.type = 'button';
            clearButton.className = 'fw-event-close'; clearButton.textContent = '×';
            clearButton.setAttribute('aria-label', 'Clear selected event'); clearButton.dataset.eventFocus = 'close';
            detail.append(selectedBox, clearButton);
            panel = document.createElement('div'); panel.className = 'fw-event-panel'; panel.hidden = true;
            panel.id = chart.canvas.id + '-event-panel'; panel.setAttribute('role', 'dialog');
            panel.setAttribute('aria-label', 'Chart events');
            allButton = document.createElement('button'); allButton.type = 'button'; allButton.className = 'fw-event-all';
            allButton.dataset.eventFocus = 'all'; allButton.setAttribute('aria-controls', panel.id);
            allButton.setAttribute('aria-expanded', 'false'); allButton.setAttribute('aria-haspopup', 'dialog');
            bar.append(detail, allButton);
            var head = document.createElement('div'); head.className = 'fw-event-panel-head';
            panelTitle = document.createElement('strong');
            var closeButton = document.createElement('button'); closeButton.type = 'button';
            closeButton.className = 'fw-event-close'; closeButton.textContent = '×';
            closeButton.setAttribute('aria-label', 'Close event list'); closeButton.dataset.eventFocus = 'panel-close';
            head.append(panelTitle, closeButton);
            list = document.createElement('div'); list.className = 'fw-adaptive-event-list';
            panel.append(head, list);
            exportKey = document.createElement('div'); exportKey.className = 'fw-event-export-key'; exportKey.hidden = true;
            root.append(bar, panel, exportKey);
            var signal = {signal: listeners.signal};
            allButton.addEventListener('click', function() {
                if (popup === 'all') { closePanel(chartRef, false); return; }
                popup = 'all'; opener = 'all'; refresh(chartRef);
                var current = list.querySelector('[data-event-pick="' + (selected[0] || '').replace(/["\\]/g, '\\$&') + '"]') || list.querySelector('button');
                if (current) current.focus({preventScroll: true});
            }, signal);
            clearButton.addEventListener('click', function() { clearSelection(chartRef); }, signal);
            closeButton.addEventListener('click', function() { closePanel(chartRef, true); }, signal);
            list.addEventListener('click', function(e) {
                var pick = e.target.closest('[data-event-pick]');
                if (pick) choose(chartRef, [pick.dataset.eventPick], opener);
            }, signal);
            root.addEventListener('keydown', function(e) {
                if (e.key !== 'Escape' || (!popup && !selected.length)) return;
                e.preventDefault();
                if (popup) closePanel(chartRef, true); else clearSelection(chartRef);
            }, signal);
            // A pointer press elsewhere dismisses the transient list.
            document.addEventListener('pointerdown', function(e) {
                if (popup && !root.contains(e.target)) closePanel(chartRef, false);
            }, {signal: listeners.signal, capture: true});
        }
        function reconcile(chart) {
            chartRef = chart;
            if (!root) build(chart); else if (panelKey) scroll = list.scrollTop;
            var focused = focusKey() || (restoreFocus && pendingFocus);
            pendingFocus = null; restoreFocus = false;
            var slot = chart.canvas.parentElement;
            if (eligible.length) {
                if (slot.nextElementSibling && slot.nextElementSibling.id === chart.canvas.id + '-month-context') slot = slot.nextElementSibling;
                if (slot.nextElementSibling !== root) slot.after(root);
            } else {
                // Empty populations retain no sibling nodes either. Existing
                // legend/title lookup contracts then remain intact.
                root.remove();
            }
            root.hidden = !eligible.length;
            describe(chart, !!eligible.length);
            var eligibleIds = new Set(eligible.map(_eventIdentity));
            selected = selected.filter(function(id) { return eligibleIds.has(id); });
            if (popup && popup !== 'all') {
                popup = popup.filter(function(id) { return eligibleIds.has(id); });
                if (!popup.length) popup = null;
            }
            var next = JSON.stringify(eligible.map(function(d) { return [_eventIdentity(d), d.event.label, d.event.date, d.event.end_date, d.annotation.xMin, d.annotation.xMax]; }));
            if (next !== signature) {
                signature = next;
                allButton.textContent = eligible.length === 1 ? '1 event' : 'All ' + eligible.length + ' events';
                allButton.setAttribute('aria-label', (eligible.length === 1 ? '1 event' : 'All ' + eligible.length + ' events') + ' · exact names and original dates');
                var exportHeading = document.createElement('strong'); exportHeading.textContent = 'Event key · original dates';
                var exportList = document.createElement('ol');
                eligible.forEach(function(d) { exportList.append(_eventRow(d)); });
                exportKey.replaceChildren(exportHeading, exportList);
            }
            exportKey.dataset.collapsed = String(items.some(function(item) { return !item.name; }));
            renderSelection(); renderPanel(); highlight(chart);
            if (focused) {
                var target = Array.from(root.querySelectorAll('[data-event-focus]')).find(function(el) {
                    return el.dataset.eventFocus === focused && !el.closest('[hidden]');
                });
                if (!target && eligible.length) target = allButton;
                if (target) target.focus({preventScroll: true}); else focusChart(chart);
            }
        }
        // Layout is measured from the actual plot: markers always exist, a name is
        // drawn only where its whole text fits beside the marker without covering
        // another marker or name. No viewport width or event-count threshold.
        function layout(chart) {
            var area = chart.chartArea, x = chart.scales.x;
            items = []; eligible = [];
            if (!area || !x || area.width <= 0) { chart._fwEventMarkers = []; return; }
            var labelIndexes = new Map(chart.data.labels.map(function(label, i) { return [label, i]; }));
            dated.forEach(function(d) {
                var start = x.getPixelForValue(labelIndexes.get(d.annotation.xMin));
                var end = x.getPixelForValue(labelIndexes.get(d.annotation.xMax));
                if (!Number.isFinite(start) || !Number.isFinite(end) || Math.max(start, end) < area.left || Math.min(start, end) > area.right) return;
                items.push({d: d, x: Math.max(area.left, Math.min(area.right, start)), name: null});
            });
            items.sort(function(a, b) { return a.x - b.x || a.d.event.date.localeCompare(b.d.event.date) || a.d.index - b.d.index; });
            eligible = items.map(function(item) { return item.d; }).sort(function(a, b) { return a.event.date.localeCompare(b.event.date) || a.index - b.index; });
            // Pack markers into rows so same-day and nearby events stay distinct.
            var rowEnd = [];
            items.forEach(function(item) {
                var row = 0;
                while (row < MAX_ROWS - 1 && rowEnd[row] !== undefined && rowEnd[row] + 1 > item.x - HALF) row++;
                rowEnd[row] = item.x + HALF;
                item.cy = area.top + 7 + row * ROW;
            });
            var size = window._fwFontPx('--font-size-label', 12), ctx = chart.ctx, accepted = [];
            ctx.save(); ctx.font = '600 ' + size + 'px ' + Chart.defaults.font.family;
            var height = size + 6;
            function nearMarkers(rect, self) {
                // Binary search the x-sorted markers, then test only the window.
                var lo = 0, hi = items.length, from = rect.x - HALF - 2;
                while (lo < hi) { var mid = (lo + hi) >> 1; if (items[mid].x < from) lo = mid + 1; else hi = mid; }
                for (var i = lo; i < items.length && items[i].x - HALF <= rect.x + rect.w + 2; i++) {
                    if (items[i] === self) continue;
                    if (rect.y < items[i].cy + HALF + 2 && items[i].cy - HALF - 2 < rect.y + rect.h) return true;
                }
                return false;
            }
            function overlaps(a, b) {
                return a.x < b.x + b.w + 3 && b.x < a.x + a.w + 3 && a.y < b.y + b.h + 1 && b.y < a.y + a.h + 1;
            }
            items.forEach(function(item) {
                var width = ctx.measureText(_eventName(item.d)).width + 8, y = item.cy - height / 2;
                [item.x + HALF + NAME_GAP, item.x - HALF - NAME_GAP - width].some(function(left) {
                    var rect = {x: left, y: y, w: width, h: height};
                    if (rect.x < area.left || rect.x + rect.w > area.right) return false;
                    if (nearMarkers(rect, item) || accepted.some(function(other) { return overlaps(rect, other); })) return false;
                    item.name = rect; accepted.push(rect);
                    return true;
                });
            });
            ctx.restore();
            chart._fwEventMarkers = items.map(function(item) {
                return {left: item.x - HALF, top: item.cy - HALF, width: HALF * 2, height: HALF * 2, anchor: item.x,
                    named: !!item.name, name: item.name, text: item.name ? _eventName(item.d) : '',
                    events: [{index: item.d.index, key: item.d.key, event: item.d.event}]};
            });
        }
        function hits(chart, e) {
            var point = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || e;
            var box = chart.canvas.getBoundingClientRect();
            var px = (point.clientX - box.left) * chart.width / box.width;
            var py = (point.clientY - box.top) * chart.height / box.height;
            var pad = window.matchMedia && window.matchMedia('(pointer: coarse)').matches ? 9 : 5;
            return items.filter(function(item) {
                var inMarker = Math.abs(px - item.x) <= HALF + pad && Math.abs(py - item.cy) <= HALF + pad;
                var n = item.name;
                return inMarker || (n && px >= n.x && px <= n.x + n.w && py >= n.y && py <= n.y + n.h);
            });
        }
        return {
            id: 'fwAdaptiveEvents',
            beforeInit: function(chart) {
                chart._fwDatedEvents = dated;
                chart._fwTakeEventDetails = function() {
                    return {selected: selected.slice(), popup: popup === 'all' ? 'all' : popup && popup.slice(), opener: opener,
                        focusKey: focusKey(), scroll: panelKey ? list.scrollTop : scroll};
                };
                removal = new MutationObserver(function() {
                    if (document.getElementById(chart.canvas.id) === chart.canvas) return;
                    var id = chart.canvas.id;
                    chart.destroy();
                    if (window._chartInstances[id] === chart) delete window._chartInstances[id];
                });
                removal.observe(document.body, {childList: true, subtree: true});
                // Capture before Chart.js and drag-zoom's native listeners. Only
                // marker/name hits are consumed; plot interactions pass through.
                ['pointerdown', 'pointerup', 'pointercancel', 'mousedown', 'mouseup', 'touchstart', 'touchend', 'click', 'dblclick'].forEach(function(type) {
                    chart.canvas.addEventListener(type, function(e) {
                        if (type === 'pointercancel') { pressed = null; beganOutside = false; return; }
                        var found = hits(chart, e);
                        var down = type === 'pointerdown' || type === 'mousedown' || type === 'touchstart';
                        if (down && !found.length) { pressed = null; beganOutside = true; return; }
                        if (type === 'click' && beganOutside) { beganOutside = false; return; }
                        if ((type === 'pointerup' || type === 'mouseup' || type === 'touchend') && !pressed) return;
                        if (!found.length && !pressed) return;
                        if (down) {
                            pressed = found;
                            beganOutside = false;
                            if (type === 'pointerdown' && chart.canvas.setPointerCapture) {
                                try { chart.canvas.setPointerCapture(e.pointerId); } catch (err) {}
                            }
                        }
                        e.preventDefault(); e.stopImmediatePropagation();
                        if (type !== 'click' && type !== 'touchend') return;
                        pressed = null;
                        if (!found.length) return;
                        choose(chart, found.map(function(item) { return _eventIdentity(item.d); }), 'canvas');
                    }, {capture: true, passive: false, signal: listeners.signal});
                });
                chart.canvas.addEventListener('pointermove', function(e) {
                    if (e.pointerType === 'touch') return;
                    var over = hits(chart, e).length > 0;
                    if (over !== (chart.canvas.style.cursor === 'pointer')) chart.canvas.style.cursor = over ? 'pointer' : '';
                }, {signal: listeners.signal});
            },
            beforeUpdate: function(chart) {
                // Apply quiet line styling before the annotation plugin resolves
                // its options; selection emphasis is re-applied in reconcile.
                var selectedSet = new Set(selected);
                dated.forEach(function(d) { Object.assign(d.annotation, styleFor(d, selectedSet.has(_eventIdentity(d)))); });
            },
            afterUpdate: function(chart) { layout(chart); reconcile(chart); },
            afterDraw: function(chart) {
                if (!items.length) return;
                var ctx = chart.ctx, css = getComputedStyle(document.documentElement);
                var text = css.getPropertyValue('--text-main').trim() || '#111';
                var card = css.getPropertyValue('--bg-card').trim() || '#fff';
                var size = window._fwFontPx('--font-size-label', 12), selectedSet = new Set(selected);
                ctx.save();
                ctx.font = '600 ' + size + 'px ' + Chart.defaults.font.family;
                ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
                items.forEach(function(item) {
                    if (!item.name) return;
                    ctx.globalAlpha = 0.9; ctx.fillStyle = card;
                    ctx.fillRect(item.name.x, item.name.y, item.name.w, item.name.h);
                    ctx.globalAlpha = 1; ctx.fillStyle = text;
                    ctx.fillText(_eventName(item.d), item.name.x + 4, item.cy + 0.5);
                });
                items.forEach(function(item) {
                    var h = HALF + (selectedSet.has(_eventIdentity(item.d)) ? 1.5 : 0);
                    ctx.beginPath();
                    ctx.moveTo(item.x, item.cy - h); ctx.lineTo(item.x + h, item.cy);
                    ctx.lineTo(item.x, item.cy + h); ctx.lineTo(item.x - h, item.cy); ctx.closePath();
                    ctx.fillStyle = _eventColor(item.d); ctx.fill();
                    if (selectedSet.has(_eventIdentity(item.d))) { ctx.strokeStyle = text; ctx.lineWidth = 2; }
                    else { ctx.strokeStyle = card; ctx.lineWidth = 1; }
                    ctx.stroke();
                });
                ctx.restore();
            },
            beforeDestroy: function(chart) {
                chart.canvas._fwEventDetailsState = chart._fwTakeEventDetails();
                listeners.abort(); if (removal) removal.disconnect(); describe(chart, false);
                chart.canvas.style.cursor = '';
                if (root) root.remove();
                delete chart._fwEventMarkers; delete chart._fwDatedEvents; delete chart._fwTakeEventDetails;
            }
        };
    }
    // Called from the shared export clone contract. Only captured descendants
    // change; the screen's disclosure, scroll and focus are never touched.
    window._fwPrepareEventCapture = function(doc, root, scale) {
        var selector = '.fw-adaptive-events:not([hidden])';
        var owners = Array.from(root.querySelectorAll(selector));
        if (root.matches(selector)) owners.unshift(root);
        if (!owners.length) return;
        owners.forEach(function(owner) {
            var key = owner.querySelector('.fw-event-export-key');
            var panel = owner.querySelector('.fw-event-panel:not([hidden])');
            // Names collapsed on the plot stay recoverable in a captured report,
            // unless the full list is already expanded there.
            if (key && key.dataset.collapsed === 'true' && !(panel && panel.dataset.scope === 'all')) key.hidden = false;
            if (panel) { panel.style.position = 'static'; panel.style.width = 'auto'; panel.style.boxShadow = 'none'; panel.style.marginTop = '4px'; }
            owner.querySelectorAll('.fw-adaptive-event-list').forEach(function(list) {
                list.style.maxHeight = 'none'; list.style.overflow = 'visible'; list.scrollTop = 0;
            });
        });
        var box = root.getBoundingClientRect(), ratio = scale || 1;
        if (box.width * ratio > 16384 || box.height * ratio > 16384 || box.width * box.height * ratio * ratio > 32000000)
            throw new Error('Event capture exceeds the 16,384px / 32 megapixel image limit. Select a smaller chart or section.');
    };

    // Per-chart native canvas plugin. It adds no layout padding, datasets or
    // annotation geometry. Canvas drawing is included in existing PNG/PDF paths.
    function _lineEventLabelsPlugin(previous, dated, mode) {
        if (mode === 'adaptive') {
            // A compatibility-mode owner may have handed its old disclosure
            // over during a mode change. Adaptive owns fresh DOM on both sides
            // of the plot, so retire that disclosure rather than stranding it.
            if (previous && previous.root) {
                if (previous.root.contains(document.activeElement)) previous.focusKey = 'details';
                previous.root.remove();
            }
            return _adaptiveEventLabelsPlugin(previous, dated);
        }
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
    // Use the annotation plugin's measured label rectangles: date spacing alone
    // cannot predict collisions after resizing, date snapping or font changes.
    // Sparse charts keep their labels; crowded charts retain every marker and
    // expose the complete events through a native keyboard-accessible disclosure.
    if (typeof Chart !== 'undefined') Chart.register({
        id: 'fwAnnotationLabels',
        afterUpdate: function(chart) {
            // Grouped LineCharts own numbered labels and their disclosure.
            if (chart._fwDatedEvents) return;
            var config = chart.config.options.plugins;
            var annotations = config && config.annotation && config.annotation.annotations;
            var plugin = window['chartjs-plugin-annotation'];
            if (!plugin || !annotations) return;
            var area = chart.chartArea, accepted = [], events = [], hidden = false;
            plugin.getAnnotations(chart).forEach(function(element) {
                var annotation = annotations[element.options.id];
                if (!annotation || !annotation._fwEvent || !element.label || element.skip) return;
                events.push(annotation._fwEvent);
                var label = element.label;
                var rect = {x: label.x, y: label.y, w: label.width, h: label.height};
                var fits = rect.x >= area.left && rect.x + rect.w <= area.right;
                var overlaps = accepted.some(function(other) {
                    return rect.x < other.x + other.w + 4 && other.x < rect.x + rect.w + 4
                        && rect.y < other.y + other.h + 2 && other.y < rect.y + rect.h + 2;
                });
                label.options.display = fits && !overlaps;
                if (label.options.display) accepted.push(rect);
                else hidden = true;
            });
            var details = chart._fwAnnotationDetails;
            if (!hidden) {
                if (details) details.remove();
                chart._fwAnnotationDetails = null;
                return;
            }
            if (!details) {
                details = document.createElement('details');
                details.className = 'fw-annotation-details';
                details.appendChild(document.createElement('summary'));
                details.appendChild(document.createElement('ul'));
                var container = chart.canvas.closest('.fw-chart-container') || chart.canvas.parentElement;
                container.insertAdjacentElement('afterend', details);
                chart._fwAnnotationDetails = details;
            }
            details.firstChild.textContent = 'Event details (' + events.length + ')';
            var text = events.map(function(ev) {
                return ev.date + (ev.end_date && ev.end_date !== ev.date ? ' – ' + ev.end_date : '')
                    + ' · ' + ((_ANNO_STYLES[ev.type] || {}).label || ev.type || 'Releases')
                    + ' · ' + ev.label;
            });
            var signature = JSON.stringify(text);
            if (details._fwEvents === signature) return;
            details._fwEvents = signature;
            details.lastChild.replaceChildren();
            text.forEach(function(value) {
                var item = document.createElement('li');
                item.textContent = value;
                details.lastChild.appendChild(item);
            });
        },
        afterDestroy: function(chart) {
            if (chart._fwAnnotationDetails) chart._fwAnnotationDetails.remove();
        }
    });

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

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

    function _buildAnnotations(events, labels) {
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

    function _refreshAllAnnotations() {
        var instances = window._chartInstances || {};
        var events = window._reportData && window._reportData._events;
        if (!events) return;
        Object.keys(instances).forEach(function(id) {
            var chart = instances[id];
            if (!chart || !chart.options || !chart.options.plugins) return;
            if (!chart.options.plugins.annotation) return;
            var labels = chart.data && chart.data.labels;
            chart.options.plugins.annotation.annotations = _buildAnnotations(events, labels);
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

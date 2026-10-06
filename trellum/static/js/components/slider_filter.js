window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['slider'] = {
    _fmt: function(fc, value) {
        var fn = window.getFormatter ? window.getFormatter(fc.format || 'number') : null;
        return fn ? fn(Number(value)) : String(value);
    },

    // Ordinal: position (0..N-1, possibly fractional mid-drag) -> the
    // category's display label, falling back to the raw category value.
    _ordinalLabel: function(fc, rawPos) {
        var values = fc.values || [];
        var pos = Math.round(Number(rawPos));
        if (pos < 0) pos = 0;
        if (pos > values.length - 1) pos = values.length - 1;
        var val = values[pos];
        var labels = fc.labels || {};
        return labels[val] !== undefined ? labels[val] : String(val);
    },

    _updateReadout: function(el, fc, values) {
        var readout = el.querySelector('.fw-slider-readout[data-filter-id="' + fc.id + '"]');
        if (!readout) return;
        var self = window._fwFilterTypes['slider'];
        var fmt = fc.ordinal ? self._ordinalLabel : self._fmt;
        if (fc.mode === 'single') {
            readout.textContent = fmt(fc, values[0]);
        } else {
            readout.textContent = fmt(fc, values[0]) + ' – ' + fmt(fc, values[1]);
        }
    },

    // Snap a raw slider position back onto one of the filter's declared
    // distinct values -- protects the 'equals' commit from float drift
    // introduced by noUiSlider's own output formatting. Numeric single
    // mode only -- ordinal single mode already lands on an exact integer
    // position, no nearest-neighbor search needed.
    _nearestValue: function(fc, raw) {
        var vals = fc.values || [];
        var best = vals[0];
        for (var i = 1; i < vals.length; i++) {
            if (Math.abs(vals[i] - raw) < Math.abs(best - raw)) best = vals[i];
        }
        return best;
    },

    // How many pip labels fit before neighbors would overlap. Measured
    // against the slider's own rendered width rather than guessed, so a
    // narrow FilterBar column degrades to endpoints-only automatically.
    _pipPositions: function(node, n) {
        var positions = [];
        for (var i = 0; i < n; i++) positions.push(i);
        if (n <= 2) return positions;
        var width = node.getBoundingClientRect ? node.getBoundingClientRect().width : 0;
        var MIN_PIP_SPACING = 56; // px per label, incl. tick + margin
        if (width > 0 && (width / n) < MIN_PIP_SPACING) {
            return [0, n - 1];
        }
        return positions;
    },

    init: function(el, fc, dsId, h) {
        var urlVal = h.urlLookup(fc.column);
        if (fc.mode === 'single') {
            var start = fc.ordinal
                ? (fc.default != null ? fc.default : 0)
                : (fc.values && fc.values.length ? fc.values[0] : fc.min);
            if (urlVal !== undefined) {
                var decoded = window._fwUrlSync ? window._fwUrlSync.decode(urlVal, 'slider') : urlVal;
                if (fc.ordinal) {
                    var idx = (fc.values || []).indexOf(String(decoded));
                    if (idx !== -1) start = idx;
                } else {
                    var match = (fc.values || []).filter(function(v) { return String(v) === String(decoded); });
                    if (match.length) start = match[0];
                }
            }
            fc._urlStart = start;
            var commitVal = fc.ordinal ? fc.values[start] : start;
            h.setFilter(fc.id, fc.column, 'equals', String(commitVal));
            h.propagate(fc.column, 'equals', String(commitVal));
        } else {
            var range = { min: fc.default_min, max: fc.default_max };
            if (urlVal !== undefined) {
                var d = window._fwUrlSync ? window._fwUrlSync.decode(urlVal, 'slider') : null;
                if (d && d.min !== undefined && d.min !== '') {
                    if (fc.ordinal) {
                        var loIdx = (fc.values || []).indexOf(String(d.min));
                        var hiIdx = (fc.values || []).indexOf(String(d.max));
                        if (loIdx !== -1 && hiIdx !== -1) {
                            range = { min: Math.min(loIdx, hiIdx), max: Math.max(loIdx, hiIdx) };
                        }
                    } else {
                        range = { min: Number(d.min), max: Number(d.max) };
                    }
                }
            }
            fc._urlStart = [range.min, range.max];
            if (fc.ordinal) {
                var span = fc.values.slice(range.min, range.max + 1);
                h.setFilter(fc.id, fc.column, 'in', span);
                h.propagate(fc.column, 'in', span);
            } else {
                h.setFilter(fc.id, fc.column, 'numrange', range);
                h.propagate(fc.column, 'numrange', range);
            }
        }
    },

    wireControls: function(el, cfg, dsId, h) {
        var self = window._fwFilterTypes['slider'];
        if (typeof noUiSlider === 'undefined') return;

        el.querySelectorAll('.fw-slider').forEach(function(node) {
            var fid = node.dataset.filterId;
            var fc = h.findFc(fid);
            if (!fc || node.noUiSlider) return;

            var isSingle = fc.mode === 'single';
            var isOrdinal = !!fc.ordinal;
            var item = node.closest('.fw-filter-item');
            var labelEl = item && item.querySelector('.fw-filter-label');
            var label = labelEl ? labelEl.textContent : 'Slider';

            var opts = { connect: true, keyboardSupport: true };

            if (isOrdinal) {
                var n = fc.values.length;
                opts.range = { min: 0, max: Math.max(n - 1, 0) };
                opts.step = 1;
                if (isSingle) {
                    opts.connect = [true, false];
                    opts.start = [fc._urlStart !== undefined ? fc._urlStart : (fc.default != null ? fc.default : 0)];
                    opts.handleAttributes = [{ 'aria-label': label }];
                } else {
                    opts.start = fc._urlStart || [fc.default_min, fc.default_max];
                    opts.handleAttributes = [
                        { 'aria-label': label + ' minimum' },
                        { 'aria-label': label + ' maximum' }
                    ];
                }
                // Pips at every category position if the label fits, else
                // just the two endpoints -- noUiSlider's own PipsMode.Values.
                opts.pips = {
                    mode: 'values',
                    values: self._pipPositions(node, n),
                    density: 100,
                    format: { to: function(pos) { return self._ordinalLabel(fc, pos); } }
                };
            } else if (isSingle) {
                var vals = fc.values && fc.values.length ? fc.values : [fc.min, fc.max];
                var lo = vals[0], hi = vals[vals.length - 1];
                if (hi <= lo) hi = lo + (fc.step || 1); // single distinct value: fake a span
                var range = { min: lo };
                for (var i = 1; i < vals.length - 1; i++) {
                    range[(i / (vals.length - 1) * 100) + '%'] = vals[i];
                }
                range.max = hi;
                opts.range = range;
                opts.snap = true;
                opts.connect = [true, false];
                opts.start = [fc._urlStart !== undefined ? fc._urlStart : lo];
                opts.handleAttributes = [{ 'aria-label': label }];
            } else {
                opts.range = { min: fc.min, max: fc.max };
                opts.step = fc.step || 1;
                opts.start = fc._urlStart || [fc.default_min, fc.default_max];
                opts.handleAttributes = [
                    { 'aria-label': label + ' minimum' },
                    { 'aria-label': label + ' maximum' }
                ];
            }

            noUiSlider.create(node, opts);

            // commit=false (the 'slide' path, mid-drag): records state and
            // propagates for LOCAL preview on non-live datasets exactly as
            // before, but never queries a live dataset -- neither this
            // slider's own dataset nor any dataset it propagates to (see
            // helpers.setFilterTransient / propagateTransient). commit=true
            // (the 'change' path, release/keyboard-settle) is the real
            // commit: auto-queries, same as every other control type.
            var applyValues = function(values, commit) {
                var set = commit ? h.setFilter : h.setFilterTransient;
                var prop = commit ? h.propagate : h.propagateTransient;
                if (isOrdinal) {
                    if (isSingle) {
                        var pos = Math.round(Number(values[0]));
                        var val = fc.values[pos];
                        set(fc.id, fc.column, 'equals', String(val));
                        prop(fc.column, 'equals', String(val));
                    } else {
                        var loPos = Math.round(Number(values[0]));
                        var hiPos = Math.round(Number(values[1]));
                        var span = fc.values.slice(loPos, hiPos + 1);
                        set(fc.id, fc.column, 'in', span);
                        prop(fc.column, 'in', span);
                    }
                } else if (isSingle) {
                    var snapped = self._nearestValue(fc, Number(values[0]));
                    set(fc.id, fc.column, 'equals', String(snapped));
                    prop(fc.column, 'equals', String(snapped));
                } else {
                    var rangeVal = { min: Number(values[0]), max: Number(values[1]) };
                    set(fc.id, fc.column, 'numrange', rangeVal);
                    prop(fc.column, 'numrange', rangeVal);
                }
            };

            // Live while dragging: every tick re-filters, but collapsed to at
            // most one apply per animation frame so a fast drag costs one
            // re-aggregation per paint, not one per pixel. The URL is written
            // only on 'change' (release / keyboard settle) so mid-drag states
            // never spam the sharable URL.
            var pending = null;
            var frame = null;
            node.noUiSlider.on('update', function(values) {
                self._updateReadout(el, fc, values);
            });
            node.noUiSlider.on('slide', function(values) {
                pending = values.slice();
                if (frame === null) {
                    frame = window.requestAnimationFrame(function() {
                        frame = null;
                        if (pending) { applyValues(pending, false); pending = null; }
                    });
                }
            });
            node.noUiSlider.on('change', function(values) {
                if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; pending = null; }
                applyValues(values, true);
                h.urlWrite();
            });
        });
    }
};

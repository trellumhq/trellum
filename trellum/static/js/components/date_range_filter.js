window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['date_range'] = {
    _fmtDate: function(dateStr) {
        return dateStr || '';
    },

    _readRange: function(dr, hourly) {
        var inputs = dr.querySelectorAll('.fw-date-input');
        var minD = inputs[0] ? inputs[0].value : '';
        var maxD = inputs[1] ? inputs[1].value : '';
        if (hourly) {
            var hours = dr.querySelectorAll('.fw-date-hour');
            var minH = hours[0] ? hours[0].value : '00:00';
            var maxH = hours[1] ? hours[1].value : '23:30';
            return { min: minD + ' ' + minH, max: maxD + ' ' + maxH };
        }
        return { min: minD, max: maxD };
    },

    _updateTrigger: function(dr, rangeVal) {
        var trigger = dr.querySelector('.fw-date-trigger-text');
        if (trigger) {
            trigger.textContent = rangeVal.min + ' \u2013 ' + rangeVal.max;
        }
    },

    _computePresetMin: function(fc, days, hours) {
        if (days === 'all') return fc.min_date;
        if (days === 'ytd') {
            var base = fc.max_date.substring(0, 4) + '-01-01';
            return fc.hourly ? base + ' 00:00' : base;
        }
        if (hours && !days) {
            var d = new Date(fc.max_date.substring(0, 10) + 'T' +
                (fc.max_date.length > 10 ? fc.max_date.substring(11, 16) : '00:00') + ':00');
            d.setHours(d.getHours() - parseInt(hours, 10));
            var y = d.getFullYear(), mo = d.getMonth() + 1, da = d.getDate();
            var hh = d.getHours(), mm = d.getMinutes();
            return y + '-' + (mo<10?'0'+mo:mo) + '-' + (da<10?'0'+da:da) +
                   ' ' + (hh<10?'0'+hh:hh) + ':' + (mm<10?'0'+mm:mm);
        }
        var numDays = parseInt(days, 10);
        var maxBase = fc.max_date.substring(0, 10);
        var d = new Date(maxBase + 'T00:00:00');
        d.setDate(d.getDate() - numDays);
        var y = d.getFullYear(), mo = d.getMonth() + 1, da = d.getDate();
        var computed = y + '-' + (mo<10?'0'+mo:mo) + '-' + (da<10?'0'+da:da);
        if (fc.hourly) computed += ' 00:00';
        var minDate = fc.min_date;
        return computed < minDate ? minDate : computed;
    },

    _matchPreset: function(fc, minVal, maxVal) {
        if (!fc.presets) return null;
        var self = window._fwFilterTypes['date_range'];
        for (var i = 0; i < fc.presets.length; i++) {
            var p = fc.presets[i];
            var pMin = self._computePresetMin(fc, p.days, p.hours);
            var pMax = fc.max_date;
            if (pMin === minVal && pMax === maxVal) return p.label;
        }
        return null;
    },

    _highlightPreset: function(container, activeLabel) {
        container.querySelectorAll('.fw-date-preset').forEach(function(btn) {
            btn.classList.toggle('active', btn.textContent.trim() === activeLabel);
        });
    },

    init: function(el, fc, dsId, h) {
        var self = window._fwFilterTypes['date_range'];
        var urlVal = h.urlLookup(fc.column);
        if (urlVal !== undefined) {
            var presetMin = null;
            var presetLabel = null;
            if (fc.presets) {
                for (var i = 0; i < fc.presets.length; i++) {
                    if (fc.presets[i].label === urlVal) {
                        presetMin = self._computePresetMin(fc, fc.presets[i].days, fc.presets[i].hours);
                        presetLabel = urlVal;
                        break;
                    }
                }
            }
            if (presetMin !== null) {
                var rangeVal = { min: presetMin, max: fc.max_date };
                h.setFilter(fc.id, fc.column, 'range', rangeVal);
                h.propagate(fc.column, 'range', rangeVal);
                var dr = el.querySelector('[data-filter-id="' + fc.id + '"].fw-date-range');
                if (dr) {
                    self._setInputs(dr, rangeVal, fc.hourly);
                    self._updateTrigger(dr, rangeVal);
                    self._highlightPreset(dr, presetLabel);
                }
            } else {
                var decoded = window._fwUrlSync ? window._fwUrlSync.decode(urlVal, 'date_range') : {};
                if (decoded.min && decoded.max) {
                    h.setFilter(fc.id, fc.column, 'range', decoded);
                    h.propagate(fc.column, 'range', decoded);
                    var dr = el.querySelector('[data-filter-id="' + fc.id + '"].fw-date-range');
                    if (dr) {
                        self._setInputs(dr, decoded, fc.hourly);
                        self._updateTrigger(dr, decoded);
                        var match = self._matchPreset(fc, decoded.min, decoded.max);
                        self._highlightPreset(dr, match || '');
                    }
                } else {
                    var rv = { min: fc.default_min_date || fc.min_date, max: fc.max_date };
                    h.setFilter(fc.id, fc.column, 'range', rv);
                    h.propagate(fc.column, 'range', rv);
                }
            }
        } else {
            var rangeVal = { min: fc.default_min_date || fc.min_date, max: fc.max_date };
            h.setFilter(fc.id, fc.column, 'range', rangeVal);
            h.propagate(fc.column, 'range', rangeVal);
        }
    },

    _setInputs: function(dr, rangeVal, hourly) {
        var inputs = dr.querySelectorAll('.fw-date-input');
        if (inputs[0]) inputs[0].value = rangeVal.min.substring(0, 10);
        if (inputs[1]) inputs[1].value = rangeVal.max.substring(0, 10);
        if (hourly) {
            var hours = dr.querySelectorAll('.fw-date-hour');
            if (hours[0] && rangeVal.min.length > 10) hours[0].value = rangeVal.min.substring(11, 16);
            if (hours[1] && rangeVal.max.length > 10) hours[1].value = rangeVal.max.substring(11, 16);
        }
    },

    wireControls: function(el, cfg, dsId, h) {
        var self = window._fwFilterTypes['date_range'];

        el.querySelectorAll('.fw-date-range').forEach(function(dr) {
            var fc = h.findFc(dr.dataset.filterId);
            if (!fc) return;
            var hourly = dr.dataset.hourly === 'true';

            // Toggle popover on trigger click
            var trigger = dr.querySelector('.fw-date-trigger');
            var popover = dr.querySelector('.fw-date-popover');
            if (trigger && popover) {
                trigger.addEventListener('click', function(e) {
                    e.stopPropagation();
                    var wasOpen = popover.classList.contains('open');
                    // Close all other open popovers first
                    document.querySelectorAll('.fw-date-popover.open').forEach(function(p) {
                        p.classList.remove('open');
                    });
                    if (!wasOpen) popover.classList.add('open');
                });
            }

            // Apply on date input change
            function applyRange() {
                var rangeVal = self._readRange(dr, hourly);
                h.setFilter(fc.id, fc.column, 'range', rangeVal);
                h.propagate(fc.column, 'range', rangeVal);
                self._updateTrigger(dr, rangeVal);
                var match = self._matchPreset(fc, rangeVal.min, rangeVal.max);
                self._highlightPreset(dr, match || '');
                h.urlWrite();
            }

            dr.querySelectorAll('.fw-date-input').forEach(function(inp) {
                inp.addEventListener('change', applyRange);
            });
            dr.querySelectorAll('.fw-date-hour').forEach(function(sel) {
                sel.addEventListener('change', applyRange);
            });

            // Preset click
            dr.addEventListener('click', function(e) {
                var preset = e.target.closest('.fw-date-preset');
                if (!preset) return;
                var days = preset.dataset.days || null;
                var hours = preset.dataset.hours || null;
                if (days === '') days = null;
                if (hours === '') hours = null;
                var minVal = self._computePresetMin(fc, days, hours);
                var rangeVal = { min: minVal, max: fc.max_date };
                self._setInputs(dr, rangeVal, hourly);
                h.setFilter(fc.id, fc.column, 'range', rangeVal);
                h.propagate(fc.column, 'range', rangeVal);
                self._updateTrigger(dr, rangeVal);
                self._highlightPreset(dr, preset.textContent.trim());
                h.urlWrite();
            });
        });

        // Close popover on outside click
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.fw-date-range')) {
                document.querySelectorAll('.fw-date-popover.open').forEach(function(p) {
                    p.classList.remove('open');
                });
            }
        });
    }
};

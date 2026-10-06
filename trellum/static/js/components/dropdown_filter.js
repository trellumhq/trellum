window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['dropdown'] = {
    _ssInstances: {},
    _batchMode: false,

    init: function(el, fc, dsId, h) {
        var self = window._fwFilterTypes['dropdown'];
        var urlVal = h.urlLookup(fc.column);
        if (urlVal !== undefined) {
            var vals = window._fwUrlSync ? window._fwUrlSync.decode(urlVal, 'dropdown') : [];
            var valid = vals.filter(function(v) {
                return fc.options && fc.options.indexOf(v) !== -1;
            });
            if (valid.length > 0) {
                fc._urlValues = valid;
                h.setFilter(fc.id, fc.column, 'in', valid);
                h.propagate(fc.column, 'in', valid);
            }
        }
    },

    wireControls: function(el, cfg, dsId, h) {
        var self = window._fwFilterTypes['dropdown'];
        if (typeof SlimSelect === 'undefined') return;

        el.querySelectorAll('select.fw-dropdown').forEach(function(sel) {
            var fid = sel.dataset.filterId;
            var fc = h.findFc(fid);
            var opts = (fc && fc.options || []).map(function(o) {
                return {text: String(o), value: String(o)};
            });

            sel.innerHTML = '';
            opts.forEach(function(o) {
                var optEl = document.createElement('option');
                optEl.value = o.value;
                optEl.textContent = o.text;
                sel.appendChild(optEl);
            });

            var ss = new SlimSelect({
                select: sel,
                settings: {
                    contentPosition: 'fixed',
                    allowDeselect: true,
                    closeOnSelect: false,
                    showSearch: true,
                    searchHighlight: true,
                    placeholderText: (fc && fc.placeholder) ? fc.placeholder : '(All)',
                    maxValuesShown: 5
                },
                events: {
                    afterChange: function(newVal) {
                        if (self._batchMode) return;
                        // Single-select mode (fc.multi === false, live-bound
                        // dropdowns): SlimSelect hands back one object (or
                        // none), not an array -- normalize to the same
                        // zero-or-one-element array shape 'in' mode expects.
                        var list = Array.isArray(newVal) ? newVal : (newVal ? [newVal] : []);
                        var arr = list.map(function(v) { return v.value; });
                        h.setFilter(fc.id, fc.column, 'in', arr);
                        h.propagate(fc.column, 'in', arr);
                        h.urlWrite();
                    }
                }
            });
            self._ssInstances[fid] = ss;
            sel._fwSlimSelect = ss;
            if (fc && fc._urlValues && fc._urlValues.length) {
                ss.setSelected(fc._urlValues);
            } else if (fc && fc.default_empty) {
                self._batchMode = true;
                try {
                    ss.setSelected([]);
                    h.setFilter(fc.id, fc.column, 'in', []);
                    h.propagate(fc.column, 'in', []);
                } finally {
                    self._batchMode = false;
                }
            }

            // ── Cascading (depends_on) support ────────────────────────
            // When the parent filter changes, recompute this dropdown's
            // option list from the parent-filtered rows of the DataSource.
            // The filter engine notifies subscribers on EVERY filter
            // change (including the child's own selection), so we cache:
            //   (a) parent column selection signature  → skip recompute
            //       when parents didn't change (prevents flicker on
            //       self-select);
            //   (b) computed option list                → skip ss.setData
            //       when options are identical (avoid re-render).
            if (fc && fc.depends_on && fc.depends_on.length) {
                var parentCols = fc.depends_on;
                var selfCol = fc.column;
                var lastParentSig = null;
                var lastOptionList = null;
                var recompute = function() {
                    if (!window._fwFilterEngine.isReady(dsId)) return;
                    // Cheap parent-signature check: only do the heavy
                    // scan + setData when a parent's selection changed.
                    var state = window._fwFilterEngine.getFilterState
                        ? window._fwFilterEngine.getFilterState(dsId)
                        : (window._fwFilterEngine._state[dsId] || {});
                    var sigParts = [];
                    for (var pi = 0; pi < parentCols.length; pi++) {
                        var pc = parentCols[pi];
                        var found = '';
                        for (var fk in state) {
                            if (state[fk] && state[fk].column === pc) {
                                found = JSON.stringify(state[fk].value || null);
                                break;
                            }
                        }
                        sigParts.push(pc + '=' + found);
                    }
                    var sig = sigParts.join('|');
                    if (sig === lastParentSig) return;
                    lastParentSig = sig;

                    var rows = window._fwFilterEngine.getFilteredExcluding(dsId, [selfCol]);
                    var seen = {};
                    for (var i = 0; i < rows.length; i++) {
                        var v = rows[i][selfCol];
                        if (v == null || v === '') continue;
                        seen[String(v)] = true;
                    }
                    var available = Object.keys(seen).sort();
                    var availableSig = available.join('');

                    var currentSel = ss.getSelected() || [];
                    var preserved = currentSel.filter(function(v) {
                        return seen[String(v)];
                    });
                    var optionsChanged = (availableSig !== lastOptionList);
                    lastOptionList = availableSig;

                    if (optionsChanged) {
                        self._batchMode = true;
                        try {
                            ss.setData(available.map(function(v) {
                                return { text: v, value: v };
                            }));
                            if (preserved.length) ss.setSelected(preserved);
                        } finally {
                            self._batchMode = false;
                        }
                    }
                    // Push filter through only if the preserved set changed
                    // — protects against ghost selections after a parent
                    // narrowing event.
                    if (preserved.length !== currentSel.length) {
                        h.setFilter(fc.id, fc.column, 'in', preserved);
                        h.propagate(fc.column, 'in', preserved);
                        h.urlWrite();
                    }
                };
                window._fwFilterEngine.onReady(dsId, function() {
                    recompute();
                    window._fwFilterEngine.subscribe(
                        dsId, 'cascade_' + fid, function() { recompute(); }
                    );
                });
            }
        });

        el.addEventListener('click', function(e) {
            var link = e.target.closest('.fw-filter-action');
            if (!link) return;
            e.preventDefault();
            var fid = link.dataset.filterId;
            var ss = self._ssInstances[fid];
            var fc = h.findFc(fid);
            if (!ss || !fc) return;
            self._batchMode = true;
            try {
                if (link.dataset.action === 'select-all') {
                    ss.setSelected(fc.options.map(String));
                } else {
                    ss.setSelected([]);
                }
            } finally {
                self._batchMode = false;
            }
            var selected = ss.getSelected();
            var arr = Array.isArray(selected) ? selected : (selected ? [selected] : []);
            h.setFilter(fc.id, fc.column, 'in', arr);
            h.propagate(fc.column, 'in', arr);
            h.urlWrite();
        });
    }
};

window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['dropdown'] = {
    _ssInstances: {},
    _batchMode: false,

    selectedValues: function(ss) {
        // Empty is reserved for the hidden single-select All placeholder;
        // actual dropdown options have always omitted empty/null cells.
        return (ss.getSelected() || []).filter(function(value) { return value !== ''; });
    },

    setCascadeOptions: function(ss, fc, available, selected, optionsChanged) {
        // SlimSelect's single-select store turns setSelected([]) into the first
        // option. Its supported placeholder option represents All coherently in
        // both the native select and visible widget, without adding a menu item.
        var needsPlaceholder = fc.multi === false && selected.length === 0;
        if (needsPlaceholder && !ss._fwCascadePlaceholder) optionsChanged = true;
        if (needsPlaceholder) ss._fwCascadePlaceholder = true;
        if (optionsChanged) {
            var data = available.map(function(value) { return {text: value, value: value}; });
            if (ss._fwCascadePlaceholder) data.unshift({text: fc.placeholder || '(All)',
                value: '', placeholder: true, display: false});
            ss.setData(data);
        }
        ss.setSelected(selected.length ? selected : (ss._fwCascadePlaceholder ? [''] : []), false);
    },

    batchControls: function(controls, fn) {
        var previous = this._batchMode;
        // SlimSelect debounces afterChange. A synchronous boolean alone cannot
        // suppress callbacks already scheduled by programmatic setData calls.
        var callbacks = controls.map(function(ss) {
            var callback = ss.events && ss.events.afterChange;
            if (ss.events) ss.events.afterChange = undefined;
            return callback;
        });
        this._batchMode = true;
        try { return fn(); }
        finally {
            this._batchMode = previous;
            controls.forEach(function(ss, i) {
                if (ss.events) ss.events.afterChange = callbacks[i];
            });
        }
    },

    disposeControls: function(el) {
        var disposers = el._fwDropdownDisposers || [];
        el._fwDropdownDisposers = [];
        disposers.forEach(function(dispose) { dispose(); });
    },

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
        var disposers = el._fwDropdownDisposers = [];

        el.querySelectorAll('select.fw-dropdown').forEach(function(sel) {
            var disposed = false;
            var unsubscribe = null;
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
                    searchHighlight: false,
                    placeholderText: (fc && fc.placeholder) ? fc.placeholder : '(All)',
                    maxValuesShown: 5
                },
                events: {
                    afterChange: function(newVal) {
                        if (disposed || self._batchMode) return;
                        // Single-select mode (fc.multi === false, live-bound
                        // dropdowns): SlimSelect hands back one object (or
                        // none), not an array -- normalize to the same
                        // zero-or-one-element array shape 'in' mode expects.
                        var list = Array.isArray(newVal) ? newVal : (newVal ? [newVal] : []);
                        var arr = list.filter(function(v) { return !v.placeholder; }).map(function(v) { return v.value; });
                        // Ignore an older debounced event after preparation
                        // corrected the control, or an already committed value.
                        var signature = function(values) { return JSON.stringify(Array.from(new Set(values)).sort()); };
                        if (signature(arr) !== signature(self.selectedValues(ss))) return;
                        var committed = window._fwFilterEngine.getFilterState(dsId)[fc.id];
                        if (committed && committed.mode === 'in' && signature(committed.value || []) === signature(arr)) return;
                        h.transaction(function() {
                            h.setFilter(fc.id, fc.column, 'in', arr);
                            h.propagate(fc.column, 'in', arr);
                            h.urlWrite();
                        });
                    }
                }
            });
            self._ssInstances[fid] = ss;
            sel._fwSlimSelect = ss;
            // SlimSelect copies aria-labelledby to its visible combobox. Its
            // search input lives in a portal outside the filter bar.
            var label = document.getElementById(sel.getAttribute('aria-labelledby'));
            var focusControl = function() { ss.render.main.main.focus(); };
            if (label) {
                ss.render.content.search.input.setAttribute('aria-label', 'Search ' + label.textContent.trim());
                label.addEventListener('click', focusControl);
            }
            disposers.push(function() {
                if (disposed) return;
                disposed = true;
                if (unsubscribe) unsubscribe();
                if (label) label.removeEventListener('click', focusControl);
                if (self._ssInstances[fid] === ss) delete self._ssInstances[fid];
                delete sel._fwSlimSelect;
                // Includes the body-level popup and SlimSelect's global
                // listeners, and restores the native select before cloning.
                ss.destroy();
            });
            if (fc && fc._urlValues && fc._urlValues.length) {
                ss.setSelected(fc._urlValues, false);
            } else if (fc && fc.default_empty) {
                self.batchControls([ss], function() {
                    self.setCascadeOptions(ss, fc, fc.options.map(String), [], true);
                    h.setFilter(fc.id, fc.column, 'in', []);
                    h.propagate(fc.column, 'in', []);
                });
            }

            // FilterBar coordinates all cascading controls before row consumers
            // are notified. A dropdown owns only its control and disposal.
            if (fc && fc.depends_on && fc.depends_on.length) {
                unsubscribe = h.registerCascade(fc, ss, function() { return disposed; });
            }
        });

        el.addEventListener('click', function(e) {
            var link = e.target.closest('.fw-filter-action');
            if (!link) return;
            e.preventDefault();
            var fid = link.dataset.filterId;
            var select = Array.from(el.querySelectorAll('select.fw-dropdown')).find(function(node) {
                return node.dataset.filterId === fid;
            });
            var ss = select && select._fwSlimSelect;
            var fc = h.findFc(fid);
            if (!ss || !fc) return;
            h.transaction(function() {
                self.batchControls([ss], function() {
                    if (link.dataset.action === 'select-all') {
                        ss.setSelected(ss.getData().filter(function(option) { return !option.disabled && !option.placeholder; }).map(function(option) {
                            return option.value;
                        }));
                    } else {
                        var available = ss.getData().filter(function(option) { return !option.placeholder; }).map(function(option) {
                            return option.value;
                        });
                        self.setCascadeOptions(ss, fc, available, [], false);
                    }
                });
                var arr = self.selectedValues(ss);
                h.setFilter(fc.id, fc.column, 'in', arr);
                h.propagate(fc.column, 'in', arr);
                h.urlWrite();
            });
        });
    }
};

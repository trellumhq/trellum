    // ── Toggle visibility helper ─────────────────────────
    function _updateToggleVis(toggleId, activeValue) {
        var targets = document.querySelectorAll('[data-toggle-target="' + toggleId + '"]');
        targets.forEach(function(el) {
            var isShowing = el.dataset.toggleValue === activeValue;
            var wasHidden = el.style.display === 'none';
            el.style.display = isShowing ? '' : 'none';
            // Charts rendered while the element was hidden (display:none) get
            // a 0×0 canvas from Chart.js. Resize them now that the canvas is visible.
            if (isShowing && wasHidden) {
                el.querySelectorAll('canvas').forEach(function(canvas) {
                    var chart = window._chartInstances && window._chartInstances[canvas.id];
                    if (chart) chart.resize();
                });
            }
        });
    }

    function _initToggleVis() {
        document.querySelectorAll('.fw-toggle-group').forEach(function(g) {
            var id = g.dataset.toggleId;
            if (!id) return;
            var active = g.querySelector('.fw-toggle-btn.active');
            if (active) _updateToggleVis(id, active.textContent.trim());
        });
    }

    window._initToggleVis = _initToggleVis;

    // ── Toggle click handler ──────────────────────────────
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.fw-toggle-btn');
        if (!btn) return;
        var group = btn.closest('.fw-toggle-group');
        if (!group) return;
        group.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var toggleId = group.dataset.toggleId;

        if (toggleId === '__scope__') {
            window._currentScope = btn.dataset.scopeKey || btn.textContent.trim();
            if (typeof window.renderAll === 'function') window.renderAll();
            if (typeof window._refreshAllAnnotations === 'function') {
                window._refreshAllAnnotations();
            }
            _updateToggleVis(toggleId, btn.textContent.trim());
            if (typeof window.onScopeChange === 'function') {
                window.onScopeChange(window._currentScope);
            }
            return;
        }

        _updateToggleVis(toggleId, btn.textContent.trim());

        if (toggleId && window._fwUrlSync && window._fwUrlSync._toggles[toggleId] !== undefined) {
            window._fwUrlSync.write();
        }

        if (toggleId && typeof window.onToggleChange === 'function') {
            window.onToggleChange(toggleId, btn.textContent.trim());
        }
    });

    // Each new group owns its panels explicitly. Older generated markup has
    // consecutive sibling panels: stop at the next control, never descend.
    function _fwTabButtons(control) {
        var root = control.closest('.fw-tab-root');
        var group = control.matches('.fw-tab-group') ? control :
            (root && Array.from(root.querySelectorAll('.fw-tab-group')).find(function(g) {
                return g.closest('.fw-tab-root') === root;
            }));
        return group ? Array.from(group.children).filter(function(el) {
            return el.matches('.fw-tab-btn');
        }) : [];
    }

    function _fwTabPanels(control) {
        var root = control.closest('.fw-tab-root');
        if (root) return Array.from(root.querySelectorAll('.fw-tab-panel')).filter(function(p) {
            return p.closest('.fw-tab-root') === root;
        });
        var panels = [];
        var el = control.nextElementSibling;
        while (el) {
            if (el.matches('.fw-tab-panel')) panels.push(el);
            else if (el.matches('.fw-tab-group, select.fw-tab-dropdown, .fw-tab-root') ||
                (!el.matches('.ss-main, .ss-content') && el.querySelector('.fw-tab-group, select.fw-tab-dropdown, .fw-tab-root'))) break;
            el = el.nextElementSibling;
        }
        return panels;
    }

    function _fwSelectTab(control, index) {
        var panels = _fwTabPanels(control);
        if (!Number.isInteger(index) || index < 0 || index >= panels.length) return;
        var buttons = _fwTabButtons(control);
        buttons.forEach(function(btn, i) {
            btn.classList.toggle('active', i === index);
            btn.setAttribute('aria-selected', String(i === index));
            btn.tabIndex = i === index ? 0 : -1;
        });
        panels.forEach(function(panel, i) { panel.style.display = i === index ? '' : 'none'; });
        var root = control.closest('.fw-tab-root');
        var select = root ? Array.from(root.querySelectorAll('select.fw-tab-dropdown')).find(function(s) {
            return s.closest('.fw-tab-root') === root;
        }) : (control.matches('select') ? control : null);
        if (select) {
            select.value = String(index);
            if (select.slim && select.slim.getSelected()[0] !== select.value) {
                select._fwSyncingTab = true;
                try { select.slim.setSelected(select.value, false); }
                finally { select._fwSyncingTab = false; }
            }
        }
        // Lazy charts initialize through their existing visibility observer.
        // Already initialized charts need their new visible panel dimensions.
        requestAnimationFrame(function() {
            panels[index].querySelectorAll('canvas').forEach(function(canvas) {
                if (!canvas.getClientRects().length) return;
                var chart = (window._chartInstances || {})[canvas.id];
                if (chart) chart.resize();
            });
        });
    }

    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.fw-tab-btn');
        if (!btn) return;
        var group = btn.closest('.fw-tab-group');
        if (!group) return;
        _fwSelectTab(group, _fwTabButtons(group).indexOf(btn));
        if (window._fwUrlSync && group.dataset.tabId) window._fwUrlSync.write();
    });

    document.addEventListener('keydown', function(e) {
        var btn = e.target.closest('.fw-tab-btn');
        if (!btn) return;
        var group = btn.closest('.fw-tab-group');
        if (!group) return;
        var buttons = _fwTabButtons(group), index = buttons.indexOf(btn);
        var vertical = group.getAttribute('aria-orientation') === 'vertical';
        if (e.key === 'Home') index = 0;
        else if (e.key === 'End') index = buttons.length - 1;
        else if (e.key === (vertical ? 'ArrowDown' : 'ArrowRight')) index = (index + 1) % buttons.length;
        else if (e.key === (vertical ? 'ArrowUp' : 'ArrowLeft')) index = (index + buttons.length - 1) % buttons.length;
        else return;
        e.preventDefault();
        buttons[index].focus();
        buttons[index].click();
    });

    document.addEventListener('change', function(e) {
        var select = e.target.closest('select.fw-tab-dropdown');
        if (!select || select._fwSyncingTab) return;
        _fwSelectTab(select, Number(select.value));
        if (window._fwUrlSync && select.dataset.tabId) window._fwUrlSync.write();
    });

    document.addEventListener('DOMContentLoaded', function() {
        // Preserve initialization of older generated dropdown groups, without
        // opting them into new presentation metadata or changing their search.
        if (typeof SlimSelect !== 'undefined') {
            document.querySelectorAll('select.fw-tab-dropdown:not([data-fw-choice])').forEach(function(select) {
                if (!select.slim) new SlimSelect({select: select,
                    settings: {showSearch: false, allowDeselect: false},
                    events: {afterChange: function() {
                        select.dispatchEvent(new Event('change', {bubbles: true}));
                    }}
                });
            });
        }
        if (typeof ResizeObserver === 'undefined') return;
        // Browsers may blur a newly hidden control before ResizeObserver runs.
        // Remember focus until another element receives it, so reflow can hand
        // it to the equivalent visible navigation control.
        var lastFocused = document.activeElement;
        document.addEventListener('focusin', function(e) { lastFocused = e.target; });
        var observer = new ResizeObserver(function(entries) {
            entries.forEach(function(entry) {
                var root = entry.target;
                if (!root.getClientRects().length) return;
                var focused = document.activeElement;
                var select = root.querySelector('.fw-tab-nav select.fw-tab-dropdown');
                var picker = select && select.slim;
                if (entry.contentRect.width > 520 && picker && picker.settings.isOpen) {
                    var inSearch = picker.render.content.main.contains(focused);
                    picker.close();
                    if (inSearch) root.querySelector('.fw-tab-btn.active').focus({preventScroll: true});
                }
                if ((!focused || focused === document.body) && lastFocused &&
                    !lastFocused.getClientRects().length) focused = lastFocused;
                if (!focused || focused.closest('.fw-tab-root') !== root ||
                    !focused.closest('.fw-tab-nav')) return;
                if (focused.getClientRects().length) return;
                var target = entry.contentRect.width <= 520 ?
                    (root.querySelector('.fw-tab-choice .ss-main') || root.querySelector('.fw-tab-choice select')) :
                    root.querySelector('.fw-tab-btn.active');
                if (target) target.focus({preventScroll: true});
            });
        });
        document.querySelectorAll('.fw-tab-root[data-tab-mode="sidebar"]').forEach(function(root) {
            observer.observe(root);
        });
    });

    // ── Theme dropdown handler (event delegation) ─────
    document.addEventListener('change', function(e) {
        if (e.target && e.target.id === 'fwThemeSelect') {
            switchTheme(e.target.value);
        }
    });

    // Sync theme dropdown value once DOM is ready
    document.addEventListener('DOMContentLoaded', function() {
        var sel = document.getElementById('fwThemeSelect');
        if (sel) {
            var active = _lsGet('fw-theme') || getActiveTheme();
            if (active && window._themes[active]) sel.value = active;
        }
    });

    // Refresh annotation chip colours whenever the theme changes so label
    // text (tick_color) and line colours re-resolve for the new theme.
    window.addEventListener('fw-theme-change', function() {
        if (typeof window._refreshAllAnnotations === 'function') {
            window._refreshAllAnnotations();
        }
    });

    // ── KPI mobile collapse toggle ───────────────────────
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.fw-kpi-more-btn');
        if (!btn) return;
        const grid = btn.previousElementSibling;
        if (!grid || !grid.classList.contains('fw-kpi-grid')) return;
        const open = grid.classList.toggle('open');
        btn.setAttribute('aria-expanded', open);
        const count = grid.querySelectorAll('.fw-kpi-extra').length;
        btn.textContent = open ? '− Show less' : '+ ' + count + ' more';
    });

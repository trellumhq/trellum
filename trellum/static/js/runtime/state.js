    // ── State Preservation ────────────────────────────────
    function _saveState() {
        const state = {};
        // Toggles
        document.querySelectorAll('.fw-toggle-group').forEach(function(g) {
            const id = g.dataset.toggleId;
            const active = g.querySelector('.fw-toggle-btn.active');
            if (id && active) state['toggle_' + id] = active.textContent.trim();
        });
        // Dropdowns
        document.querySelectorAll('select.fw-dropdown').forEach(function(d) {
            if (d.id) state['dropdown_' + d.id] = d.value;
        });
        // Tabs (button mode)
        document.querySelectorAll('.fw-tab-group').forEach(function(t) {
            const id = t.dataset.tabId;
            const active = t.querySelector('.fw-tab-btn.active');
            if (id && active) state['tab_' + id] = active.textContent.trim();
        });
        // Tabs (dropdown mode)
        document.querySelectorAll('select.fw-tab-dropdown:not([data-tab-mirror])').forEach(function(d) {
            const id = d.dataset.tabId;
            if (id) state['tab_' + id] = d.value;
        });
        // Scroll
        state._scrollY = window.scrollY;
        return state;
    }

    function _restoreState(state) {
        if (!state) return;
        // Toggles
        document.querySelectorAll('.fw-toggle-group').forEach(function(g) {
            const id = g.dataset.toggleId;
            const saved = state['toggle_' + id];
            if (!saved) return;
            g.querySelectorAll('.fw-toggle-btn').forEach(function(btn) {
                btn.classList.toggle('active', btn.textContent.trim() === saved);
            });
        });
        // Dropdowns
        document.querySelectorAll('select.fw-dropdown').forEach(function(d) {
            const saved = state['dropdown_' + d.id];
            if (saved !== undefined) {
                d.value = saved;
                if (d.hasAttribute('data-fw-choice') && d.slim && d.slim.getSelected()[0] !== d.value) d.slim.setSelected(d.value, false);
            }
        });
        // Tabs (button mode)
        document.querySelectorAll('.fw-tab-group').forEach(function(t) {
            const id = t.dataset.tabId;
            const saved = state['tab_' + id];
            if (!saved) return;
            const btns = _fwTabButtons(t);
            const idx = btns.findIndex(function(btn) { return btn.textContent.trim() === saved; });
            _fwSelectTab(t, idx);
        });
        // Tabs (dropdown mode); sidebar mirrors use their button-mode label.
        document.querySelectorAll('select.fw-tab-dropdown:not([data-tab-mirror])').forEach(function(d) {
            const saved = state['tab_' + d.dataset.tabId];
            if (saved !== undefined) _fwSelectTab(d, Number(saved));
        });
        // Scroll
        if (state._scrollY) window.scrollTo(0, state._scrollY);
    }

    window._saveState = _saveState;
    window._restoreState = _restoreState;

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
        document.querySelectorAll('.fw-dropdown').forEach(function(d) {
            if (d.id) state['dropdown_' + d.id] = d.value;
        });
        // Tabs (button mode)
        document.querySelectorAll('.fw-tab-group').forEach(function(t) {
            const id = t.dataset.tabId;
            const active = t.querySelector('.fw-tab-btn.active');
            if (id && active) state['tab_' + id] = active.textContent.trim();
        });
        // Tabs (dropdown mode)
        document.querySelectorAll('.fw-tab-dropdown').forEach(function(d) {
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
        document.querySelectorAll('.fw-dropdown').forEach(function(d) {
            const saved = state['dropdown_' + d.id];
            if (saved !== undefined) d.value = saved;
        });
        // Tabs (button mode)
        document.querySelectorAll('.fw-tab-group').forEach(function(t) {
            const id = t.dataset.tabId;
            const saved = state['tab_' + id];
            if (!saved) return;
            t.querySelectorAll('.fw-tab-btn').forEach(function(btn) {
                const match = btn.textContent.trim() === saved;
                btn.classList.toggle('active', match);
            });
            const panels = t.parentElement.querySelectorAll('.fw-tab-panel');
            const btns = Array.from(t.querySelectorAll('.fw-tab-btn'));
            const idx = btns.findIndex(function(b) { return b.classList.contains('active'); });
            panels.forEach(function(p, i) { p.style.display = i === idx ? '' : 'none'; });
        });
        // Tabs (dropdown mode)
        document.querySelectorAll('.fw-tab-dropdown').forEach(function(d) {
            const id = d.dataset.tabId;
            const saved = state['tab_' + id];
            if (saved === undefined) return;
            d.value = saved;
            const idx = parseInt(saved, 10);
            var el = d.nextElementSibling;
            var pi = 0;
            while (el) {
                if (el.classList && el.classList.contains('fw-tab-panel')) {
                    el.style.display = pi === idx ? '' : 'none';
                    pi++;
                }
                el = el.nextElementSibling;
            }
        });
        // Scroll
        if (state._scrollY) window.scrollTo(0, state._scrollY);
    }

    window._saveState = _saveState;
    window._restoreState = _restoreState;

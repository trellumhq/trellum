    /* ── keyboard: Esc closes popover -> select -> panel ────── */
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (pop.style.display === 'block') { hidePop(); return; }
        if (state.selecting) { setSelecting(false); return; }
        if (panel.classList.contains('open')) setPanel(false);
    });

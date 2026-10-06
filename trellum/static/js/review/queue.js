    /* ── queue persistence + rendering ──────────────────────── */
    function persistQueue() { lsSet(QKEY, JSON.stringify(state.items)); }

    window.addEventListener('storage', function (e) {
        if (e.key !== QKEY) return;
        try { state.items = JSON.parse(e.newValue || '[]'); } catch (err) { return; }
        // Renumber from the synced queue, or two tabs hand out the same n.
        state.items.forEach(function (it) { if (it.n >= state.nextN) state.nextN = it.n + 1; });
        render();
    });

    function pinTargetFor(item) {
        // Re-resolve the element on every render: positions move as data
        // loads and layouts settle, and the element may be gone after a
        // rebuild (the pin then simply doesn't draw).
        try { return document.querySelector(item.selector); } catch (e) { return null; }
    }

    function render() {
        countEl.textContent = state.items.length;
        countEl.classList.toggle('zero', !state.items.length);
        // The toolbar grows its own Send the moment something is queued, so
        // submitting never requires a detour through the panel.
        barSend.style.display = state.items.length && !state.ended ? 'inline-flex' : 'none';
        barSend.textContent = 'Send ' + state.items.length;
        pins.innerHTML = '';
        list.innerHTML = '';
        state.items.forEach(function (it) {
            var target = pinTargetFor(it);
            if (target) {
                var r = docRect(target);
                var pin = el('div', 'fwrv-pin');
                pin.setAttribute('data-fw-review-ui', '');
                pin.textContent = it.n;
                pin.title = it.prompt;
                pin.style.top = (r.top - 8) + 'px';
                pin.style.left = (r.left + r.w - 12) + 'px';
                pin.addEventListener('click', function () { setPanel(true); });
                pins.appendChild(pin);
            }
            var d = el('div', 'fwrv-item');
            var who = it.component
                ? '<b>' + esc(it.component.kind) + (it.component.title ? ' “' + esc(it.component.title) + '”' : '') + '</b>' +
                  (it.section && it.section.title ? ' · ' + esc(it.section.title) : '')
                : '<b>Section' + (it.section && it.section.title ? ' “' + esc(it.section.title) + '”' : '') + '</b>';
            var dataLine = it.data ? '<div style="font-size:10.5px;color:var(--text-muted,#8a8aa8);margin-top:2px">' +
                dataChipHtml(it.data).replace(/<span class="fwrv-k">/, '<b style="color:var(--bg-header,#0d9488)">').replace('</span>', '</b> ') +
                '</div>' : '';
            d.innerHTML = '<div class="fwrv-top"><span class="fwrv-n">' + it.n + '</span>' +
                '<span class="fwrv-who">' + who +
                ' <span style="font-family:Menlo,Consolas,monospace;font-size:10px;color:var(--text-muted,#8a8aa8)">' + esc(it.selector) + '</span>' + dataLine + '</span>' +
                '<button type="button" class="fwrv-x" title="Remove">&times;</button></div>';
            var ta = document.createElement('textarea');
            ta.value = it.prompt;
            ta.addEventListener('input', function () { it.prompt = ta.value; persistQueue(); });
            d.appendChild(ta);
            d.querySelector('.fwrv-x').addEventListener('click', function () {
                state.items = state.items.filter(function (x) { return x !== it; });
                persistQueue();
                render();
            });
            list.appendChild(d);
        });
        if (!state.items.length) {
            list.innerHTML = '<div class="fwrv-empty">Nothing queued yet.<br>Toggle <b>Select</b> and click an element —<br>or just type in the chat below.</div>';
            maybeOfferResend();
        }
    }

    function maybeOfferResend() {
        var sent = null;
        try { sent = JSON.parse(lsGet(SENTKEY) || 'null'); } catch (e) {}
        if (!sent || !sent.items || !sent.items.length) return;
        if (state.session === null || sent.session === state.session) return;
        var d = el('div', 'fwrv-item');
        d.innerHTML = '<div class="fwrv-who" style="font-size:11px">The server restarted after your last send (' +
            sent.items.length + ' item' + (sent.items.length === 1 ? '' : 's') + ').</div>';
        var b = el('button', 'fwrv-b');
        b.type = 'button';
        b.textContent = 'Resend last batch';
        b.style.marginTop = '6px';
        b.addEventListener('click', function () {
            state.items = sent.items;
            state.items.forEach(function (it) { if (it.n >= state.nextN) state.nextN = it.n + 1; });
            persistQueue();
            render();
        });
        d.appendChild(b);
        list.appendChild(d);
    }

    window.addEventListener('resize', function () { render(); hl.style.display = 'none'; });
    // Charts render asynchronously and shift layout; keep pins honest.
    setInterval(function () { if (state.items.length) render(); }, 1500);

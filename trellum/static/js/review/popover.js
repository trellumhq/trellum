    /* ── popover ────────────────────────────────────────────── */
    function dataChipHtml(d) {
        if (d.type === 'chart-point') {
            return '<span class="fwrv-k">POINT</span><span>' + esc(d.series) +
                ' @ ' + esc(d.x) + ' = <b>' + esc(d.value) + '</b></span>';
        }
        if (d.type === 'chart-range') {
            return '<span class="fwrv-k">RANGE</span><span>' + esc(d.x_from) +
                ' → ' + esc(d.x_to) + ' · ' + d.series.length + ' series</span>';
        }
        return '';
    }

    function showPop(t, cx, cy, data) {
        popChip.innerHTML = chipHtml(t);
        pop._data = data || null;
        if (data) {
            popData.innerHTML = dataChipHtml(data);
            popData.style.display = 'inline-flex';
            popText.placeholder = data.type === 'chart-point'
                ? 'What about this data point?' : 'What about this range?';
        } else {
            popData.style.display = 'none';
            popText.placeholder = 'What should change here?';
        }
        popText.value = '';
        pop.style.display = 'block';
        var vw = document.documentElement.clientWidth;
        var top, left;
        if (cx !== undefined) {
            // Anchor at the cursor: the popover opens where the user
            // clicked, flipped above the click when the fold is close.
            var flip = cy > window.innerHeight - 230;
            top = cy + window.scrollY + (flip ? -200 : 12);
            left = cx + window.scrollX - 24;
        } else {
            var r = docRect(t.el);
            top = r.top + r.h + 8;
            left = r.left;
        }
        pop.style.top = Math.max(window.scrollY + 8, top) + 'px';
        pop.style.left = Math.max(8, Math.min(left, window.scrollX + vw - 316)) + 'px';
        pop._target = t;
        setTimeout(function () { popText.focus(); }, 30);
    }
    function hidePop() { pop.style.display = 'none'; pop._target = null; }
    $('fwrvPopCancel').addEventListener('click', hidePop);
    $('fwrvPopQueue').addEventListener('click', function () { queueFromPop(false); });
    $('fwrvPopQueueSend').addEventListener('click', function () { queueFromPop(true); });
    popText.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            queueFromPop(e.ctrlKey || e.metaKey);
        }
        if (e.key === 'Escape') { e.stopPropagation(); hidePop(); }
    });
    function queueFromPop(sendNow) {
        var text = popText.value.trim();
        var t = pop._target;
        if (!text || !t) return;
        state.items.push({
            n: state.nextN++,
            prompt: text,
            selector: t.selector,
            tag: t.tag,
            text: t.text,
            section: t.section,
            component: t.kind === 'Section' ? null : { id: t.id, kind: t.kind, title: t.title },
            data: pop._data || null
        });
        persistQueue();
        hidePop();
        // Select mode stays ON: queueing one request and immediately
        // picking the next element is the normal rhythm. Esc or the
        // toolbar toggle leaves it.
        render();
        if (sendNow) doSend(false);
    }

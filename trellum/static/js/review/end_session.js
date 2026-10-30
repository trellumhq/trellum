    /* ── end session ────────────────────────────────────────── */
    var endArmed = false;
    $('fwrvEnd').addEventListener('click', function () {
        if (state.ended) return;
        if (!endArmed) {
            endArmed = true;
            $('fwrvEnd').textContent = 'Really end?';
            $('fwrvEnd').classList.add('fwrv-danger');
            setTimeout(function () {
                endArmed = false;
                $('fwrvEnd').textContent = 'End';
                $('fwrvEnd').classList.remove('fwrv-danger');
            }, 2500);
            return;
        }
        api('POST', '/_fw/review/end', {}).then(showEnded).catch(showEnded);
    });
    function showEnded() {
        state.ended = true;
        setSelecting(false);
        hidePop();
        root.classList.add('fwrv-ended');
        note.disabled = true;
        $('fwrvSend').disabled = true;
        $('fwrvSendEnd').disabled = true;
        barSend.disabled = true;
        $('fwrvSelect').disabled = true;
        $('fwrvEnd').disabled = true;
        panel.querySelector('.fwrv-hd b').textContent = 'Review conversation';
        endbar.style.display = 'flex';
        bar.style.display = 'none';
        // Only this session may restore the listener after a final rebuild.
        var url = new URL(location.href);
        url.searchParams.set('fw_review_session', String(state.session));
        history.replaceState(history.state, '', url);
        layoutEnded();
        render();
        if (panel.contains(document.activeElement) && document.activeElement.disabled) {
            $('fwrvPanelClose').focus({preventScroll:true});
        }
    }
    function layoutEnded() {
        if (state.ended) panel.style.top = (endbar.offsetHeight + 12) + 'px';
    }
    new ResizeObserver(layoutEnded).observe(endbar);

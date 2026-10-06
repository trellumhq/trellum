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
        setPanel(false);
        endbar.style.display = 'block';
        bar.style.display = 'none';
    }

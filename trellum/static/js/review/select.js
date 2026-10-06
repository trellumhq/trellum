    /* ── select mode ────────────────────────────────────────── */
    function setSelecting(on) {
        if (state.ended) return;
        state.selecting = on;
        $('fwrvSelect').classList.toggle('on', on);
        document.body.classList.toggle('fwrv-selecting', on);
        if (!on) { hl.style.display = 'none'; state.target = null; }
    }

    $('fwrvSelect').addEventListener('click', function () {
        hidePop();
        setSelecting(!state.selecting);
    });

    /* ── panel ──────────────────────────────────────────────── */
    function setPanel(open) {
        panel.classList.toggle('open', open);
        bar.classList.toggle('shifted', open);
        ssSet(PANELKEY, open ? '1' : '0');   // survive the auto-reload
    }
    $('fwrvQueueBtn').addEventListener('click', function () { setPanel(!panel.classList.contains('open')); });
    $('fwrvChat').addEventListener('click', function () {
        var opening = !panel.classList.contains('open');
        setPanel(opening);
        if (opening) setTimeout(function () { note.focus(); }, 240);
    });
    $('fwrvPanelClose').addEventListener('click', function () { setPanel(false); });

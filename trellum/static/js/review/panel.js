    /* ── panel ──────────────────────────────────────────────── */
    function setPanel(open) {
        panel.classList.toggle('open', open);
        bar.classList.toggle('shifted', open);
        $('fwrvConversation').setAttribute('aria-expanded', String(open));
        ssSet(PANELKEY, open ? '1' : '0');   // survive the auto-reload
        if (!open && state.ended && panel.contains(document.activeElement)) {
            $('fwrvConversation').focus({preventScroll:true});
        }
    }
    $('fwrvQueueBtn').addEventListener('click', function () { setPanel(!panel.classList.contains('open')); });
    $('fwrvChat').addEventListener('click', function () {
        var opening = !panel.classList.contains('open');
        setPanel(opening);
        if (opening) setTimeout(function () { note.focus(); }, 240);
    });
    $('fwrvPanelClose').addEventListener('click', function () { setPanel(false); });
    $('fwrvConversation').addEventListener('click', function () {
        setPanel(!panel.classList.contains('open'));
    });

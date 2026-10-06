    /* ── log + toast ────────────────────────────────────────── */
    // Light formatting for agent replies: they arrive as one long string,
    // so honor newlines, **bold**, and break enumerations like "(1) ...;
    // (2) ..." onto their own lines. User messages keep their line breaks.
    function fmtChat(text) {
        return esc(text)
            .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
            .replace(/\n/g, '<br>')
            .replace(/(^|[\s;:])\((\d+)\)\s/g, '$1<br><b>($2)</b> ')
            .replace(/^<br>/, '');
    }

    function appendLogDom(who, text) {
        var m = el('div', 'fwrv-m ' + (who === 'you' ? 'fwrv-you' : 'fwrv-agent'));
        var body = who === 'you' ? esc(text).replace(/\n/g, '<br>') : fmtChat(text);
        m.innerHTML = '<span class="fwrv-w">' + (who === 'you' ? 'You' : 'Agent') + '</span>' + body;
        log.appendChild(m);
        log.scrollTop = log.scrollHeight;
    }

    // The conversation must survive the auto-reload after every rebuild:
    // agent replies could be refetched, but the "You" side lives only here.
    function addLog(who, text, replyN) {
        appendLogDom(who, text);
        state.chatLog.push({ who: who, text: text, n: replyN || 0 });
        if (state.chatLog.length > 120) state.chatLog = state.chatLog.slice(-120);
        lsSet(CHATKEY, JSON.stringify({ session: state.session, entries: state.chatLog }));
    }
    var toastTimer = null;
    function showToast(head, text) {
        toast.innerHTML = '<span class="fwrv-w">' + esc(head) + '</span>' + esc(text);
        toast.style.display = 'block';
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.style.display = 'none'; }, 4500);
    }

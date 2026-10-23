    /* Conversation rows are reconciled in place; activity never replaces drafts. */
    function renderConversation() {
        window._fwReviewActivity.render(log, state.activities || [], state.chatLog);
    }
    function addLog(who, text, replyN, ts) {
        state.chatLog.push({ who: who, text: text, n: replyN || 0, ts: ts || Date.now()/1000 });
        if (state.chatLog.length > 120) state.chatLog = state.chatLog.slice(-120);
        lsSet(CHATKEY, JSON.stringify({ session: state.session, entries: state.chatLog }));
        renderConversation();
    }
    var toastTimer = null;
    function showToast(head, text) {
        toast.innerHTML = '<span class="fwrv-w">' + esc(head) + '</span>' + esc(text);
        toast.style.display = 'block';
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.style.display = 'none'; }, 4500);
    }

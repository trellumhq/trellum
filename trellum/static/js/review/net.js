    /* ── HTTP ───────────────────────────────────────────────── */
    function api(method, path, body, signal) {
        return fetch(path, {
            method: method,
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
            cache: 'no-store', signal: signal
        }).then(function (r) {
            return r.json().then(function (j) { return { ok: r.ok, status: r.status, json: j }; });
        });
    }

    var pendingSend = null, sendBusy = false;
    try { pendingSend=JSON.parse(ssGet(QKEY+':pending')||'null'); } catch (_) {}
    var chatDraftSession = null;
    function saveChatDraft() {
        ssSet(QKEY+':note',JSON.stringify({session:state.session,value:note.value,
            start:note.selectionStart,end:note.selectionEnd,focused:document.activeElement===note}));
    }
    function restoreChatDraft(session) {
        if (chatDraftSession===session) return;
        chatDraftSession=session;
        if (pendingSend && pendingSend.session && pendingSend.session!==session) {
            if (!note.value) note.value=pendingSend.note||'';
            pendingSend=null;ssSet(QKEY+':pending','');
            showToast('Review session changed','The previous send is unconfirmed. Your draft is retained for this session.');
        }
        var draft=null;
        try { draft=JSON.parse(ssGet(QKEY+':note')||'null'); } catch (_) {}
        if (!draft || draft.session!==session || note.value) return;
        note.value=draft.value||'';
        note.setSelectionRange(draft.start||0,draft.end||0);
        if (draft.focused && panel.classList.contains('open')) note.focus({preventScroll:true});
    }
    note.addEventListener('input',saveChatDraft);
    window.addEventListener('pagehide',saveChatDraft);
    function confirmPendingSend(feedbackId) {
        if (!pendingSend || pendingSend.request_id !== feedbackId) return;
        var submitted=pendingSend;
        lsSet(SENTKEY, JSON.stringify({ session: state.session, items: submitted.items, ts: Date.now() }));
        state.items = state.items.filter(function(it){return !submitted.items.some(function(sent){return sent.n===it.n&&sent.prompt===it.prompt&&sent.selector===it.selector;});});
        persistQueue();
        if (note.value.trim()===submitted.note) note.value = '';
        saveChatDraft();
        pendingSend=null;ssSet(QKEY+':pending','');
        render();
        if (submitted.end_session) showEnded();
    }
    function doSend(endSession) {
        if (state.ended || sendBusy) return;
        var msg = note.value.trim();
        var items = state.items.slice();
        if (!items.length && !msg && !endSession && !pendingSend) return;
        if (!pendingSend) pendingSend = {
            request_id: 'feedback-'+window.crypto.randomUUID(),
            slug: SLUG,
            page: location.pathname,
            session: state.session,
            note: msg,
            end_session: !!endSession,
            items: items.map(function (it) {
                return {
                    n: it.n,
                    prompt: it.prompt, selector: it.selector, tag: it.tag,
                    text: it.text, section: it.section, component: it.component,
                    data: it.data || null
                };
            })
        };
        ssSet(QKEY+':pending', JSON.stringify(pendingSend));
        var submitted = pendingSend;
        sendBusy = true;
        var sendAbort=new AbortController(), sendDeadline=setTimeout(function(){sendAbort.abort();},8000);
        api('POST', '/_fw/review/feedback', submitted, sendAbort.signal).then(function (res) {
            if (!pendingSend || pendingSend.request_id!==submitted.request_id) return;
            if (!res.ok) {
                showToast('Not delivered', res.json.error || ('HTTP ' + res.status));
                return;
            }
            var now=Date.now()/1000;
            state.statusRevision=(state.statusRevision||0)+1;
            state.activities=state.activities||[];
            if (!state.activities.some(function(a){return a.feedback_id===res.json.feedback_id;})) {
                state.activities.push({feedback_id:res.json.feedback_id,state:'queued',text:submitted.note||'Sent '+submitted.items.length+' change request(s).',submitted_at:now,updated_at:now});
            }
            renderConversation();
            confirmPendingSend(res.json.feedback_id);
            tickStatus();
        }).catch(function () {
            if (pendingSend) showToast('Send unconfirmed', 'Your draft is retained. Sending again checks the same submission.');
        }).finally(function () { clearTimeout(sendDeadline);sendBusy=false; });
    }
    $('fwrvSend').addEventListener('click', function () { doSend(false); });
    $('fwrvSendEnd').addEventListener('click', function () { doSend(true); });
    barSend.addEventListener('click', function () { doSend(false); });

    note.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (note.value.trim()) doSend(false);
        }
    });

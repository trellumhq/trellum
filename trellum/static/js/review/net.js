    /* ── HTTP ───────────────────────────────────────────────── */
    function api(method, path, body) {
        return fetch(path, {
            method: method,
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
            cache: 'no-store'
        }).then(function (r) {
            return r.json().then(function (j) { return { ok: r.ok, status: r.status, json: j }; });
        });
    }

    function doSend(endSession) {
        if (state.ended) return;
        var msg = note.value.trim();
        var items = state.items.slice();
        if (!items.length && !msg && !endSession) return;
        api('POST', '/_fw/review/feedback', {
            slug: SLUG,
            page: location.pathname,
            session: state.session,
            note: msg,
            end_session: !!endSession,
            items: items.map(function (it) {
                return {
                    prompt: it.prompt, selector: it.selector, tag: it.tag,
                    text: it.text, section: it.section, component: it.component,
                    data: it.data || null
                };
            })
        }).then(function (res) {
            if (!res.ok) {
                showToast('Not delivered', res.json.error || ('HTTP ' + res.status));
                return;
            }
            if (items.length) addLog('you', 'Sent ' + items.length + ' change request' + (items.length === 1 ? '' : 's') + '.');
            if (msg) addLog('you', msg);
            lsSet(SENTKEY, JSON.stringify({ session: state.session, items: items, ts: Date.now() }));
            state.items = [];
            persistQueue();
            note.value = '';
            render();
            setAgent('working');
            if (endSession) showEnded();
        }).catch(function () {
            showToast('Not delivered', 'The dev server did not answer — is it still running?');
        });
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

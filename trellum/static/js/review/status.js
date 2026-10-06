    /* ── status poll: presence, replies, reload, ended ──────── */
    function restoreScroll() {
        var saved = ssGet(SCROLLKEY);
        if (saved === null) return;
        ssSet(SCROLLKEY, '');
        var y = parseInt(saved, 10);
        if (!y) return;
        var loading = document.getElementById('fwLoading');
        var deadline = Date.now() + 1500;
        (function tryRestore() {
            var gone = !loading || loading.style.display === 'none' ||
                loading.classList.contains('fade-out');
            if (gone || Date.now() > deadline) {
                window.scrollTo(0, y);
                setTimeout(render, 200);
                return;
            }
            setTimeout(tryRestore, 100);
        })();
    }
    restoreScroll();

    function tickStatus() {
        api('GET', '/_fw/review/status?slug=' + encodeURIComponent(SLUG) + '&after=' + state.lastReplyN)
            .then(function (res) {
                if (!res.ok) return;
                var s = res.json;
                state.session = s.session;
                if (s.ended && !state.ended) { showEnded(); return; }
                if (!state.ended) setAgent(s.agent.state);

                // First tick after a load: put the saved conversation back
                // (same session only), and advance lastReplyN past the
                // restored replies so the merge below can't double them.
                if (!state.chatRestored) {
                    state.chatRestored = true;
                    var stored = null;
                    try { stored = JSON.parse(lsGet(CHATKEY) || 'null'); } catch (err) {}
                    if (stored && stored.session === s.session && stored.entries) {
                        // Dedupe by reply number while replaying: an older
                        // build persisted doubled entries, and they must not
                        // survive the next reload.
                        var seen = {};
                        state.chatLog = [];
                        stored.entries.forEach(function (en) {
                            if (en.n) {
                                if (seen[en.n]) return;
                                seen[en.n] = true;
                                if (en.n > state.lastReplyN) state.lastReplyN = en.n;
                            }
                            state.chatLog.push(en);
                            appendLogDom(en.who, en.text);
                        });
                    } else if (stored) {
                        lsDel(CHATKEY);            // a different session's chat
                    }
                }

                (s.replies || []).forEach(function (r) {
                    // The request that carried these left BEFORE the store
                    // was restored, so its `after` cursor can be stale --
                    // filter again client-side or every reload doubles the
                    // conversation.
                    if (r.n <= state.lastReplyN) return;
                    addLog('agent', r.text, r.n);
                    if (!state.firstStatus) showToast('Agent replied', r.text);
                    state.lastReplyN = r.n;
                });

                if (s.data_version) {
                    if (state.dataVersion === null) {
                        state.dataVersion = s.data_version;
                    } else if (s.data_version !== state.dataVersion) {
                        ssSet(SCROLLKEY, String(window.scrollY));
                        persistQueue();
                        flash.style.display = 'flex';
                        setTimeout(function () { location.reload(); }, 350);
                        return;
                    }
                }
                state.firstStatus = false;
            })
            .catch(function () { if (!state.ended) setAgent('absent'); });
    }
    tickStatus();
    setInterval(tickStatus, STATUS_MS);

    setAgent('absent');
    if (ssGet(PANELKEY) === '1') setPanel(true);
    render();

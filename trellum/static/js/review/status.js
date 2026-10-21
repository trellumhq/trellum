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

    var statusBusy = false, statusAbort = null;
    function tickStatus() {
        if (statusBusy || document.hidden) return;
        statusBusy = true;
        var revision = state.statusRevision || 0;
        statusAbort = new AbortController();
        var deadline = setTimeout(function () { if (statusAbort) statusAbort.abort(); }, 8000);
        api('GET', '/_fw/review/status?slug=' + encodeURIComponent(SLUG) + '&after=' + state.lastReplyN, null, statusAbort.signal)
            .then(function (res) {
                if (!res.ok) return;
                if (revision !== (state.statusRevision || 0)) return;
                var s = res.json;
                if (state.session && state.session !== s.session) {
                    state.chatLog=[];state.lastReplyN=0;state.chatRestored=false;
                }
                state.session = s.session;
                restoreChatDraft(s.session);
                state.activities = s.activities || [];
                state.activities.forEach(function (activity) { confirmPendingSend(activity.feedback_id); });
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
                    addLog('agent', r.text, r.n, r.ts);
                    if (!state.firstStatus) showToast('Agent replied', r.text);
                    state.lastReplyN = r.n;
                });
                renderConversation();
                $('fwrvDotLbl').textContent = window._fwReviewActivity.summary(s);
                $('fwrvDotLbl2').textContent = window._fwReviewActivity.summary(s);
                if (s.ended && !state.ended) { showEnded(); return; }

                if (s.data_version) {
                    if (state.dataVersion === null) {
                        state.dataVersion = s.data_version;
                    } else if (s.data_version !== state.dataVersion) {
                        ssSet(SCROLLKEY, String(window.scrollY));
                        persistQueue();
                        flash.style.display = 'flex';
                        setTimeout(function () { saveChatDraft();location.reload(); }, 350);
                        return;
                    }
                }
                state.firstStatus = false;
            })
            .catch(function () {
                if (!state.ended) {
                    setAgent('absent');
                    (state.activities || []).forEach(function (a) { if (['picked_up','working','rebuilding'].indexOf(a.state)>=0) a.display_state='unknown'; });
                    renderConversation();
                }
            }).finally(function () { clearTimeout(deadline); statusBusy=false; statusAbort=null; });
    }
    tickStatus();
    setInterval(tickStatus, STATUS_MS);
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) { if (statusAbort) statusAbort.abort(); }
        else tickStatus();
    });
    window.addEventListener('pagehide', function () { if (statusAbort) statusAbort.abort(); });

    setAgent('absent');
    if (ssGet(PANELKEY) === '1') setPanel(true);
    render();

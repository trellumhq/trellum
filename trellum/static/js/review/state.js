    /* ── session constants and shared state ───────────── */
    var SLUG = (document.body && document.body.getAttribute('data-report-slug')) || 'report';
    var WORKSPACE = window._fwWorkspaceChild || null;
    var REVIEW_SESSION = window._fwReviewSession || {};
    var WORKSPACE_NS = WORKSPACE ? ':workspace:' + WORKSPACE.session + ':' + WORKSPACE.analysis_slug : '';
    var QKEY = 'fw-review-queue:' + SLUG + WORKSPACE_NS;
    var SENTKEY = 'fw-review-sent:' + SLUG + WORKSPACE_NS;
    var SCROLLKEY = 'fw-review-scroll:' + SLUG + WORKSPACE_NS;
    var CHATKEY = 'fw-review-chat:' + SLUG + WORKSPACE_NS;
    var PANELKEY = 'fw-review-panel:' + SLUG + WORKSPACE_NS;
    var STATUS_MS = 2000;

    function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }
    function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
    function ssSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} }

    var state = {
        selecting: false,
        items: [],                 // {n, prompt, selector, tag, text, section, component}
        nextN: 1,
        session: REVIEW_SESSION.session || null,
        agent: 'absent',
        working: false,
        ended: !!REVIEW_SESSION.ended,
        dataVersion: null,
        lastReplyN: 0,
        firstStatus: true,
        target: null,
        drag: null,
        suppressClick: false,
        chatLog: [],
        chatRestored: false
    };

    // A workspace queue belongs to its parent analysis session. Never import
    // a standalone review draft just because the same report is embedded.
    try { state.items = WORKSPACE ? [] : JSON.parse(lsGet(QKEY) || '[]'); } catch (e) { state.items = []; }
    state.items.forEach(function (it) { if (it.n >= state.nextN) state.nextN = it.n + 1; });

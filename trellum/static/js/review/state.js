    /* ── session constants and shared state ───────────── */
    var SLUG = (document.body && document.body.getAttribute('data-report-slug')) || 'report';
    var QKEY = 'fw-review-queue:' + SLUG;
    var SENTKEY = 'fw-review-sent:' + SLUG;
    var SCROLLKEY = 'fw-review-scroll:' + SLUG;
    var CHATKEY = 'fw-review-chat:' + SLUG;
    var PANELKEY = 'fw-review-panel:' + SLUG;
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
        session: null,
        agent: 'absent',
        working: false,
        ended: false,
        dataVersion: null,
        lastReplyN: 0,
        firstStatus: true,
        target: null,
        drag: null,
        suppressClick: false,
        chatLog: [],
        chatRestored: false
    };

    try { state.items = JSON.parse(lsGet(QKEY) || '[]'); } catch (e) { state.items = []; }
    state.items.forEach(function (it) { if (it.n >= state.nextN) state.nextN = it.n + 1; });

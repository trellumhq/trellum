window.TrellumMetricsWorkspace = { mount: function (root, signal) {
    'use strict';
    var frame = root.querySelector('#metWorkspace');
    if (!frame) return;
    var status = root.querySelector('[data-workspace-status]');
    var observer = null, pending = null, reader = null, timer = null, epoch = 0, failed = false;
    var resolveReady;
    // Progress and success are announced to assistive technology only; a
    // problem is shown on the page.
    function say(text, quiet) {
        if (!status) return;
        status.textContent = text;
        status.classList.toggle('sr-only', !!quiet);
    }
    function retry() {
        if (signal.aborted) return;
        failed = false; discardDocument();
        say('Loading metrics…', true);
        var destination = new URL(frame.src, location.href);
        destination.pathname = destination.pathname.replace(/_metrics-workspace-[0-9a-f]{32}-page-[1-9][0-9]*\.html$/, 'index.html');
        destination.searchParams.delete('only'); destination.hash = '';
        frame.hidden = false;
        var waiting = epoch;
        timer = setTimeout(function() { if (waiting === epoch && !signal.aborted) finish('failed'); }, 12000);
        frame.src = destination.href;
    }
    var ready = new Promise(function (resolve) { resolveReady = resolve; });
    // A failed load is retried by the page itself, a few times, quietly.
    var RETRY_DELAYS = [2000, 8000, 20000], retries = 0, retryTimer = null;
    function finish(state, external) {
        clearTimeout(timer); timer = null;
        if (state === 'failed') { failed = true; discardDocument(); frame.hidden = true; }
        var again = state === 'failed' && retries < RETRY_DELAYS.length && !signal.aborted;
        if (state === 'ready') retries = 0;
        say(state === 'ready' ? 'Metrics ready.' : state === 'unconfirmed' ? (external ? 'Metrics opened on another origin; chart readiness cannot be confirmed.' : 'Metrics opened; chart readiness cannot be confirmed.') : again ? 'Metrics unavailable; retrying…' : 'Metrics unavailable.', state === 'ready');
        if (again) { clearTimeout(retryTimer); retryTimer = setTimeout(retry, RETRY_DELAYS[retries++]); }
        resolveReady();
    }
    function fit() {
        pending = null;
        if (signal.aborted || failed) return;
        // Explicit legacy viewing retains a bounded viewport; its old chart cost is separate.
        if (frame.dataset.workspaceVersion === '0') { frame.style.height = '700px'; return; }
        try {
            var doc = frame.contentDocument, height = 0;
            Array.from(doc.body.children).forEach(function (el) {
                var css = doc.defaultView.getComputedStyle(el);
                if (css.position === 'fixed') return;
                height = Math.max(height, el.getBoundingClientRect().bottom + (parseFloat(css.marginBottom) || 0));
            });
            var px = Math.ceil(height) + 'px';
            if (frame.style.height !== px) frame.style.height = px;
        } catch (error) {}
    }
    function schedule() { if (pending === null) pending = requestAnimationFrame(fit); }
    function discardDocument() {
        epoch++;
        if (reader) reader.abort(); reader = null;
        if (observer) observer.disconnect(); observer = null;
        clearTimeout(timer); timer = null;
        if (pending !== null) cancelAnimationFrame(pending); pending = null;
    }
    function mount(event) {
        discardDocument();
        if (signal.aborted || failed) return;
        frame.hidden = false;
        var doc, child;
        try {
            doc = frame.contentDocument; child = frame.contentWindow;
            var href = child && child.location.href;
            if (!doc || !doc.body || !href || href === 'about:blank') {
                var waiting = epoch;
                timer = setTimeout(function() { if (waiting === epoch && !signal.aborted) finish('failed'); }, 12000);
                return;
            }
        } catch (error) {
            if (child && error.name === 'SecurityError') { frame.style.height = '700px'; finish('unconfirmed', true); }
            else finish('failed');
            return;
        }
        var current = epoch;
        reader = new AbortController();
        child.addEventListener('pagehide', function() {
            if (current !== epoch || signal.aborted) return;
            discardDocument();
            say('Loading metrics…', true);
            var waiting = epoch;
            timer = setTimeout(function() { if (waiting === epoch && !signal.aborted) finish('failed'); }, 12000);
        }, { signal: reader.signal });
        doc.addEventListener('change', schedule, { signal: reader.signal });
        doc.addEventListener('click', schedule, { signal: reader.signal });
        observer = new ResizeObserver(schedule); observer.observe(doc.body);
        fit();
        say('Loading metrics…', true);
        var deadline = performance.now() + 12000;
        function check() {
            if (signal.aborted || current !== epoch || frame.contentDocument !== doc) return;
            var state = child._fwWorkspaceState;
            if (state === 'ready' || state === 'failed') { fit(); finish(state); return; }
            if (frame.dataset.workspaceVersion === '0') { finish('unconfirmed'); return; }
            if (performance.now() >= deadline) { finish('failed'); return; }
            clearTimeout(timer); timer = setTimeout(check, 25);
        }
        child.addEventListener('fw-workspace-state', check, { signal: reader.signal });
        check();
    }
    frame.addEventListener('load', mount, { signal: signal });
    frame.addEventListener('error', function () { discardDocument(); finish('failed'); }, { signal: signal });
    window.addEventListener('resize', schedule, { signal: signal });
    function cleanup() { clearTimeout(retryTimer); discardDocument(); resolveReady(); }
    signal.addEventListener('abort', cleanup, { once: true });
    window.addEventListener('pagehide', cleanup, { signal: signal });
    window.addEventListener('pageshow', function (event) { if (event.persisted) mount(); }, { signal: signal });
    mount();
    cleanup.ready = ready;
    return cleanup;
} };
if (!window.TrellumNavigation) {
    var metricsWorkspaceFallback = new AbortController();
    var metricsWorkspaceCleanup = window.TrellumMetricsWorkspace.mount(document, metricsWorkspaceFallback.signal);
    window.addEventListener('pagehide', function () { metricsWorkspaceFallback.abort(); if (metricsWorkspaceCleanup) metricsWorkspaceCleanup(); });
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) return;
        metricsWorkspaceFallback = new AbortController();
        metricsWorkspaceCleanup = window.TrellumMetricsWorkspace.mount(document, metricsWorkspaceFallback.signal);
    });
}

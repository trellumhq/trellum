window.TrellumMetricsWorkspace = { mount: function (root, signal) {
    'use strict';
    var frame = root.querySelector('#metWorkspace');
    if (!frame) return;
    var observer, pending, resolveReady, watchedDocument;
    var ready = new Promise(function (resolve) { resolveReady = resolve; });
    function fit() {
        pending = null;
        try {
            var doc = frame.contentDocument;
            var height = 0;
            Array.from(doc.body.children).forEach(function (el) {
                if (doc.defaultView.getComputedStyle(el).position === 'fixed') return;
                var rect = el.getBoundingClientRect();
                height = Math.max(height, rect.bottom + (parseFloat(doc.defaultView.getComputedStyle(el).marginBottom) || 0));
            });
            var px = Math.ceil(height) + 'px';
            if (frame.style.height !== px) frame.style.height = px;
        } catch (e) { /* The open-report link remains available if the frame cannot load. */ }
    }
    function schedule() { if (!pending) pending = requestAnimationFrame(fit); }
    function mount() {
        if (observer) observer.disconnect();
        try {
            if (watchedDocument !== frame.contentDocument) {
                watchedDocument = frame.contentDocument;
                watchedDocument.addEventListener('change', fit, { signal: signal });
                watchedDocument.addEventListener('click', fit, { signal: signal });
            }
            // ResizeObserver runs before paint; deferring it creates one frame of nested scrolling.
            observer = new ResizeObserver(fit);
            observer.observe(frame.contentDocument.body);
        } catch (e) {}
        fit();
    }
    frame.addEventListener('load', function () {
        mount(); resolveReady();
    }, { signal: signal });
    window.addEventListener('resize', schedule, { signal: signal });
    function cleanup() {
        if (observer) observer.disconnect();
        observer = null;
        if (pending) cancelAnimationFrame(pending);
        pending = null; resolveReady();
    }
    window.addEventListener('pagehide', cleanup, { signal: signal });
    window.addEventListener('pageshow', function (event) { if (event.persisted) mount(); }, { signal: signal });
    mount();
    try { if (frame.contentDocument && frame.contentDocument.location.href !== 'about:blank' && frame.contentDocument.readyState === 'complete') resolveReady(); } catch (error) {}
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

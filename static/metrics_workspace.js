(function () {
    'use strict';
    var frame = document.getElementById('metWorkspace');
    if (!frame) return;
    var observer, pending;
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
            // ResizeObserver runs before paint; deferring it creates one frame of nested scrolling.
            observer = new ResizeObserver(fit);
            observer.observe(frame.contentDocument.body);
        } catch (e) {}
        fit();
    }
    frame.addEventListener('load', function () {
        try {
            frame.contentDocument.addEventListener('change', fit);
            frame.contentDocument.addEventListener('click', fit);
        } catch (e) {}
        mount();
    });
    window.addEventListener('resize', schedule);
    window.addEventListener('pagehide', function () {
        if (observer) observer.disconnect();
        observer = null;
        if (pending) cancelAnimationFrame(pending);
        pending = null;
    });
    window.addEventListener('pageshow', function (event) { if (event.persisted) mount(); });
})();

    /* ── styles ──────────────────────────────────── */
    // The stylesheet lives in static/css/review.css and is substituted
    // in when this file is served -- see trellum.review.inject.
    var styleEl = document.createElement('style');
    styleEl.textContent = __FW_REVIEW_CSS__;
    document.head.appendChild(styleEl);

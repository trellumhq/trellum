window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['text'] = {
    // Text has no natural "default" the way a toggle's 'All' or a dropdown's
    // empty selection does -- an empty box just means "not set", which for a
    // live binding maps to the declared sentinel (see live_query.js). On a
    // normal (non-live) dataset an empty commit is 'equals' against '' --
    // never matches, which is the same "no rows" a stray empty filter would
    // already produce; text filters are a v1 live-binding feature primarily,
    // this plugin does not special-case client-side-only use.
    init: function(el, fc, dsId, h) {
        var urlVal = h.urlLookup(fc.column);
        if (urlVal === undefined) return;
        var input = el.querySelector('.fw-text-filter[data-filter-id="' + fc.id + '"]');
        if (input) input.value = urlVal;
        h.setFilter(fc.id, fc.column, 'equals', urlVal);
        h.propagate(fc.column, 'equals', urlVal);
    },

    wireControls: function(el, cfg, dsId, h) {
        el.querySelectorAll('.fw-text-filter').forEach(function(input) {
            var fid = input.dataset.filterId;
            var fc = h.findFc(fid);
            if (!fc) return;

            var lastCommitted = input.value;
            function commit() {
                if (input.value === lastCommitted) return; // no real change
                lastCommitted = input.value;
                var value = input.value.slice(0, fc.max_length || 200);
                h.setFilter(fc.id, fc.column, 'equals', value);
                h.propagate(fc.column, 'equals', value);
                h.urlWrite();
            }

            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    commit();
                    input.blur();
                }
            });
            // Typing alone never commits -- only Enter or a real blur-time
            // change does. This is the M1 invariant, preserved as a property
            // of this input type.
            input.addEventListener('blur', commit);
        });
    }
};

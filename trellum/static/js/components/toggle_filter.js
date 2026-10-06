window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['toggle'] = {
    init: function(el, fc, dsId, h) {
        var urlVal = h.urlLookup(fc.column);
        if (urlVal !== undefined) {
            if (urlVal === 'All' || (fc.options && fc.options.indexOf(urlVal) !== -1)) {
                var eqV = urlVal === 'All' ? '__all__' : urlVal;
                h.setFilter(fc.id, fc.column, 'equals', eqV);
                h.propagate(fc.column, 'equals', eqV);
                var grp = el.querySelector('[data-filter-id="' + fc.id + '"].fw-toggle-group');
                if (grp) {
                    grp.querySelectorAll('.fw-toggle-btn').forEach(function(btn) {
                        btn.classList.remove('active');
                        if (btn.textContent.trim() === urlVal) btn.classList.add('active');
                    });
                }
            } else if (fc.default && fc.default !== 'All') {
                h.setFilter(fc.id, fc.column, 'equals', fc.default);
                h.propagate(fc.column, 'equals', fc.default);
            }
        } else {
            if (fc.default && fc.default !== 'All') {
                h.setFilter(fc.id, fc.column, 'equals', fc.default);
                h.propagate(fc.column, 'equals', fc.default);
            }
        }
    },
    onClick: function(el, fc, dsId, h, btn) {
        var txt = btn.textContent.trim();
        var eqVal = txt === 'All' ? '__all__' : txt;
        h.setFilter(fc.id, fc.column, 'equals', eqVal);
        h.propagate(fc.column, 'equals', eqVal);
        h.urlWrite();
    }
};

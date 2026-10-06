window._fwFilterTypes = window._fwFilterTypes || {};
window._fwFilterTypes['flag'] = {
    init: function(el, fc, dsId, h) {
        var urlVal = h.urlLookup(fc.column);
        if (urlVal !== undefined) {
            if (urlVal === 'exclude' || urlVal === 'only') {
                h.setFilter(fc.id, fc.column, 'flag', urlVal, fc.flag_value);
                h.propagate(fc.column, 'flag', urlVal, fc.flag_value);
                var grp = el.querySelector('[data-filter-id="' + fc.id + '"].fw-toggle-group');
                if (grp) {
                    grp.querySelectorAll('.fw-toggle-btn').forEach(function(btn) {
                        btn.classList.remove('active');
                        var t = btn.textContent.trim();
                        if (urlVal === 'exclude' && t.indexOf('Exclude') === 0) btn.classList.add('active');
                        else if (urlVal === 'only' && t.indexOf('Only') === 0) btn.classList.add('active');
                    });
                }
            }
        }
    },
    onClick: function(el, fc, dsId, h, btn) {
        var txt = btn.textContent.trim();
        var v = 'all';
        if (txt.indexOf('Exclude') === 0) v = 'exclude';
        else if (txt.indexOf('Only') === 0) v = 'only';
        h.setFilter(fc.id, fc.column, 'flag', v, fc.flag_value);
        h.propagate(fc.column, 'flag', v, fc.flag_value);
        h.urlWrite();
    }
};

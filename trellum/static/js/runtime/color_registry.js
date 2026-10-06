    // ── Label→Color Registry ─────────────────────────────
    // Maps dsId → { label → colorIndex } so that the same label always
    // gets the same color regardless of how filters change the sort order.
    window._fwColorRegistry = {};
    window._fwGetLabelColor = function(dsId, label, colors) {
        var reg = window._fwColorRegistry[dsId] || (window._fwColorRegistry[dsId] = {});
        if (reg[label] === undefined) reg[label] = Object.keys(reg).length;
        return colors[reg[label] % colors.length];
    };

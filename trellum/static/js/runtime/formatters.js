    // ── Number Formatting ─────────────────────────────────
    function fmtCompact(n) {
        if (n == null || isNaN(n)) return '-';
        const abs = Math.abs(n);
        const sign = n < 0 ? '-' : '';
        if (abs >= 1e12) return sign + (abs / 1e12).toFixed(1) + 'T';
        if (abs >= 1e9)  return sign + (abs / 1e9).toFixed(1) + 'B';
        if (abs >= 1e6)  return sign + (abs / 1e6).toFixed(1) + 'M';
        if (abs >= 1e3)  return sign + (abs / 1e3).toFixed(1) + 'k';
        if (abs >= 1)    return sign + Math.round(abs).toString();
        return sign + abs.toFixed(2);
    }

    function fmtCompact$(n) {
        if (n == null || isNaN(n)) return '-';
        return '$' + fmtCompact(n);
    }

    function fmtChips(n) {
        if (n == null || isNaN(n)) return '-';
        return fmtCompact(n) + ' chips';
    }

    function fmtPercent(n) {
        if (n == null || isNaN(n)) return '-';
        return (n * 100).toFixed(2) + '%';
    }

    function fmt$(n) {
        if (n == null || isNaN(n)) return '-';
        return '$' + n.toFixed(2);
    }

    // Expose globally
    window.fmtCompact  = fmtCompact;
    window.fmtCompact$ = fmtCompact$;
    window.fmtChips    = fmtChips;
    window.fmtPercent  = fmtPercent;
    window.fmt$        = fmt$;

    // Formatter lookup by name
    window.getFormatter = function(name) {
        const map = {
            'number': fmtCompact,
            'currency': fmtCompact$,
            'chips': fmtChips,
            'percent': fmtPercent,
            'ratio': function(n) { return n == null ? '-' : n.toFixed(2) + 'x'; },
            'plain': function(n) { return n == null ? '-' : String(n); },
        };
        return map[name] || fmtCompact;
    };

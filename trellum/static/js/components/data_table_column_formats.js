/* Ordinary table display only. Never promote text/booleans/Decimals to numbers. */
window._fwTableColumnFormat = function(value, spec) {
    if (value == null || typeof value === 'number' && !Number.isFinite(value)) return '—';
    if (typeof value !== 'number') return String(value);
    if (Number.isInteger(value) && !Number.isSafeInteger(value)) return 'Unsafe number; supply exact text';
    if (spec.precision == null || spec.format === 'plain') return window.getFormatter(spec.format)(value);
    // Explicit precision is a fixed full-number display, as in MiniKpi(full_number=True).
    var n = spec.format === 'percent' ? value * 100 : value;
    if (!Number.isFinite(n)) return '—';
    return (spec.format === 'currency' ? '$' : '') + n.toLocaleString(undefined, {
        minimumFractionDigits: spec.precision, maximumFractionDigits: spec.precision
    }) + (spec.format === 'percent' ? '%' : spec.format === 'ratio' ? 'x' : '');
};

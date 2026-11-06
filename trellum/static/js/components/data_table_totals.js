/* Optional totals use typed inputs and bounded base-ten integer arithmetic.
   This module never reads formatted cells or writes report filter state. */
(function() {
    var RANGE = 'Numeric range is unsupported.';
    var INCOMPLETE = 'Complete finite numeric inputs are required.';
    function own(object, key) {
        return object && Object.prototype.hasOwnProperty.call(object, key) ? object[key] : undefined;
    }
    function fail(reason) { throw new Error(reason); }
    function digits(n) { return n.toString().replace('-', '').length; }
    function checked(n) { if (digits(n) > 2048) fail(RANGE); return n; }
    function scale(n, places) {
        if (n === BigInt(0)) return n;
        if (places < 0 || places > 2048 || digits(n) + places > 2048) fail(RANGE);
        return n * (BigInt(10) ** BigInt(places));
    }
    function parse(value, bound) {
        if (bound) {
            if (typeof value !== 'number' || !Number.isFinite(value)) fail(INCOMPLETE);
            if (Number.isInteger(value) && !Number.isSafeInteger(value))
                fail('Delivered integer precision is unsafe.');
            value = String(value);
        }
        if (value && value.error === 'range') fail(RANGE);
        if (typeof value !== 'string') fail(INCOMPLETE);
        // Check text bounds before regex/BigInt allocation. Typed Decimal text
        // can contain 1000 coefficient digits plus leading fractional zeros.
        if (value.length > 2020) fail(RANGE);
        var m = value.match(/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
        if (!m) fail(INCOMPLETE);
        var exponentText = (m[4] || '0').replace(/^[+-]/, '').replace(/^0+/, '');
        if (exponentText.length > 4) fail(RANGE);
        var exponent = Number(m[4] || 0) - (m[3] || '').length;
        var coefficient = (m[2] + (m[3] || '')).replace(/^0+/, '') || '0';
        if (coefficient.length > 1000 || Math.abs(exponent) > 1000) fail(RANGE);
        return {n: BigInt((m[1] === '-' ? '-' : '') + coefficient), e: exponent};
    }
    function format(numerator, denominator, spec) {
        var zero = BigInt(0), one = BigInt(1), two = BigInt(2);
        var negative = numerator.n < zero;
        var n = negative ? -numerator.n : numerator.n, d = denominator.n;
        var shift = numerator.e - denominator.e + spec.precision + (spec.format === 'percent' ? 2 : 0);
        if (shift >= 0) n = scale(n, shift);
        else d = scale(d, -shift);
        var q = n / d, r = n % d, half = d / two;
        // Half away from zero, without a potentially oversized 2*remainder.
        if (r > half || (d % two === zero && r === half)) q = checked(q + one);
        var text = q.toString().padStart(spec.precision + 1, '0');
        var whole = spec.precision ? text.slice(0, -spec.precision) : text;
        var fraction = spec.precision ? '.' + text.slice(-spec.precision) : '';
        if (spec.format !== 'plain') whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return (negative && q !== zero ? '-' : '') + (spec.format === 'currency' ? '$' : '') + whole + fraction +
            (spec.format === 'percent' ? '%' : (spec.format === 'ratio' ? 'x' : ''));
    }
    function scopeReason(cfg) {
        if (!cfg.dataset_id) return '';
        var engine = window._fwFilterEngine, source = cfg.dataset_id, visited = new Set(), chain = [];
        if (!engine) return 'Total scope is unavailable until the source is ready.';
        while (source) {
            if (visited.has(source)) return 'Total source scope is unsupported.';
            visited.add(source); chain.push(source);
            if (engine.isLiveDataset && engine.isLiveDataset(source)) return 'Live-query total scope is not declared.';
            source = own(engine._scopes, source);
            source = source ? source.parent : null;
        }
        // Check the root after the cycle-safe walk; isReady(child) recurses.
        if (!engine.isReady || !engine.isReady(chain[chain.length - 1]))
            return 'Total scope is unavailable until the source is ready.';
        return '';
    }
    window._fwTableTotals = function(cfg, rows) {
        var results = Object.create(null), scope = scopeReason(cfg), cache = Object.create(null);
        function sum(column) {
            if (own(cache, column)) {
                if (cache[column].reason) fail(cache[column].reason);
                return cache[column].value;
            }
            try {
                var exponent = null, zero = BigInt(0), positive = zero, negative = zero;
                var inputs = rows.map(function(row) {
                    var input = cfg.dataset_id ? own(row.totalsRecord, column) : own(cfg.totalsInputs, column);
                    if (!cfg.dataset_id) input = input && input[row.index];
                    var parsed = parse(input, !!cfg.dataset_id);
                    if (parsed.n !== zero) exponent = exponent === null ? parsed.e : Math.min(exponent, parsed.e);
                    return parsed;
                });
                if (exponent === null) exponent = 0;
                // Separate monotone signed accumulators make range decisions
                // independent of table sort order, even during cancellation.
                inputs.forEach(function(input) {
                    if (input.n === zero) return;
                    var coefficient = scale(input.n, input.e - exponent);
                    if (coefficient > zero) positive = checked(positive + coefficient);
                    else negative = checked(negative + coefficient);
                });
                var total = {n: checked(positive + negative), e: exponent};
                cache[column] = {value: total};
                return total;
            } catch (error) { cache[column] = {reason: error.message}; throw error; }
        }
        Object.keys(cfg.totals || {}).forEach(function(column) {
            var spec = cfg.totals[column];
            try {
                if (scope) fail(scope);
                if (typeof BigInt !== 'function') fail('Decimal totals are unavailable in this browser.');
                var numerator = sum(spec.agg === 'sum' ? column : spec.numerator);
                var denominator = spec.agg === 'sum' ? {n: BigInt(1), e: 0} : sum(spec.denominator);
                if (denominator.n <= BigInt(0)) fail('The summed denominator must be positive.');
                results[column] = {text: format(numerator, denominator, spec)};
            } catch (error) { results[column] = {text: '—', reason: error.message}; }
        });
        return results;
    };
})();

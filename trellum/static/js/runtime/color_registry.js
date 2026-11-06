    // Dataset-local fallback order remains first encounter, including numeric
    // own keys' historical Object.keys ordering. Never read inherited keys.
    window._fwColorRegistry = Object.create(null);
    window._fwGetLabelColor = function(dsId, label, colors) {
        var registry = window._fwColorRegistry;
        var reg = Object.prototype.hasOwnProperty.call(registry, dsId)
            ? registry[dsId] : (registry[dsId] = Object.create(null));
        if (!Object.prototype.hasOwnProperty.call(reg, label)) reg[label] = Object.keys(reg).length;
        return colors[reg[label] % colors.length];
    };

    // Inspect raw rows once per redraw. Legacy aggregation coerces numbers,
    // nulls and strings to labels; any coerced observation excludes its whole
    // group from the declaration, even when a string shares that label.
    // This does not change aggregate.js's separate grouping limitations.
    window._fwCategoryMembership = function(rows, column) {
        var eligible = new Map();
        rows.forEach(function(row) {
            var raw = row[column], label = String(raw == null ? '' : raw);
            eligible.set(label, typeof raw === 'string' && eligible.get(label) !== false);
        });
        return eligible;
    };

    // Domain identity is explicit and validated in Python, never inferred by
    // globally matching captions. Unknown values retain the caller's fallback.
    window._fwCategoryColor = function(declaration, category, colors, membership) {
        var own = Object.prototype.hasOwnProperty;
        if (!declaration || typeof category !== 'string' ||
                (membership && membership.get(category) !== true) ||
                !own.call(declaration.slots, category)) return null;
        if (declaration.overrides && own.call(declaration.overrides, category))
            return declaration.overrides[category];
        return colors[declaration.slots[category] % colors.length];
    };

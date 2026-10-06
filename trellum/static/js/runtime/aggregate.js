    // ── Aggregation Helpers ──────────────────────────────
    window._fwAggregate = {
        sum: function(rows, col) {
            var t = 0;
            for (var i = 0; i < rows.length; i++) t += (rows[i][col] || 0);
            return t;
        },

        sumCols: function(rows, cols) {
            var t = 0;
            for (var i = 0; i < rows.length; i++)
                for (var j = 0; j < cols.length; j++) t += (rows[i][cols[j]] || 0);
            return t;
        },

        absSum: function(rows, col) {
            var t = 0;
            for (var i = 0; i < rows.length; i++) t += Math.abs(rows[i][col] || 0);
            return t;
        },

        // Sort string labels numerically when every label parses as a finite
        // number (e.g. hour_utc 0..23), otherwise fall back to lexicographic
        // (chronological for ISO dates, alphabetical for categories).
        // Without this an integer x-axis renders as "0, 1, 10, 11, 2, 3, ...".
        _sortLabels: function(labels) {
            var allNumeric = labels.length > 0 && labels.every(function(l) {
                return !isNaN(parseFloat(l)) && isFinite(l);
            });
            if (allNumeric) {
                labels.sort(function(a, b) { return parseFloat(a) - parseFloat(b); });
            } else {
                labels.sort();
            }
            return labels;
        },

        pivot: function(rows, xCol, stackCol, valueCols, stackSort) {
            var map = {}, xlabels = [], xset = {}, stackSet = {};
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                var x = String(r[xCol] != null ? r[xCol] : '');
                var s = String(r[stackCol] != null ? r[stackCol] : '');
                if (!xset[x]) { xset[x] = true; xlabels.push(x); }
                if (!stackSet[s]) stackSet[s] = true;
                var key = x + '||' + s;
                if (!map[key]) {
                    map[key] = {};
                    for (var j = 0; j < valueCols.length; j++) map[key][valueCols[j]] = 0;
                }
                for (var j = 0; j < valueCols.length; j++) map[key][valueCols[j]] += (r[valueCols[j]] || 0);
            }
            this._sortLabels(xlabels);
            var stackNames = Object.keys(stackSet);
            var datasets = {}, totals = {};
            for (var si = 0; si < stackNames.length; si++) {
                var sn = stackNames[si]; datasets[sn] = {}; var total = 0;
                for (var ci = 0; ci < valueCols.length; ci++) {
                    var col = valueCols[ci], data = [];
                    for (var xi = 0; xi < xlabels.length; xi++) {
                        var val = (map[xlabels[xi] + '||' + sn] || {})[col] || 0;
                        data.push(val); total += Math.abs(val);
                    }
                    datasets[sn][col] = data;
                }
                totals[sn] = total;
            }
            // stackSort: 'volume_desc' (default) | 'volume_asc' | 'label_asc' | 'label_desc'.
            // Volume-desc is the natural choice for unordered categorical splits
            // (platforms, promo_types). Use label_asc when the stack values carry
            // an intrinsic order encoded in their label (e.g. "01. ...", "02. ...")
            // so the rendering matches the conceptual ordering of tier buckets.
            var sortMode = stackSort || 'volume_desc';
            if (sortMode === 'label_asc') {
                stackNames.sort();
            } else if (sortMode === 'label_desc') {
                stackNames.sort(); stackNames.reverse();
            } else if (sortMode === 'volume_asc') {
                stackNames.sort(function(a, b) { return totals[a] - totals[b]; });
            } else {
                stackNames.sort(function(a, b) { return totals[b] - totals[a]; });
            }
            return { labels: xlabels, stackNames: stackNames, datasets: datasets };
        },

        groupBy: function(rows, xCol, valueCols) {
            var map = {}, xlabels = [], xset = {};
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i], x = String(r[xCol] != null ? r[xCol] : '');
                if (!xset[x]) { xset[x] = true; xlabels.push(x); }
                if (!map[x]) {
                    map[x] = {};
                    for (var j = 0; j < valueCols.length; j++) map[x][valueCols[j]] = 0;
                }
                for (var j = 0; j < valueCols.length; j++) map[x][valueCols[j]] += (r[valueCols[j]] || 0);
            }
            this._sortLabels(xlabels);
            return { labels: xlabels, map: map };
        }
    };

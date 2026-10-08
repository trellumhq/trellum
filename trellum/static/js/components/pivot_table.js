window._fwRenderers['pivot'] = function renderPivot(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    var ctrlEl = document.getElementById(id + '_ctrl');
    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var valFmt = fmtMap[cfg.value_format] || fmtCompact;

    function _escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, function(ch) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch];
        });
    }

    var _rowDims = cfg.default_rows.slice();
    var _colDim = cfg.default_col || null;
    var _valCol = cfg.default_value;
    var _aggFn = cfg.default_agg;

    function _agg(arr) {
        if (!arr.length) return 0;
        if (_aggFn === 'count') return arr.length;
        if (_aggFn === 'avg') {
            var s = 0; for (var i = 0; i < arr.length; i++) s += (arr[i] || 0);
            return s / arr.length;
        }
        var s = 0; for (var i = 0; i < arr.length; i++) s += (arr[i] || 0);
        return s;
    }

    function _pivot(rows) {
        var rowMap = {}, rowKeys = [], rowKeySet = {};
        var colSet = {}, colLabels = [];
        var hasCols = !!_colDim;

        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            var rk = _rowDims.map(function(d) { return String(r[d] != null ? r[d] : ''); }).join('||');
            if (!rowKeySet[rk]) {
                rowKeySet[rk] = true;
                rowKeys.push(rk);
                rowMap[rk] = { _dims: _rowDims.map(function(d) { return String(r[d] != null ? r[d] : ''); }), _vals: [] };
            }
            if (hasCols) {
                var cv = String(r[_colDim] != null ? r[_colDim] : '');
                if (!colSet[cv]) { colSet[cv] = true; colLabels.push(cv); }
                var cellKey = rk + '@@' + cv;
                if (!rowMap[rk][cellKey]) rowMap[rk][cellKey] = [];
                rowMap[rk][cellKey].push(r[_valCol] || 0);
            }
            rowMap[rk]._vals.push(r[_valCol] || 0);
        }
        colLabels.sort();

        var pivotRows = [];
        var colTotals = {};
        colLabels.forEach(function(c) { colTotals[c] = []; });
        var grandTotal = [];

        for (var ki = 0; ki < rowKeys.length; ki++) {
            var rk = rowKeys[ki];
            var entry = rowMap[rk];
            var row = { _dims: entry._dims, _cells: {}, _total: _agg(entry._vals) };
            grandTotal = grandTotal.concat(entry._vals);
            if (hasCols) {
                for (var ci = 0; ci < colLabels.length; ci++) {
                    var cv = colLabels[ci];
                    var cellKey = rk + '@@' + cv;
                    var vals = entry[cellKey] || [];
                    row._cells[cv] = _agg(vals);
                    colTotals[cv] = colTotals[cv].concat(vals);
                }
            }
            pivotRows.push(row);
        }

        return { colLabels: colLabels, rows: pivotRows, colTotals: colTotals, grandTotal: grandTotal, hasCols: hasCols };
    }

    function _render(rows) {
        var p = _pivot(rows);
        var nDims = _rowDims.length;

        var hdr = _rowDims.map(function(d) { return '<th>' + _escapeHtml(d) + '</th>'; }).join('');
        if (p.hasCols) {
            hdr += p.colLabels.map(function(c) { return '<th class="fw-pivot-col-header">' + _escapeHtml(c) + '</th>'; }).join('');
        }
        hdr += '<th class="fw-pivot-col-header">' + (p.hasCols ? 'Total' : _escapeHtml(_valCol)) + '</th>';

        var body = p.rows.map(function(row) {
            var cells = row._dims.map(function(d) { return '<td class="fw-pivot-row-header">' + _escapeHtml(d) + '</td>'; }).join('');
            if (p.hasCols) {
                cells += p.colLabels.map(function(c) {
                    return '<td class="fw-pivot-value">' + valFmt(row._cells[c]) + '</td>';
                }).join('');
            }
            cells += '<td class="fw-pivot-value">' + valFmt(row._total) + '</td>';
            return '<tr>' + cells + '</tr>';
        }).join('');

        var footer = '<tr class="fw-pivot-total">';
        for (var di = 0; di < nDims; di++) footer += '<td>' + (di === 0 ? 'Total' : '') + '</td>';
        if (p.hasCols) {
            footer += p.colLabels.map(function(c) {
                return '<td class="fw-pivot-value">' + valFmt(_agg(p.colTotals[c])) + '</td>';
            }).join('');
        }
        footer += '<td class="fw-pivot-value">' + valFmt(_agg(p.grandTotal)) + '</td>';
        footer += '</tr>';

        el.innerHTML = '<table class="fw-table fw-pivot-table"><thead><tr>' + hdr + '</tr></thead>'
            + '<tbody>' + body + '</tbody><tfoot>' + footer + '</tfoot></table>';
    }

    function _buildControls() {
        if (!ctrlEl) return;
        if (typeof SlimSelect === 'undefined') return;

        function _mkOpts(arr) {
            return arr.map(function(c) { var safe = _escapeHtml(c); return '<option value="' + safe + '">' + safe + '</option>'; }).join('');
        }

        var colOpts = '<option value="">None</option>' + _mkOpts(cfg.dim_cols);

        ctrlEl.innerHTML =
            '<div class="fw-pivot-group"><label>Rows</label>' +
                '<select id="' + id + '_rowSel" multiple>' + _mkOpts(cfg.dim_cols) + '</select></div>' +
            '<div class="fw-pivot-group"><label>Column</label>' +
                '<select id="' + id + '_colSel">' + colOpts + '</select></div>' +
            '<div class="fw-pivot-group"><label>Value</label>' +
                '<select id="' + id + '_valSel">' + _mkOpts(cfg.num_cols) + '</select></div>' +
            '<div class="fw-pivot-group"><label>Agg</label>' +
                '<select id="' + id + '_aggSel">' + _mkOpts(['sum','count','avg']) + '</select></div>';

        var _skip = false;
        var ssRow = new SlimSelect({
            select: '#' + id + '_rowSel',
            settings: { closeOnSelect: false, showSearch: false, placeholderText: 'Select rows...',
                         maxValuesShown: 5, allowDeselect: true },
            events: { afterChange: function(vals) {
                if (_skip) return;
                _rowDims = (vals || []).map(function(v) { return v.value; });
                if (!_rowDims.length) { _rowDims = [cfg.default_rows[0]]; ssRow.setSelected(_rowDims); return; }
                if (_lastData) _render(_lastData);
            }}
        });
        ssRow.setSelected(_rowDims);

        var ssCol = new SlimSelect({
            select: '#' + id + '_colSel',
            settings: { showSearch: false, allowDeselect: false },
            events: { afterChange: function(vals) {
                if (_skip) return;
                _colDim = (vals && vals[0] && vals[0].value) ? vals[0].value : null;
                if (_lastData) _render(_lastData);
            }}
        });
        ssCol.setSelected([_colDim || '']);

        var ssVal = new SlimSelect({
            select: '#' + id + '_valSel',
            settings: { showSearch: false, allowDeselect: false },
            events: { afterChange: function(vals) {
                if (_skip) return;
                _valCol = vals && vals[0] ? vals[0].value : cfg.default_value;
                if (_lastData) _render(_lastData);
            }}
        });
        ssVal.setSelected([_valCol]);

        var ssAgg = new SlimSelect({
            select: '#' + id + '_aggSel',
            settings: { showSearch: false, allowDeselect: false },
            events: { afterChange: function(vals) {
                if (_skip) return;
                _aggFn = vals && vals[0] ? vals[0].value : 'sum';
                valFmt = fmtMap[cfg.value_format] || fmtCompact;
                if (_lastData) _render(_lastData);
            }}
        });
        ssAgg.setSelected([_aggFn]);
    }

    var _lastData = null;
    _buildControls();

    if (cfg.dataset_id) {
        window._fwLiveWrap(id, cfg, function(rows) {
            _lastData = rows;
            _render(rows);
        });
    } else {
        _lastData = cfg.data;
        _render(cfg.data);
    }

    var dlBtn = document.querySelector('[data-csv-target="' + id + '"]');
    if (dlBtn) {
        dlBtn.onclick = function() {
            var slug = document.body.getAttribute('data-report-slug') || 'data';
            var p = _pivot(_lastData || []);
            var valHeader = p.hasCols ? 'Total' : _valCol;
            var cols = _rowDims.concat(p.hasCols ? p.colLabels : []).concat([valHeader]);
            var csvRows = p.rows.map(function(row) {
                var r = row._dims.slice();
                if (p.hasCols) p.colLabels.forEach(function(c) { r.push(row._cells[c]); });
                r.push(row._total);
                return r;
            });
            window._fwDownloadCsv(cols, csvRows, slug + '_pivot.csv');
        };
    }
};

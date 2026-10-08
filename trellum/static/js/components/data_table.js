window._fwRenderers['table'] = function renderTable(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    var cf = cfg.conditionalFormats || {};
    /* Live tables (a live-query lookup) re-run this whole renderer on every
       commit, with different data each time. Without locked widths the
       columns re-fit to each user's rows and the table visibly jumps. Capture
       the first (snapshot) render's column widths onto the element — which
       persists across these re-invocations — and replay them as a fixed
       layout so only the rows change, never the shape. Static tables keep
       their natural, resize-friendly widths. */
    var _isLive = !!cfg.dataset_id;
    var _colWidths = _isLive ? (el._fwColWidths || null) : null;
    var _searchTerm = '';
    var _countEl = null;
    var _sortCol = -1;   /* -1 = no active sort */
    var _sortDir = 1;    /* 1 = asc, -1 = desc */

    function _escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, function(ch) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch];
        });
    }

    /* Extract a comparable number from a cell value. Handles currency,
       percent, comma formatting, and K/M/B/T suffixes produced by fmtCompact. */
    function _sortKey(v) {
        if (v == null) return { n: NaN, s: '' };
        var s = String(v).trim();
        var lower = s.toLowerCase();
        var t = s.replace(/[$,%\s]/g, '');
        var m = t.match(/^(-?\d*\.?\d+)\s*([kmbt])?$/i);
        if (m) {
            var n = parseFloat(m[1]);
            var suf = (m[2] || '').toLowerCase();
            if (suf === 'k') n *= 1e3;
            else if (suf === 'm') n *= 1e6;
            else if (suf === 'b') n *= 1e9;
            else if (suf === 't') n *= 1e12;
            return { n: n, s: lower };
        }
        return { n: NaN, s: lower };
    }

    function _sortRows(rows) {
        if (_sortCol < 0) return rows;
        var sorted = rows.slice();
        sorted.sort(function(a, b) {
            var ka = _sortKey(a[_sortCol]), kb = _sortKey(b[_sortCol]);
            var hasA = !isNaN(ka.n), hasB = !isNaN(kb.n);
            var r;
            if (hasA && hasB) r = ka.n - kb.n;
            else if (hasA) r = -1;
            else if (hasB) r = 1;
            else r = ka.s < kb.s ? -1 : (ka.s > kb.s ? 1 : 0);
            return r * _sortDir;
        });
        return sorted;
    }

    function _evalCondFmt(value, rules) {
        if (!rules || !rules.length) return null;
        var n = parseFloat(String(value).replace(/[^0-9.\-]/g, ''));
        if (isNaN(n)) return null;
        for (var r = 0; r < rules.length; r++) {
            var rule = rules[r], match = false;
            if (rule.op === '>' && n > rule.value) match = true;
            else if (rule.op === '<' && n < rule.value) match = true;
            else if (rule.op === '>=' && n >= rule.value) match = true;
            else if (rule.op === '<=' && n <= rule.value) match = true;
            else if (rule.op === '==' && n === rule.value) match = true;
            else if (rule.op === '!=' && n !== rule.value) match = true;
            else if (rule.op === 'between' && Array.isArray(rule.value) &&
                     n >= rule.value[0] && n <= rule.value[1]) match = true;
            if (match) return rule.color;
        }
        return null;
    }

    function _matchSearch(row) {
        if (!_searchTerm) return true;
        for (var i = 0; i < row.length; i++) {
            if (row[i] != null && String(row[i]).toLowerCase().indexOf(_searchTerm) >= 0) return true;
        }
        return false;
    }

    function _renderRows(columns, rows, totalRows) {
        var filtered = _searchTerm ? rows.filter(_matchSearch) : rows;
        filtered = _sortRows(filtered);
        var barCol = (cfg.barColumn != null) ? cfg.barColumn : -1;
        var barMax = 0;
        if (barCol >= 0) {
            filtered.forEach(function(row) {
                var v = parseFloat(String(row[barCol]).replace(/[^0-9.\-]/g, ''));
                if (!isNaN(v) && v > barMax) barMax = v;
            });
        }
        var hdr = columns.map(function(c, idx) {
            var label = _escapeHtml(c);
            if (!cfg.sortable) return '<th>' + label + '</th>';
            var active = (idx === _sortCol);
            var arrow = '<span class="fw-sort-arrow">' +
                (active ? (_sortDir > 0 ? '▲' : '▼') : '▲▼') + '</span>';
            var cls = 'fw-sortable' + (active ? ' fw-sort-active' : '');
            return '<th class="' + cls + '" data-col-idx="' + idx + '">'
                + label + arrow + '</th>';
        }).join('');
        var body = filtered.map(function(row) {
            return '<tr>' + row.map(function(v, i) {
                var condRules = cf[columns[i]];
                var style = '';
                if (condRules) {
                    var color = _evalCondFmt(v, condRules);
                    if (color) style = ' style="color:' + color + ';font-weight:600"';
                }
                if (i === barCol && barMax > 0) {
                    var num = parseFloat(String(v).replace(/[^0-9.\-]/g, ''));
                    var pct = isNaN(num) ? 0 : (num / barMax * 100);
                    return '<td' + style + '><div class="fw-bar-cell"><div class="fw-bar-fill" style="width:'
                        + pct.toFixed(1) + '%"></div><span>' + _escapeHtml(v != null ? v : '') + '</span></div></td>';
                }
                return '<td' + style + '>' + _escapeHtml(v != null ? v : '') + '</td>';
            }).join('') + '</tr>';
        }).join('');
        var note = '';
        if (_searchTerm) {
            note = '<div class="fw-table-note">' + filtered.length + ' of ' + totalRows + ' rows</div>';
        } else if (totalRows > cfg.maxRows) {
            note = '<div class="fw-table-note">Showing ' + cfg.maxRows
                + ' of ' + totalRows + ' rows — CSV download includes all ' + totalRows + '</div>';
        }
        var colgroup = '', tableAttr = '';
        if (_colWidths && _colWidths.length === columns.length) {
            colgroup = '<colgroup>' + _colWidths.map(function(w) {
                return '<col style="width:' + w + 'px">';
            }).join('') + '</colgroup>';
            tableAttr = ' style="table-layout:fixed"';
        }
        el.innerHTML = '<table class="fw-table"' + tableAttr + '>' + colgroup
            + '<thead><tr>' + hdr + '</tr></thead><tbody>'
            + body + '</tbody></table>' + note;
        // First natural render of a live table: lock its column widths so the
        // next commit's data cannot reshape it. Stored on the element because
        // the live path re-invokes this whole function with a fresh closure.
        if (_isLive && !_colWidths) {
            var ths = el.querySelectorAll('thead th');
            if (ths.length === columns.length) {
                _colWidths = Array.prototype.map.call(ths, function(th) {
                    return Math.round(th.getBoundingClientRect().width);
                });
                el._fwColWidths = _colWidths;
            }
        }
        if (_countEl) {
            _countEl.textContent = _searchTerm
                ? filtered.length + ' / ' + totalRows
                : '';
        }
        if (cfg.sortable) {
            el.querySelectorAll('th.fw-sortable').forEach(function(th) {
                th.addEventListener('click', function() {
                    var idx = parseInt(th.getAttribute('data-col-idx'), 10);
                    if (idx === _sortCol) {
                        _sortDir = -_sortDir;
                    } else {
                        _sortCol = idx;
                        _sortDir = -1; /* new col: default descending for numeric-first UX */
                    }
                    if (_lastRows) _renderRows(_lastCols, _lastRows, _lastTotal);
                });
            });
        }
    }

    /* ── Search wiring ── */
    var _lastRows = null, _lastCols = null, _lastTotal = 0;
    var searchInput = document.querySelector('[data-table-search="' + id + '"]');
    if (searchInput && cfg.searchable) {
        _countEl = document.createElement('span');
        _countEl.className = 'fw-table-count';
        searchInput.parentNode.insertBefore(_countEl, searchInput.nextSibling);
        var _debounce = null;
        searchInput.addEventListener('input', function() {
            clearTimeout(_debounce);
            _debounce = setTimeout(function() {
                _searchTerm = searchInput.value.trim().toLowerCase();
                if (_lastRows) _renderRows(_lastCols, _lastRows, _lastTotal);
            }, 200);
        });
    }

    /* ── Live mode ── */
    if (cfg.dataset_id) {
        function _drawTable(rawRows) {
            var cols = cfg.columns;
            var limited = rawRows.slice(0, cfg.maxRows);
            var rows = limited.map(function(r) {
                return cols.map(function(c) {
                    var v = r[c];
                    if (v == null) return '';
                    if (typeof v === 'number') return fmtCompact(v);
                    return String(v);
                });
            });
            _lastRows = rows; _lastCols = cols; _lastTotal = rawRows.length;
            _renderRows(cols, rows, rawRows.length);
        }
        window._fwLiveWrap(id, cfg, _drawTable);
    } else {
        /* ── Static mode ── */
        _lastRows = cfg.rows; _lastCols = cfg.columns; _lastTotal = cfg.totalRows;
        _renderRows(cfg.columns, cfg.rows, cfg.totalRows);
    }

    /* ── CSV download wiring ──
       CSV exports ALL filtered rows (not just visible ones -- maxRows
       caps the rendered table, not the download). */
    function _csvClean(v) {
        return v == null ? '' : v;
    }
    /* ── Live lookup wiring ──
       cfg.live marks this table as a declared live query's surface.
       The module no-ops without a host + advertised endpoint, so the
       server-rendered disabled state survives untouched standalone. */
    if (cfg.live && window._fwLiveQuery) window._fwLiveQuery.attach(id, cfg);

    var dlBtn = document.querySelector('[data-csv-target="' + id + '"]');
    if (dlBtn) {
        dlBtn.onclick = function() {
            var slug = document.body.getAttribute('data-report-slug') || 'data';
            var fn = slug + '_table.csv';
            if (cfg.dataset_id) {
                var filtered = window._fwFilterEngine.getFiltered(cfg.dataset_id);
                var cols = cfg.columns;
                var csvRows = filtered.map(function(r) {
                    return cols.map(function(c) { return _csvClean(r[c]); });
                });
                window._fwDownloadCsv(cols, csvRows, fn);
            } else {
                var csvRows = cfg.rows.map(function(row) {
                    return row.map(_csvClean);
                });
                window._fwDownloadCsv(cfg.columns, csvRows, fn);
            }
        };
    }
};

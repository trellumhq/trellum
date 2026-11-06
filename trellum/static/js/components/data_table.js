window._fwRenderers['table'] = function renderTable(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    var generation = el._fwTableGeneration = (el._fwTableGeneration || 0) + 1;
    var cf = cfg.conditionalFormats || {};
    var comparison = cfg.comparison;
    var context = comparison && comparison.context;
    function _own(object, key) {
        return object && Object.prototype.hasOwnProperty.call(object, key) ? object[key] : undefined;
    }

    // Only raw finite numbers carry comparison meaning. Display strings and
    // unknown declarations are never parsed to infer a delta or direction.
    function _comparisonRow(row, record, columns) {
        if (!context) return row;
        var spec = _own(context.metrics, record[context.metric_column]) || {};
        row.comparisonCells = columns.map(function(column, i) {
            var raw = row.raw[i], text = row.display[i], outcome = '', tone = '';
            var change = comparison.compareColumns.indexOf(column) >= 0;
            var period = _own(context.period_labels, column) !== undefined || column === comparison.valueColumn;
            if (!change && !period) return null;
            var numeric = typeof raw === 'number' && Number.isFinite(raw);
            var unavailable = raw == null || (typeof raw === 'number' && !Number.isFinite(raw));
            var measure = spec.change_measure;
            var delta = change && ['relative_percent', 'percentage_points', 'absolute'].indexOf(measure) >= 0;
            var format = spec.format || 'number';
            var precision = change ? spec.change_precision : spec.precision;
            if (precision == null) precision = change ? 1 : 2;
            if (unavailable) {
                text = '—';
                var reasonColumn = _own(context.reason_columns, column);
                var reason = reasonColumn && _own(record, reasonColumn);
                outcome = reason == null || reason === '' ? 'Unavailable' : String(reason);
            } else if (numeric && (!change || delta || measure === 'value')) {
                var value = raw, suffix = '', prefix = '';
                if (change && measure === 'relative_percent') { value *= 100; suffix = '%'; }
                else if (change && measure === 'percentage_points') suffix = ' pp';
                else if (format === 'percent') { value *= 100; suffix = '%'; }
                else if (format === 'currency') prefix = '$';
                else if (format === 'ratio') suffix = 'x';
                // Round only the display; CSV retains the original supplied unit.
                text = (delta && raw > 0 ? '+' : (value < 0 ? '-' : '')) + prefix +
                    Math.abs(value).toLocaleString('en-US', {minimumFractionDigits: precision, maximumFractionDigits: precision}) + suffix;
                if (delta) {
                    if (raw === 0) outcome = 'Unchanged';
                    else if (spec.direction === 'higher' || spec.direction === 'lower') {
                        var improved = (raw > 0) === (spec.direction === 'higher');
                        outcome = improved ? 'Improved' : 'Worsened';
                        if (comparison.autoColors) tone = improved ? 'var(--accent-green)' : 'var(--accent-red)';
                    }
                }
            } else if (numeric && change) text = String(raw);
            row.display[i] = String(text == null ? '' : text) + (outcome ? ' ' + outcome : '');
            return {text: text, outcome: outcome, tone: tone};
        });
        return row;
    }
    /* Live tables (a live-query lookup) re-run this whole renderer on every
       commit, with different data each time. Without locked widths the
       columns re-fit to each user's rows and the table visibly jumps. Capture
       the first (snapshot) render's column widths onto the element — which
       persists across these re-invocations — and replay them as a fixed
       layout so only the rows change, never the shape. Static tables keep
       their natural, resize-friendly widths. */
    var _isLive = !!cfg.dataset_id;
    // Declared comparison headers size the columns naturally on each viewport,
    // like the static comparison. Keep the ordinary/live-table width contract.
    var _colWidths = _isLive && !context ? (el._fwColWidths || null) : null;
    var _searchTerm = '';
    var _matchingRows = [];
    clearTimeout(el._fwTableSearchTimer);
    var _countEl = null;
    var _sortCol = -1;   /* -1 = no active sort */
    var _sortDir = 1;    /* 1 = asc, -1 = desc */
    // Only generated workspace definition tables opt in. Their rows are light
    // catalog metadata; ordinary authored tables keep their existing contract.
    var _pageSize = window._fwMetricsWorkspace && cfg.workspacePageSize === 25 ? 25 : 0;
    var _definitionAnchors = null, _definitionHash = '';
    if (_pageSize && window._fwMetricsWorkspace.definitionId === id) {
        _definitionAnchors = Object.create(null);
        (window._fwMetricsCatalog || []).forEach(function(row) {
            if (row.monitored === false && typeof row.anchor === 'string') _definitionAnchors[row.name] = row.anchor;
        });
        try { _definitionHash = decodeURIComponent(location.hash.slice(1)); } catch (error) {}
    }
    var _page = 1;
    if (el._fwTableState || (_pageSize && el._fwWorkspaceTableState)) {
        var saved = el._fwTableState || el._fwWorkspaceTableState;
        _page = saved.page; _searchTerm = saved.term; _sortCol = saved.sortCol; _sortDir = saved.sortDir;
    }
    if (_pageSize) {
        if (el._fwWorkspaceTableAbort) el._fwWorkspaceTableAbort.abort();
        el._fwWorkspaceTableAbort = new AbortController();
        window.addEventListener('pagehide', function() {
            clearTimeout(el._fwWorkspaceSearchTimer); el._fwWorkspaceSearchTimer = null;
        }, {signal: el._fwWorkspaceTableAbort.signal});
    }

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

    // New payloads carry dtype metadata and raw values. Legacy snapshots
    // still sort their formatted cells using the established numeric parser.
    function _isNumericColumn(columns, rows, index) {
        if (Array.isArray(cfg.numericColumns) && cfg.numericColumns.indexOf(columns[index]) >= 0) return true;
        // An empty/unknown snapshot may not have a numeric dtype yet. Use
        // actual raw numbers, never parsed display strings, in that case.
        var values = rows.map(function(row) { return row.raw[index]; }).filter(function(v) { return v != null && v !== ''; });
        return values.length > 0 && values.every(function(v) { return typeof v === 'number'; });
    }

    // Compare raw decimal text without rounding large integers/Decimals through
    // parseFloat. Ordinary raw numbers use their round-trippable JS text.
    function _numericKey(value) {
        if (value == null || value === '') return null;
        var m = String(value).match(/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
        if (!m) return null;
        var digits = (m[2] + (m[3] || '')).replace(/^0+/, '');
        return {sign: digits ? (m[1] === '-' ? -1 : 1) : 0, digits: digits,
            exponent: digits.length - (m[3] || '').length + Number(m[4] || 0)};
    }

    function _compareNumeric(a, b) {
        if (!a || !b) return a ? -1 : (b ? 1 : 0);
        if (a.sign !== b.sign) return a.sign - b.sign;
        if (!a.sign) return 0;
        if (a.exponent !== b.exponent) return (a.exponent < b.exponent ? -1 : 1) * a.sign;
        var width = Math.max(a.digits.length, b.digits.length);
        var left = a.digits.padEnd(width, '0'), right = b.digits.padEnd(width, '0');
        return (left < right ? -1 : (left > right ? 1 : 0)) * a.sign;
    }

    function _sortRows(rows) {
        if (_sortCol < 0) return rows;
        var sorted = rows.slice();
        var numeric = _isNumericColumn(_lastCols, rows, _sortCol);
        sorted.sort(function(a, b) {
            if (numeric && !a.legacy && !b.legacy) {
                return _compareNumeric(_numericKey(a.raw[_sortCol]), _numericKey(b.raw[_sortCol]))
                    * _sortDir || a.index - b.index;
            }
            function key(row) {
                var v = row.raw[_sortCol];
                if (row.legacy) return _sortKey(v);
                return {n: NaN, s: v == null ? '' : String(v).toLowerCase()};
            }
            var ka = key(a), kb = key(b);
            var hasA = !isNaN(ka.n), hasB = !isNaN(kb.n);
            var r;
            if (hasA && hasB) r = ka.n - kb.n;
            else if (hasA) r = -1;
            else if (hasB) r = 1;
            else r = ka.s < kb.s ? -1 : (ka.s > kb.s ? 1 : 0);
            return r * _sortDir || a.index - b.index;
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
        for (var i = 0; i < row.display.length; i++) {
            if (row.display[i] != null && String(row.display[i]).toLowerCase().indexOf(_searchTerm) >= 0) return true;
        }
        return false;
    }

    function _renderRows(columns, rows, totalRows) {
        var filtered = _searchTerm ? rows.filter(_matchSearch) : rows;
        filtered = _sortRows(filtered);
        _matchingRows = filtered;
        var pages = _pageSize ? Math.max(1, Math.ceil(filtered.length / _pageSize)) : 1;
        if (_pageSize) _page = Math.min(pages, Math.max(1, _page));
        el._fwTableState = {page: _page, term: _searchTerm, sortCol: _sortCol, sortDir: _sortDir};
        var visible = _pageSize ? filtered.slice((_page - 1) * _pageSize, _page * _pageSize) : filtered.slice(0, cfg.maxRows);
        var numericColumns = columns.map(function(c, index) { return _isNumericColumn(columns, rows, index); });
        var barCol = (cfg.barColumn != null) ? cfg.barColumn : -1;
        var barMax = 0;
        if (barCol >= 0) {
            filtered.forEach(function(row) {
                var v = parseFloat(String(row.display[barCol]).replace(/[^0-9.\-]/g, ''));
                if (!isNaN(v) && v > barMax) barMax = v;
            });
        }
        var hdr = columns.map(function(c, idx) {
            var label = _escapeHtml(context ? (_own(context.period_labels, c) || _own(context.change_labels, c) || c) : c);
            var numericClass = _cellClass(c, numericColumns[idx]);
            if (!cfg.sortable) return '<th class="' + numericClass.trim() + '">' + label + '</th>';
            var active = (idx === _sortCol);
            var arrow = '<span class="fw-sort-arrow">' +
                (active ? (_sortDir > 0 ? '▲' : '▼') : '▲▼') + '</span>';
            var cls = 'fw-sortable' + numericClass + (active ? ' fw-sort-active' : '');
            return '<th class="' + cls + '" data-col-idx="' + idx + '">'
                + label + arrow + '</th>';
        }).join('');
        var body = visible.map(function(row) {
            return '<tr>' + row.display.map(function(v, i) {
                var classes = _cellClass(columns[i], numericColumns[i]).trim();
                var cellClass = classes ? ' class="' + classes + '"' : '';
                var info = row.comparisonCells && row.comparisonCells[i];
                var condRules = cf[columns[i]];
                var style = info && info.tone ? ' style="color:' + info.tone + ';font-weight:600"' : '';
                if (condRules) {
                    var color = _evalCondFmt(info ? row.raw[i] : v, condRules);
                    if (color) style = ' style="color:' + (comparison ? _escapeHtml(color) : color) + ';font-weight:600"';
                }
                if (info) return '<td' + cellClass + '><span' + style + '>' + _escapeHtml(info.text == null ? '' : info.text) + '</span>' +
                    (info.outcome ? '<span class="fw-comparison-outcome">' + _escapeHtml(info.outcome) + '</span>' : '') + '</td>';
                if (i === barCol && barMax > 0) {
                    var num = parseFloat(String(v).replace(/[^0-9.\-]/g, ''));
                    var pct = isNaN(num) ? 0 : (num / barMax * 100);
                    return '<td' + cellClass + style + '><div class="fw-bar-cell"><div class="fw-bar-fill" style="width:'
                        + pct.toFixed(1) + '%"></div><span>' + _escapeHtml(v != null ? v : '') + '</span></div></td>';
                }
                return '<td' + cellClass + style + '>' + _escapeHtml(v != null ? v : '') + '</td>';
            }).join('') + '</tr>';
        }).join('');
        var note = '<div class="fw-table-note" role="status">Showing ' + visible.length
            + ' of ' + filtered.length + ' matching rows ('
            + (rows.length < totalRows ? rows.length + ' available of ' : '') + totalRows
            + ' supplied) — CSV exports ' + filtered.length + ' rows</div>';
        if (_pageSize) {
            note = '<div class="fw-table-note" data-workspace-table-status tabindex="-1" role="status">' + filtered.length + ' of ' + totalRows + ' rows · Page ' + _page + ' of ' + pages
                + ' · CSV exports ' + filtered.length + ' rows</div><div class="fw-metric-actions" data-workspace-table-pager>'
                + '<button type="button" data-workspace-table-previous' + (_page <= 1 ? ' disabled' : '') + '>Previous definitions</button>'
                + '<button type="button" data-workspace-table-next' + (_page >= pages ? ' disabled' : '') + '>Next definitions</button></div>';
            el._fwWorkspaceTableState = {page: _page, term: _searchTerm, sortCol: _sortCol, sortDir: _sortDir};
        }
        var colgroup = '', tableAttr = '';
        if (_colWidths && _colWidths.length === columns.length) {
            colgroup = '<colgroup>' + _colWidths.map(function(w) {
                return '<col style="width:' + w + 'px">';
            }).join('') + '</colgroup>';
            tableAttr = ' style="table-layout:fixed"';
        }
        el.innerHTML = '<table class="fw-table fw-data-table' + (comparison ? ' fw-comparison-table' : '') + (context ? ' fw-comparison-context' : '') + '"' + tableAttr + '>' + colgroup
            + '<thead><tr>' + hdr + '</tr></thead><tbody>'
            + body + '</tbody></table>' + note;
        if (_definitionAnchors) {
            el.querySelectorAll('tbody tr').forEach(function(row, index) {
                var anchor = _definitionAnchors[visible[index].display[0]];
                if (anchor) row.id = anchor; // Exact catalog anchor through the DOM API.
            });
            var target = _definitionHash && document.getElementById(_definitionHash);
            if (target && el.contains(target) && !el._fwDefinitionHashHandled) {
                el._fwDefinitionHashHandled = true;
                var navigation = performance.getEntriesByType('navigation')[0];
                if (!window._fwMetricsUserScrolled && !(navigation && navigation.type === 'back_forward'))
                    target.scrollIntoView({block: 'center'});
            }
        }
        // First natural render of a live table: lock its column widths so the
        // next commit's data cannot reshape it. Stored on the element because
        // the live path re-invokes this whole function with a fresh closure.
        if (_isLive && !context && !_colWidths) {
            var ths = el.querySelectorAll('thead th');
            if (ths.length === columns.length) {
                _colWidths = Array.prototype.map.call(ths, function(th) {
                    return Math.round(th.getBoundingClientRect().width);
                });
                el._fwColWidths = _colWidths;
            }
        }
        if (_countEl) {
            _countEl.textContent = filtered.length + ' matching · CSV ' + filtered.length;
            _countEl.title = 'CSV includes every matching row in table sort order';
        }
        if (cfg.sortable) {
            el.querySelectorAll('th.fw-sortable').forEach(function(th) {
                th.addEventListener('click', function() {
                    if (_definitionAnchors) el._fwDefinitionHashHandled = true;
                    var idx = parseInt(th.getAttribute('data-col-idx'), 10);
                    if (idx === _sortCol) {
                        _sortDir = -_sortDir;
                    } else {
                        _sortCol = idx;
                        _sortDir = -1; /* new col: default descending for numeric-first UX */
                    }
                    if (_pageSize) _page = 1;
                    if (_lastRows) _renderRows(_lastCols, _lastRows, _lastTotal);
                    if (_pageSize) {
                        var replacement = el.querySelector('th[data-col-idx="' + idx + '"]');
                        if (replacement) replacement.focus({preventScroll: true});
                    }
                });
                if (_pageSize) {
                    th.tabIndex = 0;
                    th.setAttribute('aria-sort', parseInt(th.dataset.colIdx, 10) === _sortCol ? (_sortDir > 0 ? 'ascending' : 'descending') : 'none');
                    th.addEventListener('keydown', function(event) {
                        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); th.click(); }
                    });
                }
            });
        }
        if (_pageSize) {
            [['previous', -1], ['next', 1]].forEach(function(direction) {
                var selector = '[data-workspace-table-' + direction[0] + ']';
                el.querySelector(selector).onclick = function() {
                    el._fwDefinitionHashHandled = true;
                    _page += direction[1]; _renderRows(_lastCols, _lastRows, _lastTotal);
                    var target = el.querySelector(selector);
                    if (target.disabled) target = el.querySelector('[data-workspace-table-' + (direction[0] === 'next' ? 'previous' : 'next') + ']');
                    if (target.disabled) target = el.querySelector('[data-workspace-table-status]');
                    target.focus({preventScroll: true});
                };
            });
        }
    }

    function _cellClass(column, numeric) {
        var classes = numeric ? ' fw-table-numeric' : '';
        if (!comparison) return classes;
        var first = cfg.columns.find(function(c) { return comparison.compareColumns.indexOf(c) >= 0; });
        if (comparison.showDivider && column === first) classes += ' fw-comparison-divider';
        if (column === comparison.valueColumn) classes += ' fw-comparison-primary';
        if (context && (comparison.compareColumns.indexOf(column) >= 0 || _own(context.period_labels, column) !== undefined || column === comparison.valueColumn)) classes += ' fw-table-numeric';
        return classes;
    }

    /* ── Search wiring ── */
    var _lastRows = null, _lastCols = null, _lastTotal = 0;
    var searchInput = document.querySelector('[data-table-search="' + id + '"]');
    if (searchInput && cfg.searchable) {
        if (_searchTerm !== searchInput.value.trim().toLowerCase()) {
            _searchTerm = searchInput.value.trim().toLowerCase(); _page = 1;
        }
        _countEl = searchInput.parentNode.querySelector('.fw-table-count');
        if (!_countEl) {
            _countEl = document.createElement('span');
            _countEl.className = 'fw-table-count';
            searchInput.parentNode.insertBefore(_countEl, searchInput.nextSibling);
        }
        var _debounce = null;
        if (_pageSize) clearTimeout(el._fwWorkspaceSearchTimer);
        function search() {
            if (_definitionAnchors) el._fwDefinitionHashHandled = true;
            clearTimeout(_debounce);
            _debounce = setTimeout(function() {
                _searchTerm = searchInput.value.trim().toLowerCase();
                if (_pageSize) _page = 1;
                if (_lastRows) _renderRows(_lastCols, _lastRows, _lastTotal);
            }, 200);
            el._fwTableSearchTimer = _debounce;
            if (_pageSize) el._fwWorkspaceSearchTimer = _debounce;
        }
        searchInput.oninput = search;
    }

    /* ── Live mode ── */
    if (cfg.dataset_id) {
        function _drawTable(rawRows) {
            if (el._fwTableGeneration !== generation) return;
            var cols = cfg.columns;
            var rows = rawRows.map(function(r, index) {
                var raw = cols.map(function(c) { return r[c]; });
                var display = cols.map(function(c) {
                    var v = r[c];
                    if (v == null) return '';
                    if (typeof v === 'number') return fmtCompact(v);
                    return String(v);
                });
                return _comparisonRow({display: display, raw: raw, index: index}, r, cols);
            });
            _lastRows = rows; _lastCols = cols; _lastTotal = rawRows.length;
            _renderRows(cols, rows, rawRows.length);
        }
        window._fwLiveWrap(id, cfg, _drawTable);
    } else {
        /* ── Static mode ── */
        _lastRows = cfg.rows.map(function(row, index) {
            return _comparisonRow({display: row.slice(), raw: cfg.rawRows ? cfg.rawRows[index] : row,
                    legacy: !cfg.rawRows && !Array.isArray(cfg.numericColumns), index: index},
                    cfg.comparisonRows ? cfg.comparisonRows[index] : {}, cfg.columns);
        });
        _lastCols = cfg.columns; _lastTotal = cfg.totalRows == null ? cfg.rows.length : cfg.totalRows;
        // A definition deep link opens the table page that holds its row.
        if (_definitionAnchors && _definitionHash && !el._fwWorkspaceTableState) {
            var _linked = cfg.rows.findIndex(function(row) { return _definitionAnchors[row[0]] === _definitionHash; });
            if (_linked >= 0) _page = Math.floor(_linked / _pageSize) + 1;
        }
        _renderRows(_lastCols, _lastRows, _lastTotal);
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
            // Export the exact population used by the last completed draw.
            // Never refetch from the filter engine: a live update may be pending
            // while this component intentionally still displays its old result.
            var csvRows = _matchingRows.map(function(row) { return row.raw.map(_csvClean); });
            window._fwDownloadCsv(_lastCols, csvRows, fn);
        };
    }
};

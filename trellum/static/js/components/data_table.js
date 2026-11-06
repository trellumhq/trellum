window._fwRenderers['table'] = function renderTable(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    if (window._fwLiveResultContext) window._fwLiveResultContext.attach(id, cfg);
    var _drillReentryFocus = el._fwDrillFocus && el._fwDrillFocus();
    if (el._fwDrillDispose) el._fwDrillDispose();
    var _drill = null;
    // Pinning owns only DOM state; keep it on the node so detached tables can
    // be collected without an additional global registry or observer.
    _configurePinning(el, cfg);
    var generation = el._fwTableGeneration = (el._fwTableGeneration || 0) + 1;
    var cf = cfg.conditionalFormats || {};
    var comparison = cfg.comparison;
    var context = comparison && comparison.context;
    var columnFormats = !comparison && cfg.workspacePageSize !== 25 && cfg.columnFormats;
    function _formatColumns(row, columns) {
        if (!columnFormats || !Object.keys(columnFormats).length) return row;
        row.legacyDisplay = row.display.slice();
        columns.forEach(function(column, i) {
            if (Object.prototype.hasOwnProperty.call(columnFormats, column))
                row.display[i] = window._fwTableColumnFormat(row.raw[i], columnFormats[column]);
        });
        return row;
    }
    function _own(object, key) {
        return object && Object.prototype.hasOwnProperty.call(object, key) ? object[key] : undefined;
    }

    function _configurePinning(node, config) {
        var enabled = config.workspacePageSize !== 25 && !config.comparison &&
            !!(config.pinHeader || config.pinFooter);
        var owned = node._fwDataTablePinning || (node._fwDataTablePinning = {});
        function setOwned(name, value) {
            if (owned[name] && node.getAttribute(name) === owned[name].value) {
                node.setAttribute(name, value);
                owned[name].value = value;
            } else if (!node.hasAttribute(name)) {
                node.setAttribute(name, value);
                owned[name] = {value: value};
            }
        }
        if (enabled) {
            if (!owned.classAdded && !node.classList.contains('fw-data-table-pinned')) {
                node.classList.add('fw-data-table-pinned'); owned.classAdded = true;
            }
            if (!owned.maxHeight && !node.style.getPropertyValue('--fw-data-table-pin-height')) {
                node.style.setProperty('--fw-data-table-pin-height', config.pinHeight + 'px');
                owned.maxHeight = config.pinHeight + 'px';
            } else if (owned.maxHeight && node.style.getPropertyValue('--fw-data-table-pin-height') === owned.maxHeight) {
                node.style.setProperty('--fw-data-table-pin-height', config.pinHeight + 'px');
                owned.maxHeight = config.pinHeight + 'px';
            }
            ['fw-pin-header', 'fw-pin-footer'].forEach(function(name) {
                var requested = name === 'fw-pin-header' ? !!config.pinHeader : !!config.pinFooter;
                if (requested && !node.classList.contains(name)) {
                    node.classList.add(name);
                    (owned.classes || (owned.classes = {}))[name] = true;
                } else if (!requested && owned.classes && owned.classes[name]) {
                    node.classList.remove(name); delete owned.classes[name];
                }
            });
            setOwned('role', 'region');
            setOwned('tabindex', '0');
            setOwned('aria-label', config.title || 'Data table');
            return;
        }
        _releasePinning(node);
    }

    function _releasePinning(node) {
        var owned = node._fwDataTablePinning;
        if (!owned) return;
        if (owned.classAdded) node.classList.remove('fw-data-table-pinned');
        if (owned.classes) Object.keys(owned.classes).forEach(function(name) { node.classList.remove(name); });
        if (owned.maxHeight && node.style.getPropertyValue('--fw-data-table-pin-height') === owned.maxHeight)
            node.style.removeProperty('--fw-data-table-pin-height');
        ['role', 'tabindex', 'aria-label'].forEach(function(name) {
            if (owned[name] && node.getAttribute(name) === owned[name].value) node.removeAttribute(name);
        });
        delete node._fwDataTablePinning;
    }

    // Only raw finite numbers carry comparison meaning. Display strings and
    // unknown declarations are never parsed to infer a delta or direction.
    function _comparisonRow(row, record, columns) {
        if (!context) return row;
        var spec = _own(context.metrics, _own(record, context.metric_column)) || {};
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
                        if (comparison.autoColors) tone = improved ? 'var(--text-positive, var(--accent-green))' : 'var(--text-negative, var(--accent-red))';
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
    // Workspace definitions and wrapper comparisons never acquire totals.
    var _totalsEnabled = cfg.workspacePageSize !== 25 && !comparison && cfg.totals && Object.keys(cfg.totals).length;
    // Re-entry may wait for a source without completing another draw. Remove
    // only owned totals metadata; ordinary retained cells stay untouched.
    el.querySelectorAll('.fw-table-totals, .fw-table-totals-note').forEach(function(node) { node.remove(); });
    var previousTotalsNote = document.getElementById(id + '-totals-note');
    if (previousTotalsNote) previousTotalsNote.remove();
    var previousTable = el.querySelector('table');
    if (previousTable) {
        var descriptions = (previousTable.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(token) {
            return token && token !== id + '-totals-note';
        });
        if (descriptions.length) previousTable.setAttribute('aria-describedby', descriptions.join(' '));
        else previousTable.removeAttribute('aria-describedby');
    }
    var _definitionAnchors = null, _definitionHash = '';
    if (_pageSize && window._fwMetricsWorkspace.definitionId === id) {
        _definitionAnchors = Object.create(null);
        (window._fwMetricsCatalog || []).forEach(function(row) {
            if (row.monitored === false && typeof row.anchor === 'string') _definitionAnchors[row.name] = row.anchor;
        });
        try { _definitionHash = decodeURIComponent(location.hash.slice(1)); } catch (error) {}
    }
    var _page = 1;
    var _rowLimitEnabled = cfg.rowLimit != null && !_pageSize;
    var _rowLimitControl = document.querySelector('[data-table-row-limit="' + id + '"]');
    var _rowLimitDefault = cfg.rowLimit;
    function _rowLimitNote(count) {
        var noteId = id + '-row-limit-note', note = document.getElementById(noteId);
        var needed = _rowLimitEnabled && _rowLimitControl && count > 1000;
        if (needed) {
            if (!note) {
                note = document.createElement('div'); note.id = noteId;
                note.className = 'fw-table-note fw-row-limit-note';
                note.setAttribute('role', 'status');
                _rowLimitControl.closest('.fw-data-table-header').appendChild(note);
            }
            note.textContent = 'All is available for up to 1,000 matching rows. CSV includes all ' + count + ' matches.';
        } else if (note) note.remove();
        if (_rowLimitControl) {
            var descriptions = (_rowLimitControl.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(token) {
                return token && token !== noteId;
            });
            if (needed) descriptions.push(noteId);
            if (descriptions.length) _rowLimitControl.setAttribute('aria-describedby', descriptions.join(' '));
            else _rowLimitControl.removeAttribute('aria-describedby');
        }
    }
    function _ensureRowLimitControl() {
        if (!_rowLimitEnabled) {
            _rowLimitNote(0);
            if (_rowLimitControl && _rowLimitControl.parentNode) _rowLimitControl.parentNode.remove();
            _rowLimitControl = null;
            return;
        }
        if (!_rowLimitControl) {
            var header = document.querySelector('[data-csv-target="' + id + '"]');
            if (!header) return;
            var label = document.createElement('label');
            label.className = 'fw-row-limit-control'; label.dataset.rowLimitFor = id;
            label.appendChild(document.createTextNode('Rows '));
            var select = document.createElement('select');
            select.dataset.tableRowLimit = id; select.setAttribute('aria-label', 'Rows');
            [10, 20, 50, 100, 'all'].forEach(function(choice) {
                var option = document.createElement('option');
                option.value = String(choice); option.textContent = choice === 'all' ? 'All' : String(choice);
                select.appendChild(option);
            });
            label.appendChild(select);
            header.parentNode.insertBefore(label, header);
            _rowLimitControl = select;
        }
    }
    _ensureRowLimitControl();
    if (_rowLimitEnabled) {
        if (el._fwRowLimitDefault !== _rowLimitDefault || !el._fwRowLimitValue) {
            el._fwRowLimitDefault = _rowLimitDefault;
            el._fwRowLimitValue = _rowLimitDefault;
        }
    } else {
        delete el._fwRowLimitDefault;
        delete el._fwRowLimitValue;
    }
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
            exponent: BigInt(digits.length - (m[3] || '').length) + BigInt(m[4] || 0)};
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

    // Snapshot decimal text is typed by numericColumns; ordinary dataset
    // strings are never promoted by potentially stale snapshot dtype metadata.
    function _cellShading(row, column, index) {
        var spec = _own(cfg.cellShading, column);
        if (!spec) return null;
        var value = row.raw[index], key = null;
        var mixed = !cfg.dataset_id && _own(cfg.cellShadingNumbers, column);
        if (mixed) key = _numericKey(mixed[row.index]);
        else if (typeof value === 'number' && Number.isFinite(value)) key = _numericKey(value);
        else if (!cfg.dataset_id && cfg.rawRows && typeof value === 'string' &&
                 Array.isArray(cfg.numericColumns) && cfg.numericColumns.indexOf(column) >= 0)
            key = _numericKey(value);
        var target = _numericKey(spec.target);
        if (!key || !target) return null;
        var order = _compareNumeric(key, target);
        var meets = spec.direction === 'lower' ? order <= 0 : order >= 0;
        return {tone: meets ? 'meets' : 'misses', description: column + ': ' +
            (meets ? 'Meets target' : (spec.direction === 'lower' ? 'Above target' : 'Below target')) +
            ' (' + (spec.direction === 'lower' ? '≤ ' : '≥ ') + spec.target + ', original units)'};
    }

    // Keep the key outside the horizontal scroller and replace it on renderer
    // re-entry. Text nodes avoid treating authored column names as markup.
    var previousKey = el.previousElementSibling;
    if (previousKey && previousKey.dataset.shadingFor === id) previousKey.remove();
    if (cfg.cellShading && Object.keys(cfg.cellShading).length) {
        var shadingKey = document.createElement('div');
        shadingKey.className = 'fw-table-shading-key';
        shadingKey.dataset.shadingFor = id;
        var intro = document.createElement('span');
        intro.textContent = 'Cell shading · original units. ';
        shadingKey.appendChild(intro);
        Object.keys(cfg.cellShading).forEach(function(column) {
            var spec = _own(cfg.cellShading, column), item = document.createElement('span');
            item.textContent = column + ': ' + (spec.direction === 'lower' ? '≤ ' : '≥ ') + spec.target +
                ' meets target; ' + (spec.direction === 'lower' ? 'above' : 'below') + ' target otherwise. ';
            shadingKey.appendChild(item);
        });
        el.before(shadingKey);
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
        var drillFocus = _drill && (_drill.focus() || _drillReentryFocus);
        _drillReentryFocus = null;
        var filtered = _searchTerm ? rows.filter(_matchSearch) : rows;
        filtered = _sortRows(filtered);
        _matchingRows = filtered;
        var pages = _pageSize ? Math.max(1, Math.ceil(filtered.length / _pageSize)) : 1;
        if (_pageSize) _page = Math.min(pages, Math.max(1, _page));
        el._fwTableState = {page: _page, term: _searchTerm, sortCol: _sortCol, sortDir: _sortDir};
        var limit = _rowLimitEnabled ? el._fwRowLimitValue : cfg.maxRows;
        if (_rowLimitEnabled && filtered.length > 1000 && limit === 'all') {
            limit = el._fwRowLimitValue = 100;
        }
        if (_rowLimitControl) {
            _rowLimitControl.value = String(el._fwRowLimitValue);
            var allOption = _rowLimitControl.querySelector('option[value="all"]');
            if (allOption) allOption.disabled = filtered.length > 1000;
        }
        _rowLimitNote(filtered.length);
        var visible = _pageSize ? filtered.slice((_page - 1) * _pageSize, _page * _pageSize)
            : filtered.slice(0, _rowLimitEnabled ? (limit === 'all' ? filtered.length : limit) : cfg.maxRows);
        var numericColumns = columns.map(function(c, index) { return _isNumericColumn(columns, rows, index); });
        var barCol = (cfg.barColumn != null) ? cfg.barColumn : -1;
        var barMax = 0;
        if (barCol >= 0) {
            filtered.forEach(function(row) {
                var v = parseFloat(String((row.legacyDisplay || row.display)[barCol]).replace(/[^0-9.\-]/g, ''));
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
                var shading = _cellShading(row, columns[i], i);
                if (shading) classes += ' fw-table-shading-' + shading.tone;
                var cellClass = classes ? ' class="' + classes + '"' : '';
                if (shading) cellClass += ' title="' + _escapeHtml(shading.description) + '"';
                var info = row.comparisonCells && row.comparisonCells[i];
                var condRules = _own(cf, columns[i]);
                var style = info && info.tone ? ' style="color:' + info.tone + ';font-weight:600"' : '';
                if (condRules) {
                    var color = _evalCondFmt(info ? row.raw[i] : (row.legacyDisplay || row.display)[i], condRules);
                    if (color) style = ' style="color:' + (comparison ? _escapeHtml(color) : color) + ';font-weight:600"';
                }
                var text = _escapeHtml(v != null ? v : '');
                if (_drill && columns[i] === cfg.drillDown.label_column)
                    text = '<button type="button" class="fw-table-row-action" data-fw-drill-action>' + text + '</button>';
                if (info) return '<td' + cellClass + '><span' + style + '>' + _escapeHtml(info.text == null ? '' : info.text) + '</span>' +
                    (info.outcome ? '<span class="fw-comparison-outcome">' + _escapeHtml(info.outcome) + '</span>' : '') + '</td>';
                if (i === barCol && barMax > 0) {
                    var num = parseFloat(String((row.legacyDisplay || row.display)[i]).replace(/[^0-9.\-]/g, ''));
                    var pct = isNaN(num) ? 0 : (num / barMax * 100);
                    return '<td' + cellClass + style + '><div class="fw-bar-cell"><div class="fw-bar-fill" style="width:'
                        + pct.toFixed(1) + '%"></div><span>' + text + '</span></div></td>';
                }
                return '<td' + cellClass + style + '>' + text + '</td>';
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
        var footer = '', totalsNote = '', totalsNoteId = id + '-totals-note';
        var oldTotalsNote = document.getElementById(totalsNoteId);
        if (oldTotalsNote) oldTotalsNote.remove();
        if (_totalsEnabled && filtered.length) {
            var totals = window._fwTableTotals(cfg, _matchingRows), reasons = [];
            footer = '<tfoot class="fw-table-totals"><tr><th scope="row">Total</th>' + columns.slice(1).map(function(column) {
                var result = _own(totals, column);
                if (result && result.reason) reasons.push(column + ': ' + result.reason);
                return '<td' + (result ? ' class="fw-table-numeric"' : '') + '>' +
                    (result ? _escapeHtml(result.text) : '') + '</td>';
            }).join('') + '</tr></tfoot>';
            totalsNote = 'Totals cover ' + filtered.length + ' matching rows before the display limit. Delivered table scope.' +
                (reasons.length ? ' ' + reasons.join(' ') : '');
        }
        if (_colWidths && _colWidths.length === columns.length) {
            colgroup = '<colgroup>' + _colWidths.map(function(w) {
                return '<col style="width:' + w + 'px">';
            }).join('') + '</colgroup>';
            tableAttr = ' style="table-layout:fixed"';
        }
        el.innerHTML = '<table class="fw-table fw-data-table' + (comparison ? ' fw-comparison-table' : '') + (context ? ' fw-comparison-context' : '') + '"' + tableAttr + '>' + colgroup
            + '<thead><tr>' + hdr + '</tr></thead><tbody>'
            + body + '</tbody>' + footer + '</table>' + note;
        if (totalsNote) {
            var scopeNote = document.createElement('div');
            scopeNote.id = totalsNoteId; scopeNote.className = 'fw-table-note fw-table-totals-note';
            scopeNote.textContent = totalsNote;
            el.after(scopeNote);
            el.querySelector('table').setAttribute('aria-describedby', totalsNoteId);
        }
        if (_drill) _drill.drawn(visible, filtered.length, rows.length, drillFocus);
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

    if (_rowLimitControl && _rowLimitEnabled) {
        _rowLimitControl.onchange = function() {
            el._fwRowLimitValue = this.value === 'all' ? 'all' : Number(this.value);
            if (_lastRows) _renderRows(_lastCols, _lastRows, _lastTotal);
            this.focus({preventScroll: true});
        };
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

    if (cfg.drillDown && !comparison && cfg.workspacePageSize !== 25) {
        _drill = _setupDrillDown();
    }

    /* ── Live mode ── */
    if (cfg.dataset_id) {
        function _totalsRecord(record) {
            if (!_totalsEnabled) return null;
            var inputs = Object.create(null);
            Object.keys(cfg.totals).forEach(function(column) {
                var spec = cfg.totals[column];
                var required = spec.agg === 'sum' ? [column] : [spec.numerator, spec.denominator];
                required.forEach(function(name) { inputs[name] = _own(record, name); });
            });
            return inputs;
        }
        function _drawTable(rawRows) {
            if (el._fwTableGeneration !== generation) return;
            var cols = cfg.columns;
            var rows = rawRows.map(function(r, index) {
                var raw = cols.map(function(c) { return _own(r, c); });
                var display = cols.map(function(c) {
                    var v = _own(r, c);
                    if (v == null) return '';
                    if (typeof v === 'number') return fmtCompact(v);
                    return String(v);
                });
                return _formatColumns(_comparisonRow({display: display, raw: raw, index: index,
                    totalsRecord: _totalsRecord(r), drillKey: _own(r, cfg.drillDown && cfg.drillDown.value_column)}, r, cols), cols);
            });
            _lastRows = rows; _lastCols = cols; _lastTotal = rawRows.length;
            _renderRows(cols, rows, rawRows.length);
        }
        if (_drill) _drill.population(_drawTable);
        else window._fwLiveWrap(id, cfg, _drawTable);
    } else {
        /* ── Static mode ── */
        _lastRows = cfg.rows.map(function(row, index) {
            return _formatColumns(_comparisonRow({display: row.slice(), raw: cfg.rawRows ? cfg.rawRows[index] : row,
                    legacy: !cfg.rawRows && !Array.isArray(cfg.numericColumns), index: index,
                    drillKey: cfg.drillDownKeys && cfg.drillDownKeys[index]},
                    cfg.comparisonRows ? cfg.comparisonRows[index] : {}, cfg.columns), cfg.columns);
        });
        _lastCols = cfg.columns; _lastTotal = cfg.totalRows == null ? cfg.rows.length : cfg.totalRows;
        // A definition deep link opens the table page that holds its row.
        if (_definitionAnchors && _definitionHash && !el._fwWorkspaceTableState) {
            var _linked = cfg.rows.findIndex(function(row) { return _definitionAnchors[row[0]] === _definitionHash; });
            if (_linked >= 0) _page = Math.floor(_linked / _pageSize) + 1;
        }
        _renderRows(_lastCols, _lastRows, _lastTotal);
    }

    function _setupDrillDown() {
        var spec = cfg.drillDown, engine = window._fwFilterEngine, active = true;
        var names = ['dataset_id', 'column', 'value_column', 'label_column'];
        var valid = spec && typeof spec === 'object' && !Array.isArray(spec) && names.every(function(name) {
            return typeof spec[name] === 'string' && spec[name].trim();
        }) && Array.isArray(cfg.columns) && cfg.columns.indexOf(spec.label_column) !== -1 && Object.keys(spec).every(function(name) { return names.concat(['context']).indexOf(name) !== -1; });
        var contexts = Array.isArray(spec.context) ? spec.context : [{column: spec.column, label: spec.column}];
        var seenColumns = new Set();
        valid = valid && (spec.context === undefined || Array.isArray(spec.context)) && contexts.every(function(item) {
            if (!item || typeof item !== 'object' || Object.keys(item).sort().join(',') !== 'column,label' ||
                typeof item.column !== 'string' || !item.column.trim() || typeof item.label !== 'string' || !item.label.trim() ||
                seenColumns.has(item.column)) return false;
            seenColumns.add(item.column); return true;
        });
        if (!valid) contexts = [{column: '', label: 'Row selection'}];
        var subscriptions = [], abort = new AbortController(), drawPopulation = null;
        var searchHandler = searchInput && searchInput.oninput, limitHandler = _rowLimitControl && _rowLimitControl.onchange;
        var downloadButton = null, downloadHandler = null;
        var chips = document.createElement('div'), empty = document.createElement('div');
        chips.className = 'fw-table-selection'; chips.tabIndex = -1;
        chips.setAttribute('role', 'group');
        chips.setAttribute('aria-label', (cfg.title || 'Data table') + ' selection context');
        empty.className = 'fw-table-drill-empty fw-table-note'; empty.setAttribute('role', 'status');
        el.before(chips); el.after(empty);
        var lastContext = null, matching = 0, supplied = 0;
        function liveBacked(dataset) {
            var seen = new Set();
            while (dataset && !seen.has(dataset)) {
                seen.add(dataset);
                if (engine.isLiveDataset(dataset)) return true;
                dataset = engine._scopes[dataset] && engine._scopes[dataset].parent;
            }
            return false;
        }
        function owner(column) {
            if (!valid) return {reason: 'Row-action declaration is invalid.'};
            if (liveBacked(cfg.dataset_id) || liveBacked(spec.dataset_id))
                return {reason: 'Live drill-down is unavailable.'};
            return window._fwTableDropdownOwner
                ? window._fwTableDropdownOwner(spec.dataset_id, column)
                : {reason: 'Selection dropdown is unavailable.'};
        }
        function keyValid(key) { return typeof key === 'string' && key.trim().length > 0; }
        function focus() {
            var node = document.activeElement;
            if (el.contains(node) && node.hasAttribute('data-fw-drill-action'))
                return {key: node._fwDrillKey, label: node.textContent};
            if (chips.contains(node) && node._fwDrillColumn) return {column: node._fwDrillColumn};
            if (node === chips) return {};
            return null;
        }
        function restore(saved) {
            if (!saved) return;
            var target;
            if (saved.key !== undefined) target = Array.from(el.querySelectorAll('[data-fw-drill-action]')).find(function(button) {
                return button._fwDrillKey === saved.key && button.textContent === saved.label && !button.disabled;
            });
            if (!target) target = Array.from(chips.querySelectorAll('button')).find(function(button) {
                return button._fwDrillColumn === (saved.column || spec.column);
            });
            (target || chips).focus({preventScroll: true});
        }
        function update() {
            if (!active || !el.isConnected) return;
            var saved = focus(), target = owner(spec.column);
            var states = contexts.map(function(item) { return {item: item, owner: owner(item.column)}; });
            var signature = JSON.stringify(states.map(function(state) {
                return [state.item.column, state.owner.values, state.owner.reason];
            }));
            if (lastContext !== signature) {
                lastContext = signature;
                chips.replaceChildren();
                var selected = false;
                states.forEach(function(state) {
                    var item = state.item, control = state.owner;
                    if (control.reason) {
                        var reason = document.createElement('span');
                        reason.textContent = item.label + ': ' + control.reason; chips.appendChild(reason); return;
                    }
                    if (!control.values.length) return;
                    selected = true;
                    var chip = document.createElement('span'); chip.className = 'fw-table-selection-chip';
                    var text = document.createElement('span');
                    text.textContent = item.label + ': ' + control.values.join(', '); chip.appendChild(text);
                    var button = document.createElement('button'); button.type = 'button';
                    button.textContent = 'Clear ' + item.label;
                    button._fwDrillColumn = item.column;
                    chip.appendChild(button); chips.appendChild(chip);
                });
                if (!selected && !chips.childNodes.length) chips.textContent = 'All · no selections in this context';
            }
            el.querySelectorAll('[data-fw-drill-action]').forEach(function(button) {
                var key = button._fwDrillKey;
                var reason = !keyValid(key) ? 'This row has no valid selection value.'
                    : target.reason || (!target.available(key) ? 'This key is unavailable in the selection dropdown.' : '');
                button.disabled = !!reason;
                button.title = reason;
                var context = contexts.find(function(item) { return item.column === spec.column; });
                var actionLabel = context ? context.label : spec.label_column;
                button.setAttribute('aria-label', (button.textContent || 'Unnamed row') + (reason ? '. ' + reason : ' · select ' + actionLabel));
                button.setAttribute('aria-pressed', String(!target.reason && target.values.indexOf(key) !== -1));
            });
            var emptySignature = JSON.stringify([matching, supplied, _searchTerm]);
            if (empty._fwSignature !== emptySignature) {
                empty._fwSignature = emptySignature; empty.replaceChildren();
                if (!matching) {
                    empty.appendChild(document.createTextNode(_searchTerm
                        ? 'No rows match this table’s local search. '
                        : cfg.dataset_id ? 'No observations match the current report filters and selection. Use the selection context to clear a local selection. '
                            : 'No observations were supplied for this table. '));
                    if (_searchTerm && searchInput) {
                        var clear = document.createElement('button'); clear.type = 'button'; clear.textContent = 'Clear search';
                        clear.onclick = function() {
                            clearTimeout(el._fwTableSearchTimer); searchInput.value = ''; _searchTerm = '';
                            _renderRows(_lastCols, _lastRows, _lastTotal); searchInput.focus({preventScroll: true});
                        };
                        empty.appendChild(clear);
                    }
                }
                empty.hidden = matching > 0;
            }
            restore(saved);
        }
        function click(event) {
            var button = event.target.closest('[data-fw-drill-action]');
            if (!button || !el.contains(button) || button.disabled) return;
            var control = owner(spec.column), key = button._fwDrillKey;
            if (control.reason || !keyValid(key) || !control.available(key)) return;
            var saved = focus();
            control.select(control.values.length === 1 && control.values[0] === key ? [] : [key]);
            update(); restore(saved);
        }
        chips.addEventListener('click', function(event) {
            var button = event.target.closest('button');
            if (!button || !chips.contains(button)) return;
            var saved = focus(), control = owner(button._fwDrillColumn);
            if (!control.reason) control.select([]);
            update(); restore(saved);
        }, {signal: abort.signal});
        el.addEventListener('click', click, {signal: abort.signal});
        if (valid) subscriptions.push(engine.subscribe(spec.dataset_id, id + '_drill_selection', update));
        function redraw() {
            if (active && el.isConnected && drawPopulation && engine.isReady(cfg.dataset_id))
                drawPopulation(engine.getFiltered(cfg.dataset_id));
        }
        window.addEventListener('fw-theme-change', redraw, {signal: abort.signal});
        window.addEventListener('fw-dropdowns-ready', function() { redraw(); update(); }, {signal: abort.signal});
        // Only opt-in tables own this observer. Disposal releases every strong
        // callback reference after removal/replacement; re-entry disposes first.
        function ownerTree(node) {
            if (node.nodeType !== 1) return false;
            var bar = node.closest('[data-fw-filter-bar]');
            if (bar && bar.dataset.fwDsId === spec.dataset_id) return true;
            return Array.from(node.querySelectorAll('[data-fw-filter-bar]')).some(function(candidate) {
                return candidate.dataset.fwDsId === spec.dataset_id;
            });
        }
        var observer = new MutationObserver(function(records) {
            if (!el.isConnected) { dispose(); return; }
            // Table draws and selection subscriptions already update their own
            // state. Only owner insertion/removal or option changes need this
            // fallback; unrelated charts must not rescan every row action.
            if (records.some(function(record) {
                var target = record.target.nodeType === 1 && record.target.closest('[data-fw-filter-bar]');
                return (target && target.dataset.fwDsId === spec.dataset_id) ||
                    Array.from(record.addedNodes).some(ownerTree) || Array.from(record.removedNodes).some(ownerTree);
            })) update();
        });
        observer.observe(document.body, {childList: true, subtree: true});
        function dispose() {
            if (!active) return;
            active = false; observer.disconnect(); abort.abort();
            subscriptions.forEach(function(unsubscribe) { unsubscribe(); });
            chips.remove(); empty.remove(); clearTimeout(el._fwTableSearchTimer);
            if (searchInput && searchInput.oninput === searchHandler) searchInput.oninput = null;
            if (_rowLimitControl && _rowLimitControl.onchange === limitHandler) _rowLimitControl.onchange = null;
            if (downloadButton && downloadButton.onclick === downloadHandler) downloadButton.onclick = null;
            if (el._fwDrillDispose === dispose) { delete el._fwDrillDispose; delete el._fwDrillFocus; }
        }
        el._fwDrillDispose = dispose; el._fwDrillFocus = focus;
        return {
            focus: focus,
            controls: function(button) { downloadButton = button; downloadHandler = button.onclick; },
            drawn: function(visible, count, rows, saved) {
                matching = count; supplied = rows;
                el.querySelectorAll('[data-fw-drill-action]').forEach(function(button, index) {
                    button._fwDrillKey = visible[index].drillKey;
                });
                update(); restore(saved);
            },
            population: function(draw) {
                drawPopulation = draw;
                subscriptions.push(engine.subscribe(cfg.dataset_id, id + '_drill_population', redraw));
                redraw();
            }
        };
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
        if (_drill) _drill.controls(dlBtn);
    }
};

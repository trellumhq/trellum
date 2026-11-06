/* Snapshot-only additive hierarchy. Expansion never writes filter state. */
(function() {
    'use strict';
    var leases = new Map();
    var MAX_BYTES = 8 * 1024 * 1024;
    var own = function(o, k) { return o && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined; };
    function fail(message) { throw new Error('HierarchyTable: ' + message); }
    function text(value, name, limit) {
        if (typeof value !== 'string' || !value.length || value.length > (limit || 1024)) fail('invalid ' + name);
        return value;
    }
    function dimension(value) {
        if (value === null) return ['null', null];
        if (typeof value === 'string') {
            if (value.length > 1024) fail('dimension text exceeds 1024 characters');
            return ['string', value];
        }
        if (typeof value === 'boolean') return ['boolean', value];
        if (typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER)
            return ['number', value];
        fail('dimension identity must be a scalar null, string, boolean or safe finite number');
    }
    function typed(part) {
        if (!Array.isArray(part) || part.length !== 2 || JSON.stringify(dimension(part[1])) !== JSON.stringify(part))
            fail('malformed typed dimension');
        return part;
    }
    function label(part) {
        if (part[0] === 'null') return 'Missing';
        if (part[0] === 'string') return part[1] === '' ? 'Empty' : part[1];
        return String(part[1]) + ' [' + part[0] + ']';
    }
    function escape(value) {
        return String(value).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; });
    }
    function boundFacts(rows, cfg) {
        if (!Array.isArray(rows) || rows.length > 50000) fail('source exceeds 50,000 facts or is malformed');
        return rows.map(function(row) {
            var v = own(row, cfg.value), reason = '';
            if (typeof v === 'number' && Number.isFinite(v) && Number.isInteger(v) && !Number.isSafeInteger(v))
                reason = 'Delivered integer precision is unsafe.';
            var input = typeof v === 'number' && Number.isFinite(v) && !reason ? String(v) : null;
            return {id: own(row, cfg.recordId), path: cfg.dimensions.map(function(d) { return dimension(own(row, d)); }),
                bucket: cfg.column ? dimension(own(row, cfg.column)) : ['null', null], input: input,
                raw: v === null ? '' : String(v), reason: reason};
        });
    }
    function validate(facts, cfg) {
        if (!Array.isArray(facts) || facts.length > 50000) fail('source exceeds 50,000 facts or is malformed');
        var ids = new Set(), paths = new Set(), buckets = new Map(), size = 2;
        facts.forEach(function(fact) {
            text(fact.id, 'record_id');
            if (ids.has(fact.id)) fail('duplicate record_id');
            ids.add(fact.id);
            if (!Array.isArray(fact.path) || fact.path.length !== cfg.dimensions.length) fail('malformed path');
            fact.path.forEach(typed); typed(fact.bucket);
            if (fact.input !== null && typeof fact.input !== 'string' && !(fact.input && fact.input.error === 'range'))
                fail('malformed numeric input');
            if (typeof fact.raw !== 'string' || fact.raw.length > 2048) fail('raw export text exceeds bounds');
            for (var depth = 1; depth <= fact.path.length; depth++) paths.add(JSON.stringify(fact.path.slice(0, depth)));
            buckets.set(JSON.stringify(fact.bucket), fact.bucket);
            if (paths.size > 2000) fail('source exceeds 2,000 path nodes');
            if (buckets.size > 32) fail('source exceeds 32 column categories');
            size += new TextEncoder().encode(JSON.stringify(fact)).length + 1;
            if (size > MAX_BYTES) fail('relevant serialized payload exceeds 8 MiB');
        });
        if ((paths.size + 1) * ((cfg.column ? buckets.size : 0) + 1) > 64000) fail('source exceeds 64,000 numeric cells');
        return {paths: paths, buckets: buckets};
    }
    function checkConfig(cfg) {
        if (!Array.isArray(cfg.dimensions) || !cfg.dimensions.length || cfg.dimensions.length > 8) fail('requires 1..8 dimensions');
        if (cfg.column !== null) text(cfg.column, 'bucket column', 128);
        if (typeof cfg.title !== 'string' || cfg.title.length > 512) fail('invalid title');
        if (cfg.dataset_id !== undefined) text(cfg.dataset_id, 'dataset_id', 128);
        var names = cfg.dimensions.concat([cfg.value, cfg.recordId], cfg.column ? [cfg.column] : []);
        names.forEach(function(name) { text(name, 'column name', 128); });
        if (new Set(names).size !== names.length) fail('columns must be distinct');
        if (!['number','currency','plain'].includes(cfg.format) || !Number.isInteger(cfg.precision) || cfg.precision < 0 || cfg.precision > 6)
            fail('unsupported additive format or precision');
        if (typeof cfg.knownSubtotals !== 'boolean') fail('invalid completeness policy');
    }
    function source(cfg) {
        var engine = window._fwFilterEngine, chain = new Set();
        if (!engine) fail('source is not ready');
        function walk(id) {
            var seen = new Set(), root;
            while (id) {
                if (seen.has(id)) fail('cyclic source ancestry');
                seen.add(id); chain.add(id); root = id;
                if (engine.isLiveDataset && engine.isLiveDataset(id)) fail('live ancestry cannot supply complete parent scope');
                id = own(engine._scopes, id);
                id = id ? id.parent : null;
            }
            return root;
        }
        var root = walk(cfg.dataset_id), configs = Object.values((window._reportData || {}).components || {}), graph = new Map();
        function connect(a, b) {
            if (!graph.has(a)) graph.set(a, new Set());
            if (!graph.has(b)) graph.set(b, new Set());
            graph.get(a).add(b); graph.get(b).add(a);
        }
        Object.keys(engine._scopes || {}).forEach(function(id) { connect(id, engine._scopes[id].parent); });
        configs.forEach(function(c) {
            if (c.type === 'filter_bar') Object.keys(c.propagate_to || {}).forEach(function(id) { connect(c.dataset_id, id); });
        });
        var pending = Array.from(chain), visited = new Set();
        while (pending.length) {
            var id = pending.pop();
            if (visited.has(id)) continue;
            visited.add(id); walk(id);
            (graph.get(id) || []).forEach(function(other) { pending.push(other); });
        }
        var chunks = window._fwChunkLoader;
        // Any manifest means the source may be a delivered slice, even if its
        // currently requested periods have finished loading.
        if (configs.some(function(c) { return c.type === 'data_source' && chain.has(c.dataset_id) && c.chunk_by; }) ||
            chain.size && Object.keys((window._reportData || {})._chunk_manifests || {}).some(function(id) { return chain.has(id); }) ||
            chunks && Object.keys(chunks._manifests || {}).some(function(id) { return chain.has(id); }))
            fail('chunked population is unsupported');
        if (!engine.isReady || !engine.isReady(root)) fail('source is not ready');
        var col = own(engine._columnar, root);
        if (!col || !Array.isArray(col._data) || col._data.length > 50000) fail('missing or oversized source');
        if (!Array.isArray(col._cols) || new Set(col._cols).size !== col._cols.length || cfg.dimensions.concat([cfg.value, cfg.recordId], cfg.column ? [cfg.column] : []).some(function(name) { return !col._cols.includes(name); }))
            fail('required columns missing from source');
        col._data.forEach(function(row) {
            if (!Array.isArray(row) || row.length !== col._cols.length) fail('malformed source row shape');
        });
        return {engine: engine, full: boundFacts(engine._materializeAll(col), cfg)};
    }
    function node(path) { return {path:path, key:JSON.stringify(path), children:new Map(), facts:[], cells:new Map()}; }
    function append(n, f) {
        n.facts.push(f);
        var key = JSON.stringify(f.bucket);
        if (!n.cells.has(key)) n.cells.set(key, []);
        n.cells.get(key).push(f);
    }
    function model(facts, pathOrder) {
        var root = node([]);
        facts.forEach(function(fact) {
            var parent = root;
            append(parent, fact);
            fact.path.forEach(function(part, index) {
                var path = fact.path.slice(0, index + 1), key = JSON.stringify(path);
                if (!parent.children.has(key)) parent.children.set(key, node(path));
                parent = parent.children.get(key); append(parent, fact);
            });
        });
        // Keep first-seen source order when filtering removes the first fact
        // of a surviving branch. This is ordering, not a user sort action.
        var rank = new Map(), position = 0;
        pathOrder.forEach(function(key) { rank.set(key, position++); });
        function order(n) {
            var entries = Array.from(n.children.entries());
            entries.sort(function(a, b) { return rank.get(a[0]) - rank.get(b[0]); });
            n.children = new Map(entries);
            n.children.forEach(order);
        }
        order(root);
        return root;
    }
    function amount(facts, cfg) {
        if (!facts || !facts.length) return {text:'—', reason:'No observations', count:0, missing:0};
        var valid = [], missing = 0, reasons = new Set();
        facts.forEach(function(fact) {
            if (fact.input === null) { missing++; reasons.add(fact.reason || 'Unavailable input'); }
            else valid.push(fact);
        });
        var reason = Array.from(reasons).join(' ');
        if (missing && (!cfg.knownSubtotals || !valid.length))
            return {text:'—', reason:reason + ' (' + missing + ' unavailable of ' + facts.length + ' facts)', count:facts.length, missing:missing};
        var numeric = window._fwTableTotals({totals:{value:{agg:'sum',format:cfg.format,precision:cfg.precision}},
            totalsInputs:{value:valid.map(function(fact) { return fact.input; })}}, valid.map(function(_, index) { return {index:index}; })).value;
        return {text:numeric.text + (missing && !numeric.reason ? '*' : ''), reason:numeric.reason ||
            (missing ? 'Known subtotal; ' + missing + ' unavailable of ' + facts.length + ' facts. ' + reason : 'Complete; ' + facts.length + ' leaf facts'),
            count:facts.length, missing:missing};
    }
    window._fwRenderers['hierarchy_table'] = function(id, cfg) {
        var el = document.getElementById(id);
        if (leases.has(id)) leases.get(id)();
        if (!el) return;
        var signature = JSON.stringify([cfg.dataset_id || null, cfg.dimensions, cfg.value, cfg.recordId, cfg.column]);
        var saved = el._fwHierarchyState;
        var initializeExpansion = !(saved && saved.signature === signature);
        var expanded = initializeExpansion ? new Set() : saved.expanded;
        el._fwHierarchyState = {signature:signature, expanded:expanded};
        var active = true, unsubscribe = function(){}, observer, lastFacts = [], currentRoot, columns = [], busy = false;
        function current() { return active && el.isConnected && document.getElementById(id) === el; }
        function dispose() {
            if (!active) return;
            active = false; unsubscribe();
            window.removeEventListener('fw-theme-change', redraw);
            window.removeEventListener('pagehide', dispose);
            el.removeEventListener('click', click);
            if (observer) observer.disconnect();
            if (leases.get(id) === dispose) leases.delete(id);
        }
        leases.set(id, dispose);
        function cells(n) {
            return (cfg.column ? columns.map(function(bucket) { return amount(n.cells.get(JSON.stringify(bucket)), cfg); }) : [])
                .concat([amount(n.facts, cfg)]).map(function(a) {
                    return '<td title="' + escape(a.reason) + '" data-fact-count="' + a.count + '" data-unavailable="' + a.missing + '">' + escape(a.text) + '</td>';
                }).join('');
        }
        function draw() {
            var oldFocus = el.contains(document.activeElement), oldButton = oldFocus && document.activeElement.closest('[data-path]');
            var focusPath = oldButton ? JSON.parse(oldButton.dataset.path) : null;
            var oldScroll = el.querySelector('.fw-hierarchy-scroll'), scroll = oldScroll ? oldScroll.scrollLeft : 0;
            var missing = lastFacts.filter(function(f) { return f.input === null; }).length;
            var out = '<div class="fw-hierarchy-actions"><button type="button" class="fw-csv-btn fw-hierarchy-csv" data-leaf-csv>CSV · leaf facts</button></div>';
            if (!lastFacts.length) out += '<p class="fw-hierarchy-empty">No leaf facts match this selection.</p>';
            else {
                out += '<div class="fw-hierarchy-scroll" tabindex="0" role="region" aria-label="' + escape(cfg.title || 'Hierarchy table') + '"><table aria-describedby="' + id + '-hierarchy-note"><thead><tr><th scope="col">' + escape(cfg.dimensions.join(' / ')) + '</th>';
                if (cfg.column) columns.forEach(function(bucket) { out += '<th scope="col" title="' + escape(JSON.stringify(bucket)) + '">' + escape(label(bucket)) + '</th>'; });
                out += '<th scope="col">Total</th></tr></thead><tbody>';
                function visit(n) {
                    n.children.forEach(function(child) {
                        var hasChildren = child.children.size > 0, open = expanded.has(child.key);
                        out += '<tr data-node="' + escape(child.key) + '"><th scope="row" title="Typed path: ' + escape(child.key) + '"><span class="fw-hierarchy-label" style="--hierarchy-depth:' + (child.path.length - 1) + '">';
                        if (hasChildren) out += '<button type="button" data-path="' + escape(child.key) + '" aria-expanded="' + open + '" aria-label="' + escape(label(child.path[child.path.length-1]) + ', ' + child.facts.length + ' matching descendant facts') + '"><span aria-hidden="true">' + (open ? '▾' : '▸') + '</span><span>';
                        out += escape(label(child.path[child.path.length-1]));
                        if (hasChildren) out += '</span></button>';
                        out += '</span></th>' + cells(child) + '</tr>';
                        if (open) visit(child);
                    });
                }
                visit(currentRoot);
                out += '</tbody><tfoot><tr><th scope="row">All matching leaf facts</th>' + cells(currentRoot) + '</tr></tfoot></table></div>';
            }
            out += '<p class="fw-hierarchy-note" id="' + id + '-hierarchy-note">' + lastFacts.length + ' matching leaf facts. Totals include collapsed descendants in the supplied population. ';
            if (missing) out += cfg.knownSubtotals ? '* Known subtotals exclude ' + missing + ' unavailable inputs; every partial result is marked *. All-unknown results remain unavailable.' : missing + ' unavailable inputs; incomplete results are withheld.';
            out += ' A dash indicates an unavailable result or no observations; cell details give the reason. CSV contains matching raw leaf facts only.</p>';
            el.innerHTML = out;
            var scroller = el.querySelector('.fw-hierarchy-scroll');
            if (scroller) scroller.scrollLeft = scroll;
            if (oldFocus) {
                var target;
                while (focusPath && focusPath.length && !target) {
                    var key = JSON.stringify(focusPath);
                    target = Array.from(el.querySelectorAll('[data-path]')).find(function(b) { return b.dataset.path === key; });
                    focusPath.pop();
                }
                (target || el.querySelector('[data-leaf-csv]') || el).focus({preventScroll:true});
            }
        }
        function redraw() {
            if (!current()) { dispose(); return; }
            if (busy) return;
            busy = true;
            try {
                checkConfig(cfg);
                var full, engine;
                if (cfg.dataset_id) {
                    var resolved = source(cfg); full = resolved.full; engine = resolved.engine;
                } else full = cfg.facts;
                var shape = validate(full, cfg);
                if (!Array.isArray(cfg.expanded) || cfg.expanded.length > 2000) fail('malformed expanded paths');
                cfg.expanded.forEach(function(path) {
                    if (!Array.isArray(path) || !path.length || path.length > cfg.dimensions.length) fail('malformed expanded path');
                    path.forEach(typed);
                    if (!shape.paths.has(JSON.stringify(path))) fail('expanded path does not exist in full source');
                });
                if (initializeExpansion) {
                    cfg.expanded.forEach(function(path) { expanded.add(JSON.stringify(path)); });
                    initializeExpansion = false;
                }
                lastFacts = engine ? boundFacts(engine.getFiltered(cfg.dataset_id), cfg) : full;
                validate(lastFacts, cfg);
                // A malformed filtered update must not smuggle in new facts or
                // change the source grain. Compare bounded relevant records.
                if (engine) {
                    var population = new Map(full.map(function(f) { return [f.id, JSON.stringify(f)]; }));
                    lastFacts.forEach(function(f) { if (population.get(f.id) !== JSON.stringify(f)) fail('filtered update differs from full source'); });
                }
                columns = Array.from(shape.buckets.values());
                currentRoot = model(lastFacts, shape.paths); draw();
            } catch (error) {
                lastFacts = []; currentRoot = null;
                var restoreFocus = el.contains(document.activeElement);
                el.innerHTML = '<p class="fw-hierarchy-empty" role="status">' + escape(error.message) + '</p>';
                if (restoreFocus) el.focus({preventScroll:true});
            } finally { busy = false; }
        }
        function click(event) {
            if (!current()) return;
            var toggle = event.target.closest('[data-path]');
            if (toggle && el.contains(toggle)) {
                var key = toggle.dataset.path;
                if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
                draw(); return;
            }
            if (!event.target.closest('[data-leaf-csv]')) return;
            // No parent rows, display rounding, display limits or expansion
            // state enters this export. Every fact retains its typed path.
            var headers = ['record_id','row_type','typed_path'].concat(cfg.dimensions.map(function(d) { return d + ' (typed)'; }),
                cfg.column ? [cfg.column + ' (typed)'] : [], ['raw_value','value_column','value_format','availability','completeness','reason']);
            var rows = lastFacts.map(function(f) {
                var a = amount([f], cfg), available = f.input !== null && !a.text.startsWith('—');
                return [f.id,'leaf_fact',JSON.stringify(f.path)].concat(f.path.map(JSON.stringify),
                    cfg.column ? [JSON.stringify(f.bucket)] : [], [f.raw,cfg.value,cfg.format,available ? 'available' : 'unavailable',
                        available ? 'complete' : 'unavailable',a.reason]);
            });
            // Preserve typed fact strings, raw numeric text and CRLF records.
            function csvCell(value) {
                var valueText = String(value);
                return /[,"\r\n]/.test(valueText) ? '"' + valueText.replace(/"/g, '""') + '"' : valueText;
            }
            var csv = headers.map(csvCell).join(',') + '\r\n';
            rows.forEach(function(row) { csv += row.map(csvCell).join(',') + '\r\n'; });
            var url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8;'}));
            var link = document.createElement('a');
            link.href = url; link.download = 'hierarchy_leaf_facts.csv'; link.click();
            URL.revokeObjectURL(url);
        }
        el.addEventListener('click', click);
        if (cfg.dataset_id && window._fwFilterEngine) unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id + '_hierarchy', redraw);
        window.addEventListener('fw-theme-change', redraw);
        window.addEventListener('pagehide', dispose);
        observer = new MutationObserver(function() { if (!current()) dispose(); });
        observer.observe(document.documentElement, {childList:true, subtree:true});
        redraw();
    };
})();

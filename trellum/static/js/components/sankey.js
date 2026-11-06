(function() {
    'use strict';
    var own = function(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); };
    var LIMIT = 4194304;
    function tooLong(value, max) {return value.length > max * 2 || Array.from(value).length > max;}
    function text(value, max) {
        if (typeof value !== 'string' || !value.trim() || tooLong(value,max)) throw Error('Invalid Sankey text or identity.');
        return value;
    }
    function category(value) { return value === null ? null : text(value, 512); }
    function measure(value, kind) {
        if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
            (kind === 'count' && !Number.isSafeInteger(value)) ||
            (kind === 'quantity' && value > 1e15)) throw Error('Invalid Sankey weight: finite nonnegative ' + kind + ' required.');
        return value;
    }
    function add(a, b, kind) { return measure(a + b, kind); }
    function bytes(value) { return new TextEncoder().encode(JSON.stringify(value)).length; }
    function model(cfg, rows, enforceDomain) {
        if (!['paths', 'edges'].includes(cfg.mode) || !['count', 'quantity'].includes(cfg.weight_kind) ||
            !Array.isArray(cfg.stages) || cfg.stages.length < 2 || cfg.stages.length > 12 ||
            !Array.isArray(rows) || rows.length > 10000) throw Error('Invalid Sankey schema or source row bound.');
        text(cfg.weight, 128); text(cfg.unit, 128);
        if (typeof cfg.title !== 'string' || tooLong(cfg.title,512) || (cfg.header_context != null &&
            (typeof cfg.header_context !== 'string' || tooLong(cfg.header_context,4096)))) throw Error('Invalid Sankey title/context.');
        var stageIndex = new Map(), nodes = new Map(), edges = new Map(), paths = new Map();
        cfg.stages.forEach(function(s, i) {
            if (!s || Object.keys(s).sort().join(',') !== 'key,label') throw Error('Invalid Sankey stage.');
            text(s.key, 128); text(s.label, 256);
            if (stageIndex.has(s.key)) throw Error('Duplicate Sankey stage key.');
            stageIndex.set(s.key, i);
        });
        var edgeCols = [cfg.source, cfg.target, cfg.source_stage, cfg.target_stage];
        if (cfg.mode === 'paths' && edgeCols.some(function(c) {return c != null;})) throw Error('Paths cannot declare edge columns.');
        if (cfg.mode === 'edges') edgeCols.forEach(function(c) {text(c, 128);});
        var cols = [cfg.weight].concat(cfg.mode === 'paths' ? cfg.stages.map(function(s) {return s.key;}) : edgeCols);
        if (new Set(cols).size !== cols.length) throw Error('Sankey columns must have distinct roles.');
        var metadata = Object.assign({},cfg); delete metadata.rows;
        var boundaries = cfg.stages.slice(1).map(function() {return 0;}), total = 0, size = bytes(metadata);
        var domain = new Set(cfg.domain || []), observed = new Set();
        if (!Array.isArray(cfg.domain) || cfg.domain.length > 200) throw Error('Invalid Sankey color domain.');
        cfg.domain.forEach(function(c) {text(c, 512);});
        function node(i, cat) {
            if (cat !== null) {
                observed.add(cat);
                if (enforceDomain && !domain.has(cat)) throw Error('Sankey category is outside the declared snapshot domain.');
            }
            var key = JSON.stringify([i, cat]);
            if (!nodes.has(key)) nodes.set(key, {id:'n' + nodes.size, stage:i, category:cat});
            return nodes.get(key);
        }
        function edge(i, a, b, w) {
            var from = node(i, a), to = node(i + 1, b), key = JSON.stringify([i, a, b]);
            if (!edges.has(key)) edges.set(key, {from:from.id, to:to.id, stage:i, source:a, target:b, flow:0});
            var link = edges.get(key);
            link.flow = add(link.flow, w, cfg.weight_kind);
            boundaries[i] = add(boundaries[i], w, cfg.weight_kind);
        }
        rows.forEach(function(row) {
            if (!row || cols.some(function(c) {return !own(row, c);})) throw Error('Sankey source is missing a required column.');
            var w = measure(row[cfg.weight], cfg.weight_kind), projected = Object.create(null);
            cols.forEach(function(c) {projected[c] = row[c];});
            size += bytes(projected) + 2;
            if (size > LIMIT) throw Error('Sankey relevant payload exceeds 4 MiB.');
            if (cfg.mode === 'paths') {
                var cats = cfg.stages.map(function(s) {return category(row[s.key]);}), key = JSON.stringify(cats);
                if (!paths.has(key)) paths.set(key, {categories:cats, weight:0});
                paths.get(key).weight = add(paths.get(key).weight, w, cfg.weight_kind);
                total = add(total, w, cfg.weight_kind);
                for (var i = 0; i < cats.length - 1; i++) edge(i, cats[i], cats[i + 1], w);
            } else {
                var sk = row[cfg.source_stage], tk = row[cfg.target_stage];
                if (!stageIndex.has(sk) || !stageIndex.has(tk) || stageIndex.get(tk) !== stageIndex.get(sk) + 1)
                    throw Error('Sankey edges must connect adjacent declared stages in order.');
                edge(stageIndex.get(sk), category(row[cfg.source]), category(row[cfg.target]), w);
            }
            if (nodes.size > 200 || edges.size > 1000 || paths.size > 1000) throw Error('Sankey exceeds 200 nodes, 1000 edges or 1000 paths.');
        });
        return {nodes:Array.from(nodes.values()), edges:Array.from(edges.values()), paths:Array.from(paths.values()),
            boundaries:boundaries, total:cfg.mode === 'paths' ? total : null, rowCount:rows.length,
            domain:Array.from(observed).sort()};
    }
    function configs() {
        var data = window._reportData || {}, scope = window._currentScope || data.defaultScope;
        return (window._scopeCache || {})[scope] || data.components || {};
    }
    function fullRows(cfg) {
        var engine = window._fwFilterEngine, id = cfg.dataset_id, seen = new Set(), all = configs();
        if (!engine) throw Error('Sankey snapshot is not ready.');
        var regs = new Map();
        Object.keys(all).forEach(function(k) {var c = all[k]; if (['data_source','scoped_data_source','live_data_source'].includes(c.type)) regs.set(c.dataset_id, c);});
        while (id) {
            if (seen.has(id) || !regs.has(id)) throw Error('Sankey snapshot is unavailable.');
            seen.add(id);
            var c = regs.get(id);
            if (c.type === 'live_data_source' || (engine._liveDatasets || {})[id]) throw Error('Sankey live sources are unsupported.');
            if (c.type !== 'scoped_data_source') {
                if ((engine._scopes || {})[id]) throw Error('Sankey scope differs from its declared snapshot.');
                break;
            }
            if (!(engine._scopes || {})[id] || engine._scopes[id].parent !== c.parent_id)
                throw Error('Sankey scope differs from its declared snapshot.');
            id = c.parent_id;
        }
        var changed = true;
        while (changed) {
            changed = false;
            Object.keys(all).forEach(function(k) {
                var c = all[k];
                if (c.type === 'scoped_data_source' && (seen.has(c.dataset_id) || seen.has(c.parent_id))) {
                    [c.dataset_id,c.parent_id].forEach(function(s) {if (!seen.has(s)) {seen.add(s); changed = true;}});
                }
                if (c.type !== 'filter_bar') return;
                var group = [c.dataset_id].concat(Object.keys(c.propagate_to || {}));
                if (group.some(function(s) {return seen.has(s);})) group.forEach(function(s) {if (!seen.has(s)) {seen.add(s); changed = true;}});
            });
        }
        seen.forEach(function(s) {
            if ((regs.get(s) || {}).type === 'live_data_source' || (engine._liveDatasets || {})[s]) throw Error('Sankey live-connected propagation is unsupported.');
        });
        if (!engine.isReady(cfg.dataset_id)) throw Error('Sankey snapshot is not ready.');
        var col = engine._columnar[id];
        if (!col || !Array.isArray(col._cols) || new Set(col._cols).size !== col._cols.length ||
            !Array.isArray(col._data) || col._data.length > 10000)
            throw Error('Invalid or oversized Sankey snapshot.');
        if (window._fwChunkLoader && (window._fwChunkLoader._manifests || {})[id])
            throw Error('Sankey requires a complete unchunked snapshot.');
        var columns = [cfg.weight].concat(cfg.mode === 'paths' ? cfg.stages.map(function(s) {return s.key;}) : [cfg.source,cfg.target,cfg.source_stage,cfg.target_stage]);
        var indices = columns.map(function(c) {var i = col._cols.indexOf(c); if (i < 0) throw Error('Sankey effective source is missing required columns.'); return i;});
        return col._data.map(function(r) {
            var out = Object.create(null);
            columns.forEach(function(c, j) {out[c] = own(col._dict || {}, c) ? col._dict[c][r[indices[j]]] : r[indices[j]];});
            return out;
        });
    }
    function label(cat) {return cat === null ? 'Unavailable observation' : cat;}
    function stage(cfg, i) {var s = cfg.stages[i]; return s.label + ' [' + s.key + ']';}
    function exact(value) {return String(value);}
    function cell(tag, value) {var e = document.createElement(tag); e.textContent = value; return e;}
    function csv(cfg, graph) {
        var headers = ['mode','record_type','weight','unit','weight_kind'], rows = [];
        if (cfg.mode === 'paths') {
            cfg.stages.forEach(function(s, i) {['key','label','category','availability'].forEach(function(k) {headers.push('stage_' + (i + 1) + '_' + k);});});
            graph.paths.forEach(function(p) {
                var r = ['paths','complete_path',p.weight,cfg.unit,cfg.weight_kind];
                cfg.stages.forEach(function(s, i) {r.push(s.key,s.label,p.categories[i],p.categories[i] === null ? 'unavailable' : 'observed');});
                rows.push(r);
            });
        } else {
            headers = headers.concat(['source_stage_key','source_stage_label','source_category','source_availability','target_stage_key','target_stage_label','target_category','target_availability']);
            graph.edges.forEach(function(e) {rows.push(['edges','adjacent_edge',e.flow,cfg.unit,cfg.weight_kind,
                cfg.stages[e.stage].key,cfg.stages[e.stage].label,e.source,e.source === null ? 'unavailable' : 'observed',
                cfg.stages[e.stage + 1].key,cfg.stages[e.stage + 1].label,e.target,e.target === null ? 'unavailable' : 'observed']);});
        }
        return {columns:headers, rows:rows};
    }
    window._fwRenderers.sankey = function(id, cfg) {
        var canvas = document.getElementById(id);
        if (!canvas) return;
        var root = canvas.closest('.fw-sankey');
        if (!root) return;
        if (root._fwSankeyOwner) root._fwSankeyOwner.dispose();
        var state = root.querySelector('.fw-sankey-state'), population = root.querySelector('.fw-sankey-population');
        var details = root.querySelector('details'), summary = details.querySelector('summary'), records = root.querySelector('.fw-sankey-records');
        var button = root.querySelector('.fw-sankey-download'), plot = root.querySelector('.fw-sankey-plot');
        var active = true, chart = null, graph = null, frame = 0, unsubscribe = null, lastSize = '', internalDestroy = false;
        function destroy() {
            if (chart) {
                var old = chart; chart = null; internalDestroy = true;
                try {old.destroy();} finally {internalDestroy = false;}
                if (window._chartInstances[id] === old) delete window._chartInstances[id];
            }
        }
        function queue() {if (active && !frame) frame = requestAnimationFrame(function() {frame = 0; draw();});}
        function color(cat) {
            var theme = getThemeColors();
            return cat === null ? theme.tick_color : theme.chart_colors[cfg.domain.indexOf(cat) % theme.chart_colors.length];
        }
        function renderDetails() {
            records.replaceChildren();
            var count = cfg.mode === 'paths' ? graph.paths.length : graph.edges.length;
            summary.textContent = 'Exact ' + (cfg.mode === 'paths' ? 'complete paths' : 'adjacent edges') + ' · ' + count + ' supplied grouped records';
            var table = document.createElement('table'), head = document.createElement('thead'), tr = document.createElement('tr'), body = document.createElement('tbody');
            var headings = cfg.mode === 'paths' ? cfg.stages.map(function(s, i) {return stage(cfg, i);}) : ['From stage / category','To stage / category'];
            headings.concat(['Weight (' + cfg.unit + ')']).forEach(function(h) {var th = cell('th', h); th.scope = 'col'; tr.appendChild(th);});
            head.appendChild(tr); table.appendChild(head);
            (cfg.mode === 'paths' ? graph.paths : graph.edges).forEach(function(p) {
                var row = document.createElement('tr');
                var values = cfg.mode === 'paths' ? p.categories.map(label) : [stage(cfg,p.stage) + ': ' + label(p.source),stage(cfg,p.stage + 1) + ': ' + label(p.target)];
                values.concat([exact(cfg.mode === 'paths' ? p.weight : p.flow)]).forEach(function(v) {row.appendChild(cell('td',v));});
                body.appendChild(row);
            });
            table.appendChild(body); records.appendChild(table);
            var totals = graph.boundaries.map(function(v, i) {return stage(cfg, i) + ' → ' + stage(cfg, i + 1) + ': ' + exact(v) + ' ' + cfg.unit;}).join('; ');
            records.appendChild(cell('p','Boundary totals: ' + totals + '.'));
        }
        function paint() {
            destroy();
            if (!graph || !graph.edges.some(function(e) {return e.flow > 0;}) || !root.getClientRects().length || !plot.clientWidth) return;
            var nodes = new Map(graph.nodes.map(function(n) {return [n.id,n];})), columns = Object.create(null), labels = Object.create(null);
            graph.nodes.forEach(function(n) {columns[n.id] = n.stage; labels[n.id] = [];});
            var links = graph.edges.filter(function(e) {return e.flow > 0;});
            chart = new Chart(canvas, {
                type:'sankey', data:{datasets:[{data:links, column:columns, labels:labels, size:'max', nodeWidth:10,
                    color:getThemeColors().tick_color, borderWidth:0,
                    colorFrom:function(c) {return color(c.dataset.data[c.dataIndex].source);},
                    colorTo:function(c) {return color(c.dataset.data[c.dataIndex].target);}}]},
                options:{responsive:true, maintainAspectRatio:false, animation:false,
                    layout:{padding:{left:5,right:16,top:4,bottom:4}},
                    scales:{x:{min:0,max:cfg.stages.length - 1}},
                    plugins:{legend:{display:false},fwLegend:{display:false},datalabels:{display:false},
                        tooltip:{callbacks:{title:function(items) {var e = items[0].raw; return stage(cfg,e.stage) + ' → ' + stage(cfg,e.stage + 1);},
                            label:function(c) {return label(c.raw.source) + ' → ' + label(c.raw.target) + ': ' + exact(c.raw.flow) + ' ' + cfg.unit;}}}}},
                plugins:[{id:'fwSankeyFittingLabels',afterDestroy:function(c) {
                    if (!internalDestroy && active) {
                        if (chart === c) chart = null;
                        if (window._chartInstances[id] === c) delete window._chartInstances[id];
                        owner.dispose();
                    }
                },afterDraw:function(c) {
                    var meta = c.getDatasetMeta(0), controller = meta.controller, ctx = c.ctx;
                    if (!controller._nodes) return;
                    var font = Chart.helpers.toFont({size:window._fwFontPx ? window._fwFontPx('--font-size-label',12) : 12}, Chart.defaults.font);
                    ctx.save(); ctx.font = font.string; ctx.textBaseline = 'middle';
                    ctx.fillStyle = getComputedStyle(root).getPropertyValue('--text-main').trim() || getThemeColors().tick_color;
                    controller._nodes.forEach(function(n, key) {
                        var value = nodes.get(key), text = label(value.category), x = meta.xScale.getPixelForValue(n.x), y = meta.yScale.getPixelForValue(n.y);
                        var h = Math.abs(meta.yScale.getPixelForValue(n.y + Math.max(n.in,n.out)) - y);
                        var available = (c.chartArea.right - c.chartArea.left) / (cfg.stages.length - 1) - 24;
                        if (h < font.lineHeight + 2 || ctx.measureText(text).width > available) return;
                        var end = value.stage === cfg.stages.length - 1;
                        ctx.textAlign = end ? 'right' : 'left'; ctx.fillText(text, x + (end ? -5 : 15), y + h / 2);
                    }); ctx.restore();
                }}]
            });
            chart._fwThemeManagedFonts = true;
            chart._fwSankey = graph;
            window._chartInstances[id] = chart;
        }
        function draw() {
            if (!active) return;
            try {
                if (!Number.isInteger(cfg.height) || cfg.height < 160 || cfg.height > 1200 ||
                    !Number.isInteger(cfg.min_width) || cfg.min_width < 320 || cfg.min_width > 2400) throw Error('Invalid Sankey dimensions.');
                var rows;
                if (cfg.dataset_id) {
                    model(cfg, fullRows(cfg), true);
                    rows = window._fwFilterEngine.getFiltered(cfg.dataset_id);
                } else rows = cfg.rows;
                graph = model(cfg, rows, true);
                headings.replaceChildren(); headings.style.gridTemplateColumns = 'repeat(' + cfg.stages.length + ',minmax(0,1fr))';
                cfg.stages.forEach(function(s,i) {var e = cell('span',stage(cfg,i)); e.title = e.textContent; headings.appendChild(e);});
                state.textContent = graph.rowCount === 0 ? 'No matching ' + (cfg.mode === 'paths' ? 'paths.' : 'transitions.') :
                    graph.edges.every(function(e) {return e.flow === 0;}) ? 'Zero flow: all supplied weights are zero. Exact records remain below.' : '';
                population.textContent = cfg.mode === 'paths'
                    ? exact(graph.total) + ' ' + cfg.unit + ' in mutually exclusive supplied complete paths. Each stage and boundary has this total. Merged ribbons show adjacent movement; they do not establish complete journeys. Use exact complete paths below.'
                    : 'Supplied adjacent transitions only; complete paths are not supplied. Boundary populations may differ and inflow/outflow may be unbalanced. No single unique-population total is assigned across boundaries. Boundary totals: ' + graph.boundaries.map(function(v,i) {return stage(cfg,i) + ' → ' + stage(cfg,i + 1) + ': ' + exact(v) + ' ' + cfg.unit;}).join('; ') + '. Exact adjacent edges are below.';
                renderDetails(); button.disabled = false;
                paint();
                root.dataset.sankeyState = graph.rowCount === 0 ? 'empty' : graph.edges.every(function(e) {return e.flow === 0;}) ? 'zero' : 'ready';
            } catch (error) {
                graph = null; destroy(); records.replaceChildren(); button.disabled = true;
                state.textContent = 'Sankey unavailable: ' + error.message;
                population.textContent = ''; summary.textContent = 'Exact records unavailable'; root.dataset.sankeyState = 'invalid';
            }
        }
        var headings = plot.querySelector('.fw-sankey-stages');
        if (!headings) {
            headings = document.createElement('div'); headings.className = 'fw-sankey-stages';
            var canvasBox = document.createElement('div'); canvasBox.className = 'fw-sankey-canvas';
            canvas.parentNode.insertBefore(headings,canvas); canvas.parentNode.insertBefore(canvasBox,canvas); canvasBox.appendChild(canvas);
        }
        function download(event) {
            event.stopPropagation();
            if (!graph) return;
            var data = csv(cfg,graph), slug = document.body.getAttribute('data-report-slug') || 'report';
            window._fwDownloadCsv(data.columns,data.rows,slug + '_sankey_' + cfg.mode + '.csv');
        }
        button.addEventListener('click',download);
        window.addEventListener('fw-theme-change',queue);
        if (cfg.dataset_id && window._fwFilterEngine) unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id,id,queue);
        var resize = new ResizeObserver(function() {var size = plot.clientWidth + ':' + plot.clientHeight; if (size !== lastSize) {lastSize = size; queue();}});
        resize.observe(plot);
        var removal = new MutationObserver(function() {if (!root.isConnected || !canvas.isConnected || root.querySelector('canvas') !== canvas) owner.dispose();});
        removal.observe(document.documentElement,{childList:true,subtree:true});
        var owner = {dispose:function() {
            if (!active) return;
            active = false; if (frame) cancelAnimationFrame(frame); frame = 0;
            if (unsubscribe) unsubscribe();
            window.removeEventListener('fw-theme-change',queue); button.removeEventListener('click',download);
            resize.disconnect(); removal.disconnect(); destroy();
            if (root._fwSankeyOwner === owner) delete root._fwSankeyOwner;
        }};
        root._fwSankeyOwner = owner;
        draw();
    };
})();

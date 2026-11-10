"""Real cascading controls derive options without full-row allocation.

The scale fixture also supports diagnostic timing with real chart consumers.
Notification coordination is independent of the option-query allocation bound.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

_ROOT = Path(os.environ.get('TRELLUM_CASCADE_RUNTIME_ROOT', str(Path(__file__).resolve().parents[1])))


_SCRIPTS = (
    "vendor/slimselect.min.js",
    "js/runtime/filter_engine.js",
    "js/runtime/url_sync.js",
    "js/runtime/chunk_loader.js",
    "js/components/filter_bar.js",
    "js/components/dropdown_filter.js",
)


_HARNESS = r"""
window.E = window._fwFilterEngine;
// Observe the original callback inside SlimSelect's 100 ms debounce without
// changing its scheduling or replacing it with an engine-only simulation.
window.controlEvents = [];
window.lastControlEvent = 0;
const BundledSlimSelect = window.SlimSelect;
window.SlimSelect = function(cfg) {
    const callback = cfg.events?.afterChange;
    if (callback) cfg.events.afterChange = function(...args) {
        const start = performance.now();
        try { return callback.apply(this, args); }
        finally {
            const end = performance.now();
            lastControlEvent = end;
            controlEvents.push({column:cfg.select.dataset.filterCol, start, end});
        }
    };
    return new BundledSlimSelect(cfg);
};
window.SlimSelect.prototype = BundledSlimSelect.prototype;
window.settle = async function() {
    const start = performance.now();
    do {
        await new Promise(resolve=>setTimeout(resolve,25));
        if (performance.now()-start > 10000) throw new Error('Dropdown callbacks did not settle within 10 seconds');
    }
    while (performance.now() - Math.max(start,lastControlEvent) < 160);
};
window.encode = function(rows) {
    const cols = Object.keys(rows[0] || {}), dict = {};
    for (const col of cols) {
        if (rows.some(r => typeof r[col] === 'string'))
            dict[col] = [...new Set(rows.map(r => r[col]))];
    }
    return {_cols: cols, _dict: dict, _data: rows.map(r => cols.map(c =>
        dict[c] ? dict[c].indexOf(r[c]) : r[c]))};
};
window.mountBar = function(id, ds, filters, propagate = {}) {
    const bar = document.createElement('div'); bar.id = id; bar.className = 'fw-filter-bar';
    for (const f of filters) {
        f.id = id + '_' + f.column; f.type = 'dropdown';
        if (!f.options) {
            const col = E._columnar[ds] || E._columnar[E._scopes[ds].parent];
            const ci = col._cols.indexOf(f.column);
            f.options = [...new Set(col._data.map(r => col._dict[f.column]
                ? col._dict[f.column][r[ci]] : r[ci]).filter(v => v != null && v !== '').map(String))].sort();
        }
        const sel = document.createElement('select');
        sel.className = 'fw-dropdown'; sel.dataset.filterId = f.id;
        sel.dataset.filterCol = f.column; sel.setAttribute('aria-label', f.column);
        sel.multiple = f.multi !== false;
        bar.appendChild(sel);
    }
    document.body.appendChild(bar);
    const cfg = {dataset_id: ds, filters, propagate_to: propagate};
    _fwRenderers.filter_bar(id, cfg);
    return cfg;
};
window.widget = col => document.querySelector('[data-filter-col="' + col + '"]')._fwSlimSelect;
window.optionsFor = col => Array.from(document.querySelector('[data-filter-col="' + col + '"]').options, o => o.value);
window.choose = async (col, values) => { widget(col).setSelected(values); await settle(); };
window.snapshot = () => ({state: E.getFilterState('facts'),
    selected: Object.fromEntries(Array.from(document.querySelectorAll('select.fw-dropdown'),
        s => [s.dataset.filterCol, s._fwSlimSelect.getSelected()])),
    options: Object.fromEntries(Array.from(document.querySelectorAll('select.fw-dropdown'),
        s => [s.dataset.filterCol, Array.from(s.options, o => o.value)]))});
window.probe = {reset() {
    this.calls = {}; this.materializedRows = 0; this.optionMaterializations = 0;
    this.optionRows = 0; this.depth = 0; this.maxDepth = 0; this.deliveries = {};
    this.optionDepth = 0; this.observed = {};
}};
probe.reset();
for (const method of ['_materializeAll', '_materializeIndices', 'getFilteredExcluding', 'getDistinctValues', 'notify']) {
    if (typeof E[method] !== 'function') continue;
    const original = E[method];
    E[method] = function(...args) {
        probe.calls[method] = (probe.calls[method] || 0) + 1;
        const options = method === 'getFilteredExcluding' || method === 'getDistinctValues';
        const materialize = method === '_materializeAll' || method === '_materializeIndices';
        if (options) probe.optionDepth++;
        if (method === 'notify') { probe.depth++; probe.maxDepth = Math.max(probe.depth, probe.maxDepth); }
        if (materialize && probe.optionDepth) probe.optionMaterializations++;
        const cachedAll = method === '_materializeAll' && !!args[0]._allRows;
        try {
            const result = original.apply(this, args);
            if (materialize) {
                // Count objects actually allocated, not a cached _allRows read.
                const count = cachedAll ? 0 : result.length;
                probe.materializedRows += count;
                if (probe.optionDepth) probe.optionRows += count;
            }
            return result;
        } finally {
            if (options) probe.optionDepth--;
            if (method === 'notify') probe.depth--;
        }
    };
}
window.watch = function(ds, id, peers = []) {
    E.subscribe(ds, id, rows => {
        probe.deliveries[id] = (probe.deliveries[id] || 0) + 1;
        probe.observed[id] = {rows: rows.map(r => r.id), state: E.getFilterState(ds),
            peers:Object.fromEntries(peers.map(peer=>[peer,E.getFilterState(peer)]))};
    });
};
window.longTasks = [];
if (PerformanceObserver.supportedEntryTypes.includes('longtask')) {
    window.longObserver = new PerformanceObserver(list => longTasks.push(...list.getEntries().map(
        e => ({start: e.startTime, duration: e.duration}))));
    longObserver.observe({type: 'longtask', buffered: true});
}
"""


class CascadePage:
    def __init__(self, browser):
        self.page = browser.new_page(viewport={"width": 1280, "height": 900})
        self.requests = []
        self.errors = []
        scripts = "\n".join((_ROOT / "static" / name).read_text() for name in _SCRIPTS)
        css = (_ROOT / "static/vendor/slimselect.css").read_text()
        html = ('<!doctype html><html><head><style>' + css + '</style></head><body>'
                '<script>window._fwRenderers={};</script><script>' + scripts + '</script>'
                '<script>' + _HARNESS + '</script></body></html>')

        def route(request):
            self.requests.append(request.request.url)
            if request.request.resource_type == 'document':
                request.fulfill(status=200, content_type='text/html', body=html)
            else:
                request.abort()

        self.page.route('**/*', route)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto('http://cascade.test/index.html', wait_until='load')

    def close(self):
        self.page.close()


@pytest.fixture(scope='module')
def cascade_browser():
    with sync_playwright() as pw:
        kwargs = {}
        if os.environ.get('TRELLUM_CHROMIUM_EXECUTABLE'):
            kwargs['executable_path'] = os.environ['TRELLUM_CHROMIUM_EXECUTABLE']
        browser = pw.chromium.launch(**kwargs)
        yield browser
        browser.close()


@pytest.fixture
def cascade(cascade_browser):
    harness = CascadePage(cascade_browser)
    yield harness
    assert harness.errors == []
    harness.close()


def _init_small(page, *, parent_multi=True, clear=False, reversed_chain=False, propagate=False):
    page.evaluate(r"""async cfg => {
        const rows = [];
        for (const parent of ['A', 'B']) for (const category of ['keep', 'drop'])
            for (const day of ['2026-11-01', '2026-11-02'])
                for (const child of ['shared', parent + '-only'])
                    rows.push({id: rows.length, parent, category, day, child,
                        sibling: 's-' + category + '-' + child, leaf: 'l-' + child, clear: child});
        for (const parent of ['A', 'B']) rows.push({id:rows.length,parent,category:'keep',day:'2026-11-01',child:'date-blocked',sibling:'date-blocked',leaf:'date-blocked',clear:'date-blocked'});
        window.sourceRows = rows;
        E.initColumnar('facts', encode(rows));
        if (cfg.propagate) E.initColumnar('target', encode(rows));
        E.setFilter('facts', 'date', 'day', 'range', {min:'2026-11-02', max:'2026-11-02'});
        E.setFilter('facts', 'category', 'category', 'equals', 'keep');
        // A single select may already display its first value without firing
        // afterChange. Seed the matching initial state for both parent modes.
        E.setFilter('facts', 'bar_parent', 'parent', 'in', ['A']);
        if (cfg.propagate) E.setFilter('target', 'prop_facts_parent_target', 'parent', 'in', ['A']);
        let filters = [
            {column:'parent', multi:cfg.parent_multi},
            {column:'child', depends_on:['parent']},
            {column:'sibling', depends_on:['parent', 'category']},
            {column:'clear', depends_on:['parent'], clear_on_parent_change:cfg.clear}
        ];
        if (cfg.reversed_chain) filters = [
            {column:'leaf', depends_on:['child']}, ...filters
        ];
        window.barConfig = mountBar('bar', 'facts', filters, cfg.propagate
            ? {target: {parent:'parent', child:'child', sibling:'sibling', clear:'clear'}} : {});
        await choose('parent', ['A']);
    }""", {'parent_multi': parent_multi, 'clear': clear,
             'reversed_chain': reversed_chain, 'propagate': propagate})


def _click_option(page, col, value):
    select = page.locator(f'select[data-filter-col="{col}"]')
    widget_id = select.get_attribute('data-id')
    page.locator(f'.ss-main[data-id="{widget_id}"]').click()
    page.locator(f'.ss-content[data-id="{widget_id}"] .ss-option').filter(has_text=value).click()
    page.keyboard.press('Escape')
    page.evaluate('settle()')


_SCALE_SETUP = r"""async () => {
    const cols = ['id', 'day', 'parent', 'category', ...Array.from({length:7}, (_,j) => 'child'+j),
        ...Array.from({length:13}, (_,j) => 'metric'+j)];
    const dict = {parent:['A','B'], category:['keep','drop'], day:['2026-11-01','2026-11-02']};
    for(let j=0;j<7;j++) dict['child'+j] = ['common','A-only','B-only'];
    const data = new Array(350000);
    for (let i=0;i<data.length;i++) {
        const parent=i%2, choice=Math.floor(i/8)%3===1 ? parent+1 : 0;
        data[i]=[i, Math.floor(i/2)%2, parent, Math.floor(i/4)%2,
            ...Array(7).fill(choice), ...Array.from({length:13},(_,j)=>(i+j)%100)];
    }
    E.initColumnar('facts', {_cols:cols, _dict:dict, _data:data});
    await new Promise((resolve, reject) => {
        const start = performance.now();
        const ready=()=> {
            if (Object.keys(dict).every(c => E._columnar.facts._indexes?.[c])) return resolve();
            if (performance.now()-start > 15000) return reject(new Error('Idle indexes not ready within 15 seconds'));
            setTimeout(ready, 10);
        }; ready();
    });
    E.setFilter('facts','date','day','range',{min:'2026-11-02',max:'2026-11-02'});
    E.setFilter('facts','category','category','equals','keep');
    E.setFilter('facts','bar_parent','parent','in',['A']);
    mountBar('bar', 'facts', [{column:'parent'}, ...Array.from({length:7},(_,j)=>
        ({column:'child'+j, depends_on:['parent']}))]);
    await choose('parent',['A']); await choose('child0',['common','A-only']);
    // Independently decode and evaluate this fixture, not engine helpers.
    window.expectedScale = parent => {
        const pred = r => dict.parent[r[2]] === parent && dict.day[r[1]] === '2026-11-02'
            && dict.category[r[3]] === 'keep';
        const valid = data.filter(pred);
        const final = valid.filter(r=>dict.child0[r[4]] === 'common');
        const options = {};
        for (let j=0;j<7;j++) options['child'+j] = [...new Set((j===0?valid:final)
            .map(r=>dict['child'+j][r[4+j]]))].sort();
        return {ids:final.map(r=>r[0]), options};
    };
    // Ten synthetic consumers do real row iteration. No chart/rendering claim.
    for(let j=0;j<10;j++) watch('facts','consumer'+j);
    await new Promise(resolve=>requestAnimationFrame(()=>setTimeout(resolve,50)));
    probe.reset();
}"""


def setup_scale(page):
    page.evaluate(_SCALE_SETUP)


def measure_switches(page, *, repetitions=10, warmup=2):
    """Actual SlimSelect callbacks; initialization and warmup kept out of samples."""
    return page.evaluate(r"""async cfg => {
        const samples = [];
        for(let i=0;i<cfg.warmup+cfg.repetitions;i++) {
            const parent = i%2 ? 'A':'B';
            // Force child pruning on EVERY measured parent replacement.
            await choose('child0',['common', (parent==='A'?'B':'A')+'-only']);
            await new Promise(resolve=>setTimeout(resolve,0));
            probe.reset();
            const heapBefore=performance.memory?.usedJSHeapSize ?? null;
            controlEvents = [];
            const start=performance.now(); await choose('parent',[parent]);
            const end=Math.max(start,...controlEvents.map(e=>e.end));
            const handler_ms=controlEvents.reduce((sum,e)=>sum+e.end-e.start,0);
            const heapAfter=performance.memory?.usedJSHeapSize ?? null;
            // Let the switch task end BEFORE independent oracle/serialization work.
            await new Promise(resolve=>setTimeout(resolve,30));
            const stats=JSON.parse(JSON.stringify(probe));
            const actual=snapshot();
            const expected=expectedScale(parent);
            const rowsOK=Object.keys(stats.observed).length===10 && Object.values(stats.observed).every(o=>
                JSON.stringify(o.rows)===JSON.stringify(expected.ids));
            const optionsOK=Object.keys(expected.options).every(c=>
                JSON.stringify(actual.options[c])===JSON.stringify(expected.options[c]));
            await new Promise(resolve=>setTimeout(resolve,30));
            if(i>=cfg.warmup) samples.push({duration_ms:end-start, handler_ms, controlEvents:controlEvents.slice(), start, end,
                parent, stats, rowsOK, optionsOK, expectedRows:expected.ids.length,
                heapBefore, heapAfter,
                maxLongTask_ms:Math.max(0,...longTasks.filter(e=>e.start<end&&e.start+e.duration>start).map(e=>e.duration))});
        }
        // Row IDs and state copies are useful to assertions, but need not bloat diagnostics.
        for(const s of samples) delete s.stats.observed;
        const sorted=samples.map(s=>s.duration_ms).sort((a,b)=>a-b);
        return {samples, p50_ms:sorted[Math.ceil(sorted.length*.5)-1],
            p95_ms:sorted[Math.ceil(sorted.length*.95)-1],
            maxLongTask_ms:Math.max(0,...samples.map(s=>s.maxLongTask_ms)),
            consumerKind:'10 synthetic row subscribers; no chart rendering',
            heapNote:'before/after usedJSHeapSize snapshots, NOT peak temporary heap'};
    }""", {'repetitions': repetitions, 'warmup': warmup})




def test_parent_options_preserve_valid_selections_without_materializing(cascade):
    page = cascade.page
    _init_small(page, parent_multi=False)
    page.evaluate("""async () => {
        await choose('child', ['shared','A-only']);
        probe.reset();
    }""")
    _click_option(page, 'parent', 'B')
    result = page.evaluate('({snap:snapshot(), probe})')
    assert result['snap']['selected']['child'] == ['shared']
    assert result['snap']['options']['child'] == ['B-only', 'shared']
    assert result['probe']['calls'].get('getDistinctValues', 0) > 0
    assert result['probe']['optionMaterializations'] == 0
    assert result['probe']['optionRows'] == 0


def test_unrelated_filter_keeps_child_selection_and_declared_parent_trigger(cascade):
    page = cascade.page
    _init_small(page)
    page.evaluate("""async () => {
        await choose('child', ['A-only']);
        E.setFilter('facts','date','day','range',{min:'2030-01-01',max:'2030-01-02'});
        await settle();
    }""")
    assert page.evaluate("widget('child').getSelected()") == ['A-only']
    assert page.evaluate("optionsFor('child')") == ['A-only', 'shared']
    assert page.evaluate("E.getFiltered('facts')") == []


def test_chunk_reinit_replaces_cached_distinct_results(cascade):
    page = cascade.page
    _init_small(page)
    result = page.evaluate("""() => {
        const before=E.getDistinctValues('facts','child',{excludingColumns:['child']});
        _fwChunkLoader._baseRows.facts=sourceRows;
        _fwChunkLoader._chunkRows.facts={extra:[{...sourceRows[2],child:'chunk-added'}]};
        _fwChunkLoader._mergeAndReinit('facts');
        probe.reset();
        const after=E.getDistinctValues('facts','child',{excludingColumns:['child']});
        return {before,after,rows:probe.optionRows};
    }""")
    assert result == {'before':['A-only','shared'], 'after':['A-only','chunk-added','shared'], 'rows':0}


@pytest.mark.heavy
def test_large_cascade_has_no_option_row_materialization(cascade, record_property):
    setup_scale(cascade.page)
    requests = len(cascade.requests)
    measurement = measure_switches(cascade.page, repetitions=3)
    record_property('cascade_measurement', json.dumps(measurement))
    for sample in measurement['samples']:
        stats = sample['stats']
        assert sample['rowsOK'] and sample['optionsOK'], sample
        assert stats['optionMaterializations'] == 0, sample
        assert stats['optionRows'] == 0, sample
        assert stats['calls'].get('getDistinctValues', 0) > 0, sample
        assert stats['calls'].get('getFilteredExcluding', 0) == 0, sample
        # Existing cascades may still deliver multiple row results. Option
        # derivation must add no full-row allocations to those deliveries.
        assert stats['calls'].get('_materializeAll', 0) + stats['calls'].get('_materializeIndices', 0) <= 3, sample
        assert stats['materializedRows'] <= 3 * sample['expectedRows'], sample
    assert len(cascade.requests) == requests

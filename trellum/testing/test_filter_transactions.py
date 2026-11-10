"""Synchronous filter transactions settle corrections before delivering rows."""

from __future__ import annotations

import pytest

from trellum.assets import load_js


@pytest.fixture(scope="module")
def transaction_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def transaction_page(transaction_browser):
    page = transaction_browser.new_page()
    page.route("**/*", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<!doctype html><html><body></body></html>",
    ))
    page.goto("http://transactions.test/")
    page.evaluate("window._fwRenderers = {}")
    for script in (
        "runtime/filter_engine.js", "runtime/url_sync.js", "components/filter_bar.js",
        "components/dropdown_filter.js", "components/date_range_filter.js", "components/slider_filter.js",
    ):
        page.add_script_tag(content=load_js(script))
    yield page
    page.close()


def test_nested_grouping_delivers_final_rows_once_and_coalesces_url(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine, seen = [], after = [];
        e.init('root', [{x:'A'}, {x:'B'}]);
        e.addScopedChild('child', 'root'); e.addScopedChild('leaf', 'child');
        let materializations = 0, writes = 0;
        const materialize = e._materializeIndices;
        e._materializeIndices = function(...args) { materializations++; return materialize.apply(this,args); };
        history.replaceState = () => writes++;
        _fwUrlSync.register('root', [], () => [{column:'x',type:'dropdown',value:e._state.root.f.value}]);
        for (const id of ['root','child','leaf']) for (const consumer of ['chart','table'])
            e.subscribe(id,consumer,rows => seen.push([id,consumer,rows.map(r=>r.x)]));
        const value = e.transaction(() => {
            e.setFilter('root','f','x','in',['A']); _fwUrlSync.write();
            e.transaction(() => {
                e.setFilter('root','f','x','in',['B']);
                e.afterTransaction('one', () => after.push('obsolete'));
                _fwUrlSync.write();
            });
            e.afterTransaction('one', () => after.push(e.getFiltered('leaf')[0].x));
            if (seen.length || writes) throw Error('Delivered before outer transaction finished');
            return 42;
        });
        return {value,seen,after,writes,materializations,stats:e.getEngineStats()};
    }""")
    assert result == {
        "value": 42,
        "seen": [[ds, consumer, ["B"]] for ds in ("root", "child", "leaf") for consumer in ("chart", "table")],
        "after": ["B"], "writes": 1, "materializations": 1,
        "stats": {"datasetCount": 3, "totalRows": 4},
    }


def test_descendant_preparers_settle_before_any_source_consumer(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine, seen = [], preparation = [];
        e.init('root', [{parent:'A',child:'a'}, {parent:'B',child:'b'}]);
        e.addScopedChild('scope','root'); e.addScopedChild('leaf','scope');
        e.setFilter('scope','child','child','in',['a']);
        e.getFiltered('leaf'); // Warm a descendant cache before invalidating its ancestor.
        let materializations = 0;
        const materialize = e._materializeIndices;
        e._materializeIndices = function(...args) { materializations++; return materialize.apply(this,args); };
        e.beforeNotify('leaf','leaf', () => preparation.push('leaf'));
        e.beforeNotify('scope','prune', () => {
            preparation.push('scope');
            const values = e.getDistinctValues('scope','child',{excludingColumns:['child']});
            if (!values.includes(e._state.scope.child.value[0]) && e._state.scope.child.value.length)
                e.setFilter('scope','child','child','in',[]);
        });
        for (const id of ['root','scope','leaf']) e.subscribe(id,id,rows => {
            seen.push([id,rows.map(r=>r.child),e._state.scope.child.value]);
        });
        // A direct call discovers managed descendants and settles synchronously.
        e.setFilter('root','parent','parent','in',['B']);
        return {seen,preparation,materializations};
    }""")
    assert result["seen"] == [[ds, ["b"], []] for ds in ("root", "scope", "leaf")]
    assert result["preparation"] == ["scope", "leaf", "scope", "leaf"]
    assert result["materializations"] == 1


@pytest.mark.parametrize("failure", ["body", "preparer", "subscriber", "after"])
def test_exception_recovery_does_not_leak_queued_work(transaction_page, failure):
    result = transaction_page.evaluate("""failure => {
        const e = _fwFilterEngine, seen = [], after = [];
        e.init('root',[{x:'A'},{x:'B'}]);
        const stopPrepare = e.beforeNotify('root','fail', () => {
            if (failure === 'preparer') throw Error('expected preparer');
        });
        const stopSubscriber = e.subscribe('root','fail',rows => {
            if (failure === 'subscriber') throw Error('expected subscriber');
            seen.push(rows[0].x);
        });
        let error;
        try {
            e.transaction(() => {
                e.setFilter('root','f','x','equals','A');
                e.afterTransaction('failed', () => {
                    if (failure === 'after') throw Error('expected after');
                    after.push('first');
                });
                if (failure === 'body') throw Error('expected body');
            });
        } catch (err) { error = err.message; }
        const first = seen.slice();
        stopPrepare(); stopSubscriber(); seen.length = 0;
        e.subscribe('root','ok', rows => seen.push(rows[0].x));
        e.transaction(() => {
            e.setFilter('root','f','x','equals','B');
            e.afterTransaction('success', () => after.push('second'));
        });
        return {error,first,seen,after};
    }""", failure)
    assert result == {
        "error": f"expected {failure}",
        "first": ["A"] if failure in ("body", "after") else [],
        "seen": ["B"], "after": ["first", "second"] if failure == "body" else ["second"],
    }


def test_nonsettling_preparation_is_bounded_and_recoverable(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine;
        e.init('root',[{x:'A'},{x:'B'}]);
        const stop = e.beforeNotify('root','loop', () => e.prepare('root'));
        let error;
        try { e.setFilter('root','f','x','equals','A'); } catch (err) { error = err.message; }
        stop();
        let delivered = 0;
        e.subscribe('root','recovered', () => delivered++);
        e.setFilter('root','f','x','equals','B');
        return {error,delivered,rows:e.getFiltered('root')};
    }""")
    assert result == {"error": "Filter preparation did not settle", "delivered": 1, "rows": [{"x": "B"}]}


def test_remove_unsubscribe_unregister_and_url_disposal_drop_pending_owners(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine, seen = [];
        e.init('gone',[{x:'A'}]); e.init('kept',[{x:'A'}]);
        e.beforeNotify('gone','stale', () => seen.push('removed preparer'));
        e.subscribe('gone','stale', () => seen.push('removed subscriber'));
        const stopPrepare = e.beforeNotify('kept','stopped', () => seen.push('stopped preparer'));
        const stopSubscriber = e.subscribe('kept','stopped', () => seen.push('stopped subscriber'));
        const oldHooks = {onChange:() => seen.push('old live')};
        e.registerLiveDataset('live',oldHooks);
        const unregisterUrl = _fwUrlSync.register('gone', [], () => { throw Error('stale URL owner'); });
        e.transaction(() => {
            e.setFilter('gone','f','x','equals','A');
            e.setFilter('kept','f','x','equals','A');
            e.setFilter('live','f','x','equals','A');
            _fwUrlSync.write();
            e.removeDataset('gone');
            stopPrepare(); stopSubscriber(); unregisterUrl();
            e.unregisterLiveDataset('live',oldHooks);
            e.registerLiveDataset('live',{onChange:() => seen.push('replacement live')});
        });
        // Removal must not leave a preparer attached to a reused dataset ID.
        e.init('gone',[{x:'B'}]);
        e.setFilter('gone','f','x','equals','B');
        return seen;
    }""")
    assert result == []


def test_live_grouping_settles_corrections_before_one_request_per_dataset(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine, requests = [], deliveries = [];
        for (const id of ['live','linked']) {
            e.init(id,[{parent:'A',child:'a'},{parent:'B',child:'b'}]);
            e.registerLiveDataset(id,{onChange:() => requests.push([id,e.getFilterState(id)])});
            e.subscribe(id,id,rows => deliveries.push([id,rows]));
        }
        e._state.live.child = {column:'child',mode:'in',value:['a']};
        e.beforeNotify('live','correction', () => {
            if (!e._state.live.child.value.length) return;
            e.setFilter('live','child','child','in',[]);
            e.setFilter('linked','child','child','in',[]);
        });
        e.transaction(() => {
            e.setFilter('live','parent','parent','in',['B']);
            e.setFilter('linked','parent','parent','in',['B']);
        });
        const snapshot = e.getFiltered('live').map(r=>r.child);
        const explicit = e.getFilteredExcluding('live',[]).map(r=>r.child);
        const options = e.getDistinctValues('live','child',{excludingColumns:['child']});
        const beforeResponse = deliveries.length;
        e.setLiveRows('live',['parent','child'],[['B','b']]);
        return {requests,beforeResponse,snapshot,explicit,options,deliveries};
    }""")
    expected_state = {
        "parent": {"column": "parent", "mode": "in", "value": ["B"], "flagValue": None},
        "child": {"column": "child", "mode": "in", "value": [], "flagValue": None},
    }
    assert result == {
        "requests": [[ds, expected_state] for ds in ("live", "linked")],
        "beforeResponse": 0, "snapshot": ["a", "b"], "explicit": ["b"], "options": ["b"],
        "deliveries": [["live", [{"parent": "B", "child": "b"}]]],
    }


def test_transient_corrections_and_propagation_preview_locally_until_commit(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine, requests = [], deliveries = [];
        for (const id of ['local','live']) e.init(id,[{x:'A',child:'a'},{x:'B',child:'b'}]);
        e.registerLiveDataset('live',{onChange:() => requests.push(e.getFilterState('live'))});
        e._state.local.child = {column:'child',mode:'in',value:['a']};
        e.beforeNotify('local','prune', context => {
            if (!e._state.local.child.value.length) return;
            const set = context.transient ? e.setFilterTransient : e.setFilter;
            set.call(e,'local','child','child','in',[]);
            set.call(e,'live','child','child','in',[]);
        });
        e.subscribe('local','preview',rows => deliveries.push(rows.map(r=>r.child)));
        e.subscribe('live','snapshot',() => { throw Error('Transient live row notification'); });
        e.transaction(() => {
            e.setFilterTransient('local','parent','x','equals','B');
            e.setFilterTransient('live','parent','x','equals','B');
        });
        const preview = {requests:requests.length,deliveries:deliveries.slice(),state:e.getFilterState('live')};
        e.transaction(() => {
            e.setFilter('local','parent','x','equals','B');
            e.setFilter('live','parent','x','equals','B');
        });
        return {preview,requests,deliveries};
    }""")
    state = {
        "parent": {"column": "x", "mode": "equals", "value": "B", "flagValue": None},
        "child": {"column": "child", "mode": "in", "value": [], "flagValue": None},
    }
    assert result == {
        "preview": {"requests": 0, "deliveries": [["b"]], "state": state},
        "requests": [state], "deliveries": [["b"], ["b"]],
    }


def test_clear_apis_join_transactions_and_remain_synchronous(transaction_page):
    result = transaction_page.evaluate("""() => {
        const e = _fwFilterEngine, seen = [];
        e.init('root',[{x:'A'},{x:'B'}]);
        e.setFilter('root','f','x','equals','A');
        e.subscribe('root','chart',rows => seen.push(rows.map(r=>r.x).join('')));
        e.transaction(() => {
            e.clearFilter('root','f');
            e.setFilter('root','other','x','equals','B');
            e.clearAllFilters('root');
        });
        const grouped = seen.slice();
        e.setFilter('root','f','x','equals','B');
        e.clearFilter('root','f');
        e.clearAllFilters('root');
        return {grouped,seen};
    }""")
    assert result == {"grouped": ["AB"], "seen": ["AB", "B", "AB", "AB"]}


@pytest.fixture
def controls_page(transaction_page):
    from trellum.assets import STATIC_ROOT

    page = transaction_page
    for name in ("slimselect", "nouislider"):
        page.add_script_tag(content=(STATIC_ROOT / "vendor" / f"{name}.min.js").read_text())
    page.add_style_tag(content=(STATIC_ROOT / "vendor" / "nouislider.min.css").read_text())
    page.evaluate("""() => {
        window.mount = filters => {
            const bar = document.createElement('div'); bar.id = 'bar';
            for (const fc of filters) {
                const control = document.createElement(fc.type === 'slider' ? 'div' : 'select');
                control.className = fc.type === 'slider' ? 'fw-slider' : 'fw-dropdown';
                control.dataset.filterId = fc.id;
                control.multiple = fc.multi !== false;
                control.style.width = '400px';
                bar.appendChild(control);
            }
            document.body.appendChild(bar);
            _fwRenderers.filter_bar('bar',{dataset_id:'facts',filters,
                propagate_to:{linked:{parent:'parent',child:'child',leaf:'leaf',sibling:'sibling'}}});
        };
        window.widget = id => document.querySelector('[data-filter-id="'+id+'"]')._fwSlimSelect;
    }""")
    return page


@pytest.mark.parametrize("reverse", [False, True])
def test_real_cascade_transaction_settles_chain_siblings_and_muted_callbacks(controls_page, reverse):
    page = controls_page
    page.evaluate("""reverse => {
        const e = _fwFilterEngine;
        const rows = [
            {parent:'A',child:'a',leaf:'la',sibling:'sa'},
            {parent:'B',child:'b',leaf:'lb',sibling:'sb'},
            {parent:'C',child:'c',leaf:'lc',sibling:'sc'}
        ];
        e.init('facts',rows); e.init('linked',rows);
        history.replaceState(null,'','?parent=A&child=a&leaf=la&sibling=sa');
        let filters = [
            {id:'parent',column:'parent',type:'dropdown',options:['A','B','C'],multi:false},
            {id:'child',column:'child',type:'dropdown',options:['a','b','c'],depends_on:['parent']},
            {id:'leaf',column:'leaf',type:'dropdown',options:['la','lb','lc'],depends_on:['child']},
            {id:'sibling',column:'sibling',type:'dropdown',options:['sa','sb','sc'],depends_on:['parent','child']}
        ];
        if (reverse) filters.reverse();
        mount(filters);
        window.deliveries = []; window.requests = []; window.writes = 0; window.materializations = 0;
        e.registerLiveDataset('linked',{onChange:() => requests.push(e.getFilterState('linked'))});
        e.subscribe('facts','chart',rows => deliveries.push({rows:rows.map(r=>r.child),state:e.getFilterState('facts')}));
        const materialize = e._materializeIndices;
        e._materializeIndices = function(...args) { materializations++; return materialize.apply(this,args); };
        history.replaceState = () => writes++;
        widget('parent').setSelected(['B']);
    }""", reverse)
    page.wait_for_function("deliveries.length === 1")
    # Allow any incorrectly queued programmatic afterChange to run as well.
    page.evaluate("() => new Promise(resolve => setTimeout(resolve,180))")
    result = page.evaluate("""() => ({deliveries,requests:requests.length,writes,materializations,
        options:['child','leaf','sibling'].map(id=>widget(id).getData().map(o=>o.value)),
        selected:['child','leaf','sibling'].map(id=>widget(id).getSelected())})""")
    assert result["deliveries"][0]["rows"] == ["b"]
    assert len(result["deliveries"]) == result["requests"] == result["writes"] == result["materializations"] == 1
    assert [result["deliveries"][0]["state"][id]["value"] for id in ("child", "leaf", "sibling")] == [[], [], []]
    assert result["options"] == [["b"], ["lb"], ["sb"]]
    assert result["selected"] == [[], [], []]
    # Two real changes inside the debounce window still commit the latest one.
    page.evaluate("widget('parent').setSelected(['A']); widget('parent').setSelected(['C'])")
    page.wait_for_function("deliveries.length === 2")
    assert page.evaluate("deliveries[1].rows") == ["c"]
    assert page.evaluate("requests.length") == 2


def test_real_slider_transient_child_correction_never_queries_propagated_live_target(controls_page):
    page = controls_page
    page.evaluate("""() => {
        const e = _fwFilterEngine, rows = [{parent:'A',child:'a'},{parent:'B',child:'b'}];
        e.init('facts',rows); e.init('linked',rows);
        history.replaceState(null,'','?child=a');
        mount([
            {id:'parent',column:'parent',type:'slider',ordinal:true,mode:'single',values:['A','B'],default:0},
            {id:'child',column:'child',type:'dropdown',options:['a','b'],depends_on:['parent']}
        ]);
        window.requests = []; window.deliveries = []; window.writes = 0;
        history.replaceState = () => writes++;
        e.registerLiveDataset('linked',{onChange:() => requests.push(e.getFilterState('linked'))});
        e.subscribe('facts','preview',rows => deliveries.push(rows.map(r=>r.child)));
    }""")
    box = page.locator(".noUi-handle").bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 350, box["y"] + box["height"] / 2, steps=4)
    page.wait_for_function("_fwFilterEngine._state.facts.parent.value === 'B' && _fwFilterEngine._state.facts.child.value.length === 0")
    page.evaluate("() => new Promise(resolve => setTimeout(resolve,180))")
    assert page.evaluate("({requests:requests.length,writes,preview:deliveries.at(-1)})") == {
        "requests": 0, "writes": 0, "preview": ["b"],
    }
    page.mouse.up()
    assert page.evaluate("({requests:requests.length,writes,child:_fwFilterEngine.getFilterState('linked').prop_facts_child_linked.value})") == {
        "requests": 1, "writes": 1, "child": [],
    }


def test_single_select_empty_correction_uses_all_placeholder(controls_page):
    page = controls_page
    page.evaluate("""() => {
        _fwFilterEngine.init('facts',[{parent:'A',child:'a'},{parent:'B',child:'b'}]);
        history.replaceState(null,'','?parent=A&child=a');
        mount([
            {id:'parent',column:'parent',type:'dropdown',options:['A','B'],multi:false},
            {id:'child',column:'child',type:'dropdown',options:['a','b'],multi:false,depends_on:['parent']}
        ]);
        widget('parent').setSelected(['B']);
    }""")
    page.wait_for_function("_fwFilterEngine._state.facts.parent.value[0] === 'B'")
    assert page.evaluate("""() => ({state:_fwFilterEngine._state.facts.child.value,
        selected:widget('child').getSelected(),text:widget('child').render.main.main.textContent.trim(),
        options:widget('child').getData().map(o=>({value:o.value,placeholder:o.placeholder}))})""") == {
        "state": [], "selected": [""], "text": "(All)",
        "options": [{"value": "", "placeholder": True}, {"value": "b", "placeholder": False}],
    }


def test_scope_replacement_cancels_readiness_and_disposes_preparation_and_url_owner(controls_page):
    result = controls_page.evaluate("""() => {
        const e = _fwFilterEngine, cancelled = [], late = [];
        e.init('facts',[{parent:'A',child:'a'}]);
        e.init('other',[{parent:'B',child:'b'}]);
        const ready = e.onReady;
        e.onReady = (id,fn) => { late.push(fn); return () => cancelled.push(id); };
        const filters = [
            {id:'parent',column:'parent',type:'dropdown',options:['A','B']},
            {id:'child',column:'child',type:'dropdown',options:['a','b'],depends_on:['parent']}
        ];
        mount(filters);
        const old = widget('child');
        _fwRenderers.filter_bar('bar',{dataset_id:'other',filters});
        e.onReady = ready;
        late.forEach(fn => fn());
        e.setFilter('facts','stale','parent','in',['A']);
        const beforeRemove = {cancelled:cancelled.slice(),oldConnected:old.render.main.main.isConnected,
            oldHooks:(e._preparers.facts || []).length,newHooks:e._preparers.other.length,
            urlOwners:_fwUrlSync._bars.map(bar=>bar.dsId)};
        e.removeDataset('other');
        return {beforeRemove,afterRemove:{cancelled,urlOwners:_fwUrlSync._bars.length,
            popup:document.querySelectorAll('.ss-content').length}};
    }""")
    assert result == {
        "beforeRemove": {"cancelled": ["facts"], "oldConnected": False, "oldHooks": 0,
                         "newHooks": 1, "urlOwners": ["other"]},
        "afterRemove": {"cancelled": ["facts", "other"], "urlOwners": 0, "popup": 0},
    }

"""Column projection, predicate compatibility and bounded index-query caching."""

from __future__ import annotations

import pytest

from trellum.assets import load_js


@pytest.fixture(scope="module")
def query_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def query_page(query_browser):
    page = query_browser.new_page()
    page.add_script_tag(content=load_js("runtime/filter_engine.js"))
    yield page
    page.close()


def test_distinct_projects_only_target_without_materializing_scoped_rows(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        const data = ['z', null, '', 'a', 'z', 0, false, '__proto__', 'constructor']
            .map(value => new Proxy([value, 'unused'], {get(row, key) {
                if (key === '1') throw Error('Read an unrelated column');
                return row[key];
            }}));
        e.initColumnar('root', {_cols:['choice','wide'], _data:data, _dict:{}});
        e.addScopedChild('child', 'root');
        e.addScopedChild('grandchild', 'child');
        e._state.child = {all:{column:'wide', mode:'equals', value:'__all__'}};
        e._state.grandchild = {empty:{column:'wide', mode:'in', value:[]}};
        for (const method of ['_materializeAll','_materializeIndices','_filterRowObjects',
                              'getFiltered','getFilteredExcluding']) {
            e[method] = () => { throw Error('Constructed or requested full rows'); };
        }
        return {values:['root','child','grandchild'].map(id => e.getDistinctValues(id,'choice')),
            missing:e.getDistinctValues('grandchild','absent'),
            materialized:!!e._columnar.root._allRows};
    }""")
    assert result == {
        "values": [["0", "__proto__", "a", "constructor", "false", "z"]] * 3,
        "missing": [],
        "materialized": False,
    }


def test_own_exclusion_retains_active_siblings_and_ancestor_same_column(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        e.init('root', [
            {group:'A', choice:'a', size:1}, {group:'A', choice:'b', size:2},
            {group:'A', choice:'c', size:3}, {group:'B', choice:'d', size:2}]);
        e.setFilter('root','parent','group','equals','A');
        e.setFilter('root','sibling','size','numrange',{min:2,max:3});
        e.setFilter('root','own','choice','in',['b']);
        const root = e.getDistinctValues('root','choice',{excludingColumns:['choice']});
        e.addScopedChild('child','root');
        e.setFilter('child','own','choice','in',['c']);
        const options = {excludingColumns:['choice']};
        const child = e.getDistinctValues('child','choice',options);
        const explicit = e.getFilteredExcluding('child',['choice']).map(r => r.choice);
        return {root, child, explicit, ordinary:e.getFiltered('child')};
    }""")
    assert result == {"root": ["b", "c"], "child": ["b"], "explicit": ["b"], "ordinary": []}


def test_filtered_nested_distinct_reads_predicates_and_target_without_full_rows(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        const col = {_cols:['group','choice','n','wide','other'],
            _dict:{group:['A','B'],choice:['p','q','r'],other:['unused']},
            _data:[[0,0,1,'wide',0],[0,1,2,'wide',0],[1,2,3,'wide',0]]};
        e.initColumnar('root',col);
        e.addScopedChild('middle','root'); e.addScopedChild('leaf','middle');
        e._state.root = {group:{column:'group',mode:'equals',value:'A'}};
        e._state.middle = {sibling:{column:'n',mode:'numrange',value:{min:2}}};
        e._state.leaf = {own:{column:'choice',mode:'in',value:['p']}};
        col._data = col._data.map(row => new Proxy(row,{get(row,key) {
            if (key === '3' || key === '4') throw Error('Read an unrelated column');
            return row[key];
        }}));
        for (const method of ['_materializeAll','_materializeIndices','_filterRowObjects',
                              'getFiltered','getFilteredExcluding']) {
            e[method] = () => { throw Error('Constructed or requested full rows'); };
        }
        const values = e.getDistinctValues('leaf','choice',{excludingColumns:['choice']});
        return {values,materialized:!!col._allRows,retained:col._queryCacheRows};
    }""")
    assert result == {"values": ["q"], "materialized": False, "retained": 1}


def test_equivalent_effective_queries_share_scans_across_scopes_and_exclusions(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        e.init('root',[{group:'A',choice:'x'}, {group:'B',choice:'y'}, {group:'C',choice:'z'}]);
        e.addScopedChild('left','root'); e.addScopedChild('right','root');
        e._state.root = {first:{column:'group',mode:'in',value:['B','A','A']},
            all:{column:'choice',mode:'equals',value:'__all__'}};
        e._state.left = {empty:{column:'choice',mode:'in',value:[]}};
        e._state.right = {inactive:{column:'unused',mode:'flag',value:'all'}};
        let scans = 0;
        const scan = e._scanIndices;
        e._scanIndices = function(...args) { scans++; return scan.apply(this,args); };
        const a = e.getDistinctValues('left','choice',{excludingColumns:['choice']});
        e._state.root = {renamed:{column:'group',mode:'in',value:['A','B']},
            duplicate:{column:'group',mode:'in',value:['B','A']}};
        const b = e.getDistinctValues('right','choice',{excludingColumns:['unused','absent']});
        const rows = e.getFiltered('root').map(r => r.choice);
        return {a,b,rows,scans,entries:e._columnar.root._queryCache.size,
            retained:e._columnar.root._queryCacheRows};
    }""")
    assert result == {"a": ["x", "y"], "b": ["x", "y"], "rows": ["x", "y"],
                      "scans": 1, "entries": 1, "retained": 2}


def test_query_cache_evicts_least_recently_used_entries(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        e.init('root',[{n:1,choice:'a'}, {n:2,choice:'b'}]);
        let scans = 0;
        const scan = e._scanIndices;
        e._scanIndices = function(...args) { scans++; return scan.apply(this,args); };
        function query(min) {
            e._state.root = {range:{column:'n',mode:'numrange',value:{min}}};
            return e.getDistinctValues('root','choice');
        }
        for (let i=0; i<16; i++) query(-i);
        query(0); query(-16);
        const beforeHit = scans;
        query(0);
        const afterHit = scans;
        query(-1);
        const col = e._columnar.root;
        return {beforeHit, afterHit, scans, entries:col._queryCache.size,
            retained:col._queryCacheRows,
            actual:Array.from(col._queryCache.values()).reduce((sum,v) => sum+(v ? v.length : 0),0)};
    }""")
    assert result == {"beforeHit": 17, "afterHit": 17, "scans": 18,
                      "entries": 16, "retained": 32, "actual": 32}


@pytest.mark.heavy
@pytest.mark.parametrize("row_count", [9000, 20000])
def test_query_cache_bounds_total_retained_positions(query_page, row_count):
    result = query_page.evaluate("""n => {
        const e = window._fwFilterEngine;
        window.requestIdleCallback = () => {};
        e.initColumnar('root',{_cols:['n','choice'],_dict:{},
            _data:Array.from({length:n},(_,i) => [i,'a'])});
        e._materializeAll = e._materializeIndices = () => { throw Error('Allocated rows'); };
        for (let i=0; i<20; i++) {
            e._state.root = {range:{column:'n',mode:'numrange',value:{min:-i}}};
            e.getDistinctValues('root','choice');
        }
        const col = e._columnar.root;
        return {entries:col._queryCache.size, retained:col._queryCacheRows,
            actual:Array.from(col._queryCache.values()).reduce((sum,v) => sum+v.length,0)};
    }""", row_count)
    capacity = max(65536, row_count * 4) // row_count
    assert result == {"entries": capacity, "retained": capacity * row_count,
                      "actual": capacity * row_count}


def test_dictionary_and_scoped_scan_keep_typed_and_null_comparison_rules(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        const values = [null,'',1,'1',true,false,0,'0',undefined,NaN];
        e.initColumnar('root',{_cols:['v','label'],_dict:{v:values},
            _data:values.map((v,i) => [i,String(i)])});
        e.addScopedChild('scope','root');
        const cases = [
            ['equals',1], ['equals','1'], ['equals',null], ['equals','null'],
            ['equals',undefined], ['equals','undefined'], ['equals',NaN], ['equals','NaN'],
            ['equals',''], ['equals',true], ['equals','true'],
            ['in',[undefined]], ['in',[null]], ['in',[null,'']], ['in',[1]], ['in',['1']],
            ['flag','only','null'], ['flag','exclude',null],
            ['numrange',{min:0,max:1}], ['numrange',{min:NaN}]
        ];
        return cases.map(([mode,value,flagValue]) => {
            const f = {column:'v',mode,value,flagValue};
            e._state.root = {test:f}; e._state.scope = {};
            const root = e.getDistinctValues('root','label');
            e._state.root = {}; e._state.scope = {test:f};
            return [root,e.getDistinctValues('scope','label')];
        });
    }""")
    all_rows = [str(i) for i in range(10)]
    non_null = ["1", "2", "3", "4", "5", "6", "7", "9"]
    assert result == [
        [["2", "3"], []], [["2", "3"], ["2", "3"]],
        [["0"], []], [["0"], []], [["8"], []], [["8"], []],
        [["9"], []], [["9"], ["9"]], [["1"], ["0", "1", "8"]],
        [["4"], []], [["4"], ["4"]], [["8"], []], [["0"], []],
        [["0", "1"], ["0", "1", "8"]], [["2", "3"], []],
        [["2", "3"], ["2", "3"]], [["0"], []], [all_rows[1:], all_rows],
        [non_null, non_null], [non_null, non_null],
    ]


def test_nested_scopes_include_all_ancestors_and_preserve_missing_column_rules(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        e.init('root',[{x:'A',choice:'apple'}, {x:'A',choice:'apricot'}, {x:'B',choice:'banana'}]);
        e.addScopedChild('middle','root'); e.addScopedChild('leaf','middle');
        e.setFilter('root','ancestor','x','equals','A');
        e.setFilter('root','missing','absent','equals','never');
        e.setFilter('middle','same-column','choice','equals','apricot');
        e.setFilter('middle','missing','absent','equals','');
        e.setFilter('leaf','own','choice','equals','apple');
        const options = {excludingColumns:['choice']};
        const before = e.getDistinctValues('leaf','choice',options);
        const explicit = e.getFilteredExcluding('leaf',['choice']).map(r => r.choice);
        e.setFilter('middle','missing','absent','equals','no');
        const narrowed = e.getDistinctValues('leaf','choice',options);
        e.clearFilter('middle','missing');
        const restored = e.getDistinctValues('leaf','choice',options);
        e.init('other',[{x:'C',choice:'pear'}]);
        e.addScopedChild('middle','other');
        const reparented = e.getDistinctValues('leaf','choice',options);
        e.clearAllFilters('middle');
        return {before,explicit,narrowed,restored,reparented,
            cleared:e.getDistinctValues('leaf','choice',options)};
    }""")
    assert result == {"before": ["apricot"], "explicit": ["apricot"], "narrowed": [],
                      "restored": ["apricot"], "reparented": [], "cleared": ["pear"]}


def test_live_normal_reads_snapshot_but_explicit_exclusion_applies_local_filters(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine;
        let changes = 0;
        const hooks = {onChange() { changes++; }};
        e.init('live',[{x:'A'}, {x:'B'}]); e.registerLiveDataset('live',hooks);
        e.setFilterTransient('live','own','x','equals','A');
        const ordinary = e.getFiltered('live').map(r => r.x);
        const explicit = e.getFilteredExcluding('live',[]).map(r => r.x);
        const excluded = e.getFilteredExcluding('live',['x']).map(r => r.x);
        const distinct = e.getDistinctValues('live','x');
        const distinctExcluded = e.getDistinctValues('live','x',{excludingColumns:['x']});
        e.addScopedChild('child','live');
        e.setFilter('child','own','x','equals','B');
        const child = e.getDistinctValues('child','x');
        const childExcluded = e.getDistinctValues('child','x',{excludingColumns:['x']});
        e.setFilter('live','own','x','equals','B');
        const committed = e.getDistinctValues('live','x');
        e.setLiveRows('live',['x'],[['C'],['D']]);
        const replaced = [e.getFiltered('live').map(r => r.x), e.getDistinctValues('live','x'),
            e.getDistinctValues('live','x',{excludingColumns:['x']})];
        e.clearFilter('live','own');
        const cleared = e.getDistinctValues('live','x');
        e.unregisterLiveDataset('live',{});
        const guarded = e.isLiveDataset('live');
        e.unregisterLiveDataset('live',hooks);
        return {ordinary,explicit,excluded,distinct,distinctExcluded,child,childExcluded,
            committed,replaced,cleared,changes,guarded,unregistered:!e.isLiveDataset('live')};
    }""")
    assert result == {
        "ordinary": ["A", "B"], "explicit": ["A"], "excluded": ["A", "B"],
        "distinct": ["A"], "distinctExcluded": ["A", "B"], "child": ["B"],
        "childExcluded": ["A", "B"], "committed": ["B"],
        "replaced": [["C", "D"], [], ["C", "D"]], "cleared": ["C", "D"],
        "changes": 2, "guarded": True, "unregistered": True,
    }


def test_reinit_remove_clear_and_lifecycle_do_not_reuse_obsolete_indices(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine, events = [];
        let disposed = 0;
        const unsubscribe = e.subscribeLifecycle(event => events.push(event.type+':'+event.dataset_id));
        const col = {_cols:['x'],_dict:{x:['A','B']},_data:[[0],[1]]};
        e.initColumnar('root',col); e.addScopedChild('child','root');
        e.setFilter('root','own','x','equals','A');
        const first = e.getDistinctValues('child','x');
        const oldCache = col._queryCache;
        col._dict = {x:['B','C']}; col._data = [[1],[0]];
        e.initColumnar('root',col);
        const reinit = e.getDistinctValues('child','x');
        const reset = oldCache !== col._queryCache;
        e.clearFilter('root','own');
        const cleared = e.getDistinctValues('child','x');
        e.setFilter('root','own','x','equals','B');
        const set = e.getDistinctValues('child','x');
        e.clearAllFilters('root');
        const clearedAll = e.getDistinctValues('child','x');
        e.registerLiveDataset('root',{onChange(){},onDispose(){disposed++;}});
        e.removeDataset('root');
        const removed = e.getDistinctValues('child','x');
        e.init('root',[{x:'D'}]);
        const recreated = e.getDistinctValues('child','x');
        e.removeDataset('child');
        const removedChild = e.getDistinctValues('child','x');
        const stats = e.getEngineStats();
        unsubscribe(); e.init('silent',[]);
        return {first,reinit,reset,cleared,set,clearedAll,removed,recreated,removedChild,
            disposed,events,stats};
    }""")
    assert result == {
        "first": ["A"], "reinit": [], "reset": True, "cleared": ["B", "C"],
        "set": ["B"], "clearedAll": ["B", "C"], "removed": [], "recreated": ["D"],
        "removedChild": [], "disposed": 1,
        "events": ["init:root", "scope:child", "init:root", "remove:root", "init:root", "remove:child"],
        "stats": {"datasetCount": 1, "totalRows": 1},
    }


@pytest.mark.heavy
def test_idle_index_build_invalidates_queries_and_stale_builds_do_nothing(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine, idle = [];
        window.requestIdleCallback = fn => idle.push(fn);
        function payload() { return {_cols:['x'],_dict:{x:[null,'']},
            _data:Array.from({length:10000},(_,i) => [i%2])}; }
        e.initColumnar('root',payload());
        e._state.root = {own:{column:'x',mode:'equals',value:''}};
        const before = e.getFilteredExcluding('root',[]).length;
        const oldCache = e._columnar.root._queryCache;
        idle.shift()();
        const reset = oldCache !== e._columnar.root._queryCache;
        const after = e.getFilteredExcluding('root',[]).length;
        e.initColumnar('root',payload());
        const staleReplacement = idle.shift();
        e.init('root',[{x:'fresh'}]); e.clearAllFilters('root');
        const snapshot = e.getFiltered('root'), cache = e._columnar.root._queryCache;
        staleReplacement();
        const unchanged = e.getFiltered('root') === snapshot && e._columnar.root._queryCache === cache;
        const reused = payload();
        e.initColumnar('gone',reused);
        const staleSameObject = idle.shift();
        e.initColumnar('gone',reused);
        staleSameObject();
        const skippedSameObject = !reused._indexes;
        const staleRemoval = idle.shift();
        e.removeDataset('gone'); staleRemoval();
        return {before,after,reset,unchanged,skippedSameObject,
            removed:!e.isReady('gone'), revived:!!reused._indexes,
            fresh:e.getDistinctValues('root','x')};
    }""")
    assert result == {"before": 10000, "after": 5000, "reset": True, "unchanged": True,
                      "skippedSameObject": True, "removed": True, "revived": False, "fresh": ["fresh"]}


def test_recursive_notifications_keep_existing_delivery_order(query_page):
    result = query_page.evaluate("""() => {
        const e = window._fwFilterEngine, seen = [];
        e.init('root',[{x:'A'},{x:'B'}]);
        let nested = false;
        e.subscribe('root','first',rows => {
            seen.push('first:'+rows.map(r => r.x).join(''));
            if (!nested) { nested = true; e.setFilter('root','own','x','equals','B'); }
        });
        e.subscribe('root','second',rows => seen.push('second:'+rows.map(r => r.x).join('')));
        e.setFilter('root','own','x','equals','A');
        return seen;
    }""")
    assert result == ["first:A", "first:B", "second:B", "second:A"]

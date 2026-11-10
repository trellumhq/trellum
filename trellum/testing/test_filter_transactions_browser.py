"""Independent real-control checks for one settled cascading delivery."""

from __future__ import annotations

import json

import pytest

from trellum.testing.test_filter_option_queries import (
    _click_option,
    _init_small,
    measure_switches,
    setup_scale,
)
from trellum.testing.test_filter_option_queries import cascade as cascade
from trellum.testing.test_filter_option_queries import cascade_browser as cascade_browser


@pytest.mark.parametrize("parent_multi", [False, True])
def test_parent_switch_delivers_only_settled_source_and_propagated_rows(cascade, parent_multi):
    page = cascade.page
    _init_small(page, parent_multi=parent_multi, propagate=True)
    page.evaluate("""async multi => {
        await choose('child',['shared','A-only']);
        if (multi) await choose('parent',[]);
        watch('facts','source',['target']); watch('target','target',['facts']);
        const replace=history.replaceState.bind(history);
        window.urlWrites=0;
        history.replaceState=(...args)=>{urlWrites++;return replace(...args);};
        controlEvents=[]; probe.reset();
    }""", parent_multi)
    requests = len(cascade.requests)
    _click_option(page, "parent", "B")
    result = page.evaluate("({snap:snapshot(),probe,urlWrites,controlEvents})")
    assert result["snap"]["selected"]["child"] == ["shared"]
    assert result["snap"]["options"]["child"] == ["B-only", "shared"]
    assert result["probe"]["deliveries"] == {"source": 1, "target": 1}
    assert result["urlWrites"] == 1
    assert [event["column"] for event in result["controlEvents"]] == ["parent"]
    for ds, subscriber in [("facts", "source"), ("target", "target")]:
        observed = result["probe"]["observed"][subscriber]
        expected = page.evaluate("""ds => sourceRows.filter(r =>
            r.parent==='B' && r.child==='shared' &&
            (ds==='target' || (r.day==='2026-11-02' && r.category==='keep'))).map(r=>r.id)
        """, ds)
        assert observed["rows"] == expected
        for state in [observed["state"], *observed["peers"].values()]:
            assert next(f["value"] for f in state.values() if f["column"] == "parent") == ["B"]
            assert next(f["value"] for f in state.values() if f["column"] == "child") == ["shared"]
    assert len(cascade.requests) == requests


def test_reversed_chain_settles_options_before_delivery_and_honors_second_parent(cascade):
    page = cascade.page
    _init_small(page, reversed_chain=True)
    page.evaluate("""async () => {
        await choose('child',['A-only']); await choose('leaf',['l-A-only']);
        watch('facts','consumer'); probe.reset(); await choose('parent',['B']);
    }""")
    result = page.evaluate("({snap:snapshot(),probe})")
    assert result["snap"]["selected"]["child"] == []
    assert result["snap"]["selected"]["leaf"] == []
    assert result["snap"]["options"]["child"] == ["B-only", "shared"]
    assert result["snap"]["options"]["leaf"] == ["l-B-only", "l-shared"]
    assert result["probe"]["deliveries"] == {"consumer": 1}
    page.evaluate("E.setFilter('facts','category','category','equals','drop')")
    assert page.evaluate("optionsFor('sibling')") == ["s-drop-B-only", "s-drop-shared"]


def test_unrelated_empty_result_keeps_selection_but_declared_parent_clear_is_atomic(cascade):
    page = cascade.page
    _init_small(page, clear=True)
    page.evaluate("""async () => {
        await choose('child',['A-only']); await choose('clear',['A-only']);
        watch('facts','consumer'); probe.reset();
        E.setFilter('facts','date','day','range',{min:'2030-01-01',max:'2030-01-02'});
        await settle();
    }""")
    assert page.evaluate("widget('child').getSelected()") == ["A-only"]
    assert page.evaluate("widget('clear').getSelected()") == ["A-only"]
    assert page.evaluate("probe.deliveries") == {"consumer": 1}
    assert page.evaluate("probe.observed.consumer.rows") == []
    page.evaluate("""async () => {
        E.clearFilter('facts','date'); await settle();
        probe.reset(); await choose('parent',['A','B']);
    }""")
    assert page.evaluate("widget('child').getSelected()") == ["A-only"]
    assert page.evaluate("widget('clear').getSelected()") == []
    assert page.evaluate("probe.deliveries") == {"consumer": 1}


def test_single_select_child_clear_keeps_widget_and_engine_consistent(cascade):
    page = cascade.page
    page.evaluate("""async () => {
        E.init('facts',[{id:0,parent:'A',child:'a'},{id:1,parent:'B',child:'b'},
            {id:2,parent:'B',child:'c'}]);
        E.setFilter('facts','bar_parent','parent','in',['A']);
        mountBar('bar','facts',[{column:'parent',multi:false},
            {column:'child',multi:false,depends_on:['parent']}]);
        await choose('child',['a']); watch('facts','consumer'); probe.reset();
        await choose('parent',['B']);
    }""")
    assert page.evaluate("widget('child').getSelected().filter(v=>v!=='')") == []
    assert page.evaluate("E.getFilterState('facts').bar_child.value") == []
    assert page.locator('select[data-filter-col="child"]').input_value() == ""
    assert page.evaluate("probe.observed.consumer.rows") == [1, 2]
    assert page.evaluate("probe.deliveries") == {"consumer": 1}


@pytest.mark.parametrize("scoped", [False, True])
def test_cascade_survives_dataset_reentry_and_retires_with_its_dom(cascade, scoped):
    page = cascade.page
    page.evaluate("""async scoped => {
        window.reentryRows = [{id:0,parent:'A',child:'a'}, {id:1,parent:'B',child:'b'}];
        E.init('base', reentryRows);
        if (scoped) E.addScopedChild('facts','base');
        else E.init('facts',reentryRows);
        mountBar('bar','facts',[{column:'parent',multi:false},
            {column:'child',depends_on:['parent']}]);
        await choose('parent',['A']); await choose('child',['a']);
        window.originalParent = widget('parent');
        window.originalChild = widget('child');
        E.removeDataset('facts');
        if (scoped) E.addScopedChild('facts','base');
        else E.init('facts',reentryRows);
    }""", scoped)
    assert page.evaluate("widget('parent')===originalParent && widget('child')===originalChild")
    _click_option(page, "parent", "B")
    assert page.evaluate("optionsFor('child')") == ["b"]
    assert page.evaluate("widget('child').getSelected()") == []
    assert page.evaluate("E.getFiltered('facts').map(r=>r.id)") == [1]
    assert page.evaluate("E._preparers.facts.length") == 1
    page.evaluate("document.getElementById('bar').remove()")
    page.wait_for_function("!E._preparers.facts.length && E._lifecycleListeners.size===0")
    assert page.evaluate("_fwUrlSync._bars.length") == 0


def test_user_selection_after_programmatic_pruning_is_not_suppressed(cascade):
    page = cascade.page
    _init_small(page, parent_multi=False)
    page.evaluate("""async () => {
        await choose('child',['A-only']);
        widget('parent').setSelected(['B']);
    }""")
    page.wait_for_function("E.getFilterState('facts').bar_parent.value[0]==='B'")
    page.evaluate("""async () => {
        widget('child').setSelected(['shared']);
        widget('parent').setSelected(['A']);
        await settle();
    }""")
    assert page.evaluate("widget('child').getSelected()") == ["shared"]
    assert page.evaluate("E.getFilterState('facts').bar_child.value") == ["shared"]
    assert page.evaluate("E.getFiltered('facts').every(r=>r.parent==='A' && r.child==='shared')")


def test_url_reload_restores_final_selection_and_preserves_page_location(cascade):
    page = cascade.page
    _init_small(page, parent_multi=False)
    page.evaluate("""async () => {
        history.replaceState(null,'','/index.html?only=chart-1#evidence');
        await choose('child',['shared','A-only']); await choose('parent',['B']);
    }""")
    assert "only=chart-1" in page.url and page.url.endswith("#evidence")
    page.reload(wait_until="load")
    page.evaluate("""async () => {
        const rows=[];
        for(const parent of ['A','B']) for(const child of ['shared',parent+'-only'])
            rows.push({id:rows.length,parent,child});
        E.initColumnar('facts',encode(rows));
        mountBar('bar','facts',[{column:'parent',multi:false},
            {column:'child',depends_on:['parent']}]);
        await settle();
    }""")
    assert page.evaluate("widget('parent').getSelected()") == ["B"]
    assert page.evaluate("widget('child').getSelected()") == ["shared"]
    assert page.evaluate("optionsFor('child')") == ["B-only", "shared"]


def test_live_parent_waits_for_new_snapshot_before_pruning_valid_child(cascade):
    page = cascade.page
    page.evaluate("""async () => {
        E.init('facts',[{id:0,parent:'A',child:'shared'}, {id:1,parent:'A',child:'A-only'}]);
        E.setFilter('facts','bar_parent','parent','in',['A']);
        mountBar('bar','facts',[{column:'parent',multi:false,options:['A','B']},
            {column:'child',depends_on:['parent'],options:['A-only','B-only','shared']}]);
        await choose('child',['shared']);
        window.liveRequests=[];
        E.registerLiveDataset('facts',{onChange(){liveRequests.push(E.getFilterState('facts'));}});
        watch('facts','consumer'); probe.reset(); await choose('parent',['B']);
    }""")
    pending = page.evaluate("({state:E.getFilterState('facts'),liveRequests,deliveries:probe.deliveries})")
    assert pending["state"]["bar_child"]["value"] == ["shared"]
    assert len(pending["liveRequests"]) == 1
    assert pending["liveRequests"][0]["bar_child"]["value"] == ["shared"]
    assert pending["deliveries"] == {}
    page.evaluate("""async () => {
        E.setLiveRows('facts',['id','parent','child'],[[2,'B','shared']]);
        await settle();
    }""")
    assert page.evaluate("widget('child').getSelected()") == ["shared"]
    assert page.evaluate("liveRequests.length") == 1
    assert page.evaluate("probe.deliveries") == {"consumer": 1}
    assert page.evaluate("probe.observed.consumer.rows") == [2]


def test_live_invalid_child_waits_for_corrected_query_before_delivery(cascade):
    page = cascade.page
    page.evaluate("""async () => {
        E.init('facts',[{id:0,parent:'A',child:'a'}]);
        E.setFilter('facts','bar_parent','parent','in',['A']);
        mountBar('bar','facts',[{column:'parent',multi:false,options:['A','B']},
            {column:'child',depends_on:['parent'],options:['a','b']}]);
        await choose('child',['a']);
        window.liveRequests=[];
        E.registerLiveDataset('facts',{onChange(){liveRequests.push(E.getFilterState('facts'));}});
        E.addScopedChild('local','facts');
        watch('local','scoped'); watch('facts','consumer'); probe.reset(); await choose('parent',['B']);
        // The server confirms that B has no rows with the old child selection.
        E.setLiveRows('facts',['id','parent','child'],[]);
        await settle();
    }""")
    assert page.evaluate("E.getFilterState('facts').bar_child.value") == []
    assert page.evaluate("liveRequests.length") == 2
    assert page.evaluate("probe.deliveries") == {}
    page.evaluate("""async () => {
        E.setLiveRows('facts',['id','parent','child'],[[1,'B','b']]); await settle();
    }""")
    assert page.evaluate("optionsFor('child')") == ["b"]
    assert page.evaluate("liveRequests.length") == 2
    assert page.evaluate("probe.deliveries") == {"consumer": 1, "scoped": 1}
    assert page.evaluate("probe.observed.consumer.rows") == [1]


@pytest.mark.heavy
def test_large_parent_switch_materializes_once_and_delivers_once(cascade, record_property):
    setup_scale(cascade.page)
    requests = len(cascade.requests)
    measurement = measure_switches(cascade.page, repetitions=3)
    record_property("cascade_measurement", json.dumps(measurement))
    for sample in measurement["samples"]:
        stats = sample["stats"]
        assert sample["rowsOK"] and sample["optionsOK"], sample
        assert stats["optionMaterializations"] == stats["optionRows"] == 0, sample
        assert stats["calls"].get("_materializeAll", 0) + stats["calls"].get("_materializeIndices", 0) <= 1, sample
        assert stats["materializedRows"] <= sample["expectedRows"], sample
        assert stats["deliveries"] == {f"consumer{i}": 1 for i in range(10)}, sample
        assert all(event["column"] == "parent" for event in sample["controlEvents"]), sample
    assert len(cascade.requests) == requests

"""Deterministic production binder regressions; native rendering is tested separately."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
let now=0, serial=0, timers=new Map(), events={}, calls=[];
const root={isConnected:true}, bar={setAttribute(){},querySelector(){return null},querySelectorAll(){return []}};
const context={console, AbortController, performance:{now:()=>now}, Date:class extends Date {
  constructor(...args){super(...(args.length?args:[now]));} static now(){return now;}
}, setTimeout:(fn,ms)=>{timers.set(++serial,{fn,at:now+(Number(ms)||0)});return serial},
clearTimeout:id=>timers.delete(id), document:{cookie:'',body:{getAttribute:()=>''},getElementById:()=>root},
fetch:(url,opts)=>new Promise((resolve,reject)=>calls.push({url,opts,resolve,reject})),
MutationObserver:class {observe(){} disconnect(){}}};
context.window=context;context._fwRenderers={};context._fwHasHost=true;context._fwLiveQueryUrl='/query';
context.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
vm.createContext(context);
for(const file of ['runtime/filter_engine.js','runtime/live_query.js','components/live_data_source.js'])
 vm.runInContext(fs.readFileSync('trellum/static/js/'+file,'utf8'),context);
const E=context._fwFilterEngine,L=context._fwLiveQuery;
let raw={_cols:['id','value'],_data:[[1,35]],_dict:{}};
context._reportData={_ds_a:raw};
const cfg={dataset_id:'a',live:{query_id:'lookup',params:[{name:'id',type:'int'}],defaults:{id:1,private:99},
 bindings:[{column:'id',param:'id',filter_type:'text'}]}};
const render=()=>context._fwRenderers.live_data_source('source',cfg);
const select=id=>E.setFilter('a','choice','id','text',String(id));
const rows=()=>JSON.parse(JSON.stringify(E.getFiltered('a')));
const state=()=>plain(L.getDatasetState('a'));
const flush=async()=>{for(let i=0;i<15;i++) await Promise.resolve()};
async function tick(ms){let end=now+ms;while(true){let next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();}now=end;await flush();}
const response=(i,body,status=200)=>calls[i].resolve({ok:status===200,status,headers:{get:()=> '2'},json:()=>Promise.resolve(body)});
const plain=value=>JSON.parse(JSON.stringify(value));
async function ready(id=2,body={columns:['id','value'],rows:[[2,120]],truncated:false}){select(id);await tick(250);response(calls.length-1,body);await flush();}
render();
'''


def run_js(source):
    result = subprocess.run(['node', '-e', HARNESS + '\n(async()=>{' + source +
                             '\n})().catch(e=>{console.error(e);process.exitCode=1})'],
                            cwd=ROOT, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_same_source_render_keeps_queried_rows():
    run_js('''await ready(); render(); assert.deepEqual(rows(),[{id:2,value:120}]);
      select(2);await tick(300);assert.equal(calls.length,1);''')


def test_retry_owns_slot_until_latest_selection_completes():
    run_js('''select(2);await tick(250);response(0,{error:'busy'},429);await flush();
      select(3);await tick(250);assert.equal(calls.length,2);
      await tick(2200);assert.equal(calls.length,2,'obsolete retry overlaps latest request');
      response(1,{columns:['id','value'],rows:[[3,80]],truncated:false});await flush();
      assert.deepEqual(rows(),[{id:3,value:80}]);''')


def test_source_replacement_rejects_delayed_old_body():
    run_js('''select(2);await tick(250);let resolveBody;
      calls[0].resolve({ok:true,status:200,json:()=>new Promise(r=>resolveBody=r)});await flush();
      context._reportData={_ds_a:{_cols:['id','value'],_data:[[1,99]],_dict:{}}};render();
      resolveBody({columns:['id','value'],rows:[[2,120]],truncated:false});await flush();
      assert.deepEqual(rows(),[{id:1,value:99}]);''')


def test_state_identity_notifications_and_selected_vs_displayed():
    run_js('''const initial=state();assert.equal(initial.origin,'snapshot');assert.equal(initial.row_count,1);
      assert.equal(initial.truncated,null);assert.equal(initial.completed_at,null);
      assert.deepEqual(initial.displayed_params,{id:1});assert.equal(initial.has_result,true);
      let seen=[];const off=L.subscribeDataset('a',s=>seen.push(s));
      const bad=L.subscribeDataset('a',()=>{throw new Error('consumer')});
      const detached=state();detached.displayed_params.id=900;assert.equal(state().displayed_params.id,1);
      select('2.5junk');assert.equal(state().phase,'pending');assert.equal(state().requested_params.id,2);
      assert.equal(state().displayed_params.id,1);assert.equal(L.getCaptureStatus().pending,true);
      const n=seen.length;select(2);assert.equal(seen.length,n);
      await tick(250);assert.deepEqual(JSON.parse(calls[0].opts.body),{query_id:'lookup',params:{id:2}});
      response(0,{columns:['id','value'],rows:[[2,0]],truncated:true});await flush();
      assert.equal(state().row_count,1);assert.equal(state().truncated,true);assert.equal(state().origin,'query');
      assert.equal(state().phase,'ready');assert.equal(state().completed_at,new Date(250).toISOString());
      assert.ok(state().revision>initial.revision);assert.deepEqual(rows(),[{id:2,value:0}]);
      select(3);await tick(250);calls[1].reject(new Error('offline'));await flush();
      assert.equal(state().phase,'error');assert.equal(state().displayed_params.id,2);assert.equal(state().requested_params.id,3);
      assert.deepEqual(rows(),[{id:2,value:0}]);assert.equal(L.getCaptureStatus().error,'offline');
      select(2);assert.equal(state().phase,'ready');assert.equal(state().error,null);await tick(500);assert.equal(calls.length,2);
      off();bad();const count=seen.length;select(4);await tick(250);assert.equal(seen.length,count);
      assert.equal(L.getDatasetState('unknown'),null);
      let unknown='none';const unknownOff=L.subscribeDataset('unknown',s=>unknown=s);assert.equal(unknown,null);unknownOff();''')


@pytest.mark.parametrize('payload', [None, {}, {'columns': [], 'rows': None},
    {'columns': ['a'], 'rows': [[]]}, {'columns': [9], 'rows': [[1]]},
    {'columns': ['a'], 'rows': [[1]], 'truncated': 'yes'}])
def test_malformed_success_retains_result_and_revision(payload):
    run_js(f'''const rev=state().revision;select(2);await tick(250);
      response(0,{json.dumps(payload)});await flush();assert.equal(state().phase,'error');
      assert.equal(state().revision,rev);assert.equal(state().displayed_params.id,1);
      assert.deepEqual(rows(),[{{id:1,value:35}}]);''')


def test_valid_empty_query_null_cells_and_snapshot_absence():
    run_js('''await ready(2,{columns:['id','value'],rows:[],truncated:false});
      assert.equal(state().phase,'ready');assert.equal(state().has_result,true);assert.equal(state().row_count,0);
      assert.equal(state().origin,'query');assert.deepEqual(rows(),[]);
      await ready(3,{columns:['id','value'],rows:[[3,null]],truncated:false});assert.deepEqual(rows(),[{id:3,value:null}]);
      events.pagehide[0]();context._reportData={_ds_a:{_cols:['id','value'],_data:[],_dict:{}}};render();
      assert.equal(state().has_result,true);assert.equal(state().origin,'snapshot');assert.equal(state().row_count,0);
      context._reportData={};render();assert.equal(state().phase,'unavailable');assert.equal(state().has_result,false);
      assert.equal(state().row_count,null);assert.equal(state().displayed_params,null);
      context._reportData={_ds_a:{_cols:['id'],_data:[[1,2]],_dict:{}}};render();assert.equal(state().has_result,false);''')


def test_coalescing_clear_back_and_stale_error_after_body():
    run_js('''select(2);await tick(200);select(3);await tick(50);assert.equal(calls.length,1);
      assert.equal(JSON.parse(calls[0].opts.body).params.id,3);
      let rejectBody;calls[0].resolve({ok:false,status:400,json:()=>new Promise((r,j)=>rejectBody=j)});await flush();
      select(4);rejectBody(new Error('old'));await flush();assert.equal(state().error,null);
      await tick(250);response(1,{columns:['id','value'],rows:[[4,45]],truncated:false});await flush();
      select(5);await tick(250);E.clearAllFilters('a');assert.equal(state().requested_params.id,1);
      response(2,{columns:['id','value'],rows:[[5,60]],truncated:false});await flush();
      assert.equal(state().displayed_params.id,4);await tick(250);assert.equal(JSON.parse(calls[3].opts.body).params.id,1);
      response(3,{columns:['id','value'],rows:[[1,35]],truncated:false});await flush();
      select(6);await tick(250);select(1);assert.equal(calls[4].opts.signal.aborted,true);
      response(4,{columns:['id','value'],rows:[[6,70]],truncated:false});await flush();await tick(500);
      assert.equal(calls.length,5);assert.equal(state().phase,'ready');assert.equal(state().displayed_params.id,1);''')


def test_first_429_one_retry_second_429_terminal_and_no_generic_retry():
    run_js('''select(2);await tick(250);response(0,{error:'busy'},429);await flush();
      assert.equal(state().phase,'pending');assert.equal(L.getCaptureStatus().pending,true);
      await tick(1999);assert.equal(calls.length,1);await tick(1);assert.equal(calls.length,2);
      response(1,{error:'busy again'},429);await flush();assert.equal(state().phase,'error');
      await tick(60000);assert.equal(calls.length,2);assert.equal(L.getCaptureStatus().pending,false);''')


def test_deadline_covers_body_parsing_late_transport_and_new_selection():
    run_js('''select(2);await tick(250);let resolveBody;
      calls[0].resolve({ok:true,status:200,json:()=>new Promise(r=>resolveBody=r)});await flush();
      await tick(29999);assert.equal(state().phase,'pending');assert.equal(calls[0].opts.signal.aborted,false);
      await tick(1);assert.equal(calls[0].opts.signal.aborted,true);assert.equal(state().phase,'error');
      assert.match(state().error,/30 seconds/);assert.equal(timers.size,0);
      select(3);await tick(250);resolveBody({columns:['id','value'],rows:[[2,20]],truncated:false});await flush();
      assert.equal(state().phase,'pending');response(1,{columns:['id','value'],rows:[[3,30]],truncated:false});await flush();
      assert.equal(state().displayed_params.id,3);assert.equal(timers.size,0);''')


def test_replacement_config_generations_and_independent_dataset_failure():
    run_js('''await ready();let old=state();render();assert.deepEqual(state(),old);
      L.registerDataset('b',{query_id:'other',defaults:{id:1},params:[{name:'id',type:'int'}],
        bindings:[{column:'id',param:'id',filter_type:'text'}]},
        {raw:{_cols:['id'],_data:[[1]],_dict:{}},report:context._reportData,owner:null});
      E.setFilter('b','bchoice','id','text','9');await tick(250);calls[1].reject(new Error('only b'));await flush();
      assert.equal(L.getDatasetState('b').phase,'error');assert.equal(state().phase,'ready');
      assert.equal(state().error,null);assert.equal(L.getCaptureStatus().error,'only b');
      cfg.live.query_id='replacement';cfg.live.defaults={id:1};render();
      assert.ok(state().generation>old.generation);assert.equal(state().origin,'snapshot');
      assert.equal(state().displayed_params.id,1);assert.equal(state().requested_params.id,2);
      await tick(250);assert.equal(JSON.parse(calls[2].opts.body).query_id,'replacement');
      response(2,{columns:['id','value'],rows:[[2,200]],truncated:false});await flush();
      assert.equal(state().displayed_params.id,2);assert.equal(L.getDatasetState('b').error,'only b');''')


def test_disposal_cancels_retry_listeners_and_bfcache_resumes_latest():
    run_js('''let seen=[];L.subscribeDataset('a',s=>seen.push(s));
      select(2);await tick(250);response(0,{error:'busy'},429);await flush();E.removeDataset('a');
      assert.equal(state(),null);assert.equal(E.isLiveDataset('a'),false);assert.equal(seen.at(-1),null);
      assert.equal(timers.size,0);await tick(4000);assert.equal(calls.length,1);render();
      const n=seen.length;select(3);await tick(250);events.pagehide[0]();
      assert.equal(calls[1].opts.signal.aborted,true);assert.equal(timers.size,0);
      assert.equal(L.getCaptureStatus().pending,false);assert.equal(state().displayed_params.id,1);
      response(1,{columns:['id','value'],rows:[[3,300]],truncated:false});await flush();
      assert.equal(state().origin,'snapshot');events.pageshow[0]();events.pageshow[0]();
      await tick(250);assert.equal(calls.length,3);response(2,{columns:['id','value'],rows:[[3,30]],truncated:false});await flush();
      assert.equal(state().displayed_params.id,3);assert.equal(seen.length,n);''')


def test_pacing_preserves_budget_and_transient_filters_do_not_commit():
    run_js('''E.setFilterTransient('a','choice','id','text','2');assert.equal(calls.length,0);
      assert.equal(state().requested_params.id,1);await tick(300);assert.equal(calls.length,0);
      for(let i=2;i<=31;i++) await ready(i,{columns:['id','value'],rows:[[i,i]],truncated:false});
      select(32);await tick(250);assert.equal(calls.length,30);assert.equal(state().phase,'pending');
      await tick(60000);assert.equal(calls.length,31);''')


def test_scope_root_reuse_retires_old_dataset_without_canceling_other_owners():
    run_js('''select(2);await tick(250);let deliveries=[];L.subscribeDataset('a',s=>deliveries.push(s));
      L.registerDataset('b',cfg.live,{raw,report:context._reportData,owner:root});
      assert.equal(state(),null);assert.equal(deliveries.at(-1),null);assert.equal(calls[0].opts.signal.aborted,true);
      assert.equal(E.isLiveDataset('a'),false);assert.equal(E.isLiveDataset('b'),true);
      response(0,{columns:['id','value'],rows:[[2,120]],truncated:false});await flush();
      assert.equal(L.getDatasetState('b').origin,'snapshot');assert.equal(L.getCaptureStatus().pending,false);
      render();assert.ok(state().generation>1);assert.equal(L.getDatasetState('b'),null);
      assert.equal(state().requested_params.id,2);await tick(250);assert.equal(calls.length,2);
      response(1,{columns:['id','value'],rows:[[2,20]],truncated:false});await flush();assert.equal(state().origin,'query');''')


def test_bfcache_does_not_retry_generic_failure():
    run_js('''select(2);await tick(250);calls[0].reject(new Error('offline'));await flush();
      events.pagehide[0]();events.pageshow[0]();await tick(60000);
      assert.equal(calls.length,1);assert.equal(state().phase,'error');''')


def test_wire_normalization_does_not_loop_on_omitted_numeric_binding():
    run_js('''cfg.live.bindings=[{column:'id',param:'id',filter_type:'slider'}];render();
      E.setFilter('a','choice','id','equals','bad');assert.deepEqual(state().requested_params,{});
      await tick(250);assert.deepEqual(JSON.parse(calls[0].opts.body).params,{});
      response(0,{columns:['id','value'],rows:[[0,null]],truncated:false});await flush();
      assert.equal(state().phase,'ready');assert.deepEqual(state().displayed_params,{});
      await tick(2000);assert.equal(calls.length,1);''')


def test_stale_headers_abort_unconsumed_body_before_new_request():
    run_js('''select(2);await tick(250);select(3);
      let parsed=false;calls[0].resolve({ok:true,status:200,json:()=>{parsed=true;return Promise.resolve({})}});await flush();
      assert.equal(parsed,false);assert.equal(calls[0].opts.signal.aborted,true);
      assert.equal(state().phase,'pending');assert.equal(state().error,null);
      await tick(250);assert.equal(calls.length,2);assert.equal(calls[1].opts.signal.aborted,false);''')


def test_transient_during_debounce_or_body_does_not_query_or_relabel_selection():
    run_js('''select(2);E.setFilterTransient('a','choice','id','text','3');await tick(250);
      assert.equal(JSON.parse(calls[0].opts.body).params.id,2);
      let resolveBody;calls[0].resolve({ok:true,status:200,json:()=>new Promise(r=>resolveBody=r)});await flush();
      E.setFilterTransient('a','choice','id','text','4');
      resolveBody({columns:['id','value'],rows:[[2,20]],truncated:false});await flush();
      assert.equal(state().displayed_params.id,2);assert.equal(state().requested_params.id,2);
      assert.equal(state().phase,'ready');await tick(2000);assert.equal(calls.length,1);
      select(4);await tick(250);assert.equal(calls.length,2);assert.equal(JSON.parse(calls[1].opts.body).params.id,4);''')


def test_capture_revision_initial_baseline_and_unknown_snapshot_defaults():
    run_js('''assert.equal(L.getCaptureStatus().revision,0);assert.equal(state().revision,0);
      const old=state().generation;events.pagehide[0]();cfg.live.defaults={private:99};render();
      assert.ok(state().generation>old);assert.equal(state().has_result,true);
      assert.equal(state().displayed_params,null);assert.equal(state().revision,1);
      context._reportData={};render();assert.equal(state().has_result,false);assert.equal(state().revision,1);
      assert.equal(L.getCaptureStatus().revision,1);''')


def test_source_replaced_while_suspended_resumes_committed_selection():
    run_js('''select(2);await tick(250);events.pagehide[0]();
      context._reportData={_ds_a:{_cols:['id','value'],_data:[[1,99]],_dict:{}}};render();
      assert.equal(state().requested_params.id,2);assert.equal(state().displayed_params.id,1);
      assert.equal(timers.size,0);events.pageshow[0]();await tick(250);assert.equal(calls.length,2);
      response(1,{columns:['id','value'],rows:[[2,20]],truncated:false});await flush();
      assert.equal(state().displayed_params.id,2);assert.equal(state().phase,'ready');''')

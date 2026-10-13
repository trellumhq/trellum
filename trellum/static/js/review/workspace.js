(function () {
  'use strict';
  var cfg = window._fwWorkspaceConfig;
  if (!cfg || !cfg.session || !cfg.analysis_slug) return;
  var analysisFrame = document.getElementById('fw-workspace-analysis');
  var sourceFrame = document.getElementById('fw-workspace-report');
  var sourceSelect = document.getElementById('source-select');
  var statusEl = document.getElementById('session-status');
  var sourceStatus = document.getElementById('source-status');
  var note = document.getElementById('chat-note');
  var selectionNote = document.getElementById('selection-note');
  var selectBox = document.getElementById('selection-box');
  var queue = [], reports = [], activeSource = null, pendingSource = null, selectedItem = null;
  var captureWaiters = Object.create(null), openRequests = Object.create(null), buildRequested = Object.create(null);
  var viewState = {analysis:Object.create(null), source:Object.create(null)};
  var requestSeq = 0, stateGeneration = 0, pendingSourceGeneration = 0;
  var pollBusy = false, sendBusy = false, ended = false, invalidSession = false;
  var stateVersion = {analysis:null, source:null}, initialSource = cfg.initial_source || '';
  var initialHandled = false, lastReplySignature = '', contentOrigin = {origin_slug:cfg.analysis_slug,origin_name:'Analysis',origin_kind:'analysis',frame_id:''}, cancelledSelections=Object.create(null);
  var noteOrigin = null, pendingSend = null, selectionDrafts = Object.create(null), captureFailures = [];
  var storageKey = 'fw-workspace:' + cfg.session + ':' + cfg.analysis_slug;
  var tabButtons = Array.prototype.slice.call(document.querySelectorAll('.fw-tabs button'));
  var userTabGeneration=0, workspaceSelecting=false, nextQueueN=1;

  function id(prefix) {
    var bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(bytes);
    else for (var i=0;i<bytes.length;i++) bytes[i]=Math.floor(Math.random()*256);
    return prefix + '-' + Array.prototype.map.call(bytes,function(b){return b.toString(16).padStart(2,'0');}).join('');
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function escText(el, text) { el.textContent = text == null ? '' : String(text); }
  function persistableItem(item) {
    var copy={}; Object.keys(item||{}).forEach(function(k){if(k.charAt(0)!=='_'&&k!=='capture_payload')copy[k]=item[k];}); return copy;
  }
  function readDraft() {
    try { var x=JSON.parse(sessionStorage.getItem(storageKey)||'{}'); queue=Array.isArray(x.queue)?x.queue:[]; nextQueueN=queue.reduce(function(n,it){return Math.max(n,Number(it.n||0)+1);},1); note.value=x.note||''; noteOrigin=x.noteOrigin||null; contentOrigin=x.contentOrigin||contentOrigin; pendingSend=x.pendingSend||null; }
    catch (_) { queue=[]; pendingSend=null; }
  }
  function saveDraft() {
    try { sessionStorage.setItem(storageKey,JSON.stringify({queue:queue.map(persistableItem),note:note.value,noteOrigin:noteOrigin,contentOrigin:contentOrigin,pendingSend:pendingSend})); } catch (_) {}
  }
  function api(path, method, body, requestId) {
    var headers={'Accept':'application/json','X-Trellum-Analysis-Token':cfg.token||''};
    if(body)headers['Content-Type']='application/json'; if(requestId)headers['X-Request-ID']=requestId;
    return fetch('/_fw/review/workspace/'+path,{method:method||'GET',headers:headers,body:body?JSON.stringify(body):undefined,cache:'no-store',credentials:'same-origin'})
      .then(function(r){return r.json().catch(function(){return {};}).then(function(json){if(!r.ok){var e=new Error(json.error||json.message||('HTTP '+r.status));e.status=r.status;throw e;}return json;});});
  }
  function frameRole(frame) { return frame===analysisFrame?'analysis':'source'; }
  function postFrame(frame,type,payload) {
    if(!frame||!frame.contentWindow||!frame.dataset.frameId)return;
    frame.contentWindow.postMessage(Object.assign({type:type,session:cfg.session,analysis_slug:cfg.analysis_slug,frame_id:frame.dataset.frameId,slug:frame.dataset.slug},payload||{}),location.origin);
  }
  function setSelect(on) {
    workspaceSelecting=!!on;
    var button=document.getElementById('global-select'); button.setAttribute('aria-pressed',on?'true':'false');
    [analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-select',{selecting:!!on});});
  }
  document.getElementById('global-select').addEventListener('click',function(e){if(ended)return;setSelect(e.currentTarget.getAttribute('aria-pressed')!=='true');});
  function activeTab() { var b=tabButtons.find(function(x){return x.getAttribute('aria-selected')==='true';});return b&&b.dataset.tab||'analysis'; }
  function activateTab(tab) {
    tabButtons.forEach(function(b){b.setAttribute('aria-selected',b.dataset.tab===tab?'true':'false');});
    document.querySelectorAll('.fw-pane').forEach(function(p){p.classList.toggle('is-active',p.dataset.pane===tab);});
    if(tab==='analysis')contentOrigin=originForFrame(analysisFrame,'analysis');
    if(tab==='reports'&&activeSource)contentOrigin=originForFrame(sourceFrame,'report');
  }
  function originForFrame(frame,kind) { return {origin_slug:frame.dataset.slug||(kind==='analysis'?cfg.analysis_slug:''),origin_name:frame.dataset.name||(kind==='analysis'?'Analysis':'Source report'),origin_kind:kind,frame_id:frame.dataset.frameId||''}; }
  function rememberFocusedFrame() {
    var frame=document.activeElement;
    if(frame===analysisFrame||frame===sourceFrame)contentOrigin=originForFrame(frame,frame===analysisFrame?'analysis':'report');
  }
  window.addEventListener('blur',rememberFocusedFrame);
  window.addEventListener('focus',rememberFocusedFrame);
  tabButtons.forEach(function(b){b.addEventListener('click',function(){if(captureFailures.some(function(f){return f.inflight;})&&b.dataset.tab!=='reports'){sourceStatus.textContent='Keep this report open until its snapshot finishes.';return;}userTabGeneration++;activateTab(b.dataset.tab);});});

  [analysisFrame,sourceFrame].forEach(function(frame){frame.addEventListener('load',function(){if(frame.dataset.frameId){postFrame(frame,'fw-workspace-init',{selecting:workspaceSelecting,items:queue.map(persistableItem),agent:statusEl.textContent,replies:[],ended:ended});var scroll=Number(frame.dataset.restoreScroll||0);if(scroll)frame.contentWindow.scrollTo(0,scroll);}});});
  window.addEventListener('message',function(event){
    if(event.origin!==location.origin||!event.data||typeof event.data!=='object')return;
    var d=event.data;
    var frame=[analysisFrame,sourceFrame].find(function(f){return f.contentWindow===event.source&&f.dataset.frameId===d.frame_id&&f.dataset.slug===d.slug;});
    if(!frame||d.session!==cfg.session||d.analysis_slug!==cfg.analysis_slug)return;
    if(d.type==='fw-workspace-focus') { contentOrigin=originForFrame(frame,frame===analysisFrame?'analysis':'report'); return; }
    if(d.type==='fw-workspace-select-mode') { setSelect(!!d.selecting); return; }
    if(d.type==='fw-workspace-capture-result') {
      var waiter=captureWaiters[d.selection_id]; if(!waiter)return; delete captureWaiters[d.selection_id];
      if(d.error)waiter.reject(new Error(d.error));else waiter.resolve(d.capture);
    } else if(d.type==='fw-workspace-queue-add') {
      queueWidgetItem(frame,d);
    } else if(d.type==='fw-workspace-queue-update') {
      var changed=queue.find(function(x){return x.request_id===d.request_id;});
      if(changed&&typeof d.prompt==='string'){changed.prompt=d.prompt;saveDraft();renderQueue();broadcastQueue();}
    } else if(d.type==='fw-workspace-queue-remove') {
      var before=queue.length;queue=queue.filter(function(x){return x.request_id!==d.request_id;});
      if(queue.length!==before){saveDraft();renderQueue();broadcastQueue();}
    } else if(d.type==='fw-workspace-send') {
      queueWidgetNote(frame,d);
    }
  });
  function rememberView(frame) {
    var role=frameRole(frame), slug=frame.dataset.slug; if(!slug||!frame.contentWindow)return;
    try {var current=new URL(frame.contentWindow.location.href);var params={};current.searchParams.forEach(function(v,k){if(!['fw_workspace','fw_session','fw_frame'].includes(k))params[k]=v;});viewState[role][slug]={params:params,scroll:frame.contentWindow.scrollY||0};}catch(_){}
  }
  function frameUrl(item,role) {
    var u=new URL(item.url,location.origin), saved=viewState[role][item.slug];
    if(saved&&saved.params)Object.keys(saved.params).forEach(function(k){u.searchParams.set(k,saved.params[k]);});
    var frameId=id('frame');u.searchParams.set('fw_workspace',cfg.analysis_slug);u.searchParams.set('fw_session',cfg.session);u.searchParams.set('fw_frame',frameId);
    return {url:u.toString(),frameId:frameId,scroll:saved&&saved.scroll||0};
  }
  function loadFrame(frame,item) {
    if(!item||!item.url)return;
    if(frame.dataset.frameId)rememberView(frame);
    var role=frameRole(frame), f=frameUrl(item,role);
    frame.dataset.frameId=f.frameId;frame.dataset.slug=item.slug;frame.dataset.name=item.name||item.slug;
    if(role==='source')frame.hidden=false;
    frame.dataset.restoreScroll=String(f.scroll);frame.src=f.url;
  }
  function renderReports(preferred) {
    var keep=preferred!==undefined?preferred:(pendingSource||sourceSelect.value||initialSource);
    sourceSelect.replaceChildren();
    var placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Choose a source report';sourceSelect.appendChild(placeholder);
    reports.forEach(function(r){var opt=document.createElement('option');opt.value=r.slug;opt.textContent=r.name||r.slug;sourceSelect.appendChild(opt);});
    sourceSelect.value=reports.some(function(r){return r.slug===keep;})?keep:'';
    document.getElementById('open-report').disabled=!sourceSelect.value||ended||invalidSession;
    updateSourceStatus();
  }
  function selectedReport() {return reports.find(function(r){return r.slug===sourceSelect.value;})||null;}
  function updateSourceStatus() {
    var r=selectedReport(), retry=document.getElementById('retry-build'), open=document.getElementById('open-report');
    retry.hidden=!r||r.build_state!=='failed';retry.disabled=ended||invalidSession;
    open.textContent=r&&r.build_state==='ready'?'Open report':'Build report';
    if(!r){escText(sourceStatus,'Choose a source report.');return;}
    var labels={missing:'Report has not been built.',stale:'Report needs a rebuild.',building:'Building report…',failed:'Build failed: '+(r.error||'Try again.'),ready:'Report is ready.'};
    escText(sourceStatus,labels[r.build_state]||'Report status unavailable.');
    open.disabled=ended||invalidSession||r.build_state==='building';
  }
  function openReadyReport(r,generationAtSelection) {
    if(!r||r.build_state!=='ready'||pendingSource!==r.slug||generationAtSelection!==pendingSourceGeneration)return;
    var unchanged=activeSource&&activeSource.slug===r.slug&&stateVersion.source===r.version&&sourceFrame.dataset.slug===r.slug;
    activeSource=r;stateVersion.source=r.version;if(!unchanged)loadFrame(sourceFrame,r);sourceFrame.hidden=false;sourceStatus.textContent='';if(activeTab()==='reports')contentOrigin=originForFrame(sourceFrame,'report');
  }
  function requestBuild(r,force,generationAtSelection) {
    if(!r||ended||invalidSession)return;
    if(!force&&r.build_state==='building')return;
    if(!force&&(openRequests[r.slug]||buildRequested[r.slug]))return;
    if(force)delete buildRequested[r.slug];
    var req=id('open');openRequests[r.slug]=req;buildRequested[r.slug]=true;sourceStatus.textContent='Starting report build…';
    api('open','POST',{session:cfg.session,analysis_slug:cfg.analysis_slug,slug:r.slug,request_id:req},req).then(function(){if(openRequests[r.slug]===req)delete openRequests[r.slug];pollState();}).catch(function(e){if(openRequests[r.slug]===req)delete openRequests[r.slug];delete buildRequested[r.slug];if(pendingSource===r.slug&&generationAtSelection===pendingSourceGeneration){sourceStatus.textContent='Could not start report build: '+e.message;document.getElementById('open-report').disabled=false;}});
  }
  function selectSource(slug,forceBuild) {
    pendingSource=slug||null;pendingSourceGeneration++;var thisGeneration=pendingSourceGeneration;
    if(!slug)return;
    var r=reports.find(function(x){return x.slug===slug;});if(!r)return;
    if(r.build_state==='ready'){openReadyReport(r,thisGeneration);return;}
    if(r.build_state==='building'){sourceStatus.textContent='Building report…';return;}
    if(r.build_state==='failed'&&!forceBuild){updateSourceStatus();return;}
    requestBuild(r,!!forceBuild,thisGeneration);
  }
  sourceSelect.addEventListener('change',function(){selectSource(sourceSelect.value,false);updateSourceStatus();});
  document.getElementById('open-report').addEventListener('click',function(){var r=selectedReport();if(!r)return;if(r.build_state==='ready'){selectSource(r.slug,false);}else selectSource(r.slug,true);});
  document.getElementById('retry-build').addEventListener('click',function(){var r=selectedReport();if(r)selectSource(r.slug,true);});

  function renderSelection() {
    if(!selectedItem)return;
    var title=selectedItem.component&&(selectedItem.component.title||selectedItem.component.kind)||selectedItem.tag||'Selection';
    escText(document.getElementById('selection-context'),(selectedItem.origin_name||selectedItem.origin_slug)+' · '+title+(selectedItem.section&&selectedItem.section.title?' · '+selectedItem.section.title:'')+(selectedItem.text?' — '+selectedItem.text.slice(0,150):''));
    document.getElementById('capture-status').replaceChildren();
  }
  document.getElementById('dismiss-selection').addEventListener('click',function(){if(selectedItem)cancelledSelections[selectedItem.selection_id]=true;selectBox.hidden=true;selectedItem=null;});
  selectionNote.addEventListener('input',function(){if(selectedItem){selectedItem.prompt=selectionNote.value;selectionDrafts[selectedItem.selection_id]=selectionNote.value;}});
  function currentFrameFor(item){return item.origin_kind==='analysis'?analysisFrame:sourceFrame;}
  function requestCapture(item) {
    return new Promise(function(resolve,reject){var f=currentFrameFor(item);if(!f.dataset.frameId||f.dataset.frameId!==item.frame_id){reject(new Error('That report view has changed. Select it again.'));return;}
      captureWaiters[item.selection_id]={resolve:resolve,reject:reject};postFrame(f,'fw-workspace-capture',{selection_id:item.selection_id});
      setTimeout(function(){if(captureWaiters[item.selection_id]){delete captureWaiters[item.selection_id];reject(new Error('The report did not return its snapshot.'));}},20000);
    });
  }
  function isCaptureTarget(item){return item.origin_kind==='report'&&item.component&&/chart|section|table|image|rawhtml|kpi/i.test(item.component.kind||'');}
  function setCaptureMessage(message,actions) {
    var status=document.getElementById('capture-status');status.replaceChildren(document.createTextNode(message+' '));
    (actions||[]).forEach(function(action){var b=document.createElement('button');b.type='button';b.className='fw-button';b.textContent=action.label;b.addEventListener('click',action.run);status.appendChild(b);});
  }
  function finishCapture(item,captureId) {
    captureFailures=captureFailures.filter(function(f){return f.item.request_id!==item.request_id;});
    if(cancelledSelections[item.selection_id]){delete cancelledSelections[item.selection_id];document.getElementById('queue-selection').disabled=false;return;}
    item.capture_id=captureId||null;delete item._capturePayload;delete item._captureRequestId;
    queue.push(item);saveDraft();renderQueue();
    if(selectedItem&&selectedItem.selection_id===item.selection_id&&selectionNote.value.trim()===item.prompt){selectedItem=null;selectionNote.value='';selectBox.hidden=true;}
    var button=document.getElementById('queue-selection');button.disabled=false;
  }
  function uploadCapture(item,capturePayload) {
    item._capturePayload=capturePayload;item._captureRequestId=item._captureRequestId||id('capture');
    var body={session:cfg.session,analysis_slug:cfg.analysis_slug,slug:item.origin_slug,capture:capturePayload,selection_id:item.selection_id,request_id:item._captureRequestId};
    return api('capture','POST',body,item._captureRequestId).then(function(res){return res.capture_id;});
  }
  function queueSelected() {
    if(!selectedItem)return;
    var item=clone(selectedItem);item.prompt=selectionNote.value.trim();item.request_id=id('item');
    if(!item.prompt){selectionNote.focus();setCaptureMessage('Add a short change request before queuing.');return;}
    var button=document.getElementById('queue-selection');button.disabled=true;
    var tabGenerationAtCapture=userTabGeneration;
    if(!isCaptureTarget(item)){finishCapture(item,null);return;}
    var sourceState=reports.find(function(r){return r.slug===item.origin_slug;});
    if(item.origin_kind==='report'&&(!sourceState||sourceState.build_state!=='ready'||sourceState.version!==stateVersion.source)){button.disabled=false;captureFailure(new Error('The selected report is rebuilding or no longer current. Wait until it is ready, then select it again.'),function(){document.getElementById('send-status').textContent='Select the refreshed report element again before retrying its snapshot.';},function(){finishCapture(item,null);});return;}
    // A child document must remain rendered while its chart snapshot is taken.
    activateTab('reports');
    setCaptureMessage('Capturing the selected report…');
    requestAnimationFrame(function(){requestAnimationFrame(function(){
      requestCapture(item).then(function(payload){return uploadCapture(item,payload);}).then(function(captureId){finishCapture(item,captureId);if(userTabGeneration===tabGenerationAtCapture&&activeTab()==='reports')activateTab('queue');}).catch(function(e){
        button.disabled=false;
        var retry=function(){var payload=item._capturePayload;var retryGeneration=userTabGeneration;var attempt=function(){var captureAttempt=payload?Promise.resolve(payload):requestCapture(item);captureAttempt.then(function(capture){return uploadCapture(item,capture);}).then(function(captureId){finishCapture(item,captureId);if(retryGeneration===userTabGeneration&&activeTab()==='reports')activateTab('queue');}).catch(function(err){captureFailure(err,retry,textOnly);});};if(!payload&&activeTab()!=='reports'){activateTab('reports');requestAnimationFrame(function(){requestAnimationFrame(attempt);});}else attempt();};
        var textOnly=function(){finishCapture(item,null);if(userTabGeneration===tabGenerationAtCapture&&activeTab()==='reports')activateTab('queue');};
        captureFailure(e,retry,textOnly);
      });
    });});
    function captureFailure(error,retry,textOnly){var existing=captureFailures.find(function(f){return f.item.request_id===item.request_id;});if(existing){existing.error=error.message;existing.retry=retry;existing.textOnly=textOnly;}else captureFailures.push({item:item,error:error.message,retry:retry,textOnly:textOnly});renderQueue();if(selectedItem&&selectedItem.selection_id===item.selection_id){if(userTabGeneration===tabGenerationAtCapture&&activeTab()==='reports')activateTab('queue');setCaptureMessage('Snapshot failed: '+error.message+' ',[{label:'Retry snapshot',run:retry},{label:'Queue text without snapshot',run:textOnly}]);}else{document.getElementById('send-status').textContent='Snapshot failed for '+item.origin_name+': '+error.message+'. The note and selection are available in the queue.';}}
  }
  document.getElementById('queue-selection').addEventListener('click',queueSelected);
  function broadcastStatus(s) {
    [analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-status',{agent:s.agent||'absent',replies:s.replies||[],ended:!!(s.ended||s.active===false)});});
  }
  function broadcastQueue() {
    [analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-queue',{items:queue.map(persistableItem)});});
  }
  function queueWidgetItem(frame, message) {
    var incoming=message.item||{}, requestId=String(incoming.request_id||'');
    if(!requestId||!/^.{16,}$/.test(requestId)||!frame.dataset.frameId||incoming.frame_id!==frame.dataset.frameId||incoming.origin_slug!==frame.dataset.slug){
      postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'This report view changed. Select the element again.'});return;
    }
    if(queue.some(function(x){return x.request_id===requestId;})){postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:true,items:queue.map(persistableItem)});return;}
    var inflight=captureFailures.find(function(x){return x.item.request_id===requestId&&x.inflight;});if(inflight){postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'Snapshot capture is still running.'});return;}
    var retryEntry=captureFailures.find(function(x){return x.item.request_id===requestId;});
    var item=clone(retryEntry?retryEntry.item:incoming), source=reports.find(function(r){return r.slug===item.origin_slug;});
    item.prompt=incoming.prompt;
    item.origin_slug=frame.dataset.slug;item.origin_name=frame.dataset.name||frame.dataset.slug;item.origin_kind=frame===analysisFrame?'analysis':'report';item.frame_id=frame.dataset.frameId;
    item.n=retryEntry?retryEntry.item.n:nextQueueN++;
    if(message.capture_required){
      activateTab('reports');
      if(item.origin_kind!=='report'||!source||source.build_state!=='ready'||!source.version){postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'This report is not ready for a snapshot. Wait for it to finish, then select it again.'});return;}
      var captureId=message.selection_id||item.selection_id||('selection-'+requestId);item.selection_id=captureId;
      var prior=captureFailures.find(function(x){return x.item.request_id===requestId;});
      var captured=prior&&prior.item._capturePayload?Promise.resolve(prior.item._capturePayload):requestCapture(item);
      if(prior)prior.inflight=true;else captureFailures.push({item:item,error:'Capture in progress',frame:frame,message:message,inflight:true});
      captured.then(function(payload){return uploadCapture(item,payload);}).then(function(idValue){
        item.capture_id=idValue||null;delete item.selection_id;delete item._capturePayload;delete item._captureRequestId;
        captureFailures=captureFailures.filter(function(x){return x.item.request_id!==requestId;});
        queue.push(item);saveDraft();renderQueue();broadcastQueue();
        postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:true,items:queue.map(persistableItem)});
      }).catch(function(error){
        var failure=captureFailures.find(function(x){return x.item.request_id===requestId;});
        if(failure){failure.error=error.message;failure.frame=frame;failure.message=message;failure.inflight=false;}
        else captureFailures.push({item:item,error:error.message,frame:frame,message:message,inflight:false});
        postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'Snapshot failed: '+error.message+'. Your note is still available; choose Queue again to retry.'});renderQueue();
      });
      return;
    }
    queue.push(item);saveDraft();renderQueue();broadcastQueue();
    postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:true,items:queue.map(persistableItem)});
  }
  function queueWidgetNote(frame, message) {
    if(ended||invalidSession)return;
    if(sendBusy){postFrame(frame,'fw-workspace-send-result',{request_id:message.request_id,ok:false,message:'A submission is already in progress. Your draft is still available.'});return;}
    // Keep chat notes out of the queue until every report snapshot is resolved.
    if(!pendingSend&&captureFailures.length){postFrame(frame,'fw-workspace-send-result',{request_id:message.request_id,ok:false,message:'Resolve report snapshots before sending.'});return;}
    // The frozen parent submission is authoritative after a timeout. Retrying
    // never appends the same plain note a second time.
    if(pendingSend){saveDraft();send(pendingSend.endSession,frame,message.request_id);return;}
    var text=String(message.note||'').trim();
    if(text){var sourceContext=message.note_origin||{}, origin=Object.assign({},sourceContext,originForFrame(frame,frame===analysisFrame?'analysis':'report'));queue.push(Object.assign({request_id:id('item'),n:nextQueueN++,prompt:text,selector:'',tag:'message',text:'',section:null,component:null,data:null,filters:null,source_built_at:null},origin,{filters:sourceContext.filters||null,source_built_at:sourceContext.source_built_at||null}));saveDraft();renderQueue();broadcastQueue();}
    send(!!message.end_session,frame,message.request_id,text,Number(message.note_revision||0));
  }
  function renderQueue() {
    var list=document.getElementById('queue-list');list.replaceChildren();
    queue.forEach(function(item,index){var li=document.createElement('li');li.className='fw-queue-item';
      var remove=document.createElement('button');remove.type='button';remove.className='fw-button';remove.textContent='Remove';remove.setAttribute('aria-label','Remove queued request');remove.disabled=sendBusy||!!pendingSend||ended||invalidSession;remove.addEventListener('click',function(){queue.splice(index,1);saveDraft();renderQueue();});
      var strong=document.createElement('strong');strong.textContent=item.origin_name+' · '+((item.component&&item.component.kind)||item.tag||'Selection')+((item.component&&item.component.title)?' “'+item.component.title+'”':'');
      var desc=document.createElement('span');desc.textContent=item.prompt;li.append(remove,strong,desc);if(item.capture_id){var ev=document.createElement('div');ev.textContent='Snapshot ready';li.appendChild(ev);}list.appendChild(li);
    });
    captureFailures.forEach(function(f){var li=document.createElement('li');li.className='fw-queue-item';var strong=document.createElement('strong');strong.textContent=f.item.origin_name+' · Snapshot needs attention';var desc=document.createElement('span');desc.textContent=f.item.prompt+' — '+f.error;li.append(strong,desc);var retry=document.createElement('button');retry.type='button';retry.className='fw-button';retry.textContent=f.inflight?'Capturing…':'Retry snapshot';retry.disabled=!!f.inflight;retry.addEventListener('click',function(){if(f.frame&&f.message)queueWidgetItem(f.frame,f.message);});li.append(retry);
      if(!f.inflight){
        var textOnly=document.createElement('button');textOnly.type='button';textOnly.className='fw-button';textOnly.textContent='Queue text without snapshot';
        textOnly.addEventListener('click',function(){
          captureFailures=captureFailures.filter(function(x){return x.item.request_id!==f.item.request_id;});
          var item=persistableItem(f.item);delete item.selection_id;item.capture_id=null;
          if(!queue.some(function(x){return x.request_id===item.request_id;}))queue.push(item);
          saveDraft();renderQueue();broadcastQueue();
          if(f.frame&&f.frame.dataset.frameId===item.frame_id)postFrame(f.frame,'fw-workspace-queue-result',{request_id:item.request_id,ok:true,items:queue.map(persistableItem)});
        });
        var discard=document.createElement('button');discard.type='button';discard.className='fw-button';discard.textContent='Discard failed selection';
        discard.addEventListener('click',function(){captureFailures=captureFailures.filter(function(x){return x.item.request_id!==f.item.request_id;});renderQueue();});
        li.append(textOnly,discard);
      }
      list.appendChild(li);});
    document.getElementById('queue-count').textContent=String(queue.length);
    document.getElementById('send').disabled=ended||invalidSession||sendBusy||(!queue.length&&!note.value.trim()&&!pendingSend);
    document.getElementById('queue-send').disabled=ended||invalidSession||(!note.value.trim());
    document.getElementById('end-session').disabled=ended||invalidSession||sendBusy;
  }
  function takeNoteOrigin() {if(noteOrigin)return noteOrigin;return clone(contentOrigin);}
  note.addEventListener('input',function(){if(note.value.trim()&&!noteOrigin)noteOrigin=clone(contentOrigin);if(!note.value.trim())noteOrigin=null;saveDraft();renderQueue();});
  function queuePlainNote() {
    var text=note.value.trim();if(!text)return;
    var origin=takeNoteOrigin();queue.push(Object.assign({request_id:id('item'),prompt:text,selector:'',tag:'message',text:'',section:null,component:null,data:null,filters:null,source_built_at:null},origin));
    if(note.value.trim()===text)note.value='';noteOrigin=null;saveDraft();renderQueue();
  }
  document.getElementById('queue-send').addEventListener('click',queuePlainNote);

  function snapshotSubmission(endSession) {
    var noteText=note.value.trim(), noteItem=null, origin;
    if(noteText&&!endSession){origin=takeNoteOrigin();noteItem=Object.assign({request_id:id('item'),prompt:noteText,selector:'',tag:'message',text:'',section:null,component:null,data:null,filters:null,source_built_at:null},origin);}
    var items=queue.map(function(x){return clone(persistableItem(x));});if(noteItem)items.push(noteItem);
    var body={session:cfg.session,analysis_slug:cfg.analysis_slug,request_id:id('feedback'),note:'',items:items.map(function(it){return {prompt:it.prompt,selector:it.selector,tag:it.tag,text:it.text,section:it.section,component:it.component,data:it.data||null,origin_slug:it.origin_slug,origin_name:it.origin_name,origin_kind:it.origin_kind,filters:it.filters||null,source_built_at:it.source_built_at||null,capture_id:it.capture_id||null,frame_id:it.frame_id||null};}),end_session:!!endSession};
    return {requestId:body.request_id,body:body,itemIds:queue.map(function(x){return x.request_id;}),itemSnapshot:queue.map(function(x){return clone(persistableItem(x));}),noteText:noteText,endSession:!!endSession};
  }
  function send(endSession, childFrame, childRequestId, childNoteText, childNoteRevision) {
    if(sendBusy||ended||invalidSession)return;
    if(captureFailures.some(function(f){return f.inflight;})){document.getElementById('send-status').textContent='Waiting for report snapshots before sending.';if(childFrame&&childRequestId)postFrame(childFrame,'fw-workspace-send-result',{request_id:childRequestId,ok:false,message:'Wait for report snapshots to finish before sending.'});return;}
    if(captureFailures.length){document.getElementById('send-status').textContent='Resolve failed report snapshots from the workspace queue before sending.';if(childFrame&&childRequestId)postFrame(childFrame,'fw-workspace-send-result',{request_id:childRequestId,ok:false,message:'Resolve the failed report snapshot before sending.'});return;}
    if(pendingSend&&pendingSend.endSession!==!!endSession){document.getElementById('send-status').textContent='Retry the unconfirmed submission before starting another action.';return;}
    if(!pendingSend){if(!endSession&&!queue.length&&!note.value.trim())return;pendingSend=snapshotSubmission(endSession);if(childFrame&&childRequestId){pendingSend.childFrameId=childFrame.dataset.frameId;pendingSend.childRequestId=childRequestId;pendingSend.childNoteOrigin=childFrame.dataset.slug;pendingSend.childNoteText=childNoteText||'';pendingSend.childNoteRevision=Number(childNoteRevision||0);}saveDraft();}
    var pending=clone(pendingSend);var replyFrame=childFrame||[analysisFrame,sourceFrame].find(function(f){return f.dataset.frameId===pending.childFrameId;});var replyRequestId=childRequestId||pending.childRequestId;sendBusy=true;renderQueue();document.getElementById('send-status').textContent='Sending…';
    api('feedback','POST',pending.body,pending.requestId).then(function(){
      var submitted=pending.itemSnapshot||[], submittedById=Object.create(null);submitted.forEach(function(x){submittedById[x.request_id]=JSON.stringify(x);});
      queue=queue.filter(function(x){return !submittedById[x.request_id]||JSON.stringify(persistableItem(x))!==submittedById[x.request_id];});
      if(!pending.endSession&&pending.noteText&&note.value.trim()===pending.noteText){note.value='';noteOrigin=null;}
      pendingSend=null;saveDraft();renderQueue();broadcastQueue();document.getElementById('send-status').textContent=pending.endSession?'Session ended.':'Sent to the analysis session.';
      if(pending.endSession){ended=true;setEnded();}if(replyFrame&&replyRequestId){postFrame(replyFrame,'fw-workspace-send-result',{request_id:replyRequestId,ok:true,ended:!!pending.endSession,note_origin:pending.childNoteOrigin,note_text:pending.childNoteText,note_revision:pending.childNoteRevision,items:queue.map(persistableItem),message:'Feedback sent to the agent.'});}pollState();
    }).catch(function(e){document.getElementById('send-status').textContent='Send not confirmed: '+e.message+'. Retry uses the same submission ID and content.';if(replyFrame&&replyRequestId){postFrame(replyFrame,'fw-workspace-send-result',{request_id:replyRequestId,ok:false,note_origin:pending.childNoteOrigin,note_text:pending.childNoteText,note_revision:pending.childNoteRevision,message:'Send not confirmed. Retry uses the same submission ID and content.'});}})
      .finally(function(){sendBusy=false;saveDraft();renderQueue();});
  }
  document.getElementById('send').addEventListener('click',function(){send(false);});
  document.getElementById('end-session').addEventListener('click',function(){send(true);});
  function disableForInvalidSession() {
    invalidSession=true;statusEl.textContent='Session changed';setSelect(false);
    [sourceSelect,document.getElementById('open-report'),document.getElementById('retry-build'),document.getElementById('global-select'),document.getElementById('send'),document.getElementById('queue-send'),document.getElementById('end-session'),document.getElementById('queue-selection'),document.getElementById('dismiss-selection'),note,selectionNote].forEach(function(x){if(x)x.disabled=true;});renderQueue();
  }
  function setEnded(){statusEl.textContent='Session ended';ended=true;[analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-status',{agent:'absent',replies:[],ended:true});});setSelect(false);sourceSelect.disabled=true;document.getElementById('open-report').disabled=true;document.getElementById('retry-build').disabled=true;document.getElementById('global-select').disabled=true;document.getElementById('queue-selection').disabled=true;document.getElementById('dismiss-selection').disabled=true;note.disabled=true;selectionNote.disabled=true;renderQueue();}
  function renderReplies(s) {
    var replies=Array.isArray(s.replies)?s.replies:[];var signature=JSON.stringify(replies);if(signature===lastReplySignature)return;lastReplySignature=signature;
    var list=document.getElementById('reply-list');list.replaceChildren();replies.forEach(function(r){var d=document.createElement('div');d.className='fw-reply';var t=document.createElement('time');t.textContent=r.created_at||r.time||'Agent reply';var body=document.createElement('div');body.textContent=r.text||r.message||'';d.append(t,body);list.appendChild(d);});
  }
  function agentStatus(s) {
    var agent=s.agent||{}, state=typeof agent==='string'?agent:agent.state||'';
    var age=Number((typeof agent==='object'&&agent.seen_ms_ago)||s.seen_ms_ago||0);
    var queued=Array.isArray(s.queued)?s.queued.length:(s.queued&&typeof s.queued==='object'?Object.keys(s.queued).length:Number(s.queued||s.queue_count||0));
    if(age>90000)return 'Waiting for agent';
    if(s.applying||state==='applying')return 'Applying';
    if(s.working||state==='working')return 'Working';
    if(state==='listening')return 'Listening';
    if(state==='absent'||!state)return 'Waiting for agent';
    if(queued)return 'Queued';
    return 'Session active';
  }
  function applyState(s) {
    if(!s)return false;
    var a=s.analysis||{};
    if(s.session!==cfg.session){disableForInvalidSession();return false;}
    if(s.ended||s.active===false){renderReplies(s);setEnded();return true;}
    if(a.slug!==cfg.analysis_slug){disableForInvalidSession();return false;}
    reports=Array.isArray(s.reports)?s.reports:[];
    escText(document.getElementById('analysis-name'),a.name||a.slug||'Analysis workspace');renderReports(pendingSource||sourceSelect.value||initialSource);
    if(s.ended||s.active===false){setEnded();}else if(!ended)statusEl.textContent=agentStatus(s);
    renderReplies(s);broadcastStatus(s);
    if(a.url&&(stateVersion.analysis!==a.version||!analysisFrame.src)){stateVersion.analysis=a.version;loadFrame(analysisFrame,{slug:a.slug,name:a.name||'Analysis',url:a.url});}
    if(!initialHandled&&initialSource){initialHandled=true;if(reports.some(function(r){return r.slug===initialSource;})){sourceSelect.value=initialSource;selectSource(initialSource,false);}}
    var active=activeSource&&reports.find(function(r){return r.slug===activeSource.slug;});
    if(active){activeSource=active;if(active.build_state==='ready'&&active.version!==stateVersion.source){stateVersion.source=active.version;loadFrame(sourceFrame,active);}}
    if(pendingSource){var chosen=reports.find(function(r){return r.slug===pendingSource;});if(chosen){if(chosen.build_state==='ready'||chosen.build_state==='failed')delete buildRequested[chosen.slug];updateSourceStatus();if(chosen.build_state==='ready')openReadyReport(chosen,pendingSourceGeneration);else if(chosen.build_state==='failed'){sourceStatus.textContent='Build failed: '+(chosen.error||'Use Retry build to try again.');}else if(chosen.build_state==='missing'||chosen.build_state==='stale'){requestBuild(chosen,false,pendingSourceGeneration);}else sourceStatus.textContent='Building report…';}}
    return true;
  }
  function pollState() {
    if(pollBusy||invalidSession)return;pollBusy=true;var g=++stateGeneration;
    api('state','GET').then(function(s){if(g!==stateGeneration)return;applyState(s);}).catch(function(e){if(g===stateGeneration){if([404,409,410].indexOf(e.status)>=0||/session/i.test(e.message))disableForInvalidSession();else statusEl.textContent='Connection issue: '+e.message;}}).finally(function(){if(g===stateGeneration)pollBusy=false;else pollBusy=false;});
  }
  readDraft();
  if(pendingSend)document.getElementById('send-status').textContent='A previous submission is unconfirmed. Queue & send retries the same request.';
  // The first pane must be present on mobile before the first tab interaction.
  activateTab('analysis');renderQueue();pollState();window.setInterval(pollState,2500);
})();

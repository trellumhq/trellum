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
  var queue = [], reports = [], activeSource = null, pendingSource = null;
  var captureWaiters = Object.create(null), openRequests = Object.create(null), buildRequested = Object.create(null);
  var viewState = {analysis:Object.create(null), source:Object.create(null)};
  var requestSeq = 0, stateGeneration = 0, pendingSourceGeneration = 0;
  var pollBusy = false, sendBusy = false, ended = false, invalidSession = false;
  var stateVersion = {analysis:null, source:null}, initialSource = cfg.initial_source || '';
  var initialHandled = false, lastReplySignature = '', contentOrigin = {origin_slug:cfg.analysis_slug,origin_name:'Analysis',origin_kind:'analysis',frame_id:''};
  var noteOrigin = null, pendingSend = null, captureFailures = [];
  var storageKey = 'fw-workspace:' + cfg.session + ':' + cfg.analysis_slug;
  var tabButtons = Array.prototype.slice.call(document.querySelectorAll('.fw-tabs button'));
  var workspaceSelecting=false, nextQueueN=1, currentTab='analysis', cleanView=false;
  var queueDialog=document.getElementById('queue-dialog'), queueToggle=document.getElementById('queue-toggle');
  var notice=document.getElementById('workspace-status'), noticeTimer, cleanHintTimer, cleanRevealTimer;
  var queueOpener=null, restoringHistory=false, cleanEntry=null, reportOptionsSignature='';


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
  function activeFrame() { return currentTab==='reports'?sourceFrame:analysisFrame; }
  function captureInFlight() { return captureFailures.some(function(f){return f.inflight;}); }
  function announce(message) {
    clearTimeout(noticeTimer);notice.hidden=false;notice.textContent=message;
    noticeTimer=setTimeout(function(){notice.hidden=true;},6000);
  }
  function canChangeView() {
    if(!captureInFlight())return true;
    announce('Keep this report open until its snapshot finishes.');return false;
  }
  function updateControls() {
    var frame=activeFrame(), ready=frame.dataset.ready==='true'&&!frame.hidden;
    var source=selectedReport();
    if(currentTab==='reports')ready=ready&&!!source&&source.build_state==='ready'&&source.slug===frame.dataset.slug&&source.version===stateVersion.source;
    document.getElementById('global-select').disabled=ended||invalidSession||!ready||cleanView||captureInFlight();
    document.getElementById('content-only').disabled=!frame.dataset.ready||frame.hidden||captureInFlight();
    document.getElementById('content-only').textContent=currentTab==='reports'?'Report only':'Analysis only';
    sourceSelect.disabled=ended||invalidSession||captureInFlight();
  }
  function setSelect(on) {
    workspaceSelecting=!!on;
    var button=document.getElementById('global-select');button.setAttribute('aria-pressed',on?'true':'false');
    button.textContent=on?'Cancel selection':'Select element';
    [analysisFrame,sourceFrame].forEach(function(f){postFrame(f,'fw-workspace-select',{selecting:!!on&&f===activeFrame()});});
  }
  document.getElementById('global-select').addEventListener('click',function(){if(ended||invalidSession)return;setSelect(!workspaceSelecting);});
  function activeTab() { return currentTab; }
  function syncFrameViews(restoreFrame) {
    [analysisFrame,sourceFrame].forEach(function(frame){
      var saved=viewState[frameRole(frame)][frame.dataset.slug];
      var view={active:frame===activeFrame(),clean:cleanView};
      if(frame===restoreFrame)view.scroll=saved?saved.scroll:Number(frame.dataset.restoreScroll||0);
      postFrame(frame,'fw-workspace-view',view);
    });
    updateControls();
  }
  function updateHistory(replace) {
    if(restoringHistory)return;
    var url=new URL(location.href);url.searchParams.set('page',currentTab);
    if(pendingSource)url.searchParams.set('source',pendingSource);else url.searchParams.delete('source');
    var method=replace?'replaceState':'pushState';
    if(replace||url.href!==location.href)history[method](history.state,'',url.href);
  }
  function activateTab(tab,recordHistory) {
    if(tab==='queue'){openQueue();return;}
    if(tab!=='analysis'&&tab!=='reports')return;
    var changed=currentTab!==tab;
    if(changed){rememberView(activeFrame());setSelect(false);}
    currentTab=tab;
    tabButtons.forEach(function(b){if(b.dataset.tab===tab)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    document.querySelectorAll('.fw-pane').forEach(function(p){p.hidden=p.dataset.pane!==tab;p.classList.toggle('is-active',p.dataset.pane===tab);});
    document.getElementById('source-controls').hidden=tab!=='reports';
    contentOrigin=originForFrame(activeFrame(),tab==='reports'?'report':'analysis');
    updateNoteContext();syncFrameViews(changed?activeFrame():null);
    if(recordHistory)updateHistory(false);
  }
  function originForFrame(frame,kind) {
    var origin={origin_slug:frame.dataset.slug||(kind==='analysis'?cfg.analysis_slug:''),origin_name:frame.dataset.name||(kind==='analysis'?'Analysis':'Source report'),origin_kind:kind,frame_id:frame.dataset.frameId||''};
    try {
      var win=frame.contentWindow, engine=win.fw&&win.fw.filterEngine, filters={};
      if(engine&&engine.getDatasetIds)engine.getDatasetIds().forEach(function(ds){filters[ds]=engine.getFilterState(ds);});
      origin.filters=filters;origin.source_built_at=win._reportData&&win._reportData._freshness&&win._reportData._freshness.generated_at||null;
    }catch(_){}
    return origin;
  }
  function updateNoteContext() {
    var origin=noteOrigin||contentOrigin;
    document.getElementById('note-context').textContent='Context: '+(origin.origin_name||origin.origin_slug||'Analysis');
  }
  function openQueue(focusNote) {
    if(queueDialog.open)return;
    queueOpener=document.activeElement;
    contentOrigin=originForFrame(activeFrame(),currentTab==='reports'?'report':'analysis');
    updateNoteContext();queueDialog.showModal();queueToggle.setAttribute('aria-expanded','true');
    if(focusNote)note.focus();
  }
  function closeQueue() { queueDialog.close(); }
  queueToggle.addEventListener('click',function(){openQueue();});
  document.getElementById('queue-close').addEventListener('click',closeQueue);
  queueDialog.addEventListener('close',function(){
    queueToggle.setAttribute('aria-expanded','false');
    var opener=queueOpener&&queueOpener.isConnected?queueOpener:queueToggle;
    if(opener===analysisFrame||opener===sourceFrame)opener=queueToggle;
    opener.focus({preventScroll:true});
  });
  queueDialog.addEventListener('keydown',function(e){
    if(e.key!=='Tab')return;
    var controls=Array.prototype.filter.call(queueDialog.querySelectorAll('button,textarea,select,input,[tabindex]'),function(el){return !el.disabled&&el.tabIndex>=0&&el.getClientRects().length;});
    var first=controls[0],last=controls[controls.length-1];
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
  });
  queueDialog.addEventListener('click',function(e){
    var box=queueDialog.getBoundingClientRect();
    if(e.target===queueDialog&&(e.clientX<box.left||e.clientX>box.right||e.clientY<box.top||e.clientY>box.bottom))closeQueue();
  });
  function revealCleanExit() {
    clearTimeout(cleanRevealTimer);document.body.classList.add('fw-clean-reveal');
    cleanRevealTimer=setTimeout(function(){document.body.classList.remove('fw-clean-reveal');},2400);
  }
  function setClean(on) {
    if(on&&!canChangeView())return;
    if(on){rememberView(activeFrame());cleanEntry={frame:activeFrame(),slug:activeFrame().dataset.slug,view:clone(viewState[frameRole(activeFrame())][activeFrame().dataset.slug]||{scroll:0,params:{}})};}
    else if(cleanEntry&&cleanEntry.frame.dataset.slug===cleanEntry.slug)viewState[frameRole(cleanEntry.frame)][cleanEntry.slug]=cleanEntry.view;
    cleanView=!!on;
    if(queueDialog.open)closeQueue();
    document.body.classList.toggle('fw-clean',cleanView);
    document.getElementById('exit-content-only').hidden=!cleanView;
    clearTimeout(cleanHintTimer);document.getElementById('content-only-hint').hidden=!cleanView;
    if(cleanView){
      setSelect(false);revealCleanExit();
      cleanHintTimer=setTimeout(function(){document.getElementById('content-only-hint').hidden=true;},3000);
      activeFrame().focus();
    }else document.getElementById('content-only').focus({preventScroll:true});
    syncFrameViews(activeFrame());
    if(!on)cleanEntry=null;
  }
  document.getElementById('content-only').addEventListener('click',function(){setClean(true);});
  document.getElementById('exit-content-only').addEventListener('click',function(){setClean(false);});
  document.addEventListener('pointermove',function(e){if(cleanView&&e.clientY<60)revealCleanExit();});
  document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!queueDialog.open){if(cleanView)setClean(false);else setSelect(false);}});
  tabButtons.forEach(function(b){b.addEventListener('click',function(){if(canChangeView())activateTab(b.dataset.tab,true);});});
  window.addEventListener('popstate',function(){
    if(!canChangeView()){updateHistory(true);return;}
    var url=new URL(location.href), slug=url.searchParams.get('source')||'';
    restoringHistory=true;
    if(cleanView)setClean(false);
    if(!slug||reports.some(function(r){return r.slug===slug;})){sourceSelect.value=slug;selectSource(slug,false);updateSourceStatus();}
    activateTab(url.searchParams.get('page')==='reports'?'reports':'analysis');restoringHistory=false;
  });
  function initFrame(frame) {
    postFrame(frame,'fw-workspace-init',{selecting:workspaceSelecting&&frame===activeFrame(),items:queue.map(persistableItem),agent:statusEl.textContent,replies:[],ended:ended});
    syncFrameViews(frame);
  }
  [analysisFrame,sourceFrame].forEach(function(frame){frame.addEventListener('load',function(){if(frame.dataset.frameId)initFrame(frame);});});
  window.addEventListener('message',function(event){
    if(event.origin!==location.origin||!event.data||typeof event.data!=='object')return;
    var d=event.data;
    var frame=[analysisFrame,sourceFrame].find(function(f){return f.contentWindow===event.source&&f.dataset.frameId===d.frame_id&&f.dataset.slug===d.slug;});
    if(!frame||d.session!==cfg.session||d.analysis_slug!==cfg.analysis_slug)return;
    if(d.type==='fw-workspace-ready') { delete frame.dataset.loadError;frame.dataset.ready='true';initFrame(frame);return; }
    if(d.type==='fw-workspace-load-error') {
      delete frame.dataset.ready;frame.dataset.loadError=String(d.message||'The report could not finish loading.');
      if(frame===sourceFrame)updateSourceStatus();else announce('Analysis could not load. Reload the workspace to try again.');
      updateControls();return;
    }
    if(d.type==='fw-workspace-open-queue') { if(frame===activeFrame())openQueue(!!d.focus_note);return; }
    if(d.type==='fw-workspace-exit-clean') { if(frame===activeFrame()&&cleanView)setClean(false);return; }
    if(d.type==='fw-workspace-focus') { if(frame===activeFrame())contentOrigin=originForFrame(frame,frame===analysisFrame?'analysis':'report');return; }
    if(d.type==='fw-workspace-select-mode') { if(frame===activeFrame()&&!cleanView)setSelect(!!d.selecting);return; }
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
    var role=frameRole(frame), slug=frame.dataset.slug; if(!slug||!frame.contentWindow||frame.dataset.ready!=='true'||frame.parentElement.hidden)return;
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
    frame.dataset.restoreScroll=String(f.scroll);delete frame.dataset.ready;delete frame.dataset.loadError;
    // Replacing a source must not add a second, iframe-only history entry.
    if(frame.getAttribute('src'))frame.contentWindow.location.replace(f.url);else frame.src=f.url;
    updateControls();
  }
  function renderReports(preferred) {
    var keep=preferred!==undefined?preferred:(pendingSource||sourceSelect.value||initialSource);
    var signature=JSON.stringify(reports.map(function(r){return [r.slug,r.name];}));
    if(signature!==reportOptionsSignature){
      sourceSelect.replaceChildren();
      var placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Choose a source report';sourceSelect.appendChild(placeholder);
      reports.forEach(function(r){var opt=document.createElement('option');opt.value=r.slug;opt.textContent=r.name||r.slug;sourceSelect.appendChild(opt);});
      reportOptionsSignature=signature;
    }
    sourceSelect.value=reports.some(function(r){return r.slug===keep;})?keep:'';
    document.getElementById('open-report').disabled=!sourceSelect.value||ended||invalidSession;
    updateSourceStatus();
  }
  function selectedReport() {return reports.find(function(r){return r.slug===sourceSelect.value;})||null;}
  function updateSourceStatus() {
    var r=selectedReport(), retry=document.getElementById('retry-build'), open=document.getElementById('open-report');
    retry.hidden=!r||r.build_state!=='failed';retry.disabled=ended||invalidSession||captureInFlight();open.hidden=false;
    open.textContent=!r?'Open report':r.build_state==='ready'?(sourceFrame.dataset.loadError?'Reload report':'Open report'):r.build_state==='stale'?'Rebuild report':'Build report';
    if(!r){escText(sourceStatus,reports.length?'Choose a source report.':'No source reports are available in this project.');open.disabled=true;updateControls();return;}
    var hasPreview=sourceFrame.dataset.slug===r.slug&&sourceFrame.dataset.ready==='true';
    var otherPreview=sourceFrame.dataset.ready==='true'&&sourceFrame.dataset.slug!==r.slug;
    var previousLabel=otherPreview?' Showing the last opened report: '+(sourceFrame.dataset.name||sourceFrame.dataset.slug)+'.':'';
    var labels={missing:'Report has not been built locally.'+previousLabel,stale:hasPreview?'Showing the last built report. Rebuild to update it.':'Report is out of date and needs a rebuild.'+previousLabel,building:'Building report…',failed:'Build failed: '+(r.error||'Try again.')+previousLabel,ready:'Report is ready.'};
    escText(sourceStatus,r.build_state==='ready'&&sourceFrame.dataset.slug===r.slug?(sourceFrame.dataset.loadError||''):labels[r.build_state]||'Report status unavailable.');
    open.disabled=ended||invalidSession||r.build_state==='building'||captureInFlight();
    open.hidden=r.build_state==='failed'||(r.build_state==='ready'&&sourceFrame.dataset.slug===r.slug&&!sourceFrame.dataset.loadError);updateControls();
  }
  function openReadyReport(r,generationAtSelection) {
    if(!r||r.build_state!=='ready'||pendingSource!==r.slug||generationAtSelection!==pendingSourceGeneration)return;
    var unchanged=activeSource&&activeSource.slug===r.slug&&stateVersion.source===r.version&&sourceFrame.dataset.slug===r.slug;
    if(!unchanged&&captureInFlight())return;
    activeSource=r;stateVersion.source=r.version;if(!unchanged)loadFrame(sourceFrame,r);sourceFrame.hidden=false;sourceStatus.textContent=sourceFrame.dataset.loadError||'';if(activeTab()==='reports')contentOrigin=originForFrame(sourceFrame,'report');updateControls();
  }
  function requestBuild(r,generationAtSelection) {
    if(!r||ended||invalidSession)return;
    if(r.build_state==='building')return;
    if(openRequests[r.slug]||buildRequested[r.slug])return;
    var req=id('open');openRequests[r.slug]=req;buildRequested[r.slug]=true;sourceStatus.textContent='Starting report build…';
    api('open','POST',{session:cfg.session,analysis_slug:cfg.analysis_slug,slug:r.slug,request_id:req},req).then(function(){if(openRequests[r.slug]===req)delete openRequests[r.slug];pollState();}).catch(function(e){if(openRequests[r.slug]===req)delete openRequests[r.slug];delete buildRequested[r.slug];if(pendingSource===r.slug&&generationAtSelection===pendingSourceGeneration){sourceStatus.textContent='Could not start report build: '+e.message;document.getElementById('open-report').disabled=false;}});
  }
  function selectSource(slug) {
    if(!canChangeView()){sourceSelect.value=pendingSource||'';return;}
    pendingSource=slug||null;pendingSourceGeneration++;var thisGeneration=pendingSourceGeneration;
    setSelect(false);updateHistory(false);
    if(!slug){rememberView(sourceFrame);sourceFrame.hidden=true;activeSource=null;updateControls();return;}
    var r=reports.find(function(x){return x.slug===slug;});if(!r)return;
    if(r.build_state==='ready'){openReadyReport(r,thisGeneration);return;}
    updateSourceStatus();
  }
  sourceSelect.addEventListener('change',function(){selectSource(sourceSelect.value);updateSourceStatus();});
  document.getElementById('open-report').addEventListener('click',function(){var r=selectedReport();if(!r)return;if(!canChangeView())return;if(r.build_state==='ready'){if(sourceFrame.dataset.loadError)loadFrame(sourceFrame,r);else selectSource(r.slug);}else if(r.build_state==='missing'||r.build_state==='stale')requestBuild(r,pendingSourceGeneration);});
  document.getElementById('retry-build').addEventListener('click',function(){var r=selectedReport();if(r&&canChangeView())requestBuild(r,pendingSourceGeneration);});

  function currentFrameFor(item){return item.origin_kind==='analysis'?analysisFrame:sourceFrame;}
  function requestCapture(item) {
    return new Promise(function(resolve,reject){var f=currentFrameFor(item);if(!f.dataset.frameId||f.dataset.frameId!==item.frame_id){reject(new Error('That report view has changed. Select it again.'));return;}
      captureWaiters[item.selection_id]={resolve:resolve,reject:reject};postFrame(f,'fw-workspace-capture',{selection_id:item.selection_id});
      setTimeout(function(){if(captureWaiters[item.selection_id]){delete captureWaiters[item.selection_id];reject(new Error('The report did not return its snapshot.'));}},20000);
    });
  }
  function uploadCapture(item,capturePayload) {
    item._capturePayload=capturePayload;item._captureRequestId=item._captureRequestId||id('capture');
    var body={session:cfg.session,analysis_slug:cfg.analysis_slug,slug:item.origin_slug,capture:capturePayload,selection_id:item.selection_id,request_id:item._captureRequestId};
    return api('capture','POST',body,item._captureRequestId).then(function(res){return res.capture_id;});
  }
  function broadcastStatus(s) {
    [analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-status',{agent:s.agent||'absent',replies:s.replies||[],ended:!!(s.ended||s.active===false)});});
  }
  function broadcastQueue() {
    [analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-queue',{items:queue.map(persistableItem)});});
  }
  function queueWidgetItem(frame, message) {
    if(ended||invalidSession)return;
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
      if(cleanView||frame!==activeFrame()){postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'Open the source report before capturing this selection.'});return;}
      if(item.origin_kind!=='report'||!source||source.build_state!=='ready'||!source.version||source.version!==stateVersion.source){postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'This report is not ready for a snapshot. Wait for it to finish, then select it again.'});return;}
      var captureId=message.selection_id||item.selection_id||('selection-'+requestId);item.selection_id=captureId;
      var prior=captureFailures.find(function(x){return x.item.request_id===requestId;});
      var captured=prior&&prior.item._capturePayload?Promise.resolve(prior.item._capturePayload):requestCapture(item);
      if(prior)prior.inflight=true;else captureFailures.push({item:item,error:'Capture in progress',frame:frame,message:message,inflight:true});
      renderQueue();
      captured.then(function(payload){return uploadCapture(item,payload);}).then(function(idValue){
        item.capture_id=idValue||null;delete item.selection_id;delete item._capturePayload;delete item._captureRequestId;
        captureFailures=captureFailures.filter(function(x){return x.item.request_id!==requestId;});
        queue.push(item);saveDraft();renderQueue();broadcastQueue();announce('Added to queue.');
        postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:true,items:queue.map(persistableItem)});
      }).catch(function(error){
        var failure=captureFailures.find(function(x){return x.item.request_id===requestId;});
        if(failure){failure.error=error.message;failure.frame=frame;failure.message=message;failure.inflight=false;}
        else captureFailures.push({item:item,error:error.message,frame:frame,message:message,inflight:false});
        postFrame(frame,'fw-workspace-queue-result',{request_id:requestId,ok:false,message:'Snapshot failed: '+error.message+'. Your note is still available; choose Queue again to retry.'});renderQueue();announce('Snapshot failed. Your note is retained; open Queue to retry.');
      });
      return;
    }
    queue.push(item);saveDraft();renderQueue();broadcastQueue();announce('Added to queue.');
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
    var list=document.getElementById('queue-list'), focused=document.activeElement;
    var focusId=focused&&focused.dataset&&focused.dataset.fwRequestId, selectionStart=focusId?focused.selectionStart:null, selectionEnd=focusId?focused.selectionEnd:null;
    list.replaceChildren();
    queue.forEach(function(item,index){
      var li=document.createElement('li');li.className='fw-queue-item';
      var strong=document.createElement('strong');strong.id='queued-label-'+index;
      strong.textContent=(item.origin_name||item.origin_slug)+' · '+((item.component&&item.component.kind)||item.tag||'Selection')+((item.component&&item.component.title)?' “'+item.component.title+'”':'');
      var edit=document.createElement('textarea');edit.value=item.prompt;edit.rows=2;edit.dataset.fwRequestId=item.request_id;
      edit.setAttribute('aria-labelledby',strong.id);edit.disabled=sendBusy||!!pendingSend||ended||invalidSession;
      edit.addEventListener('input',function(){item.prompt=edit.value;saveDraft();broadcastQueue();});
      var row=document.createElement('div');row.className='fw-row';
      var context=document.createElement('span');context.className='fw-help';context.textContent=item.capture_id?'Snapshot ready':'Text feedback';
      var remove=document.createElement('button');remove.type='button';remove.className='fw-button';remove.textContent='Remove';remove.setAttribute('aria-label','Remove queued request '+(index+1));remove.disabled=edit.disabled;
      remove.addEventListener('click',function(){queue.splice(index,1);saveDraft();renderQueue();broadcastQueue();var fallback=list.querySelector('textarea')||note;fallback.focus({preventScroll:true});});
      row.append(context,remove);li.append(strong,edit,row);
      var destination=document.createElement('p');destination.className='fw-help';destination.textContent='Destination: '+document.getElementById('analysis-name').textContent;li.append(destination);
      if(item.filters&&Object.keys(item.filters).length){var filters=document.createElement('p');filters.className='fw-help';filters.textContent='Filters: '+JSON.stringify(item.filters);li.append(filters);}
      list.appendChild(li);
    });
    captureFailures.forEach(function(f){
      var li=document.createElement('li');li.className='fw-queue-item';
      var strong=document.createElement('strong');strong.textContent=f.item.origin_name+' · Snapshot needs attention';
      var desc=document.createElement('p');desc.textContent=f.item.prompt+' — '+f.error;li.append(strong,desc);
      var retry=document.createElement('button');retry.type='button';retry.className='fw-button';retry.textContent=f.inflight?'Capturing…':'Retry snapshot';retry.disabled=!!f.inflight||ended||invalidSession;
      retry.addEventListener('click',function(){
        if(f.frame&&f.message&&f.item.frame_id===f.frame.dataset.frameId){
          closeQueue();activateTab('reports',true);
          requestAnimationFrame(function(){requestAnimationFrame(function(){queueWidgetItem(f.frame,f.message);});});
        }else{desc.textContent='This report view changed. Reopen '+f.item.origin_name+' and select the element again, or queue the saved text without a snapshot.';}
      });li.append(retry);
      if(!f.inflight){
        var textOnly=document.createElement('button');textOnly.type='button';textOnly.className='fw-button';textOnly.textContent='Queue text without snapshot';textOnly.disabled=ended||invalidSession;
        textOnly.addEventListener('click',function(){
          captureFailures=captureFailures.filter(function(x){return x.item.request_id!==f.item.request_id;});
          var item=persistableItem(f.item);delete item.selection_id;item.capture_id=null;
          if(!queue.some(function(x){return x.request_id===item.request_id;}))queue.push(item);
          saveDraft();renderQueue();broadcastQueue();
          if(f.frame&&f.frame.dataset.frameId===item.frame_id)postFrame(f.frame,'fw-workspace-queue-result',{request_id:item.request_id,ok:true,items:queue.map(persistableItem)});
        });
        var discard=document.createElement('button');discard.type='button';discard.className='fw-button';discard.textContent='Discard failed selection';discard.disabled=ended||invalidSession;
        discard.addEventListener('click',function(){captureFailures=captureFailures.filter(function(x){return x.item.request_id!==f.item.request_id;});renderQueue();note.focus();});
        li.append(textOnly,discard);
      }
      list.appendChild(li);
    });
    if(!queue.length&&!captureFailures.length){var empty=document.createElement('li');empty.className='fw-queue-empty';empty.textContent='Nothing queued yet. Select an element on a report or analysis, or write a message below.';list.appendChild(empty);}
    if(focusId){var restore=Array.prototype.find.call(list.querySelectorAll('textarea'),function(el){return el.dataset.fwRequestId===focusId;});if(restore){restore.focus({preventScroll:true});restore.setSelectionRange(selectionStart,selectionEnd);}}
    document.getElementById('queue-count').textContent=String(queue.length+captureFailures.length);
    document.getElementById('queue-summary').textContent=captureFailures.length?'Snapshots need attention':queue.length?queue.length+' queued request'+(queue.length===1?'':'s'):'No items queued';
    document.getElementById('send').disabled=ended||invalidSession||sendBusy||(!queue.length&&!note.value.trim()&&!pendingSend)||!!captureFailures.length;
    document.getElementById('end-session').disabled=ended||invalidSession||sendBusy||captureInFlight();
    updateControls();
  }
  function takeNoteOrigin() {return noteOrigin||originForFrame(activeFrame(),currentTab==='reports'?'report':'analysis');}
  note.addEventListener('input',function(){if(note.value.trim()&&!noteOrigin)noteOrigin=takeNoteOrigin();if(!note.value.trim())noteOrigin=null;saveDraft();renderQueue();updateNoteContext();});
  note.addEventListener('keydown',function(event){if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();send(false);}});

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
  document.getElementById('end-session').addEventListener('click',function(){
    if(pendingSend&&!pendingSend.endSession){openQueue();document.getElementById('send-status').textContent='Retry the unconfirmed submission before ending the session.';return;}
    if(captureFailures.length||note.value.trim()){openQueue(true);document.getElementById('send-status').textContent='Send or clear your draft and resolve snapshots before ending the session.';return;}
    if(window.confirm('Send queued feedback and end this analysis session?')){openQueue();send(true);}
  });
  function disableForInvalidSession() {
    invalidSession=true;statusEl.textContent='Session changed';setSelect(false);
    [sourceSelect,document.getElementById('open-report'),document.getElementById('retry-build'),document.getElementById('global-select'),document.getElementById('send'),document.getElementById('end-session'),note].forEach(function(x){if(x)x.disabled=true;});renderQueue();
  }
  function setEnded(){statusEl.textContent='Session ended';ended=true;[analysisFrame,sourceFrame].forEach(function(f){if(f.dataset.frameId)postFrame(f,'fw-workspace-status',{agent:'absent',replies:[],ended:true});});setSelect(false);sourceSelect.disabled=true;document.getElementById('open-report').disabled=true;document.getElementById('retry-build').disabled=true;document.getElementById('global-select').disabled=true;note.disabled=true;renderQueue();}
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
    if(!captureInFlight()&&a.url&&(stateVersion.analysis!==a.version||!analysisFrame.src)){stateVersion.analysis=a.version;loadFrame(analysisFrame,{slug:a.slug,name:a.name||'Analysis',url:a.url});}
    if(!initialHandled&&initialSource){initialHandled=true;if(reports.some(function(r){return r.slug===initialSource;})){sourceSelect.value=initialSource;restoringHistory=true;selectSource(initialSource);restoringHistory=false;updateHistory(true);}}
    var active=activeSource&&reports.find(function(r){return r.slug===activeSource.slug;});
    if(active){activeSource=active;if(!captureInFlight()&&active.build_state==='ready'&&active.version!==stateVersion.source){stateVersion.source=active.version;loadFrame(sourceFrame,active);}}
    if(pendingSource){var chosen=reports.find(function(r){return r.slug===pendingSource;});if(chosen){if(chosen.build_state==='ready'||chosen.build_state==='failed')delete buildRequested[chosen.slug];updateSourceStatus();if(chosen.build_state==='ready')openReadyReport(chosen,pendingSourceGeneration);else if(chosen.build_state==='failed'){sourceStatus.textContent='Build failed: '+(chosen.error||'Use Retry build to try again.');}else if(chosen.build_state==='building'){sourceStatus.textContent='Building report…';}}}
    updateControls();return true;
  }
  function pollState() {
    if(pollBusy||invalidSession)return;pollBusy=true;var g=++stateGeneration;
    api('state','GET').then(function(s){if(g!==stateGeneration)return;applyState(s);}).catch(function(e){if(g===stateGeneration){if([404,409,410].indexOf(e.status)>=0||/session/i.test(e.message))disableForInvalidSession();else statusEl.textContent='Connection issue: '+e.message;}}).finally(function(){if(g===stateGeneration)pollBusy=false;else pollBusy=false;});
  }
  readDraft();
  if(pendingSend)document.getElementById('send-status').textContent='A previous submission is unconfirmed. Queue & send retries the same request.';
  // Page visibility is shared by desktop and phone; iframe state stays alive.
  activateTab(new URL(location.href).searchParams.get('page')==='reports'?'reports':'analysis');
  renderQueue();pollState();window.setInterval(pollState,2500);
})();

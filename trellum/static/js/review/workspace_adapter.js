    /* Workspace-only adapter: the surrounding review widget stays familiar,
       while all durable queue, capture and send work belongs to its parent. */
    var workspaceChild = WORKSPACE;
    var workspaceId = workspaceChild && workspaceChild.session + ':' + workspaceChild.analysis_slug + ':' + workspaceChild.slug;
    var workspacePopoverKey = workspaceChild && 'fw-workspace-pop:' + workspaceId;
    var workspaceRecoverableText = '';
    var workspaceMirror = [];
    var workspaceTargets = Object.create(null);
    var workspaceSelectionSeq = 0;
    var workspaceNoteOrigin = null;
    var workspaceNoteRevision = 0;
    var workspaceSendWait = Object.create(null);
    var workspaceFailedSend = null;
    var workspaceAddWait = Object.create(null);
    var workspaceEnded = false;

    function workspaceIdValue(prefix) {
        var bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return prefix + '-' + Array.prototype.map.call(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    }
    function workspaceEnvelope(type, extra) {
        return Object.assign({type: type, session: workspaceChild.session,
            analysis_slug: workspaceChild.analysis_slug, slug: workspaceChild.slug,
            frame_id: workspaceChild.frame_id}, extra || {});
    }
    function workspaceSend(type, extra) {
        if (window.parent !== window) window.parent.postMessage(workspaceEnvelope(type, extra), location.origin);
    }
    function workspaceValid(d) {
        return d && d.session === workspaceChild.session && d.analysis_slug === workspaceChild.analysis_slug &&
            d.slug === workspaceChild.slug && d.frame_id === workspaceChild.frame_id;
    }
    function workspaceClone(x) { return JSON.parse(JSON.stringify(x)); }
    function workspaceOrigin() {
        return workspaceNoteOrigin || {origin_slug: workspaceChild.slug,
            origin_name: workspaceChild.kind === 'analysis' ? (document.title || 'Analysis') : (document.title || workspaceChild.slug),
            origin_kind: workspaceChild.kind, frame_id: workspaceChild.frame_id,
            filters:workspaceFilters(), source_built_at:workspaceBuiltAt()};
    }
    function workspaceFilters() {
        try {
            if (window.fw && fw.filterEngine && typeof fw.filterEngine.getFilterState === 'function') {
                var ids = fw.filterEngine.getDatasetIds ? fw.filterEngine.getDatasetIds() : [], all = {};
                ids.forEach(function (id) {
                    var value = fw.filterEngine.getFilterState(id);
                    if (value && Object.keys(value).length) all[id] = value;
                });
                return all;
            }
        } catch (_) {}
        return null;
    }
    function workspaceBuiltAt() {
        try { return window._reportData && _reportData._freshness && _reportData._freshness.generated_at || null; } catch (_) { return null; }
    }
    function workspaceFingerprint(target) {
        return JSON.stringify({text:(target.textContent || '').replace(/\s+/g, ' ').slice(0, 500),
            filters:workspaceFilters(), built:workspaceBuiltAt()});
    }
    function workspaceComponentNeedsCapture(item) {
        return workspaceChild.kind === 'report';
    }

    function workspaceError(message) {
        var box = document.getElementById('fwrvWorkspaceError');
        if (!box) { box = el('div', 'fwrv-workspace-error'); box.id = 'fwrvWorkspaceError'; box.setAttribute('role', 'status'); pop.appendChild(box); }
        box.textContent = message || '';
        box.style.display = message ? 'block' : 'none';
    }
    function queueFromPop(sendNow) {
        if (!workspaceChild) return;
        var prompt = popText.value.trim(), t = pop._target;
        if (!prompt || !t || workspaceEnded) return;
        var retry = pop._workspacePending;
        if (retry && retry.item.selector === t.selector) { retry.item.prompt=prompt;retry.send_after_queue=!!sendNow;workspaceSend('fw-workspace-queue-add', retry); workspaceError('Retrying the same queue request…'); return; }
        var selectionId = workspaceIdValue('selection');
        var origin = workspaceOrigin();
        var item = Object.assign({request_id:workspaceIdValue('item'), prompt:prompt,
            selector:t.selector, tag:t.tag, text:t.text, section:t.section,
            component:{id:t.id, kind:t.kind, title:t.title},
            data:pop._data ? workspaceClone(pop._data) : null,
            filters:workspaceFilters(), source_built_at:workspaceBuiltAt(), selection_id:selectionId}, origin);
        // Freeze the actual node and revision at the button click. Switching
        // panes while capture is pending cannot retarget this evidence.
        workspaceTargets[selectionId] = {target:t.el, fingerprint:workspaceFingerprint(t.el),
            item:item, promptRevision:workspaceNoteRevision};
        var pending = {request_id:item.request_id, item:item, selection_id:selectionId,
            capture_required:workspaceComponentNeedsCapture(item), send_after_queue:!!sendNow,
            send_request_id:sendNow ? workspaceIdValue('send') : null};
        workspaceAddWait[item.request_id] = pending; pop._workspacePending = pending;
        workspaceSend('fw-workspace-queue-add', pending);
        workspaceError('Adding to shared queue…');
    }
    function persistQueue() {
        if (!workspaceChild) { lsSet(QKEY, JSON.stringify(state.items)); return; }
        var oldById = Object.create(null), newById = Object.create(null);
        workspaceMirror.forEach(function (item) { oldById[item.request_id] = item; });
        state.items.forEach(function (item) { newById[item.request_id] = item; });
        Object.keys(oldById).forEach(function (key) {
            if (!newById[key]) workspaceSend('fw-workspace-queue-remove', {request_id:key});
        });
        Object.keys(newById).forEach(function (key) {
            var before = oldById[key], after = newById[key];
            if (before && before.prompt !== after.prompt) workspaceSend('fw-workspace-queue-update', {request_id:key, prompt:after.prompt});
        });
        workspaceMirror = state.items.map(workspaceClone);
    }
    function maybeOfferResend() { /* standalone resend batches are never workspace drafts */ }
    function pinTargetFor(item) {
        if (!workspaceChild) { try { return document.querySelector(item.selector); } catch (_) { return null; } }
        if (item.origin_slug !== workspaceChild.slug || item.frame_id !== workspaceChild.frame_id) return null;
        try { return document.querySelector(item.selector); } catch (_) { return null; }
    }
    function render() {
        if (!workspaceChild) return;
        countEl.textContent = state.items.length; countEl.classList.toggle('zero', !state.items.length);
        barSend.style.display = state.items.length && !workspaceEnded ? 'inline-flex' : 'none';
        barSend.textContent = 'Send ' + state.items.length;
        var focused=document.activeElement, focusedId=focused&&focused.getAttribute&&focused.getAttribute('data-fw-request-id');
        var focusedValue=focusedId?focused.value:null, focusedStart=focusedId?focused.selectionStart:null, focusedEnd=focusedId?focused.selectionEnd:null;
        pins.replaceChildren(); list.replaceChildren();
        state.items.forEach(function (item) {
            var target = pinTargetFor(item);
            if (target) {
                var r = docRect(target), pin = el('div', 'fwrv-pin');
                pin.setAttribute('data-fw-review-ui', ''); pin.textContent = item.n || '•';
                pin.title = item.prompt; pin.style.top = (r.top - 8) + 'px'; pin.style.left = (r.left + r.w - 12) + 'px';
                pin.addEventListener('click', function () { setPanel(true); }); pins.appendChild(pin);
            }
            var row = el('div', 'fwrv-item');
            var label = (item.origin_name || item.origin_slug || 'Source') + ' · ' +
                ((item.component && item.component.kind) || item.tag || 'Selection') +
                (item.component && item.component.title ? ' “' + item.component.title + '”' : '');
            var top = el('div', 'fwrv-top');
            top.innerHTML = '<span class="fwrv-n">' + esc(item.n || '•') + '</span><span class="fwrv-who"><b>' + esc(label) + '</b> ' +
                '<span class="fwrv-sel">' + esc(item.selector || 'General feedback') + '</span></span>' +
                '<button type="button" class="fwrv-x" title="Remove" aria-label="Remove queued feedback">&times;</button>';
            var ta = document.createElement('textarea'); ta.value = item.prompt || ''; ta.setAttribute('data-fw-request-id', item.request_id || '');
            ta.addEventListener('input', function () { item.prompt = ta.value; persistQueue(); });
            top.querySelector('button').addEventListener('click', function () {
                state.items = state.items.filter(function (candidate) { return candidate.request_id !== item.request_id; });
                persistQueue(); render();
            });
            row.append(top, ta); list.appendChild(row);
        });
        if (focusedId) { var restore=list.querySelector('[data-fw-request-id=\"'+focusedId+'\"]'); if(restore){restore.value=focusedValue;restore.focus({preventScroll:true});try{restore.setSelectionRange(focusedStart,focusedEnd);}catch(_){}} }
        if (!state.items.length) list.innerHTML = '<div class="fwrv-empty">Nothing queued yet.<br>Toggle <b>Select</b> and click a report element —<br>or open Chat to send a note.</div>';
    }
    function doSend(endSession) {
        if (!workspaceChild || workspaceEnded) return;
        var text = note.value.trim();
        if (!text && !state.items.length && !endSession) return;
        var requestId = workspaceFailedSend ? workspaceFailedSend.request_id : workspaceIdValue('send');
        var submitted = workspaceFailedSend || {request_id:requestId,text:text,revision:workspaceNoteRevision,end_session:!!endSession,origin:workspaceOrigin()};
        workspaceSend('fw-workspace-send', {request_id:requestId, note:submitted.text,
            note_origin:submitted.origin, end_session:submitted.end_session, note_revision:submitted.revision});
        workspaceSendWait[requestId] = submitted;
        $('fwrvSend').disabled = true; $('fwrvSendEnd').disabled = true;
        showToast('Sending', 'Waiting for the shared workspace queue.');
    }

    note.addEventListener('input', function () {
        workspaceNoteRevision++;
        if (note.value.trim() && !workspaceNoteOrigin) workspaceNoteOrigin = workspaceOrigin();
        if (!note.value.trim()) workspaceNoteOrigin = null;
    });
    document.addEventListener('focusin', function (event) {
        if (event.target === note || event.target === popText) return;
        workspaceSend('fw-workspace-focus');
    }, true);
    document.addEventListener('pointerdown', function (event) {
        if (!event.target.closest('[data-fw-review-ui]')) workspaceSend('fw-workspace-focus');
    }, true);
    setSelecting(false);
    $('fwrvSelect').setAttribute('aria-label', 'Select an element');
    $('fwrvSelect').addEventListener('click', function () { workspaceSend('fw-workspace-select-mode', {selecting:state.selecting}); });
    $('fwrvPopCancel').addEventListener('click', function () { ssSet(workspacePopoverKey, ''); workspaceRecoverableText=''; });
    popText.addEventListener('input', function () {
        if (!pop._target) return;
        var value=popText.value;
        ssSet(workspacePopoverKey, value ? JSON.stringify({slug:workspaceChild.slug,selector:pop._target.selector,prompt:value,kind:pop._target.kind,title:pop._target.title,tag:pop._target.tag,text:pop._target.text,section:pop._target.section,data:pop._data||null,filters:workspaceFilters(),built:workspaceBuiltAt(),fingerprint:workspaceFingerprint(pop._target.el)}) : '');
    });
    $('fwrvSend').addEventListener('click', function () { doSend(false); });
    $('fwrvSendEnd').addEventListener('click', function () { doSend(true); });
    barSend.addEventListener('click', function () { doSend(false); });
    note.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); doSend(false); }
    });
    var workspaceEndArmed=false;
    $('fwrvEnd').addEventListener('click', function () {
        if(workspaceEnded)return;
        if(!workspaceEndArmed){workspaceEndArmed=true;$('fwrvEnd').textContent='Really end?';$('fwrvEnd').classList.add('fwrv-danger');setTimeout(function(){workspaceEndArmed=false;$('fwrvEnd').textContent='End';$('fwrvEnd').classList.remove('fwrv-danger');},2500);return;}
        workspaceEndArmed=false;doSend(true);
    });
    $('fwrvWorkspaceError');

    window.addEventListener('message', function (event) {
        if (event.origin !== location.origin || event.source !== window.parent || !workspaceValid(event.data)) return;
        var message = event.data;
        if (message.type === 'fw-workspace-select') { setSelecting(!!message.selecting); return; }
        if (message.type === 'fw-workspace-init' || message.type === 'fw-workspace-queue') {
            if (Array.isArray(message.items)) {
                state.items = workspaceClone(message.items); workspaceMirror = state.items.map(workspaceClone);
                state.nextN = state.items.reduce(function (next, it) { return Math.max(next, Number(it.n || 0) + 1); }, 1);
                render();
            }
            if (message.selecting !== undefined) setSelecting(!!message.selecting);
            if (message.agent) setAgent(typeof message.agent === 'string' ? message.agent : message.agent.state || 'absent');
            if (Array.isArray(message.replies)) message.replies.forEach(function (reply) {
                if (reply.n && reply.n <= state.lastReplyN) return;
                addLog('agent', reply.text || reply.message || '', reply.n);
                state.lastReplyN = Math.max(state.lastReplyN, Number(reply.n || 0));
            });
            if (message.ended) { setSelecting(false); workspaceEnded = true; state.ended = true; endbar.style.display='block'; bar.style.display='none'; }
        } else if (message.type === 'fw-workspace-status') {
            if (message.agent) setAgent(typeof message.agent === 'string' ? message.agent : message.agent.state || 'absent');
            if (Array.isArray(message.replies)) message.replies.forEach(function (reply) {
                if (reply.n && reply.n <= state.lastReplyN) return;
                addLog('agent', reply.text || reply.message || '', reply.n);
                state.lastReplyN = Math.max(state.lastReplyN, Number(reply.n || 0));
            });
            if (message.ended) { setSelecting(false); workspaceEnded = true; state.ended = true; endbar.style.display='block'; bar.style.display='none'; }
        } else if (message.type === 'fw-workspace-capture') {
            var reference = workspaceTargets[message.selection_id];
            if (!reference) { workspaceSend('fw-workspace-capture-result', {selection_id:message.selection_id, error:'The selected element is no longer available.'}); return; }
            if (workspaceFingerprint(reference.target) !== reference.fingerprint) {
                workspaceSend('fw-workspace-capture-result', {selection_id:message.selection_id, error:'The report or its filters changed after selection. Select it again.'}); return;
            }
            if (!(window.fw && typeof fw.captureElementSnapshotForAnalysis === 'function')) {
                workspaceSend('fw-workspace-capture-result', {selection_id:message.selection_id, error:'Snapshot capture is unavailable for this report.'}); return;
            }
            fw.captureElementSnapshotForAnalysis(reference.target).then(function (capture) {
                if (workspaceFingerprint(reference.target) !== reference.fingerprint) throw new Error('The report changed while capture was rendering. Select it again.');
                // Custom canvases may read filtered data without declaring a
                // dataset. Preserve the same page context as the queued item.
                capture.source.filters = workspaceClone(workspaceFilters() || {});
                workspaceSend('fw-workspace-capture-result', {selection_id:message.selection_id, capture:capture});
            }).catch(function (error) {
                workspaceSend('fw-workspace-capture-result', {selection_id:message.selection_id, error:error.message || 'Snapshot capture failed.'});
            });
        } else if (message.type === 'fw-workspace-queue-result') {
            var pending = workspaceAddWait[message.request_id]; if (!pending) return;
            if (!message.ok) { workspaceError(message.message || 'Queueing failed. Your note is still here; retry with Queue.'); return; }
            delete workspaceAddWait[message.request_id]; delete workspaceTargets[pending.selection_id];
            if (pop._workspacePending === pending && popText.value.trim() === pending.item.prompt) { hidePop(); ssSet(workspacePopoverKey, ''); workspaceError(''); } else workspaceError('');
            if (Array.isArray(message.items)) { state.items = workspaceClone(message.items); workspaceMirror = state.items.map(workspaceClone); }
            render();
            if (pending.send_after_queue) doSend(false);
        } else if (message.type === 'fw-workspace-send-result') {
            var waiting = workspaceSendWait[message.request_id]; if (!waiting) return;
            delete workspaceSendWait[message.request_id]; $('fwrvSend').disabled = false; $('fwrvSendEnd').disabled = false;
            if (!message.ok) { workspaceFailedSend=waiting; showToast('Send not confirmed', message.message || 'Retry the same send from this widget.'); return; }
            workspaceFailedSend=null;
            if (message.note_origin === workspaceChild.slug && message.note_text === waiting.text && note.value.trim() === waiting.text && workspaceNoteRevision === waiting.revision) { note.value = ''; workspaceNoteOrigin = null; ssSet('fw-workspace-note:' + workspaceId, ''); }
            if (Array.isArray(message.items)) { state.items = workspaceClone(message.items); workspaceMirror = state.items.map(workspaceClone); }
            if (message.ended) { setSelecting(false); workspaceEnded = true; state.ended = true; endbar.style.display='block'; bar.style.display='none'; }
            else showToast('Sent', message.message || 'Feedback was sent to the agent.');
            render();
        }
    });

    // Preserve notes locally in the session-scoped widget state. Queue bodies,
    // snapshots and standalone review keys never enter this storage.
    note.value = ssGet('fw-workspace-note:' + workspaceId) || '';
    note.addEventListener('input', function () { ssSet('fw-workspace-note:' + workspaceId, note.value); });
    $('fwrvEnd').title = 'End the workspace session';
    if (workspaceChild.kind === 'report') {
        $('fwrvSelect').title = 'Select a whole chart or section to add to the analysis';
        $('fwrvPopQueue').title = 'Queue this evidence and context for the analysis';
        $('fwrvPopQueueSend').title = 'Add this evidence and context, then send the shared queue';
        popText.placeholder = 'What context should this add to the analysis?';
        note.placeholder = 'Describe what this source should contribute to the analysis…';
        root.querySelector('.fwrv-note-l').textContent = 'Context for the analysis';
    }
    workspaceSend('fw-workspace-focus');

    try {
        var savedPop=JSON.parse(ssGet(workspacePopoverKey)||'null');
        if(savedPop&&savedPop.slug===workspaceChild.slug&&savedPop.prompt){
            var savedTarget=null;try{savedTarget=document.querySelector(savedPop.selector);}catch(_){}
            if(savedTarget&&workspaceFingerprint(savedTarget)===savedPop.fingerprint){
                var targetInfo=identify(savedTarget);if(targetInfo){showPop(targetInfo,undefined,undefined,savedPop.data);popText.value=savedPop.prompt;workspaceError('Restored your unfinished selection note.');}
            } else {
                workspaceRecoverableText=savedPop.prompt;
                showToast('Draft saved','Select the same report element to restore its note.');
            }
        }
    } catch (_) {}

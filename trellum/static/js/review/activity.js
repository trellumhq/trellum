/* Shared conversation observations for standalone review and the workspace. */
(function () {
  'use strict';
  var labels = {queued:'Queued · awaiting pickup', picked_up:'Picked up · working', working:'Working', rebuilding:'Rebuilding', waiting:'Waiting for input', unknown:'Waiting for agent · activity unknown', failed:'Failed', completed:'Completed', ended:'Session ended'};
  function duration(seconds) {
    seconds=Math.max(0,Math.floor(seconds));
    if(seconds<60)return seconds+'s';
    if(seconds<3600)return Math.floor(seconds/60)+'m '+seconds%60+'s';
    return Math.floor(seconds/3600)+'h '+Math.floor(seconds%3600/60)+'m';
  }
  function text(node,value){if(node.textContent!==value)node.textContent=value;}
  function message(node,value) {
    if(node.dataset.message===value)return;
    node.dataset.message=value;
    node.innerHTML=value.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')
      .replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/\n/g,'<br>')
      .replace(/(^|[\s;:])\((\d+)\)\s/g,'$1<br><b>($2)</b> ').replace(/^<br>/,'');
  }
  function render(list, activities, messages) {
    var scroller=list.closest('.fw-queue-scroll')||list;
    var nearBottom=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<40, scroll=scroller.scrollTop;
    var rows=[], now=Date.now()/1000;
    activities=Array.isArray(activities)?activities:[];
    var hasWork=activities.some(function(a){return ['picked_up','working','rebuilding','waiting'].indexOf(a.state)>=0;});
    activities.forEach(function(a){rows.push({key:'feedback:'+a.feedback_id,at:a.submitted_at||0,activity:a});});
    (messages||[]).forEach(function(m,i){rows.push({key:'message:'+(m.n||i),at:m.ts||0,message:m});});
    rows.sort(function(a,b){return a.at-b.at;});
    var existing=Object.create(null), keys=Object.create(null);
    Array.prototype.forEach.call(list.children,function(node){existing[node.dataset.conversationKey]=node;});
    rows.forEach(function(row,index){
      keys[row.key]=true;
      var node=existing[row.key];
      if(!node){
        node=document.createElement('article');node.dataset.conversationKey=row.key;
        if(row.activity){
          node.className='fw-review-request fwrv-m fwrv-you';node.dataset.feedbackId=row.activity.feedback_id;
          var who=document.createElement('strong');who.className='fwrv-w';who.textContent='You';
          var feedback=document.createElement('div');feedback.className='fw-review-feedback';
          var activity=document.createElement('div');activity.className='fw-review-activity';
          var observation=document.createElement('div');observation.className='fw-review-observation';observation.setAttribute('role','status');observation.setAttribute('aria-live','polite');observation.setAttribute('aria-atomic','true');
          var label=document.createElement('strong');label.className='fw-review-activity-state';
          var summary=document.createElement('p');summary.className='fw-review-activity-summary';
          var times=document.createElement('small');times.className='fw-review-activity-time';
          observation.append(label,summary);activity.append(observation,times);node.append(who,feedback,activity);
        }else{
          node.className='fw-reply fwrv-m '+(row.message.who==='you'?'fwrv-you':'fwrv-agent');
          var author=document.createElement('strong');author.className='fwrv-w';author.textContent=row.message.who==='you'?'You':'Agent';
          var body=document.createElement('div');body.className='fw-review-message';node.append(author,body);
        }
      }
      if(row.activity){
        var a=row.activity, state=a.display_state||a.state, status=labels[state]||'Activity unknown';
        if(state==='queued'&&hasWork)status='Queued follow-up · awaiting pickup';
        node.dataset.activityState=state;
        text(node.querySelector('.fw-review-feedback'),a.text||'Feedback sent');
        text(node.querySelector('.fw-review-activity-state'),status);
        text(node.querySelector('.fw-review-activity-summary'),a.summary||(['picked_up','working'].indexOf(state)>=0?'Working; no further detail reported.':state==='unknown'?'No recent agent contact. Last activity is unconfirmed.':''));
        var elapsed=duration((a.completed_at||now)-(a.submitted_at||now));
        var updated=a.updated_at||a.submitted_at||now;
        var timing='Elapsed '+elapsed+' · Last update '+duration(now-updated)+' ago';
        text(node.querySelector('.fw-review-activity-time'),timing);
        node.querySelector('.fw-review-activity-time').title='Last meaningful update: '+new Date(updated*1000).toLocaleString();
      }else message(node.querySelector('.fw-review-message'),row.message.text||row.message.message||'');
      if(list.children[index]!==node)list.insertBefore(node,list.children[index]||null);
    });
    Object.keys(existing).forEach(function(key){if(!keys[key])existing[key].remove();});
    scroller.scrollTop=nearBottom?scroller.scrollHeight:scroll;
  }
  function summary(s) {
    if(s.ended)return 'Session ended';
    var activities=s.activities||[], current=activities.filter(function(a){return ['queued','completed','failed'].indexOf(a.state)<0;}), queued=activities.filter(function(a){return a.state==='queued';}).length;
    var row=current[current.length-1];
    var result=row?(labels[row.display_state||row.state]||'Activity unknown'):queued?'Queued · awaiting pickup':(s.agent&&s.agent.state==='listening'?'Agent listening':activities.some(function(a){return a.state==='failed';})?'Feedback failed':'Ready · waiting for agent');
    return result+(row&&queued?' · '+queued+' queued follow-up'+(queued===1?'':'s'):'');
  }
  function markUnknown(activities) {
    (activities||[]).forEach(function(a){
      if(['queued','picked_up','working','rebuilding','waiting'].indexOf(a.state)>=0)a.display_state='unknown';
    });
  }
  function pollInterval(activities, idleMs, now) {
    now=now===undefined?Date.now()/1000:now;
    activities=Array.isArray(activities)?activities:[];
    var fast=activities.some(function(a){
      var state=a.display_state||(a.state==='queued'?'queued':'');
      if(state==='queued')return Number(a.submitted_at)>0&&now-Number(a.submitted_at)<90;
      return ['picked_up','working','rebuilding'].indexOf(state)>=0;
    });
    return fast?500:idleMs;
  }
  window._fwReviewActivity={render:render,summary:summary,markUnknown:markUnknown,pollInterval:pollInterval};
})();

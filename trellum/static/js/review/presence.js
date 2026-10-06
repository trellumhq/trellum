    /* ── agent presence ─────────────────────────────────────── */
    function setAgent(s) {
        state.agent = s;
        ['fwrvDot', 'fwrvDot2'].forEach(function (id) {
            $(id).className = 'fwrv-dot ' + (s === 'listening' ? 'listening' : s === 'working' ? 'working' : '');
        });
        var lbl = s === 'listening' ? 'agent listening' : s === 'working' ? 'agent working…' : 'agent absent';
        $('fwrvDotLbl').textContent = lbl;
        $('fwrvDotLbl2').textContent = lbl;
        // Presence is information, not a lock: the user asked to send a
        // follow-up while the agent was working and found themselves stuck.
        // Batches queue server-side in order, so sending while busy is safe.
        $('fwrvSend').textContent = s === 'working' ? 'Send (agent busy)' : 'Send to agent';
    }

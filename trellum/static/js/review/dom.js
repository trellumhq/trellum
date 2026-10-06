    /* ── DOM ────────────────────────────────────────────────── */
    function el(tag, cls, html) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (html !== undefined) e.innerHTML = html;
        return e;
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }

    var root = el('div', 'fwrv-root');
    root.setAttribute('data-fw-review-ui', '');
    root.innerHTML =
        '<div class="fwrv-endbar" id="fwrvEndbar">Review session ended — feedback was delivered to the agent.</div>' +
        '<div class="fwrv-bar" id="fwrvBar">' +
          '<button type="button" class="fwrv-btn" id="fwrvSelect" title="Select an element (Esc exits)">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M4 4l7.5 16 2.2-6.3L20 11.5z"/></svg>Select</button>' +
          '<button type="button" class="fwrv-btn" id="fwrvChat" title="Message the agent — no element needed">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a8 8 0 0 1-8 8H4l2.2-2.6A8 8 0 1 1 21 12z"/></svg>Chat</button>' +
          '<button type="button" class="fwrv-btn" id="fwrvQueueBtn" title="Open the review queue">Queue <span class="fwrv-count zero" id="fwrvCount">0</span></button>' +
          '<button type="button" class="fwrv-btn fwrv-send-mini" id="fwrvBarSend" title="Send the queued requests to the agent now"></button>' +
          '<span class="fwrv-sep"></span>' +
          '<span class="fwrv-btn fwrv-static" id="fwrvStatusBtn"><span class="fwrv-dot" id="fwrvDot"></span><span id="fwrvDotLbl">agent absent</span></span>' +
          '<span class="fwrv-sep"></span>' +
          '<button type="button" class="fwrv-btn" id="fwrvEnd" title="End the review session">End</button>' +
        '</div>' +
        '<div class="fwrv-panel" id="fwrvPanel">' +
          '<div class="fwrv-hd"><b>Review queue</b>' +
            '<span style="display:flex;align-items:center;gap:10px">' +
              '<span class="fwrv-status"><span class="fwrv-dot" id="fwrvDot2"></span><span id="fwrvDotLbl2">agent absent</span></span>' +
              '<button type="button" class="fwrv-x" id="fwrvPanelClose" title="Close (Esc)">&times;</button>' +
            '</span></div>' +
          '<div class="fwrv-list" id="fwrvList"></div>' +
          '<div class="fwrv-log" id="fwrvLog"></div>' +
          '<div class="fwrv-foot">' +
            '<div class="fwrv-note-l">Chat with the agent</div>' +
            '<textarea id="fwrvNote" placeholder="Type any feedback — Enter sends it right away, no element needed…"></textarea>' +
            '<div class="fwrv-keys">Enter sends now · Shift+Enter newline · or it rides along with &quot;Send to agent&quot;</div>' +
            '<div class="fwrv-send-row">' +
              '<button type="button" class="fwrv-b fwrv-pri" id="fwrvSend">Send to agent</button>' +
              '<button type="button" class="fwrv-b" id="fwrvSendEnd" title="Send everything and end the session">Send &amp; end</button>' +
            '</div></div></div>' +
        '<div class="fwrv-lasso" id="fwrvLasso"></div>' +
        '<div class="fwrv-pop" id="fwrvPop">' +
          '<div class="fwrv-chip" id="fwrvPopChip"></div>' +
          '<div class="fwrv-chip" id="fwrvPopData" style="display:none"></div>' +
          '<textarea id="fwrvPopText" placeholder="What should change here?"></textarea>' +
          '<div class="fwrv-hint" style="margin:6px 0 2px 2px">Enter queue · Ctrl+Enter send · Esc cancel</div>' +
          '<div class="fwrv-row">' +
            '<button type="button" class="fwrv-b" id="fwrvPopCancel">Cancel</button>' +
            '<button type="button" class="fwrv-b" id="fwrvPopQueue">Queue</button>' +
            '<button type="button" class="fwrv-b fwrv-pri" id="fwrvPopQueueSend" title="Queue this and send everything now">Queue &amp; send</button></div></div>' +
        '<div class="fwrv-hl" id="fwrvHl"><span class="fwrv-hl-lbl" id="fwrvHlLbl"></span></div>' +
        '<div id="fwrvPins"></div>' +
        '<div class="fwrv-toast" id="fwrvToast"></div>' +
        '<div class="fwrv-flash" id="fwrvFlash"><div class="fwrv-box"><span class="fwrv-spin"></span><span>Report updated — reloading…</span></div></div>';
    document.body.appendChild(root);

    function $(id) { return document.getElementById(id); }
    var bar = $('fwrvBar'), panel = $('fwrvPanel'), hl = $('fwrvHl'), hlLbl = $('fwrvHlLbl'),
        pop = $('fwrvPop'), popChip = $('fwrvPopChip'), popText = $('fwrvPopText'),
        pins = $('fwrvPins'), list = $('fwrvList'), log = $('fwrvLog'), note = $('fwrvNote'),
        toast = $('fwrvToast'), flash = $('fwrvFlash'), endbar = $('fwrvEndbar'),
        countEl = $('fwrvCount'), barSend = $('fwrvBarSend'),
        popData = $('fwrvPopData'), lasso = $('fwrvLasso');

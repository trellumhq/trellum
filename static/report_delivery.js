/* Email delivery: "send me this report" schedules. Lives in one file,
 * served three ways:
 *
 *   - dashboard  -> loaded as a normal <script src="{% static %}"> tag
 *     (templates/portal/index.html); the dashboard's per-row envelope
 *     button (static/portal.js) calls window.ReportDelivery.open(...).
 *   - report page -> the SAME file, served byte-for-byte at the frozen URL
 *     /api/reports/delivery-widget.js (apps.reports.views.delivery_widget_js),
 *     injected by apps.runner.executor.portal_extensions()["scripts"] into
 *     every built report. Auto-mounts its own header button (see
 *     _autoMount below) because report pages carry no server template to
 *     hand-place one in.
 *   - delivery center -> templates/reports/my_deliveries.html (the
 *     cross-studio "My deliveries" page) calls window.ReportDelivery
 *     .mountCenter(...) once on load. Each server-rendered schedule row
 *     expands inline into the SAME form the drawer uses (buildScheduleForm
 *     / wireScheduleForm below) -- one implementation behind three call
 *     sites, not three parallel widgets.
 *
 * Styling is self-injected (like static/assistant.js) so this works on report
 * pages, which never load ui.css/portal.css: colors resolve through the
 * dashboard/report shared 17-token contract (docs/design-system.md) with a
 * framework-var fallback chain, same convention as assistant.css.
 */
(function () {
    'use strict';

    var CSS = ''
        // Drawer animation: slide in from the right + a slight fade,
        // ~180ms ease-out; reverse on close. opacity+visibility (not just
        // transform) are what let the drawer go non-interactive/invisible
        // only once the CLOSE transition actually finishes -- visibility
        // in a transition's property list flips at the START when going
        // toward visible, but at the END when going toward hidden, so this
        // needs no JS timer of its own. See playOpen() below for the other
        // half: forcing a reflow before the FIRST open, or this animation
        // (and the overlay's identical trick, already in place) has
        // nothing to transition from and just snaps open instead.
        + '#rdwOverlay{position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:9200;opacity:0;visibility:hidden;transition:opacity .18s ease-out,visibility .18s}'
        + '#rdwOverlay.open{opacity:1;visibility:visible}'
        + '#rdwDrawer{position:fixed;top:0;right:0;bottom:0;width:480px;max-width:92vw;'
        + 'background:var(--bg-card,var(--bg-main,#fff));border-left:1px solid var(--border,var(--border-color,#ddd));'
        + 'box-shadow:-4px 0 24px rgba(0,0,0,0.16);z-index:9201;opacity:0;visibility:hidden;transform:translateX(100%);'
        + 'transition:transform .18s ease-out,opacity .18s ease-out,visibility .18s;display:flex;flex-direction:column;font-size:13px;'
        + 'color:var(--text,var(--text-main,#1a1a2e));font-family:inherit}'
        + '#rdwDrawer.open{opacity:1;visibility:visible;transform:translateX(0)}'
        + '#rdwDrawer *{box-sizing:border-box}'
        + '@media(prefers-reduced-motion:reduce){#rdwOverlay,#rdwDrawer{transition:none}}'
        + '.rdw-header{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;'
        + 'padding:16px 20px;border-bottom:1px solid var(--border,var(--border-color,#ddd));flex-shrink:0}'
        + '.rdw-title{font-size:15px;font-weight:600;margin:0}'
        + '.rdw-subtitle{font-size:12px;color:var(--text2,var(--text-secondary,#777));margin-top:2px;'
        + 'max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.rdw-close{background:none;border:none;font-size:20px;line-height:1;cursor:pointer;'
        + 'color:var(--text3,var(--text-secondary,#999));padding:0 2px}'
        + '.rdw-close:hover{color:var(--text,var(--text-main,#1a1a2e))}'
        + '.rdw-body{flex:1;overflow-y:auto;padding:16px 20px 28px}'
        + '.rdw-section{margin-bottom:22px}'
        + '.rdw-section-title{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;'
        + 'color:var(--text2,var(--text-secondary,#777));margin:0 0 10px}'
        + '.rdw-note{color:var(--text2,var(--text-secondary,#777));font-size:12px;margin:0;line-height:1.5}'
        // !important below (chip/pill buttons, the recipient search input,
        // and this row) is deliberate, not decorative: on the delivery
        // center (/me/deliveries, body.mgmt) these elements sit inside
        // <main>, where ui.css's `body.mgmt main button:not(.ui-btn)` and
        // `body.mgmt label` rules OUTRANK this file's single-class selectors
        // on real CSS specificity (2 classes + 3 elements beats 1 class,
        // regardless of injection order) and silently repaint every chip
        // button and check-row at mgmt's generic 7px/14px/12px scale --
        // wrecking the 999px pill radius in the process. The drawer never
        // shows this because it renders outside <main>. Self-injected widget
        // CSS has to be able to assume it always wins; !important is that
        // guarantee, scoped to exactly the properties ui.css would otherwise
        // clobber.
        + '.rdw-check-row{display:flex!important;align-items:flex-start;gap:8px;padding:6px 0;cursor:pointer}'
        + '.rdw-check-row input{margin-top:2px;flex-shrink:0}'
        + '.rdw-check-row .rdw-check-label{font-weight:500}'
        + '.rdw-check-row .rdw-help{display:block;color:var(--text3,var(--text-secondary,#999));font-size:11px;margin-top:1px}'
        + '.rdw-btn{display:inline-flex!important;align-items:center;gap:6px;border-radius:6px!important;padding:6px 12px!important;'
        + 'font-size:12px!important;font-weight:500;cursor:pointer;border:1px solid var(--border,var(--border-color,#ddd))!important;'
        + 'background:var(--bg-card,var(--bg-main,#fff))!important;color:var(--text,var(--text-main,#1a1a2e));transition:all .15s}'
        + '.rdw-btn:hover{border-color:var(--accent,var(--bg-header,#4e79a7));color:inherit}'
        + '.rdw-btn:disabled{opacity:.5;cursor:default}'
        + '.rdw-btn.primary{background:var(--accent,var(--bg-header,#4e79a7))!important;border-color:var(--accent,var(--bg-header,#4e79a7))!important;color:var(--on-accent,#fff)}'
        + '.rdw-btn.danger{color:var(--red,var(--accent-red,#dc2626))}'
        + '.rdw-btn.danger:hover{border-color:var(--red,var(--accent-red,#dc2626))}'
        // The "small" variant is exactly what body.mgmt's blanket button rule
        // has no concept of -- it applies the same padding/font to every
        // <button>, so this is the one most visibly flattened without the
        // !important overrides above and below.
        + '.rdw-btn.small{padding:3px 8px!important;font-size:11px!important}'
        + '.rdw-field{margin-bottom:12px}'
        + '.rdw-label{display:block;font-size:11px;font-weight:600;color:var(--text2,var(--text-secondary,#777));margin-bottom:4px}'
        + '.rdw-input,.rdw-select{width:100%;padding:6px 8px;border-radius:6px;font-size:13px;'
        + 'border:1px solid var(--border,var(--border-color,#ddd));background:var(--bg,var(--bg-main,#fff));'
        + 'color:var(--text,var(--text-main,#1a1a2e))}'
        + '.rdw-input:focus,.rdw-select:focus,.rdw-btn:focus-visible,.rdw-close:focus-visible{'
        + 'outline:2px solid var(--focus,var(--accent,#4e79a7));outline-offset:1px}'
        + '.rdw-row2{display:flex;gap:8px}'
        + '.rdw-row2 .rdw-field{flex:1}'
        + '.rdw-field-error{color:var(--red,var(--accent-red,#dc2626));font-size:11px;margin-top:3px}'
        + '.rdw-help{display:block;color:var(--text3,var(--text-secondary,#999));font-size:11px;margin-top:4px}'
        // Recipient chip picker: quick-add chips (unselected roles/groups),
        // the selected area (removable chips, member chips overflowing past
        // ~6 into "+N more…"), and the type-to-search dropdown that appends
        // member chips. One recipe per docs/design-system.md -- color: token,
        // background: color-mix(token 14%, transparent) -- so role (accent),
        // group (studio purple) and individual (info blue) chips read apart
        // at a glance without inventing a fourth badge system.
        + '.rdw-quickadd{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}'
        + '.rdw-chip-add{font-size:11px!important;padding:3px 9px!important;border-radius:999px!important;cursor:pointer;'
        + 'border:1px dashed var(--border,var(--border-color,#ddd))!important;background:none!important;'
        + 'color:var(--text2,var(--text-secondary,#777));transition:all .15s}'
        + '.rdw-chip-add:hover{border-color:var(--accent,var(--bg-header,#4e79a7));color:var(--accent,var(--bg-header,#4e79a7))}'
        + '.rdw-selected{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:24px;margin-bottom:8px}'
        + '.rdw-chip{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;'
        + 'padding:3px 6px 3px 10px;border-radius:999px;white-space:nowrap}'
        + '.rdw-chip-role{color:var(--accent,var(--bg-header,#4e79a7));'
        + 'background:color-mix(in srgb,var(--accent,#4e79a7) 14%,transparent)}'
        + '.rdw-chip-group{color:var(--scope-studio,#7C6FE0);'
        + 'background:color-mix(in srgb,var(--scope-studio,#7C6FE0) 14%,transparent)}'
        + '.rdw-chip-member{font-weight:500;color:var(--blue,#2563eb);'
        + 'background:color-mix(in srgb,var(--blue,#2563eb) 14%,transparent)}'
        + '.rdw-chip-x{background:none!important;border:none!important;cursor:pointer;color:inherit!important;opacity:.65;'
        + 'font-size:13px!important;line-height:1!important;padding:0 1px!important;border-radius:999px!important}'
        + '.rdw-chip-x:hover{opacity:1}'
        + '.rdw-chip-more{font-size:11px!important;padding:3px 9px!important;border-radius:999px!important;cursor:pointer;'
        + 'border:1px solid var(--border,var(--border-color,#ddd))!important;background:none!important;color:var(--text2,var(--text-secondary,#777))}'
        + '.rdw-chip-more:hover{border-color:var(--accent,var(--bg-header,#4e79a7))}'
        + '.rdw-search-results{position:relative;margin-top:4px;max-height:160px;overflow-y:auto;'
        + 'border:1px solid var(--border,var(--border-color,#ddd));border-radius:6px;'
        + 'background:var(--bg-card,var(--bg-main,#fff))}'
        + '.rdw-search-result{display:block;width:100%;text-align:left;padding:6px 8px!important;border:none!important;'
        + 'background:none!important;cursor:pointer;font-size:12px!important;color:var(--text,var(--text-main,#1a1a2e))}'
        + '.rdw-search-result:hover{background:var(--bg-hover,var(--bg,#eef1f3))}'
        + '.rdw-schedule-row{border:1px solid var(--border,var(--border-color,#ddd));border-radius:8px;'
        + 'padding:10px 12px;margin-bottom:8px}'
        + '.rdw-schedule-summary{font-weight:500;margin-bottom:2px}'
        + '.rdw-schedule-meta{color:var(--text2,var(--text-secondary,#777));font-size:11px;margin-bottom:8px}'
        + '.rdw-schedule-actions{display:flex;gap:6px;flex-wrap:wrap}'
        + '.rdw-badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;'
        + 'letter-spacing:.02em;margin-left:6px}'
        + '.rdw-badge.on{color:var(--green,#00b894);background:color-mix(in srgb,var(--green,#00b894) 14%,transparent)}'
        + '.rdw-badge.off{color:var(--text3,var(--text-secondary,#999));background:color-mix(in srgb,var(--text3,#999) 14%,transparent)}'
        + '.rdw-empty{color:var(--text3,var(--text-secondary,#999));font-size:12px;padding:10px 0}'
        + '.rdw-flash{border-radius:6px;padding:8px 12px;font-size:12px;margin-bottom:12px;display:flex;'
        + 'align-items:flex-start;justify-content:space-between;gap:8px}'
        + '.rdw-flash.success{color:var(--green,#00b894);background:color-mix(in srgb,var(--green,#00b894) 14%,transparent)}'
        + '.rdw-flash.error{color:var(--red,#dc2626);background:color-mix(in srgb,var(--red,#dc2626) 14%,transparent)}'
        + '.rdw-flash-close{background:none;border:none;cursor:pointer;color:inherit;font-size:14px;line-height:1;opacity:.7}';

    // The header button (Email delivery/Share/Activity/Export, all in one
    // "Options" dropdown) lives in static/report_menu.js now -- this file
    // registers an item instead of mounting its own button. See autoMount()
    // below.

    var FREQ_OPTIONS = [
        { value: 'daily', label: 'Daily' },
        { value: 'weekdays', label: 'Weekdays' },
        { value: 'weekly', label: 'Weekly' },
        { value: 'monthly', label: 'Monthly' }
    ];
    var WEEKDAY_OPTIONS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

    var ALERT_NOTE = "When this report breaks or recovers, studio developers and admins are emailed automatically — no setup.";

    // ── timezone select (curated list + live hour:minute conversion) ───────
    // A <select>, not free text (apps.reports.models.EmailSchedule.clean()
    // now rejects anything that isn't a real IANA zone server-side; this is
    // the client-side half -- picking from a list instead of typing one).
    var COMMON_TIMEZONES = [
        'UTC',
        'Europe/London',
        'Europe/Amsterdam',
        'Europe/Berlin',
        'Europe/Paris',
        'America/New_York',
        'America/Chicago',
        'America/Denver',
        'America/Los_Angeles',
        'America/Sao_Paulo',
        'Asia/Kolkata',
        'Asia/Dubai',
        'Asia/Shanghai',
        'Asia/Tokyo',
        'Australia/Sydney'
    ];

    // The zones offered always include the schedule's own stored zone and
    // the report's configured default, even when neither is on the curated
    // list -- switching zones must never look like it silently drops the
    // one already in effect.
    function timezoneOptionList(current, reportDefault) {
        var list = COMMON_TIMEZONES.slice();
        [current, reportDefault].forEach(function (tz) {
            if (tz && list.indexOf(tz) === -1) list.push(tz);
        });
        return list;
    }

    function browserTimezone() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        } catch (e) { return ''; }
    }

    // UTC offset of `tz`, in minutes, at the current instant -- "now" is the
    // DST reference the whole conversion uses (per spec: today's date, not
    // the schedule's own send time, which would be circular to resolve).
    // Never throws: an unrecognized/unsupported zone just contributes no
    // shift, so a failure here degrades to "don't reconvert" rather than
    // breaking the form.
    function tzOffsetMinutes(tz) {
        try {
            var parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'shortOffset' })
                .formatToParts(new Date());
            var part = parts.filter(function (p) { return p.type === 'timeZoneName'; })[0];
            var m = part && part.value.match(/GMT([+-])(\d{1,2})(?::?(\d{2}))?/);
            if (!m) return 0;
            var sign = m[1] === '-' ? -1 : 1;
            return sign * ((parseInt(m[2], 10) || 0) * 60 + (m[3] ? parseInt(m[3], 10) : 0));
        } catch (e) { return 0; }
    }

    // What hour:minute in `toTz` names the same instant as hour:minute in
    // `fromTz`, today. Wraps across midnight (only the wall-clock pair is
    // stored, not a date, so 23:30 Tokyo -> 14:30 UTC the same nominal day
    // is exactly as valid a "daily at" as any other).
    function convertHourMinute(hour, minute, fromTz, toTz) {
        if (!fromTz || !toTz || fromTz === toTz) return { hour: hour, minute: minute };
        var total = hour * 60 + minute - tzOffsetMinutes(fromTz) + tzOffsetMinutes(toTz);
        total = ((total % 1440) + 1440) % 1440;
        return { hour: Math.floor(total / 60), minute: total % 60 };
    }

    function hm(hour, minute) { return pad2(hour) + ':' + pad2(minute); }

    // Adds the option if `select` doesn't already have it (a converted
    // minute can land off the [0,15,30,45] quick-pick list the same way an
    // existing schedule's own minute already does at render time).
    function ensureOptionSelected(select, value) {
        var has = Array.prototype.some.call(select.options, function (o) { return parseInt(o.value, 10) === value; });
        if (!has) {
            var opt = document.createElement('option');
            opt.value = String(value);
            opt.textContent = pad2(value);
            select.appendChild(opt);
        }
        select.value = String(value);
    }

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function pad2(n) { return (n < 10 ? '0' : '') + n; }

    // ── cookie / fetch (mirrors static/portal.js + static/assistant.js) ────────
    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return m ? decodeURIComponent(m[1]) : '';
    }

    function apiFetch(url, options) {
        options = options || {};
        options.headers = options.headers || {};
        options.credentials = 'same-origin';
        var method = (options.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD') {
            options.headers['X-CSRFToken'] = getCookie('csrftoken');
        }
        return fetch(url, options).then(function (resp) {
            if (resp.status === 401) window.location = '/login';
            return resp;
        });
    }

    function postJson(url, method, body) {
        return apiFetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function (r) {
            // A non-JSON body (proxy error page, empty 500) must not leave a
            // disabled button stuck forever -- degrade to {} so the caller's
            // `.ok` check still fires the "could not save" flash.
            return r.json().catch(function () { return {}; }).then(function (parsed) {
                return { ok: r.ok, data: parsed };
            });
        });
    }

    // ── shared schedule form (drawer + delivery center) ─────────────────────
    // One implementation behind two call sites: the drawer's own schedule
    // list (open/close, single flow, module state below) and the delivery
    // center's rows (wireDeliveryRow, fully self-contained per row -- no
    // shared state, so several rows can be open at once). Every DOM lookup
    // is scoped to the `root` element the caller passes in, not global ids,
    // so this is safe to instantiate more than once on the same page.
    function roleLabel(role, studioName) {
        if (role === 'admins') return 'All admins';
        if (role === 'developers') return 'All developers';
        if (role === 'everyone') return 'Everyone in ' + studioName;
        return role;
    }

    // Static scaffold for the recipient chip picker -- wireRecipientPicker()
    // fills it in and owns all its interaction after mount. Kept as plain
    // containers (not string-templated per-chip) so add/remove never has to
    // re-run esc()/HTML-build for the whole picker on every click.
    function recipientPickerHtml() {
        // name/id/autocomplete deliberately avoid anything that reads as a
        // login field ("email", "user", "search" alone) plus the vendor
        // ignore attributes -- this is a chip-picker filter box, not a
        // credential field, but "add people by name or email" placeholder
        // text plus a bare text input is exactly the shape password
        // managers heuristically match, so this input got offered a saved
        // login/vault suggestion in the wild.
        return '<div class="rdw-field" data-f="recipients">'
            + '<span class="rdw-label">Recipients</span>'
            + '<div class="rdw-quickadd" data-picker="quickadd"></div>'
            + '<div class="rdw-selected" data-picker="selected"></div>'
            + '<input class="rdw-input" type="text" data-picker="search" '
            + 'name="recipient-search" id="recipient-search" autocomplete="off" '
            + 'data-1p-ignore data-lpignore="true" data-bwignore role="combobox" '
            + 'placeholder="Add people by name or email…">'
            + '<div class="rdw-search-results" data-picker="results" hidden></div>'
            + '</div>';
    }

    // Owns the picker's live state (selected roles/groups/members) inside
    // `root` and returns {getPayload()} for submitScheduleForm to read at
    // submit time. opts: { schedule (existing schedule dict, or null for
    // "new"), chipData: {members, roles, groups, studio_name} }.
    var MEMBER_OVERFLOW_AT = 6;

    function wireRecipientPicker(root, opts) {
        var chipData = opts.chipData || { members: [], roles: {}, groups: [], studio_name: '' };
        var schedule = opts.schedule || null;
        var wrap = root.querySelector('[data-f="recipients"]');
        var quickaddEl = wrap.querySelector('[data-picker="quickadd"]');
        var selectedEl = wrap.querySelector('[data-picker="selected"]');
        var searchEl = wrap.querySelector('[data-picker="search"]');
        var resultsEl = wrap.querySelector('[data-picker="results"]');

        function memberName(id, fallbackEmail) {
            var m = (chipData.members || []).filter(function (x) { return x.id === id; })[0];
            return (m && m.name) || fallbackEmail;
        }

        var selRoles = schedule ? (schedule.recipient_roles || []).slice() : [];
        var selGroups = schedule
            ? (schedule.recipient_groups || []).map(function (g) { return { id: g.id, name: g.name }; })
            : [];
        var selMembers = schedule
            ? schedule.recipients.map(function (r) { return { id: r.id, email: r.email, name: memberName(r.id, r.email) }; })
            : [];
        var membersExpanded = false;

        function roleCount(role) { return (chipData.roles || {})[role] || 0; }

        function renderQuickAdd() {
            var html = '';
            ['admins', 'developers', 'everyone'].forEach(function (role) {
                if (selRoles.indexOf(role) !== -1) return;
                html += '<button type="button" class="rdw-chip-add" data-add-role="' + role + '">'
                    + esc(roleLabel(role, chipData.studio_name)) + ' (' + roleCount(role) + ')</button>';
            });
            (chipData.groups || []).forEach(function (g) {
                if (selGroups.some(function (sg) { return sg.id === g.id; })) return;
                html += '<button type="button" class="rdw-chip-add" data-add-group="' + g.id + '">'
                    + 'Group: ' + esc(g.name) + ' (' + g.count + ')</button>';
            });
            quickaddEl.innerHTML = html;
        }

        function chipHtml(cls, label, removeAttr) {
            return '<span class="rdw-chip ' + cls + '">' + esc(label)
                + '<button type="button" class="rdw-chip-x" ' + removeAttr + ' aria-label="Remove ' + esc(label) + '">&times;</button></span>';
        }

        function renderSelected() {
            var html = '';
            selRoles.forEach(function (role) {
                html += chipHtml('rdw-chip-role', roleLabel(role, chipData.studio_name), 'data-remove-role="' + role + '"');
            });
            selGroups.forEach(function (g) {
                html += chipHtml('rdw-chip-group', 'Group: ' + g.name, 'data-remove-group="' + g.id + '"');
            });
            var visible = membersExpanded ? selMembers : selMembers.slice(0, MEMBER_OVERFLOW_AT);
            visible.forEach(function (m) {
                html += chipHtml('rdw-chip-member', m.name, 'data-remove-member="' + m.id + '"');
            });
            if (!membersExpanded && selMembers.length > MEMBER_OVERFLOW_AT) {
                html += '<button type="button" class="rdw-chip-more" data-expand-members>+'
                    + (selMembers.length - MEMBER_OVERFLOW_AT) + ' more…</button>';
            }
            if (!selRoles.length && !selGroups.length && !selMembers.length) {
                html += '<span class="rdw-help">No recipients selected yet.</span>';
            }
            selectedEl.innerHTML = html;
        }

        function renderAll() { renderQuickAdd(); renderSelected(); }

        quickaddEl.addEventListener('click', function (e) {
            var roleBtn = e.target.closest('[data-add-role]');
            if (roleBtn) { selRoles.push(roleBtn.getAttribute('data-add-role')); renderAll(); return; }
            var groupBtn = e.target.closest('[data-add-group]');
            if (groupBtn) {
                var gid = parseInt(groupBtn.getAttribute('data-add-group'), 10);
                var g = (chipData.groups || []).filter(function (x) { return x.id === gid; })[0];
                if (g) { selGroups.push({ id: g.id, name: g.name }); renderAll(); }
            }
        });

        selectedEl.addEventListener('click', function (e) {
            if (e.target.closest('[data-expand-members]')) { membersExpanded = true; renderSelected(); return; }
            var rmRole = e.target.closest('[data-remove-role]');
            if (rmRole) {
                var role = rmRole.getAttribute('data-remove-role');
                selRoles = selRoles.filter(function (r) { return r !== role; });
                renderAll();
                return;
            }
            var rmGroup = e.target.closest('[data-remove-group]');
            if (rmGroup) {
                var gid = parseInt(rmGroup.getAttribute('data-remove-group'), 10);
                selGroups = selGroups.filter(function (g) { return g.id !== gid; });
                renderAll();
                return;
            }
            var rmMember = e.target.closest('[data-remove-member]');
            if (rmMember) {
                var mid = parseInt(rmMember.getAttribute('data-remove-member'), 10);
                selMembers = selMembers.filter(function (m) { return m.id !== mid; });
                renderSelected();
            }
        });

        searchEl.addEventListener('input', function () {
            var q = searchEl.value.trim().toLowerCase();
            if (!q) { resultsEl.hidden = true; resultsEl.innerHTML = ''; return; }
            var picked = {};
            selMembers.forEach(function (m) { picked[m.id] = true; });
            var matches = (chipData.members || []).filter(function (m) {
                if (picked[m.id]) return false;
                return (m.name && m.name.toLowerCase().indexOf(q) !== -1)
                    || (m.email && m.email.toLowerCase().indexOf(q) !== -1);
            }).slice(0, 8);
            if (!matches.length) { resultsEl.hidden = true; resultsEl.innerHTML = ''; return; }
            resultsEl.innerHTML = matches.map(function (m) {
                return '<button type="button" class="rdw-search-result" data-pick-member="' + m.id + '">'
                    + esc(m.name || m.email) + ' <span class="rdw-help">' + esc(m.email) + '</span></button>';
            }).join('');
            resultsEl.hidden = false;
        });

        resultsEl.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-pick-member]');
            if (!btn) return;
            var mid = parseInt(btn.getAttribute('data-pick-member'), 10);
            var m = (chipData.members || []).filter(function (x) { return x.id === mid; })[0];
            if (m) selMembers.push({ id: m.id, email: m.email, name: m.name || m.email });
            searchEl.value = '';
            resultsEl.hidden = true;
            resultsEl.innerHTML = '';
            renderSelected();
            searchEl.focus();
        });

        renderAll();

        return {
            getPayload: function () {
                return {
                    recipient_ids: selMembers.map(function (m) { return m.id; }),
                    recipient_roles: selRoles.slice(),
                    recipient_group_ids: selGroups.map(function (g) { return g.id; })
                };
            }
        };
    }

    function scheduleFormHtml(opts) {
        var schedule = opts.schedule || null;
        var freq = schedule ? schedule.freq : 'daily';
        var hour = schedule ? schedule.send_hour : 8;
        var minute = schedule ? schedule.send_minute : 0;
        var weekday = schedule ? schedule.weekday : 0;
        var monthDay = schedule ? schedule.month_day : 1;
        // New schedule: default to the browser's own zone (what the person
        // filling the form actually experiences as "9am"), falling back to
        // the report's configured default, then UTC. Editing an existing
        // schedule always keeps what it was already set to.
        var timezone = schedule ? schedule.timezone : (browserTimezone() || opts.defaultTimezone || 'UTC');
        var attachPdf = schedule ? schedule.attach_pdf : false;
        var enabled = schedule ? schedule.enabled : true;

        var hourOpts = '';
        for (var h = 0; h < 24; h++) hourOpts += '<option value="' + h + '"' + (h === hour ? ' selected' : '') + '>' + pad2(h) + '</option>';
        var minuteChoices = [0, 15, 30, 45];
        if (minuteChoices.indexOf(minute) === -1) minuteChoices.push(minute);
        minuteChoices.sort(function (a, b) { return a - b; });
        var minuteOpts = minuteChoices.map(function (m) { return '<option value="' + m + '"' + (m === minute ? ' selected' : '') + '>' + pad2(m) + '</option>'; }).join('');
        var freqOpts = FREQ_OPTIONS.map(function (f) { return '<option value="' + f.value + '"' + (f.value === freq ? ' selected' : '') + '>' + f.label + '</option>'; }).join('');
        var weekdayOpts = WEEKDAY_OPTIONS.map(function (w, i) { return '<option value="' + i + '"' + (i === weekday ? ' selected' : '') + '>' + w + '</option>'; }).join('');
        var monthDayOpts = '';
        for (var d = 1; d <= 28; d++) monthDayOpts += '<option value="' + d + '"' + (d === monthDay ? ' selected' : '') + '>' + d + '</option>';
        var tzOpts = timezoneOptionList(timezone, opts.defaultTimezone).map(function (tz) {
            return '<option value="' + esc(tz) + '"' + (tz === timezone ? ' selected' : '') + '>' + esc(tz) + '</option>';
        }).join('');

        return ''
            + '<form data-schedule-form>'
            + recipientPickerHtml()
            + '<div class="rdw-row2">'
            + '<div class="rdw-field"><label class="rdw-label">Frequency</label><select class="rdw-select" data-f="freq">' + freqOpts + '</select></div>'
            + '<div class="rdw-field" data-f="weekdayField" style="display:' + (freq === 'weekly' ? '' : 'none') + '">'
            + '<label class="rdw-label">Day</label><select class="rdw-select" data-f="weekday">' + weekdayOpts + '</select></div>'
            + '<div class="rdw-field" data-f="monthDayField" style="display:' + (freq === 'monthly' ? '' : 'none') + '">'
            + '<label class="rdw-label">Day of month</label><select class="rdw-select" data-f="monthDay">' + monthDayOpts + '</select></div>'
            + '</div>'
            + '<div class="rdw-row2">'
            + '<div class="rdw-field"><label class="rdw-label">Hour</label><select class="rdw-select" data-f="hour">' + hourOpts + '</select></div>'
            + '<div class="rdw-field"><label class="rdw-label">Minute</label><select class="rdw-select" data-f="minute">' + minuteOpts + '</select></div>'
            + '<div class="rdw-field"><label class="rdw-label">Timezone</label>'
            + '<select class="rdw-select" data-f="timezone">' + tzOpts + '</select>'
            + '<div class="rdw-help" data-f="tzCaption"></div></div>'
            + '</div>'
            + '<label class="rdw-check-row"><input type="checkbox" data-f="attachPdf"' + (attachPdf ? ' checked' : '') + '>'
            + '<span class="rdw-check-label">Also attach as PDF</span></label>'
            + '<label class="rdw-check-row"><input type="checkbox" data-f="enabled"' + (enabled ? ' checked' : '') + '>'
            + '<span class="rdw-check-label">Enabled</span></label>'
            + '<div data-f="errors"></div>'
            + '<div style="display:flex;gap:8px;margin-top:10px">'
            + '<button type="submit" class="rdw-btn primary">' + (schedule ? 'Save changes' : 'Create schedule') + '</button>'
            + '<button type="button" class="rdw-btn" data-f="cancel">Cancel</button>'
            + '</div>'
            + '</form>';
    }

    // Wires the form markup scheduleFormHtml() just inserted into `root`.
    // ctx: { apiBase, editingId (schedule pk, or null for "new"), schedule
    //        (existing schedule dict, or null), chipData (see
    //        wireRecipientPicker), onSaved(schedule, wasEditing), onCancel(),
    //        onFlash(msg, type) }
    function wireScheduleForm(root, ctx) {
        root.querySelector('[data-f="freq"]').addEventListener('change', function () {
            var v = this.value;
            root.querySelector('[data-f="weekdayField"]').style.display = v === 'weekly' ? '' : 'none';
            root.querySelector('[data-f="monthDayField"]').style.display = v === 'monthly' ? '' : 'none';
        });
        root.querySelector('[data-f="cancel"]').addEventListener('click', function () { ctx.onCancel(); });

        // Timezone select: switching zones recalculates the shown hour:minute
        // so the actual send INSTANT stays put -- 09:00 Europe/Amsterdam
        // becomes 08:00 UTC, not "09:00 UTC" (a silent hour shift). Whatever
        // hour/minute/zone the form shows at submit time is exactly what
        // gets stored (apps.reports.views._apply_schedule_fields takes the
        // three fields as given, with no server-side re-derivation).
        var tzSelect = root.querySelector('[data-f="timezone"]');
        var hourSelect = root.querySelector('[data-f="hour"]');
        var minuteSelect = root.querySelector('[data-f="minute"]');
        var tzCaption = root.querySelector('[data-f="tzCaption"]');
        var lastTz = tzSelect.value;

        function refreshCaption() {
            var tz = tzSelect.value;
            var hour = parseInt(hourSelect.value, 10);
            var minute = parseInt(minuteSelect.value, 10);
            var toUtc = convertHourMinute(hour, minute, tz, 'UTC');
            var toMine = convertHourMinute(hour, minute, tz, browserTimezone() || 'UTC');
            tzCaption.textContent = '= ' + hm(toUtc.hour, toUtc.minute) + ' UTC · '
                + hm(toMine.hour, toMine.minute) + ' your time';
        }

        tzSelect.addEventListener('change', function () {
            var newTz = tzSelect.value;
            var converted = convertHourMinute(
                parseInt(hourSelect.value, 10), parseInt(minuteSelect.value, 10), lastTz, newTz
            );
            ensureOptionSelected(hourSelect, converted.hour);
            ensureOptionSelected(minuteSelect, converted.minute);
            lastTz = newTz;
            refreshCaption();
        });
        hourSelect.addEventListener('change', refreshCaption);
        minuteSelect.addEventListener('change', refreshCaption);
        refreshCaption();

        // Stashed on `root` (not module state -- several delivery-center rows
        // can be open at once) so submitScheduleForm can read the picker's
        // live selection without re-parsing the DOM.
        root._rdwRecipientPicker = wireRecipientPicker(root, { schedule: ctx.schedule, chipData: ctx.chipData });
        root.querySelector('[data-schedule-form]').addEventListener('submit', function (e) {
            e.preventDefault();
            submitScheduleForm(root, ctx);
        });
    }

    function submitScheduleForm(root, ctx) {
        var recipients = root._rdwRecipientPicker
            ? root._rdwRecipientPicker.getPayload()
            : { recipient_ids: [], recipient_roles: [], recipient_group_ids: [] };
        var body = {
            recipient_ids: recipients.recipient_ids,
            recipient_roles: recipients.recipient_roles,
            recipient_group_ids: recipients.recipient_group_ids,
            freq: root.querySelector('[data-f="freq"]').value,
            send_hour: parseInt(root.querySelector('[data-f="hour"]').value, 10),
            send_minute: parseInt(root.querySelector('[data-f="minute"]').value, 10),
            weekday: parseInt(root.querySelector('[data-f="weekday"]').value, 10),
            month_day: parseInt(root.querySelector('[data-f="monthDay"]').value, 10),
            timezone: root.querySelector('[data-f="timezone"]').value.trim() || 'UTC',
            attach_pdf: root.querySelector('[data-f="attachPdf"]').checked,
            enabled: root.querySelector('[data-f="enabled"]').checked
        };
        var url = ctx.editingId ? ctx.apiBase + '/schedules/' + ctx.editingId : ctx.apiBase + '/schedules';
        var submitBtn = root.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        postJson(url, 'POST', body).then(function (res) {
            submitBtn.disabled = false;
            if (!res.ok) {
                var errs = res.data.errors || {};
                var msgs = Object.keys(errs).map(function (k) { return k + ': ' + errs[k].join(' '); });
                var errBox = root.querySelector('[data-f="errors"]');
                errBox.innerHTML = msgs.length ? '<div class="rdw-field-error">' + esc(msgs.join(' — ')) + '</div>' : '';
                if (!msgs.length && ctx.onFlash) ctx.onFlash(res.data.error || 'Could not save the schedule.', 'error');
                return;
            }
            ctx.onSaved(res.data.schedule, !!ctx.editingId);
        }).catch(function () {
            submitBtn.disabled = false;
            if (ctx.onFlash) ctx.onFlash('Could not save the schedule — check your connection and try again.', 'error');
        });
    }

    // ── state (drawer only -- the delivery center keeps no module state,
    // see wireDeliveryRow) ──────────────────────────────────────────────
    var state = null;         // { prefix, slug, name, triggerEl }
    var data = null;          // last GET /schedules payload
    // Report API URL -> eligible recipients. Refresh on each open/edit because
    // group access can change while the page remains open.
    var membersCache = {};
    var editingId = null;     // schedule pk being edited, or null for "new"
    var built = false;

    function apiBase() { return state.prefix + '/api/reports/' + encodeURIComponent(state.slug); }

    var EMPTY_CHIP_DATA = { members: [], roles: {}, groups: [], studio_name: '' };

    function loadMembersFor(reportApiBase, cb) {
        apiFetch(reportApiBase + '/recipients').then(function (r) { return r.ok ? r.json() : EMPTY_CHIP_DATA; })
            .then(function (d) {
                membersCache[reportApiBase] = {
                    members: d.members || [], roles: d.roles || {}, groups: d.groups || [],
                    studio_name: d.studio_name || ''
                };
                cb(membersCache[reportApiBase]);
            }).catch(function () {
                membersCache[reportApiBase] = EMPTY_CHIP_DATA;
                cb(EMPTY_CHIP_DATA);
            });
    }

    // ── style injection ──────────────────────────────────────────────────
    // The header button that used to make this eager (report-page autoMount
    // creating a button before its CSS existed -- the FOUC bug) now lives in
    // static/report_menu.js instead; this file only ever shows UI it creates
    // and displays in the same call (ensureDom() then immediately open()),
    // so lazy injection from ensureDom() is sufficient here. injectStyle()
    // stays idempotent regardless.
    var styleInjected = false;
    function injectStyle() {
        if (styleInjected) return;
        styleInjected = true;
        var style = document.createElement('style');
        style.id = 'rdwStyle';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    // ── DOM scaffold (built once, lazily) ───────────────────────────────────
    // Called by both the drawer (open) and the delivery center (mountCenter)
    // -- either entry point needs the injected <style>, only the drawer
    // needs the overlay/panel elements it also creates here.
    function ensureDom() {
        if (built) return;
        built = true;
        injectStyle();

        var overlay = document.createElement('div');
        overlay.id = 'rdwOverlay';
        overlay.addEventListener('click', close);
        document.body.appendChild(overlay);

        var drawer = document.createElement('div');
        drawer.id = 'rdwDrawer';
        drawer.setAttribute('role', 'dialog');
        drawer.setAttribute('aria-modal', 'true');
        drawer.setAttribute('aria-labelledby', 'rdwTitle');
        drawer.innerHTML =
            '<div class="rdw-header">'
            + '<div><h2 class="rdw-title" id="rdwTitle">Deliveries</h2><div class="rdw-subtitle" id="rdwSubtitle"></div></div>'
            + '<button type="button" class="rdw-close" id="rdwClose" title="Close" aria-label="Close">&times;</button>'
            + '</div>'
            + '<div class="rdw-body" id="rdwBody"></div>';
        document.body.appendChild(drawer);

        document.getElementById('rdwClose').addEventListener('click', close);
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (!drawer.classList.contains('open')) return;
            e.stopPropagation();
            close();
        });
        drawer.addEventListener('keydown', trapTab);
    }

    function trapTab(e) {
        if (e.key !== 'Tab') return;
        var drawer = document.getElementById('rdwDrawer');
        var nodes = drawer.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        var list = Array.prototype.filter.call(nodes, function (el) { return !el.disabled && el.offsetParent !== null; });
        if (!list.length) return;
        var first = list[0], last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }

    // ── open / close ─────────────────────────────────────────────────────
    // Force a reflow so the browser has actually painted the CLOSED state
    // at least once, then defer adding .open by two animation frames.
    // Skipping this is exactly the "first open doesn't animate" bug:
    // without it, an element's closed style and its open style can land in
    // the same paint, leaving nothing for the transition to interpolate
    // between -- reflow alone is usually enough, the double rAF is extra
    // insurance across browsers that don't commit a reflow's result before
    // the next same-frame class change.
    function playOpen(overlay, drawer) {
        if (!overlay || !drawer) return;
        void drawer.offsetHeight;
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                overlay.classList.add('open');
                drawer.classList.add('open');
            });
        });
    }

    function open(prefix, slug, name, triggerEl) {
        ensureDom();
        state = { prefix: prefix, slug: slug, name: name, triggerEl: triggerEl || document.activeElement };
        editingId = null;
        document.getElementById('rdwSubtitle').textContent = name || slug;
        document.getElementById('rdwBody').innerHTML = '<div class="rdw-empty">Loading…</div>';
        playOpen(document.getElementById('rdwOverlay'), document.getElementById('rdwDrawer'));
        var closeBtn = document.getElementById('rdwClose');
        if (closeBtn) closeBtn.focus();
        loadAndRender();
    }

    function close() {
        var overlay = document.getElementById('rdwOverlay');
        var drawer = document.getElementById('rdwDrawer');
        if (!drawer || !drawer.classList.contains('open')) return;
        overlay.classList.remove('open');
        drawer.classList.remove('open');
        if (state && state.triggerEl && typeof state.triggerEl.focus === 'function') {
            state.triggerEl.focus();
        }
    }

    // ── data loading ─────────────────────────────────────────────────────
    function loadAndRender() {
        apiFetch(apiBase() + '/schedules').then(function (r) {
            if (!r.ok) throw new Error('failed to load');
            return r.json();
        }).then(function (d) {
            data = d;
            loadMembersFor(apiBase(), function () { render(); });
        }).catch(function () {
            document.getElementById('rdwBody').innerHTML =
                '<div class="rdw-flash error">Could not load email settings.</div>';
        });
    }

    // ── flash (self-contained: no alert(), errors persist, success fades) ──
    function flash(msg, type) {
        var body = document.getElementById('rdwBody');
        var old = body.querySelector('.rdw-flash');
        if (old) old.remove();
        var div = document.createElement('div');
        div.className = 'rdw-flash ' + type;
        div.setAttribute('role', type === 'error' ? 'alert' : 'status');
        div.innerHTML = '<span>' + esc(msg) + '</span><button type="button" class="rdw-flash-close" aria-label="Dismiss">&times;</button>';
        div.querySelector('.rdw-flash-close').addEventListener('click', function () { div.remove(); });
        body.insertBefore(div, body.firstChild);
        if (type !== 'error') setTimeout(function () { if (div.parentNode) div.remove(); }, 4000);
    }

    // ── envelope state (dashboard row + report-page Options menu item) ─────
    // "Subscribed" here means "this user has a schedule on this report".
    // Called after every load/mutation so the report-page menu item's label
    // and the dashboard's own envelope (a separate document, updated via
    // the custom event) never drift from what the drawer shows.
    function _notifySubscriptionChange() {
        if (!state || !data) return;
        // subscribed_count (enabled + this user a resolved recipient) is
        // what api_my_subscriptions counts for the badge -- NOT
        // data.schedules.length, which since the drawer started listing
        // every schedule the user can see (not just their own) can include
        // rows they don't own and disabled rows that reach nobody. The
        // broadcast count must always match the badge it's replacing.
        var count = typeof data.subscribed_count === 'number' ? data.subscribed_count : (data.schedules || []).length;
        var subscribed = count > 0;
        if (window.__reportMenu) {
            // Label-only update: merges onto whatever autoMount() already
            // registered (id + order + onSelect), see report_menu.js
            // ::register()'s merge behavior.
            window.__reportMenu.register({
                id: 'email-delivery',
                label: subscribed ? 'Email delivery (' + count + ')' : 'Email delivery'
            });
        }
        try {
            window.dispatchEvent(new CustomEvent('rdw:subscription-change', {
                detail: { slug: state.slug, subscribed: subscribed, count: count }
            }));
        } catch (e) { /* no CustomEvent constructor (ancient browser): skip, not fatal */ }
    }

    // ── render ───────────────────────────────────────────────────────────
    function scheduleSummary(s) {
        var freqLabel = (FREQ_OPTIONS.filter(function (f) { return f.value === s.freq; })[0] || {}).label || s.freq;
        var when = pad2(s.send_hour) + ':' + pad2(s.send_minute);
        if (s.freq === 'weekly') when += ' on ' + (WEEKDAY_OPTIONS[s.weekday] || '?');
        if (s.freq === 'monthly') when += ' on day ' + s.month_day;
        return freqLabel + ' at ' + when + ' ' + s.timezone;
    }

    function render() {
        var body = document.getElementById('rdwBody');

        var html = '';
        html += '<div class="rdw-section"><h3 class="rdw-section-title">Scheduled delivery</h3><div id="rdwScheduleList">';
        if (!data.schedules.length) {
            html += '<div class="rdw-empty">No scheduled deliveries yet.</div>';
        } else {
            data.schedules.forEach(function (s) {
                // Non-owned rows (someone else's schedule that reaches this
                // user via a role/group chip) render read-only: the server
                // already refuses edit/sample/delete for a schedule this
                // user didn't create (created_by-scoped lookups in
                // apps.reports.views), so offering those buttons here would
                // just be a dead click.
                var ownerNote = s.editable ? '' : ' &middot; set up by ' + esc(s.owner_name);
                html += '<div class="rdw-schedule-row" data-id="' + s.id + '">'
                    + '<div class="rdw-schedule-summary">' + esc(scheduleSummary(s))
                    + '<span class="rdw-badge ' + (s.enabled ? 'on">On' : 'off">Off') + '</span></div>'
                    + '<div class="rdw-schedule-meta">' + esc(s.recipient_summary)
                    + (s.attach_pdf ? ' &middot; PDF attached' : '') + ownerNote + '</div>';
                if (s.editable) {
                    html += '<div class="rdw-schedule-actions">'
                        + '<button type="button" class="rdw-btn small" data-edit="' + s.id + '">Edit</button>'
                        + '<button type="button" class="rdw-btn small" data-sample="' + s.id + '">Send sample</button>'
                        + '<button type="button" class="rdw-btn small danger" data-delete="' + s.id + '">Delete</button>'
                        + '</div>';
                }
                html += '</div>';
            });
        }
        html += '</div>'
            + '<button type="button" class="rdw-btn" id="rdwNewSchedule">+ New schedule</button>'
            + '<div id="rdwFormWrap"></div>'
            + '</div>';

        html += '<div class="rdw-section"><p class="rdw-note">' + esc(ALERT_NOTE) + '</p></div>';

        body.innerHTML = html;
        wireStaticControls();
        _notifySubscriptionChange();
    }

    function wireStaticControls() {
        document.getElementById('rdwNewSchedule').addEventListener('click', function () {
            editingId = null;
            renderForm(null);
        });

        document.getElementById('rdwScheduleList').addEventListener('click', function (e) {
            var editBtn = e.target.closest('[data-edit]');
            if (editBtn) {
                var s = data.schedules.filter(function (x) { return String(x.id) === editBtn.dataset.edit; })[0];
                editingId = s.id;
                renderForm(s);
                return;
            }
            var sampleBtn = e.target.closest('[data-sample]');
            if (sampleBtn) {
                sampleBtn.disabled = true;
                postJson(apiBase() + '/schedules/' + sampleBtn.dataset.sample + '/sample', 'POST', {}).then(function (res) {
                    sampleBtn.disabled = false;
                    // The endpoint kicks the actual send off in the
                    // background and answers right away (202) -- the
                    // message it returns already says so; the fallback
                    // here only fires if a proxy/500 ate the body.
                    flash(res.ok ? (res.data.message || 'Sending — check your inbox shortly.') : (res.data.error || 'Could not send sample.'), res.ok ? 'success' : 'error');
                }).catch(function () {
                    sampleBtn.disabled = false;
                    flash('Could not send the sample — check your connection and try again.', 'error');
                });
                return;
            }
            var delBtn = e.target.closest('[data-delete]');
            if (delBtn) {
                if (!window.confirm('Delete this schedule? This cannot be undone.')) return;
                delBtn.disabled = true;
                apiFetch(apiBase() + '/schedules/' + delBtn.dataset.delete, { method: 'DELETE' }).then(function (r) {
                    if (!r.ok) { delBtn.disabled = false; flash('Could not delete the schedule.', 'error'); return; }
                    data.schedules = data.schedules.filter(function (x) { return String(x.id) !== delBtn.dataset.delete; });
                    render();
                    flash('Schedule deleted.', 'success');
                }).catch(function () {
                    delBtn.disabled = false;
                    flash('Could not delete the schedule — check your connection and try again.', 'error');
                });
            }
        });
    }

    // ── create/edit form (drawer) ────────────────────────────────────────
    function renderForm(schedule) {
        var wrap = document.getElementById('rdwFormWrap');
        var chipData = membersCache[apiBase()] || EMPTY_CHIP_DATA;
        wrap.innerHTML =
            '<div class="rdw-section" style="border-top:1px solid var(--border,var(--border-color,#ddd));padding-top:14px;margin-top:14px">'
            + '<h3 class="rdw-section-title">' + (schedule ? 'Edit schedule' : 'New schedule') + '</h3>'
            + scheduleFormHtml({ schedule: schedule, chipData: chipData, defaultTimezone: data.default_timezone })
            + '</div>';
        wireScheduleForm(wrap, {
            apiBase: apiBase(),
            editingId: editingId,
            schedule: schedule,
            chipData: chipData,
            onFlash: flash,
            onCancel: function () { editingId = null; wrap.innerHTML = ''; },
            onSaved: function (saved, wasEditing) {
                if (wasEditing) {
                    data.schedules = data.schedules.map(function (s) { return s.id === saved.id ? saved : s; });
                } else {
                    data.schedules.push(saved);
                }
                editingId = null;
                render();
                flash(wasEditing ? 'Schedule updated.' : 'Schedule created.', 'success');
            }
        });
    }

    // ── delivery center (templates/reports/my_deliveries.html) ─────────────
    // Each server-rendered <tr data-delivery-row> is followed by a
    // <tr data-delivery-detail> holding a flash slot, a form slot, and a
    // <script type="application/json" data-schedule-json> with that row's
    // schedule (same shape as apps.reports.views._schedule_dict). Full
    // create is out of scope here -- creation happens from a report --
    // so this only wires edit / enable-disable / delete / sample on rows
    // that already exist. No shared module state: each row closes over its
    // own schedule object, so several can be expanded at once.
    function mountCenter(root) {
        ensureDom();
        root = root || document;
        var rows = root.querySelectorAll('[data-delivery-row]');
        Array.prototype.forEach.call(rows, wireDeliveryRow);
    }

    function wireDeliveryRow(row) {
        var detail = row.nextElementSibling;
        if (!detail) return;
        var flashSlot = detail.querySelector('[data-flash-slot]');
        var formSlot = detail.querySelector('[data-form-slot]');
        var scheduleEl = detail.querySelector('[data-schedule-json]');
        var scheduleData;
        try { scheduleData = JSON.parse(scheduleEl.textContent); } catch (e) { return; }

        var prefix = row.getAttribute('data-prefix');
        var slug = row.getAttribute('data-slug');
        var rowApiBase = prefix + '/api/reports/' + encodeURIComponent(slug);

        function syncVisibility() {
            detail.hidden = !(formSlot.innerHTML || flashSlot.innerHTML);
        }

        function rowFlash(msg, type) {
            flashSlot.innerHTML = '<div class="rdw-flash ' + type + '"><span>' + esc(msg) + '</span></div>';
            syncVisibility();
            if (type !== 'error') setTimeout(function () { flashSlot.innerHTML = ''; syncVisibility(); }, 4000);
        }

        function applySchedule(saved) {
            scheduleData = saved;
            scheduleEl.textContent = JSON.stringify(saved);
            var cadenceCell = row.querySelector('[data-field="cadence"]');
            var formatCell = row.querySelector('[data-field="format"]');
            var recipientsCell = row.querySelector('[data-field="recipients"]');
            var statusCell = row.querySelector('[data-field="status"]');
            var enableBtn = row.querySelector('[data-toggle-enable]');
            if (cadenceCell) cadenceCell.textContent = scheduleSummary(saved);
            if (formatCell) formatCell.textContent = saved.attach_pdf ? 'HTML + PDF' : 'HTML';
            if (recipientsCell) recipientsCell.textContent = saved.recipient_summary;
            if (statusCell) {
                statusCell.innerHTML = saved.enabled
                    ? '<span class="ui-badge pass">Enabled</span>'
                    : '<span class="ui-badge warn">Disabled</span>';
            }
            if (enableBtn) enableBtn.textContent = saved.enabled ? 'Disable' : 'Enable';
        }

        var editBtn = row.querySelector('[data-toggle-edit]');
        if (editBtn) {
            editBtn.addEventListener('click', function () {
                if (formSlot.innerHTML) {
                    formSlot.innerHTML = '';
                    editBtn.textContent = 'Edit';
                    syncVisibility();
                    return;
                }
                loadMembersFor(rowApiBase, function (chipData) {
                    formSlot.innerHTML = scheduleFormHtml({ schedule: scheduleData, chipData: chipData });
                    wireScheduleForm(formSlot, {
                        apiBase: rowApiBase,
                        editingId: scheduleData.id,
                        schedule: scheduleData,
                        chipData: chipData,
                        onFlash: rowFlash,
                        onCancel: function () {
                            formSlot.innerHTML = '';
                            editBtn.textContent = 'Edit';
                            syncVisibility();
                        },
                        onSaved: function (saved) {
                            applySchedule(saved);
                            formSlot.innerHTML = '';
                            editBtn.textContent = 'Edit';
                            rowFlash('Schedule updated.', 'success');
                        }
                    });
                    editBtn.textContent = 'Cancel';
                    syncVisibility();
                });
            });
        }

        var enableBtn = row.querySelector('[data-toggle-enable]');
        if (enableBtn) {
            enableBtn.addEventListener('click', function () {
                var next = !scheduleData.enabled;
                enableBtn.disabled = true;
                postJson(rowApiBase + '/schedules/' + scheduleData.id, 'POST', { enabled: next }).then(function (res) {
                    enableBtn.disabled = false;
                    if (!res.ok) { rowFlash('Could not update the schedule.', 'error'); return; }
                    applySchedule(res.data.schedule);
                    rowFlash(res.data.schedule.enabled ? 'Schedule enabled.' : 'Schedule disabled.', 'success');
                });
            });
        }

        var sampleBtn = row.querySelector('[data-send-sample]');
        if (sampleBtn) {
            sampleBtn.addEventListener('click', function () {
                sampleBtn.disabled = true;
                postJson(rowApiBase + '/schedules/' + scheduleData.id + '/sample', 'POST', {}).then(function (res) {
                    sampleBtn.disabled = false;
                    // See the matching drawer handler above: the endpoint
                    // answers 202 immediately and its own message already
                    // reflects the background send.
                    rowFlash(res.ok ? (res.data.message || 'Sending — check your inbox shortly.') : (res.data.error || 'Could not send sample.'), res.ok ? 'success' : 'error');
                }).catch(function () {
                    sampleBtn.disabled = false;
                    rowFlash('Could not send the sample — check your connection and try again.', 'error');
                });
            });
        }

        var deleteBtn = row.querySelector('[data-delete-row]');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', function () {
                if (!window.confirm('Delete this schedule? This cannot be undone.')) return;
                deleteBtn.disabled = true;
                apiFetch(rowApiBase + '/schedules/' + scheduleData.id, { method: 'DELETE' }).then(function (r) {
                    if (!r.ok) { deleteBtn.disabled = false; rowFlash('Could not delete the schedule.', 'error'); return; }
                    row.remove();
                    detail.remove();
                }).catch(function () {
                    deleteBtn.disabled = false;
                    rowFlash('Could not delete the schedule — check your connection and try again.', 'error');
                });
            });
        }
    }

    window.ReportDelivery = { open: open, close: close, mountCenter: mountCenter };

    // ── report-page auto-mount ──────────────────────────────────────────
    // Only runs when this file is loaded on a built report page (no
    // window.PORTAL_CTX -- that means the dashboard, which wires its own
    // per-row button instead; see static/portal.js). Registers a
    // "Deliveries" item on the shared Options menu (static/report_menu.js)
    // rather than mounting its own header button.
    var _registered = false;
    function autoMount() {
        if (window.PORTAL_CTX) return;
        var m = window.location.pathname.match(/^(\/s\/[a-z0-9-]+\/[a-z0-9-]+)\/r\/([A-Za-z0-9_-]+)\//);
        if (!m) return;
        var prefix = m[1], slug = m[2];
        var mount = function () {
            if (_registered || !window.__reportMenu) return;
            _registered = true;

            var openNow = function () {
                var h1 = document.querySelector('.fw-header h1');
                var name = h1 ? h1.textContent : document.title;
                open(prefix, slug, name, window.__reportMenu.getTrigger());
            };

            // No permission gate here (unlike Share/Activity): any studio
            // member can set up their own delivery schedule, so this item
            // registers unconditionally.
            window.__reportMenu.register({
                id: 'email-delivery', order: 30, label: 'Deliveries', onSelect: openNow,
                icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="4.5" width="15" height="11" rx="2"/><path d="M3.2 5.5l6.8 5.2 6.8-5.2"/></svg>'
            });

            // Initial label: one bulk call (static/portal.js's dashboard
            // makes the same call) rather than the full /schedules payload
            // just to learn a count. Contract: {"reports": {"<slug>":
            // {"schedules": N}}}. _notifySubscriptionChange() keeps the
            // label in sync after this from every load/mutation inside the
            // drawer itself.
            apiFetch(prefix + '/api/my-subscriptions')
                .then(function (r) { return r.ok ? r.json() : { reports: {} }; })
                .then(function (d) {
                    var info = (d.reports || {})[slug];
                    var count = info ? (info.schedules || 0) : 0;
                    window.__reportMenu.register({
                        id: 'email-delivery',
                        label: count > 0 ? 'Deliveries (' + count + ')' : 'Deliveries'
                    });
                }).catch(function () {});

            // "My deliveries" (templates/reports/my_deliveries.html) used to
            // link its Edit actions here with ?rdw=1 so "edit" opened the
            // drawer instead of just landing on the report -- editing is now
            // inline on that page (mountCenter), but an old link or bookmark
            // carrying the param should still work rather than silently do
            // nothing. Strip the param afterwards so a refresh does not
            // reopen it.
            if (/(^|[?&])rdw=1(&|$)/.test(window.location.search)) {
                openNow();
                if (window.history && window.history.replaceState) {
                    var params = new URLSearchParams(window.location.search);
                    params.delete('rdw');
                    var qs = params.toString();
                    window.history.replaceState(
                        {}, '',
                        window.location.pathname + (qs ? '?' + qs : '') + window.location.hash
                    );
                }
            }
        };
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
        else mount();
    }
    autoMount();
})();

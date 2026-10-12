/* Shared Studio report delivery schedules. Lives in one file,
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
 *     cross-studio delivery list) calls window.ReportDelivery
 *     .mountCenter(...) once on load. Current rows retain native actions and
 *     dedicated edit pages; compatibility rows use the drawer's shared form.
 *
 * Scoped styling and shared picker assets work on reports without importing
 * the portal stylesheet. Native schedule fields retain the existing JSON API.
 */
(function () {
    'use strict';
    if (window.ReportDelivery) return;

    var CSS = ''
        + '#rdwOverlay{position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:9200;opacity:0;visibility:hidden}'
        + '#rdwOverlay.open{opacity:1;visibility:visible}'
        + '#rdwDrawer{position:fixed;inset:0 0 0 auto;width:560px;max-width:100vw;z-index:9201;'
        + 'background:var(--bg-card,var(--bg-main,#fff));color:var(--text,var(--text-main,#172126));'
        + 'border-left:1px solid var(--border,var(--border-color,#e2e6ea));box-shadow:-4px 0 24px rgba(0,0,0,.16);'
        + 'visibility:hidden;transform:translateX(100%);transition:transform .18s ease-out,visibility .18s;'
        + 'display:flex;flex-direction:column;font:14px/1.4 var(--font,Inter,sans-serif)}'
        + '#rdwDrawer.open{visibility:visible;transform:translateX(0)}'
        + '#rdwDrawer *,.rdw-form *{box-sizing:border-box}'
        + '.rdw-header{display:flex;align-items:start;justify-content:space-between;gap:16px;padding:24px;border-bottom:1px solid var(--border,var(--border-color,#e2e6ea))}'
        + '#rdwDrawer .rdw-title{font-size:24px;font-weight:600;margin:0}.rdw-subtitle{font-size:12px;color:var(--text2,var(--text-secondary,#475569));margin-top:4px;overflow-wrap:anywhere}'
        + '.rdw-close{background:none;border:0;color:inherit;min-width:36px;min-height:36px;font-size:24px;cursor:pointer}'
        + '.rdw-body{flex:1;min-height:0;overflow:auto;padding:24px}'
        + '.rdw-section{margin-bottom:24px}.rdw-section-title{font-size:16px;font-weight:600;margin:0 0 16px}'
        + '.rdw-note,.rdw-help{display:block;color:var(--text2,var(--text-secondary,#475569));font-size:12px;line-height:1.5;margin:6px 0 0}'
        + '.rdw-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border-radius:8px;padding:7px 10px;min-height:38px;'
        + 'font:14px/1.4 var(--font,Inter,sans-serif);cursor:pointer;border:1px solid var(--border,var(--border-color,#e2e6ea));'
        + 'background:var(--bg-card,var(--bg-main,#fff));color:var(--text,var(--text-main,#172126))}'
        + '.rdw-btn:hover{background:var(--bg-hover,var(--bg-main,#eef1f3))}.rdw-btn:disabled{opacity:.6;cursor:default}'
        + '.rdw-btn.primary{background:var(--primary,var(--accent,#0f766e));border-color:var(--primary,var(--accent,#0f766e));color:var(--on-accent,#fff)}'
        + '.rdw-btn.danger{color:var(--red,#b91c1c)}'
        + '.rdw-form{max-width:760px;min-width:0}.rdw-form-section{margin-top:24px;padding-top:24px;border-top:1px solid var(--border,var(--border-color,#e2e6ea))}'
        + '.rdw-form [hidden]{display:none!important}'
        + 'body.mgmt main .ui-table.delivery-list tbody tr.delivery-row-detail[data-rdw-editor-open]:has([data-form-slot]:not(:empty)){display:table-row}'
        + '.rdw-field{min-width:0}.rdw-form .rdw-label{display:block;font-size:13px;color:var(--text2,var(--text-secondary,#475569));margin:0 0 4px}'
        + '.rdw-form .rdw-input,.rdw-form .rdw-select{box-sizing:border-box;width:100%;min-width:0;min-height:38px;padding:7px 10px;'
        + 'font:14px/1.4 var(--font,Inter,sans-serif);border-radius:8px;border:1px solid var(--border,var(--border-color,#e2e6ea));background:var(--bg,var(--bg-main,#f6f7f8));color:var(--text,var(--text-main,#172126))}'
        + '.rdw-form select:not([data-choice-picker]){appearance:none;padding-right:28px;background-image:linear-gradient(45deg,transparent 50%,currentColor 50%),linear-gradient(135deg,currentColor 50%,transparent 50%);background-position:calc(100% - 13px) calc(50% - 1px),calc(100% - 9px) calc(50% - 1px);background-repeat:no-repeat;background-size:4px 4px}'
        + '.rdw-row2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.rdw-row2>.rdw-wide{grid-column:1/-1}'
        + '.rdw-form .rdw-check-row{display:flex;align-items:center;gap:8px;margin:16px 0 0;min-height:36px;font-size:14px}'
        + '.rdw-check-row input{width:auto;flex:none}.rdw-form-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}'
        + '.rdw-field-error{color:var(--red,#b91c1c);font-size:13px;margin:4px 0}.rdw-form [aria-invalid=true]{border-color:var(--red,#b91c1c)}'
        + '.rdw-schedule-row{border:1px solid var(--border,var(--border-color,#e2e6ea));border-radius:10px;padding:16px;margin-bottom:16px}'
        + '.rdw-schedule-summary{font-weight:600;overflow-wrap:anywhere}.rdw-schedule-meta{color:var(--text2,var(--text-secondary,#475569));font-size:12px;margin:6px 0 12px;overflow-wrap:anywhere}'
        + '.rdw-schedule-actions{display:flex;gap:8px;flex-wrap:wrap}.rdw-badge{display:inline-block;padding:2px 8px;border-radius:8px;font-size:12px;margin-left:8px}'
        + '.rdw-badge.on{color:var(--green,#15803d);background:color-mix(in srgb,var(--green,#15803d) 12%,transparent)}'
        + '.rdw-badge.off{color:var(--text2,var(--text-secondary,#475569));background:var(--bg-hover,var(--bg-main,#eef1f3))}'
        + '.rdw-empty{color:var(--text2,var(--text-secondary,#475569));padding:12px 0}.rdw-flash{padding:12px;border-radius:8px;display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:16px}'
        + '.rdw-flash.success{color:var(--green,#15803d);background:color-mix(in srgb,var(--green,#15803d) 12%,transparent)}'
        + '.rdw-flash.error{color:var(--red,#b91c1c);background:color-mix(in srgb,var(--red,#b91c1c) 12%,transparent)}'
        + '.rdw-flash-close{background:none;border:0;color:inherit;cursor:pointer;min-width:36px;min-height:36px}'
        + '#rdwDrawer :focus-visible,.rdw-form :focus-visible{outline:2px solid var(--focus,var(--accent,#0f766e));outline-offset:2px}'
        + '@media(max-width:767px){.rdw-header,.rdw-body{padding:16px}.rdw-row2{grid-template-columns:minmax(0,1fr)}'
        + 'body.mgmt main .ui-table.delivery-list tbody tr.delivery-row-detail[data-rdw-editor-open]:has([data-form-slot]:not(:empty)){display:block}'
        + '.rdw-form .rdw-input,.rdw-form .rdw-select{font-size:16px;min-height:44px}.rdw-btn,.rdw-close,.rdw-flash-close,.rdw-form .rdw-check-row{min-height:44px}}'
        + '@media(prefers-reduced-motion:reduce){#rdwDrawer{transition:none}}';

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

    var formSerial = 0, assetsPromise = null;
    function updateTimeSchemes(root) {
        (root || document).querySelectorAll('.rdw-form input[type="time"]').forEach(function (input) {
            var rgb = getComputedStyle(input).backgroundColor.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
            if (rgb) input.style.colorScheme = Number(rgb[1]) * 299 + Number(rgb[2]) * 587 + Number(rgb[3]) * 114 < 128000 ? 'dark' : 'light';
        });
    }
    function loadAsset(url, kind) {
        return new Promise(function (resolve, reject) {
            var tag = kind === 'css' ? 'link' : 'script';
            var found = Array.prototype.find.call(document.querySelectorAll(tag), function (node) {
                return (kind === 'css' ? node.href : node.src) === new URL(url, location.href).href;
            });
            if (found && (kind === 'css' ? found.sheet : found.dataset.rdwLoaded)) { resolve(); return; }
            var node = found || document.createElement(tag);
            node.addEventListener('load', function () { node.dataset.rdwLoaded = '1'; resolve(); }, { once: true });
            node.addEventListener('error', function () { node.remove(); reject(new Error('Shared controls could not load.')); }, { once: true });
            if (!found) {
                if (kind === 'css') { node.rel = 'stylesheet'; node.href = url; }
                else { node.src = url; node.async = false; }
                document.head.appendChild(node);
            }
        });
    }
    function ensurePickerAssets(assets) {
        if (assetsPromise) return assetsPromise;
        // Manifest deployments retain original files; older API payloads use those same-origin URLs.
        assets = assets || { choice_css: '/static/choice-picker.css', choice_js: '/static/choice-picker.js',
            recipient_css: '/static/recipient-picker.css', recipient_js: '/static/recipient-picker.js' };
        var scripts = (window.TrellumChoices ? Promise.resolve() : loadAsset(assets.choice_js, 'js')).then(function () {
                if (!window.TrellumChoices) throw new Error('Shared choices unavailable.');
            }).then(function () { return window.TrellumRecipients ? null : loadAsset(assets.recipient_js, 'js'); })
            .then(function () { if (!window.TrellumRecipients) throw new Error('Shared recipients unavailable.'); });
        assetsPromise = Promise.all([loadAsset(assets.choice_css, 'css'), loadAsset(assets.recipient_css, 'css'), scripts]).then(function () {
            if (!window.TrellumRecipients) throw new Error('Shared recipients unavailable.');
        }).catch(function (error) { assetsPromise = null; throw error; });
        return assetsPromise;
    }
    function validTimezone(zone) {
        try { return !!zone && !!new Intl.DateTimeFormat('en', { timeZone: zone }); } catch (error) { return false; }
    }
    function recipientPickerHtml(schedule, chips, id, ready) {
        schedule = schedule || {};
        chips = chips || EMPTY_CHIP_DATA;
        var members = (chips.members || []).slice(), groups = (chips.groups || []).slice();
        (schedule.recipients || []).forEach(function (person) {
            if (!members.some(function (row) { return String(row.id) === String(person.id); })) members.push(person);
        });
        (schedule.recipient_groups || []).forEach(function (group) {
            if (!groups.some(function (row) { return String(row.id) === String(group.id); })) groups.push(group);
        });
        var people = (schedule.recipients || []).map(function (row) { return String(row.id); });
        var roles = schedule.recipient_roles || [];
        var selectedGroups = (schedule.recipient_groups || []).map(function (row) { return String(row.id); });
        function checkbox(name, value, text, identity, selected) {
            return '<label><input type="checkbox" name="' + name + '" value="' + esc(value)
                + '" data-recipient-name="' + esc(text) + '" data-recipient-identity="' + esc(identity || '') + '"'
                + (selected ? ' checked' : '') + (ready ? '' : ' disabled') + '>' + esc(text) + '</label>';
        }
        return '<div class="rdw-field rdw-wide" data-f="recipients" data-recipient-picker data-picker-label="Recipients">'
            + '<label class="rdw-label">Recipients</label><div data-recipient-type="people">'
            + members.map(function (row) { return checkbox('recipient_ids', row.id, row.name || row.email, row.email, people.indexOf(String(row.id)) >= 0); }).join('')
            + '</div><div data-recipient-type="roles">'
            + ['admins', 'developers', 'everyone'].map(function (role) { return checkbox('recipient_roles', role, roleLabel(role, chips.studio_name || 'this studio'), '', roles.indexOf(role) >= 0); }).join('')
            + '</div><div data-recipient-type="groups">'
            + groups.map(function (row) { return checkbox('recipient_group_ids', row.id, row.name, '', selectedGroups.indexOf(String(row.id)) >= 0); }).join('')
            + '</div></div>';
    }
    function scheduleFormHtml(opts) {
        var schedule = opts.schedule || null, id = 'rdw-form-' + (++formSerial);
        var freq = schedule ? schedule.freq : 'daily';
        var browserZone = browserTimezone();
        var zone = schedule ? schedule.timezone : validTimezone(browserZone) ? browserZone : (opts.defaultTimezone || 'UTC');
        var time = hm(schedule ? schedule.send_hour : 8, schedule ? schedule.send_minute : 0);
        var zones = timezoneOptionList(zone, opts.defaultTimezone);
        if (Intl.supportedValuesOf) {
            try { zones = Intl.supportedValuesOf('timeZone').concat(zones).filter(function (value, index, all) { return all.indexOf(value) === index; }).sort(); } catch (error) {}
        }
        function field(name, label, html, wide) {
            return '<div class="rdw-field' + (wide ? ' rdw-wide' : '') + '"><label class="rdw-label" for="' + id + '-' + name + '">' + label + '</label>' + html + '</div>';
        }
        function select(name, choices, current, picker) {
            return '<select id="' + id + '-' + name + '" class="rdw-select" name="' + name + '" data-f="' + name + '"' + (picker ? ' data-choice-picker' : '') + '>'
                + choices.map(function (row) { return '<option value="' + esc(row.value) + '"' + (String(row.value) === String(current) ? ' selected' : '') + '>' + esc(row.label) + '</option>'; }).join('') + '</select>';
        }
        return '<form id="' + id + '" class="rdw-form" data-schedule-form>'
            + '<section class="rdw-form-section"><h4 class="rdw-section-title">Schedule</h4><div class="rdw-row2">'
            + field('freq', 'Frequency', select('freq', FREQ_OPTIONS, freq))
            + field('time', 'Local time', '<input id="' + id + '-time" type="time" step="60" required class="rdw-input" name="local_time" data-f="time" value="' + time + '">')
            + '<div class="rdw-field" data-f="weekdayField"' + (freq === 'weekly' ? '' : ' hidden') + '><label class="rdw-label" for="' + id + '-weekday">Day</label>'
            + select('weekday', WEEKDAY_OPTIONS.map(function (label, value) { return { value: value, label: label }; }), schedule ? schedule.weekday : 0) + '</div>'
            + '<div class="rdw-field" data-f="monthDayField"' + (freq === 'monthly' ? '' : ' hidden') + '><label class="rdw-label" for="' + id + '-monthDay">Day of month</label>'
            + select('monthDay', Array.from({ length: 28 }, function (_, index) { return { value: index + 1, label: index + 1 }; }), schedule ? schedule.month_day : 1) + '</div>'
            + field('timezone', 'Timezone', select('timezone', zones.map(function (value) { return { value: value, label: value.replace(/_/g, ' ') }; }), zone, true), true)
            + '</div><p class="rdw-help">' + (schedule ? 'Schedule uses ' + esc(zone) + '.' : validTimezone(browserZone) ? 'Defaulted to your browser timezone: ' + esc(zone) + '.' : 'Browser timezone unavailable. Application fallback: ' + esc(zone) + '.')
            + ' Changing timezone converts local time to keep the same instant using today’s offsets; recurring sends follow the chosen timezone’s daylight saving rules.</p>'
            + '<p class="rdw-help" data-f="tzCaption"></p></section>'
            + '<section class="rdw-form-section"><h4 class="rdw-section-title">Recipients</h4>'
            + recipientPickerHtml(schedule, opts.chipData, id, !!opts.chipData)
            + '<p class="rdw-help" role="status" data-recipient-status>Loading recipient choices…</p><button class="ui-btn rdw-btn" type="button" data-recipient-retry hidden>Retry recipient choices</button></section>'
            + '<section class="rdw-form-section"><h4 class="rdw-section-title">Delivery</h4>'
            + '<label class="rdw-check-row"><input name="attach_pdf" type="checkbox" data-f="attachPdf"' + (schedule && schedule.attach_pdf ? ' checked' : '') + '>Also attach as PDF</label>'
            + '<label class="rdw-check-row"><input name="enabled" type="checkbox" data-f="enabled"' + ((!schedule && opts.defaultEnabled !== false) || (schedule && schedule.enabled) ? ' checked' : '') + '>Enabled</label></section>'
            + '<div data-f="errors" role="alert"></div><div class="rdw-form-actions"><button type="submit" class="ui-btn rdw-btn primary" disabled>'
            + (schedule ? 'Save delivery' : 'Create delivery') + '</button><button type="button" class="ui-btn rdw-btn" data-f="cancel">Cancel</button></div>'
            + '<p class="rdw-help" role="status" data-save-status></p></form>';
    }
    function currentForm(root, ctx) { return ctx.navigation === pageGeneration && root.isConnected && root.querySelector('[data-schedule-form]') === ctx.form && (!ctx.current || ctx.current()); }
    function canDiscard(ctx) {
        if (!ctx) return true;
        if (ctx.saving) return window.confirm('Saving is in progress. Leaving keeps its outcome unconfirmed until you check the saved delivery. Leave this editor?');
        return ctx.version === ctx.savedVersion || window.confirm('Discard unsaved delivery changes? Cancel keeps this draft.');
    }
    function selection(root) {
        function checked(name) { return Array.prototype.map.call(root.querySelectorAll('input[name="' + name + '"]:checked'), function (node) { return node.value; }); }
        return { recipients: checked('recipient_ids').map(function (id) { return { id: id, email: '' }; }),
            recipient_roles: checked('recipient_roles'), recipient_groups: checked('recipient_group_ids').map(function (id) { return { id: id, name: '' }; }) };
    }
    function wireScheduleForm(root, ctx) {
        var form = root.querySelector('[data-schedule-form]');
        ctx.form = form;
        root._rdwContext = ctx;
        ctx.navigation = pageGeneration;
        ctx.ready = false;
        ctx.version = 0;
        ctx.savedVersion = 0;
        var submit = form.querySelector('button[type="submit"]');
        var status = form.querySelector('[data-save-status]');
        var loadStatus = form.querySelector('[data-recipient-status]');
        var retry = form.querySelector('[data-recipient-retry]');
        function clearErrors() {
            root.querySelectorAll('[data-rdw-error]').forEach(function (node) { node.remove(); });
            root.querySelectorAll('[data-rdw-invalid]').forEach(function (node) {
                node.removeAttribute('aria-invalid'); node.removeAttribute('aria-describedby'); node.removeAttribute('data-rdw-invalid');
            });
            root.querySelector('[data-f="errors"]').replaceChildren();
        }
        function showErrors(errors) {
            Object.keys(errors).forEach(function (name, index) {
                var target = name === 'send_hour' || name === 'send_minute' ? time :
                    name === 'month_day' ? root.querySelector('[data-f="monthDay"]') : root.querySelector('[name="' + name + '"]');
                var message = document.createElement('p');
                message.className = 'rdw-field-error'; message.dataset.rdwError = '';
                message.id = form.id + '-error-' + index;
                message.textContent = [].concat(errors[name]).join(' ');
                if (target) {
                    (target.closest('.rdw-field') || target.parentElement).appendChild(message);
                    var fields = /^(recipient_ids|recipient_roles|recipient_group_ids)$/.test(name) ? root.querySelectorAll('[name="' + name + '"]') : [target];
                    Array.prototype.forEach.call(fields, function (field) { field.dataset.rdwInvalid = ''; field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', message.id); });
                } else root.querySelector('[data-f="errors"]').appendChild(message);
            });
        }
        form.addEventListener('input', function () { ctx.version++; });
        form.addEventListener('change', function () { ctx.version++; });
        root.querySelector('[data-f="freq"]').addEventListener('change', function () {
            root.querySelector('[data-f="weekdayField"]').hidden = this.value !== 'weekly';
            root.querySelector('[data-f="monthDayField"]').hidden = this.value !== 'monthly';
        });
        root.querySelector('[data-f="cancel"]').addEventListener('click', function () { if (canDiscard(ctx)) ctx.onCancel(); });
        var zone = root.querySelector('[data-f="timezone"]'), time = root.querySelector('[data-f="time"]'), lastZone = zone.value;
        function refreshCaption() {
            if (!time.value) return;
            var parts = time.value.split(':'), hour = Number(parts[0]), minute = Number(parts[1]);
            var utc = convertHourMinute(hour, minute, zone.value, 'UTC');
            var mine = convertHourMinute(hour, minute, zone.value, browserTimezone() || 'UTC');
            root.querySelector('[data-f="tzCaption"]').textContent = '= ' + hm(utc.hour, utc.minute) + ' UTC · ' + hm(mine.hour, mine.minute) + ' your time';
        }
        zone.addEventListener('change', function () {
            if (time.value) {
                var parts = time.value.split(':');
                var converted = convertHourMinute(Number(parts[0]), Number(parts[1]), lastZone, zone.value);
                time.value = hm(converted.hour, converted.minute);
            }
            lastZone = zone.value;
            refreshCaption();
        });
        time.addEventListener('input', refreshCaption);
        refreshCaption();
        updateTimeSchemes(root);
        function loadChoices() {
            ctx.ready = false; submit.disabled = true; retry.hidden = true;
            loadStatus.textContent = 'Loading recipient choices…';
            var attempt = ctx.loadAttempt = (ctx.loadAttempt || 0) + 1;
            var choices = loadMembersFor(ctx.apiBase);
            ensurePickerAssets(ctx.assets).then(function () {
                if (!currentForm(root, ctx) || attempt !== ctx.loadAttempt) return null;
                TrellumChoices.init(root); TrellumRecipients.init(root);
                return choices;
            }).then(function (result) {
                if (!result || !currentForm(root, ctx) || attempt !== ctx.loadAttempt) return;
                if (!result.ok) throw new Error('Recipient choices could not load. Saved selections are kept.');
                var selected = selection(root);
                // Retain saved display identities while replacing only the checkbox catalog.
                selected.recipients.forEach(function (person) {
                    var prior = (ctx.schedule && ctx.schedule.recipients || []).find(function (row) { return String(row.id) === person.id; });
                    if (prior) Object.assign(person, prior);
                });
                selected.recipient_groups.forEach(function (group) {
                    var prior = (ctx.schedule && ctx.schedule.recipient_groups || []).find(function (row) { return String(row.id) === group.id; });
                    if (prior) Object.assign(group, prior);
                });
                root.querySelector('[data-f="recipients"]').outerHTML = recipientPickerHtml(selected, result.data, '', true);
                TrellumRecipients.init(root);
                ctx.ready = true; submit.disabled = !!ctx.saving;
                loadStatus.textContent = !result.data.members.length && !result.data.groups.length ? 'No eligible people or groups. Studio role choices remain available.' : 'Only recipients with access to this report can receive it.';
            }).catch(function (error) {
                if (!currentForm(root, ctx) || attempt !== ctx.loadAttempt) return;
                loadStatus.textContent = error.message || 'Recipient choices unavailable. Saved selections are kept.';
                retry.hidden = false; submit.disabled = true;
            });
        }
        retry.addEventListener('click', loadChoices);
        loadChoices();
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            if (!ctx.ready || ctx.saving || !form.reportValidity()) return;
            var selected = selection(root), parts = time.value.split(':');
            var body = { recipient_ids: selected.recipients.map(function (row) { return Number(row.id); }), recipient_roles: selected.recipient_roles,
                recipient_group_ids: selected.recipient_groups.map(function (row) { return Number(row.id); }), freq: root.querySelector('[data-f="freq"]').value,
                send_hour: Number(parts[0]), send_minute: Number(parts[1]), weekday: Number(root.querySelector('[data-f="weekday"]').value),
                month_day: Number(root.querySelector('[data-f="monthDay"]').value), timezone: zone.value,
                attach_pdf: root.querySelector('[data-f="attachPdf"]').checked, enabled: root.querySelector('[data-f="enabled"]').checked };
            var wasEditing = !!ctx.editingId, snapshot = ctx.version;
            ctx.saving = true; submit.disabled = true; status.textContent = 'Saving…'; form.setAttribute('aria-busy', 'true');
            clearErrors();
            var url = ctx.editingId ? ctx.apiBase + '/schedules/' + ctx.editingId : ctx.apiBase + '/schedules';
            postJson(url, 'POST', body).then(function (result) {
                if (!currentForm(root, ctx)) return;
                if (!result.ok || !result.data.schedule) {
                    var errors = result.data.errors || {};
                    showErrors(errors);
                    root.querySelector('[data-f="errors"]').appendChild(document.createTextNode('Check the highlighted fields.'));
                    status.textContent = result.data.error || 'Could not save. Your draft is kept.';
                    return;
                }
                ctx.editingId = result.data.schedule.id;
                ctx.schedule = result.data.schedule;
                ctx.savedVersion = snapshot;
                submit.textContent = 'Save delivery';
                status.textContent = ctx.version === snapshot ? 'Delivery saved.' : 'Submitted changes saved; newer edits are still unsaved.';
                ctx.onSaved(result.data.schedule, wasEditing, ctx.version !== snapshot);
            }).catch(function () {
                if (currentForm(root, ctx)) status.textContent = 'Save outcome unconfirmed. Your draft is kept. Check the saved schedule before retrying.';
            }).finally(function () {
                if (!currentForm(root, ctx)) return;
                ctx.saving = false; submit.disabled = !ctx.ready; form.removeAttribute('aria-busy');
            });
        });
    }

    // ── state (drawer only -- the delivery center keeps no module state,
    // see wireDeliveryRow) ──────────────────────────────────────────────
    var state = null;         // { prefix, slug, name, triggerEl }
    var pageGeneration = 0;
    var data = null;          // last GET /schedules payload
    // Report API URL -> eligible recipients. Refresh on each open/edit because
    // group access can change while the page remains open.
    var editingId = null;     // schedule pk being edited, or null for "new"
    var built = false;

    function apiBase() { return state.prefix + '/api/reports/' + encodeURIComponent(state.slug); }

    var EMPTY_CHIP_DATA = { members: [], roles: {}, groups: [], studio_name: '' };

    function loadMembersFor(reportApiBase) {
        return apiFetch(reportApiBase + '/recipients').then(function (response) {
            if (!response.ok) throw new Error('Could not load recipients.');
            return response.json();
        }).then(function (result) {
            return { ok: true, data: { members: result.members || [], roles: result.roles || {}, groups: result.groups || [], studio_name: result.studio_name || '' } };
        }).catch(function () { return { ok: false }; });
    }
    function currentSession(session) { return state === session && session.active; }

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
        var themeObserver = new MutationObserver(function () { updateTimeSchemes(); });
        themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });
        themeObserver.observe(document.body, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });

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
        var list = Array.prototype.filter.call(nodes, function (el) { return !el.matches(':disabled') && el.tabIndex !== -1 && el.getClientRects().length && !el.closest('[hidden]'); });
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
        var session = state;
        if (!overlay || !drawer) return;
        void drawer.offsetHeight;
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                if (!currentSession(session)) return;
                overlay.classList.add('open');
                drawer.classList.add('open');
            });
        });
    }

    function open(prefix, slug, name, triggerEl) {
        var oldForm = document.getElementById('rdwFormWrap');
        if (state && state.active && oldForm && oldForm.querySelector('form') && !canDiscard(oldForm._rdwContext)) return;
        ensureDom();
        if (state) state.active = false;
        state = { prefix: prefix, slug: slug, name: name, triggerEl: triggerEl || document.activeElement, active: true };
        data = null;
        editingId = null;
        document.getElementById('rdwSubtitle').textContent = name || slug;
        document.getElementById('rdwBody').innerHTML = '<div class="rdw-empty">Loading…</div>';
        playOpen(document.getElementById('rdwOverlay'), document.getElementById('rdwDrawer'));
        var closeBtn = document.getElementById('rdwClose');
        if (closeBtn) closeBtn.focus();
        loadAndRender();
    }

    function close(skipConfirmation) {
        var overlay = document.getElementById('rdwOverlay');
        var drawer = document.getElementById('rdwDrawer');
        if (!drawer || !state || !state.active) return;
        var wrap = document.getElementById('rdwFormWrap');
        if (skipConfirmation !== true && wrap && wrap.querySelector('form') && !canDiscard(wrap._rdwContext)) return;
        state.active = false;
        drawer.querySelectorAll('[data-recipient-picker],select[data-choice-picker]').forEach(function (node) {
            var picker = node._trellumRecipients || node._trellumChoice;
            if (picker) picker.close(false);
        });
        overlay.classList.remove('open');
        drawer.classList.remove('open');
        if (state && state.triggerEl && typeof state.triggerEl.focus === 'function') {
            state.triggerEl.focus({ preventScroll: true });
        }
    }

    // ── data loading ─────────────────────────────────────────────────────
    function loadAndRender() {
        var session = state, endpoint = apiBase();
        apiFetch(endpoint + '/schedules').then(function (response) {
            if (!response.ok) throw new Error('Could not load schedules.');
            return response.json();
        }).then(function (result) {
            if (!currentSession(session)) return;
            data = result;
            render();
        }).catch(function () {
            if (!currentSession(session)) return;
            var body = document.getElementById('rdwBody');
            body.innerHTML = '<p class="rdw-flash error" role="alert">Could not load deliveries.</p><button type="button" class="ui-btn rdw-btn" data-retry-schedules>Retry</button>';
            body.querySelector('[data-retry-schedules]').addEventListener('click', loadAndRender);
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

    function scheduleRowsHtml() {
        var html = '';
        if (!data.schedules.length) {
            html += '<div class="rdw-empty">No scheduled deliveries yet.</div>';
        } else {
            data.schedules.forEach(function (s) {
                html += '<div class="rdw-schedule-row" data-id="' + s.id + '">'
                    + '<div class="rdw-schedule-summary">' + esc(scheduleSummary(s))
                    + '<span class="rdw-badge ' + (s.enabled ? 'on">On' : 'off">Off') + '</span></div>'
                    + '<div class="rdw-schedule-meta">' + esc(s.recipient_summary)
                    + (s.attach_pdf ? ' &middot; PDF attached' : '') + '</div>';
                if (s.editable) {
                    html += '<div class="rdw-schedule-actions">'
                        + '<button type="button" class="ui-btn rdw-btn small" data-edit="' + s.id + '">Edit</button>'
                        + '<button type="button" class="ui-btn rdw-btn small" data-sample="' + s.id + '">Send sample</button>'
                        + '<button type="button" class="ui-btn rdw-btn small danger" data-delete="' + s.id + '">Delete</button>'
                        + '</div>';
                }
                html += '</div>';
            });
        }
        return html;
    }

    function render() {
        var body = document.getElementById('rdwBody');

        var html = '';
        html += '<div class="rdw-section"><h3 class="rdw-section-title">Scheduled delivery</h3><div id="rdwScheduleList">';
        html += scheduleRowsHtml();
        html += '</div>'
            + (data.can_manage ? '<button type="button" class="ui-btn rdw-btn" id="rdwNewSchedule">+ New schedule</button>' : '<p class="rdw-note">Read only. Developers and admins manage shared schedules.</p>')
            + '<div id="rdwFormWrap"></div>'
            + '</div>';

        html += '<div class="rdw-section"><p class="rdw-note">' + esc(ALERT_NOTE) + '</p></div>';

        body.innerHTML = html;
        wireStaticControls();
        _notifySubscriptionChange();
    }

    function wireStaticControls() {
        var newButton = document.getElementById('rdwNewSchedule');
        if (newButton) newButton.addEventListener('click', function () {
            editingId = null;
            renderForm(null);
        });

        var session = state;
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
                    if (!currentSession(session)) return;
                    sampleBtn.disabled = false;
                    // The endpoint kicks the actual send off in the
                    // background and answers right away (202) -- the
                    // message it returns already says so; the fallback
                    // here only fires if a proxy/500 ate the body.
                    flash(res.ok ? (res.data.message || 'Sending — check your inbox shortly.') : (res.data.error || 'Could not send sample.'), res.ok ? 'success' : 'error');
                }).catch(function () {
                    if (!currentSession(session)) return;
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
                    if (!currentSession(session)) return;
                    if (!r.ok) { delBtn.disabled = false; flash('Could not delete the schedule.', 'error'); return; }
                    data.schedules = data.schedules.filter(function (x) { return String(x.id) !== delBtn.dataset.delete; });
                    var wrap = document.getElementById('rdwFormWrap');
                    if (wrap._rdwContext && String(wrap._rdwContext.editingId) === delBtn.dataset.delete) wrap.innerHTML = '';
                    document.getElementById('rdwScheduleList').innerHTML = scheduleRowsHtml();
                    _notifySubscriptionChange();
                    flash('Schedule deleted.', 'success');
                }).catch(function () {
                    if (!currentSession(session)) return;
                    delBtn.disabled = false;
                    flash('Could not delete the schedule — check your connection and try again.', 'error');
                });
            }
        });
    }

    // ── create/edit form (drawer) ────────────────────────────────────────
    function renderForm(schedule) {
        var wrap = document.getElementById('rdwFormWrap'), session = state;
        if (wrap.querySelector('form') && !canDiscard(wrap._rdwContext)) return;
        wrap.innerHTML = '<h3 class="rdw-section-title">' + (schedule ? 'Edit delivery' : 'New delivery') + '</h3>'
            + scheduleFormHtml({ schedule: schedule, defaultTimezone: data.default_timezone, defaultEnabled: data.default_enabled });
        wireScheduleForm(wrap, {
            apiBase: apiBase(), editingId: schedule ? schedule.id : null, schedule: schedule, assets: data.picker_assets,
            current: function () { return currentSession(session); }, onFlash: flash,
            onCancel: function () { editingId = null; wrap.innerHTML = ''; document.getElementById('rdwNewSchedule').focus({ preventScroll: true }); },
            onSaved: function (saved, wasEditing, newerDraft) {
                if (wasEditing) data.schedules = data.schedules.map(function (row) { return row.id === saved.id ? saved : row; });
                else data.schedules.push(saved);
                editingId = saved.id;
                if (!wasEditing && !newerDraft) { render(); document.getElementById('rdwNewSchedule').focus({ preventScroll: true }); }
                else {
                    document.getElementById('rdwScheduleList').innerHTML = scheduleRowsHtml();
                    _notifySubscriptionChange();
                }
            }
        });
    }

    // ── delivery center (templates/reports/my_deliveries.html) ─────────────
    // Server rows retain enable/disable, delete and sample actions;
    // Add and Edit navigate to dedicated pages.
    function mountCenter(root) {
        ensureDom();
        root = root || document;
        var rows = root.querySelectorAll('[data-delivery-row]');
        Array.prototype.forEach.call(rows, function (row) {
            if (row.hasAttribute('data-server-form')) wireServerDeliveryRow(row);
            else wireDeliveryRow(row);
        });
    }

    function wireServerDeliveryRow(row) {
        if (row._deliveryWired) return;
        row._deliveryWired = true;
        var detail = row.nextElementSibling;
        var scheduleEl = detail.querySelector('[data-schedule-json]');
        var saved = JSON.parse(scheduleEl.textContent);
        var forms = Array.prototype.slice.call(row.querySelectorAll('form')).concat(Array.prototype.slice.call(detail.querySelectorAll('form')));
        forms.forEach(function (form) {
            form.addEventListener('submit', function (event) {
                if (form.dataset.deleteConfirm && !window.confirm(form.dataset.deleteConfirm)) {
                    event.preventDefault(); event.stopImmediatePropagation();
                }
            }, true);
            form.addEventListener('settings:success', function (event) {
                var result = event.detail;
                if (result.deleted_id) { row.remove(); detail.remove(); return; }
                if (!result.schedule || result.schedule.delivery_revision < saved.delivery_revision) return;
                saved = result.schedule;
                scheduleEl.textContent = JSON.stringify(saved);
                row.querySelector('[data-field="cadence"]').textContent = scheduleSummary(saved);
                row.querySelector('[data-field="format"]').textContent = saved.attach_pdf ? 'HTML + PDF' : 'HTML';
                row.querySelector('[data-field="recipients"]').textContent = saved.recipient_summary;
                row.querySelector('[data-field="status"]').textContent = saved.enabled ? 'Enabled' : 'Disabled';
                row.querySelector('[data-field="creator"]').textContent = saved.creator_name;
                row.querySelector('[data-field="next"]').textContent = saved.next_at || '—';
                var toggle = row.querySelector('[data-toggle-enable]');
                toggle.textContent = saved.enabled ? 'Disable' : 'Enable';
                toggle.form.querySelector('[name="enabled"]').value = saved.enabled ? 'false' : 'true';
                if (saved.delete_confirmation) row.querySelector('[data-delete-confirm]').dataset.deleteConfirm = saved.delete_confirmation;
            });
        });
        var sampleButton = row.querySelector('[data-send-sample]');
        if (sampleButton) sampleButton.addEventListener('click', function () {
            var btn = this;
            btn.disabled = true;
            postJson(row.dataset.prefix + '/api/reports/' + encodeURIComponent(row.dataset.slug) + '/schedules/' + saved.id + '/sample', 'POST', {})
                .then(function (result) { detail.querySelector('[data-flash-slot]').textContent = result.data.message || result.data.error || 'Sample request completed.'; })
                .catch(function () { detail.querySelector('[data-flash-slot]').textContent = 'Sample outcome unconfirmed.'; })
                .then(function () { btn.disabled = false; });
        });
    }
    document.addEventListener('settings:updated', function () { mountCenter(document); });

    function wireDeliveryRow(row) {
        if (row._deliveryWired) return;
        row._deliveryWired = true;
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
            // Native center rows hide empty flash slots; compatibility editors also occupy that row.
            detail.toggleAttribute('data-rdw-editor-open', !!formSlot.innerHTML);
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
                    if (!canDiscard(formSlot._rdwContext)) return;
                    formSlot.innerHTML = '';
                    editBtn.textContent = 'Edit';
                    syncVisibility();
                    return;
                }
                formSlot.innerHTML = scheduleFormHtml({ schedule: scheduleData });
                var editor = formSlot.querySelector('[data-schedule-form]');
                wireScheduleForm(formSlot, {
                    apiBase: rowApiBase, editingId: scheduleData.id, schedule: scheduleData,
                    current: function () { return row.isConnected && formSlot.querySelector('[data-schedule-form]') === editor; },
                    onFlash: rowFlash,
                    onCancel: function () { formSlot.innerHTML = ''; editBtn.textContent = 'Edit'; syncVisibility(); editBtn.focus({ preventScroll: true }); },
                    onSaved: function (saved) { applySchedule(saved); rowFlash('Schedule updated.', 'success'); }
                });
                editBtn.textContent = 'Cancel';
                syncVisibility();
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

    window.addEventListener('pagehide', function () { close(true); pageGeneration++; });
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

            // All report readers can inspect shared delivery schedules.
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

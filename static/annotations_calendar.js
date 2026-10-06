/* Annotations calendar — month grid + list view, vanilla JS, no build step.
 *
 * The server (apps/studios/annotations.py -> templates/studios/annotations.html)
 * embeds a SLIM payload via {{ client_payload|json_script:"calendar-data" }} --
 * just events/type_counts/scope_counts/type_defaults/too_many, the fields this
 * script actually reads -- and separately renders the List view's rows for
 * real, as HTML (the no-JS fallback). The full per-event data intentionally
 * exists in only two forms on this page: the JSON blob (for the Month grid)
 * and the server-rendered <div class="evrow"> markup (for List) -- there is
 * no third, redundant copy. This script:
 *   - builds the Month grid entirely client-side from that same payload
 *   - wires the Month/List toggle
 *   - wires the type/scope filter pills (which also re-filter the server-rendered
 *     List rows by their data-type/data-scope attributes, rather than re-rendering
 *     them)
 *   - adds the detail popover, keyboard navigation, and localStorage persistence
 *
 * State shape: {year, month, view, hiddenTypes, scope}. `hiddenTypes` and `scope`
 * persist to localStorage under `anno-cal:<org>/<studio>` so a user's filter
 * choices survive a reload; `view` persists too. `year`/`month` do not persist —
 * every page load starts on the current month, which is less surprising than
 * remembering wherever the user last scrolled to.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('calendar-data');
  if (!dataEl) return; // the empty-state page has no calendar mounted at all

  var payload = JSON.parse(dataEl.textContent);

  var calGrid = document.getElementById('calGrid');
  var calList = document.getElementById('calList');
  var calTitle = document.getElementById('calTitle');
  var btnPrev = document.getElementById('calPrev');
  var btnNext = document.getElementById('calNext');
  var btnToday = document.getElementById('calToday');
  var btnViewMonth = document.getElementById('calViewMonth');
  var btnViewList = document.getElementById('calViewList');
  var pillsEl = document.getElementById('annoPills');
  if (!calGrid || !calList) return;

  // Derive org/studio from the URL rather than threading another context
  // variable through the view: this page only ever lives at
  // /s/<org>/<studio>/annotations.
  var pathParts = location.pathname.split('/').filter(Boolean);
  var ORG = pathParts[1] || 'org';
  var STUDIO = pathParts[2] || 'studio';
  var STORAGE_KEY = 'anno-cal:' + ORG + '/' + STUDIO;

  var WEEKDAY_HEADERS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  var MAX_CHIPS_SHOWN = 3; // when a day overflows, show 3 + "+N more"

  // ── date helpers ──────────────────────────────────────────────────────
  // Event dates are naive YYYY-MM-DD strings with no time component, so they
  // compare correctly as plain strings/lexicographically. Date objects below
  // are only used for calendar-grid arithmetic (weekday, month length, day
  // stepping) and are always constructed with the LOCAL constructor
  // (`new Date(y, m, d)`), never `toISOString()` — that keeps every date in
  // this file in one consistent (local) frame, so there's no UTC/local
  // seam to get wrong.
  function pad2(n) { return n < 10 ? '0' + n : '' + n; }
  function iso(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function addDays(d, n) { var r = new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); return r; }
  function daysInMonth(y, m) { return new Date(y, m, 0).getDate(); } // m is 1-12

  // Monday=0..Sunday=6 (the grid header is Mon..Sun, unlike JS's native
  // Sunday=0 week).
  function mondayIndex(d) { return (d.getDay() + 6) % 7; }

  // Today, per the BROWSER's clock. Deliberately client-local: the month
  // grid is read like a wall calendar, so "today" should match whatever day
  // it is where the person is sitting -- unlike day *counts* elsewhere in
  // the product (report data, run history), which are UTC by convention.
  var today = new Date();
  var todayIso = iso(today);

  // ── persisted state ──────────────────────────────────────────────────
  function loadPersisted() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) || {}) : {};
    } catch (e) { return {}; }
  }
  function savePersisted() {
    try {
      var hidden = Object.keys(state.hiddenTypes).filter(function (t) { return state.hiddenTypes[t]; });
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        view: state.view, hiddenTypes: hidden, scope: state.scope
      }));
    } catch (e) { /* localStorage unavailable (private mode etc.) -- filters just won't persist */ }
  }

  var persisted = loadPersisted();

  // Per-type initial visibility from the server-resolved defaults
  // (payload.type_defaults, sourced from events.yaml's `type_defaults:`
  // section). A type absent from that map defaults to visible. localStorage
  // overrides this once the user has touched a pill.
  var initialHidden = {};
  (payload.type_counts || []).forEach(function (row) {
    var visible = Object.prototype.hasOwnProperty.call(payload.type_defaults || {}, row.value)
      ? payload.type_defaults[row.value] : true;
    if (!visible) initialHidden[row.value] = true;
  });
  if (Array.isArray(persisted.hiddenTypes)) {
    initialHidden = {};
    persisted.hiddenTypes.forEach(function (t) { initialHidden[t] = true; });
  }

  var state = {
    year: today.getFullYear(),
    month: today.getMonth() + 1,
    view: persisted.view === 'list' ? 'list' : 'month',
    hiddenTypes: initialHidden,
    scope: typeof persisted.scope === 'string' ? persisted.scope : ''
  };

  // The day cell currently in the roving-tabindex path (ISO string).
  var focusedDate = todayIso;

  // ── event indexing ──────────────────────────────────────────────────
  var allEvents = payload.events || [];

  // The one filter predicate. Both the Month grid (via the index below) and
  // the server-rendered List rows (applyListFilters, by data-type/data-scope)
  // go through this -- previously each re-implemented the same
  // hiddenTypes+scope check independently, ~270 lines apart, and could drift.
  function matchesFilters(type, scope) {
    if (state.hiddenTypes[type]) return false;
    if (state.scope && scope !== state.scope) return false;
    return true;
  }

  function isRanged(ev) { return ev.end_date && ev.end_date !== ev.date; }

  // Filtered events, indexed once per filter change (not once per day cell).
  // At the 5000-event soft cap, re-running Array.filter over every event for
  // each of the ~42 cells in a rendered month -- plus again per week for
  // ranged bars -- meant several hundred thousand predicate calls on every
  // pill click or month navigation. rebuildFilteredIndex() pays that cost
  // once per FILTER change; renderMonthGrid() then does O(1) map lookups per
  // cell and only scans the (typically much smaller) ranged list per week.
  var filteredIndex = null; // { byDate: Map<isoDate, Array<event>>, ranged: Array<event> }

  function rebuildFilteredIndex() {
    var byDate = new Map();
    var ranged = [];
    allEvents.forEach(function (ev) {
      if (!matchesFilters(ev.type, ev.scope)) return;
      if (isRanged(ev)) {
        ranged.push(ev);
      } else {
        var bucket = byDate.get(ev.date);
        if (!bucket) { bucket = []; byDate.set(ev.date, bucket); }
        bucket.push(ev);
      }
    });
    filteredIndex = { byDate: byDate, ranged: ranged };
  }

  // ── month grid rendering ─────────────────────────────────────────────
  function gridWeeks(year, month) {
    var first = new Date(year, month - 1, 1);
    var gridStart = addDays(first, -mondayIndex(first));
    var lastDay = new Date(year, month - 1, daysInMonth(year, month));
    var gridEnd = addDays(lastDay, 6 - mondayIndex(lastDay));

    var weeks = [];
    var cursor = gridStart;
    while (cursor <= gridEnd) {
      var week = [];
      for (var i = 0; i < 7; i++) { week.push(cursor); cursor = addDays(cursor, 1); }
      weeks.push(week);
    }
    return weeks;
  }

  function buildChip(ev) {
    var chip = document.createElement('span');
    chip.className = 'chip t-' + ev.type_css;
    chip.textContent = ev.label || '(untitled)';
    chip.title = ev.label + ' — ' + ev.type_label + ' — ' + ev.dates_display;
    chip.tabIndex = -1;
    chip.addEventListener('click', function (e) {
      e.stopPropagation();
      showPopover(chip, ev);
    });
    return chip;
  }

  function buildMoreLink(dayIso, hiddenCount) {
    var more = document.createElement('button');
    more.type = 'button';
    more.className = 'more';
    more.textContent = '+' + hiddenCount + ' more';
    more.addEventListener('click', function (e) {
      e.stopPropagation();
      showDayInList(dayIso);
    });
    return more;
  }

  function renderMonthGrid() {
    calGrid.innerHTML = '';
    calGrid.setAttribute('aria-label', MONTH_NAMES[state.month - 1] + ' ' + state.year);

    var head = document.createElement('div');
    head.className = 'cal-head';
    WEEKDAY_HEADERS.forEach(function (label) {
      var d = document.createElement('div');
      d.textContent = label;
      head.appendChild(d);
    });
    calGrid.appendChild(head);

    var weeks = gridWeeks(state.year, state.month);
    weeks.forEach(function (week) {
      var weekIsoList = week.map(iso);
      var weekEl = document.createElement('div');
      weekEl.className = 'cal-week';

      // Single-day events, bucketed per day within this week.
      week.forEach(function (dateObj) {
        var dayIso = iso(dateObj);
        var inMonth = dateObj.getMonth() + 1 === state.month;
        var dayEl = document.createElement('div');
        dayEl.className = 'day' + (inMonth ? '' : ' dim') + (dayIso === todayIso ? ' today' : '');
        dayEl.setAttribute('role', 'gridcell');
        dayEl.dataset.date = dayIso;
        dayEl.tabIndex = dayIso === focusedDate ? 0 : -1;

        var dnum = document.createElement('span');
        dnum.className = 'dnum';
        dnum.textContent = dateObj.getDate();
        dayEl.appendChild(dnum);

        var dayEvents = filteredIndex.byDate.get(dayIso) || [];
        var shown = dayEvents.length > MAX_CHIPS_SHOWN + 1 ? dayEvents.slice(0, MAX_CHIPS_SHOWN) : dayEvents;
        shown.forEach(function (ev) { dayEl.appendChild(buildChip(ev)); });
        if (dayEvents.length > shown.length) {
          dayEl.appendChild(buildMoreLink(dayIso, dayEvents.length - shown.length));
        }

        dayEl.addEventListener('click', function () { setFocusedDate(dayIso, false); });
        weekEl.appendChild(dayEl);
      });

      // Ranged events overlapping this week, as absolutely positioned bars.
      // filteredIndex.ranged is already filter-matched and typically much
      // smaller than allEvents, so this is a cheap per-week scan rather than
      // a full pass over every event in the studio.
      var rangedThisWeek = filteredIndex.ranged.filter(function (ev) {
        return ev.end_date >= weekIsoList[0] && ev.date <= weekIsoList[6];
      }).sort(function (a, b) { return a.date < b.date ? -1 : 1; });

      rangedThisWeek.forEach(function (ev, i) {
        var clampedStart = ev.date < weekIsoList[0] ? weekIsoList[0] : ev.date;
        var clampedEnd = ev.end_date > weekIsoList[6] ? weekIsoList[6] : ev.end_date;
        var startIdx = weekIsoList.indexOf(clampedStart);
        var endIdx = weekIsoList.indexOf(clampedEnd);
        if (startIdx < 0 || endIdx < 0) return;

        var bar = document.createElement('div');
        var contL = ev.date < weekIsoList[0];
        var contR = ev.end_date > weekIsoList[6];
        bar.className = 'span-bar t-' + ev.type_css + (contL ? ' cont-l' : '') + (contR ? ' cont-r' : '');
        bar.style.left = 'calc(' + (startIdx * 100 / 7) + '% + 3px)';
        bar.style.width = 'calc(' + ((endIdx - startIdx + 1) * 100 / 7) + '% - 6px)';
        bar.style.bottom = (4 + i * 20) + 'px';
        bar.textContent = ev.label || '(untitled)';
        bar.title = ev.label + ' — ' + ev.type_label + ' — ' + ev.dates_display;
        bar.tabIndex = -1;
        bar.addEventListener('click', function (e) { e.stopPropagation(); showPopover(bar, ev); });
        weekEl.appendChild(bar);
      });

      if (rangedThisWeek.length) {
        weekEl.style.paddingBottom = (rangedThisWeek.length * 20) + 'px';
      }
      calGrid.appendChild(weekEl);
    });
  }

  function setFocusedDate(dayIso, moveFocus) {
    var d = parseIso(dayIso);
    var changedMonth = d.getFullYear() !== state.year || d.getMonth() + 1 !== state.month;
    focusedDate = dayIso;
    if (changedMonth) {
      state.year = d.getFullYear();
      state.month = d.getMonth() + 1;
      renderMonthGrid();
      updateTitle();
    } else {
      // Keep tabindex in sync without a full rebuild.
      calGrid.querySelectorAll('.day').forEach(function (el) {
        el.tabIndex = el.dataset.date === focusedDate ? 0 : -1;
      });
    }
    if (moveFocus) {
      var cell = calGrid.querySelector('.day[data-date="' + dayIso + '"]');
      if (cell) cell.focus();
    }
  }

  function parseIso(s) {
    var parts = s.split('-');
    return new Date(+parts[0], +parts[1] - 1, +parts[2]);
  }

  function updateTitle() {
    calTitle.textContent = MONTH_NAMES[state.month - 1] + ' ' + state.year;
  }

  // ── detail popover ───────────────────────────────────────────────────
  var openPopover = null;

  function closePopover() {
    if (openPopover) { openPopover.remove(); openPopover = null; }
  }

  function showPopover(anchorEl, ev) {
    closePopover();
    var pop = document.createElement('div');
    pop.className = 'pop';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', ev.label || 'Event details');

    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'pop-close';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', closePopover);
    pop.appendChild(closeBtn);

    var h4 = document.createElement('h4');
    var dot = document.createElement('span');
    dot.className = 'dot t-' + ev.type_css;
    h4.appendChild(dot);
    h4.appendChild(document.createTextNode(ev.label || '(untitled)'));
    pop.appendChild(h4);

    var dl = document.createElement('dl');
    function row(term, buildDd) {
      var dt = document.createElement('dt'); dt.textContent = term;
      var dd = document.createElement('dd'); buildDd(dd);
      dl.appendChild(dt); dl.appendChild(dd);
    }
    row('Type', function (dd) { dd.textContent = ev.type_label; });
    row('Dates', function (dd) { dd.textContent = ev.dates_display; });
    row('Scope', function (dd) {
      var badge = document.createElement('span');
      badge.className = 'ui-badge';
      badge.textContent = ev.scope;
      dd.appendChild(badge);
    });
    row('Source', function (dd) {
      if (ev.report_url) {
        var a = document.createElement('a');
        a.href = ev.report_url;
        a.textContent = ev.source_label;
        dd.appendChild(a);
      } else {
        dd.textContent = ev.source_label;
      }
    });
    pop.appendChild(dl);

    document.body.appendChild(pop);
    var rect = anchorEl.getBoundingClientRect();
    var top = rect.bottom + 6;
    var left = rect.left;
    var popRect = pop.getBoundingClientRect();
    if (left + popRect.width > window.innerWidth - 8) left = window.innerWidth - popRect.width - 8;
    if (left < 8) left = 8;
    if (top + popRect.height > window.innerHeight - 8) top = rect.top - popRect.height - 6;
    if (top < 8) top = 8;
    pop.style.position = 'fixed';
    pop.style.top = top + 'px';
    pop.style.left = left + 'px';
    openPopover = pop;
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && openPopover) { closePopover(); }
  });
  document.addEventListener('click', function (e) {
    if (openPopover && !openPopover.contains(e.target)) closePopover();
  });

  // ── view toggle ───────────────────────────────────────────────────────
  function applyView() {
    var isMonth = state.view === 'month';
    calGrid.hidden = !isMonth;
    calList.hidden = isMonth;
    if (btnViewMonth) { btnViewMonth.classList.toggle('active', isMonth); btnViewMonth.setAttribute('aria-selected', isMonth ? 'true' : 'false'); }
    if (btnViewList) { btnViewList.classList.toggle('active', !isMonth); btnViewList.setAttribute('aria-selected', !isMonth ? 'true' : 'false'); }
  }

  function switchView(view) {
    state.view = view;
    applyView();
    savePersisted();
  }

  if (btnViewMonth) btnViewMonth.addEventListener('click', function () { switchView('month'); });
  if (btnViewList) btnViewList.addEventListener('click', function () { switchView('list'); });

  function showDayInList(dayIso) {
    switchView('list');
    var target = calList.querySelector('.evrow[data-date="' + dayIso + '"]:not([hidden])');
    if (target) {
      target.scrollIntoView({ block: 'center' });
      target.classList.add('evrow-anchor');
      setTimeout(function () { target.classList.remove('evrow-anchor'); }, 2000);
    }
  }

  // ── list filtering (re-filters the server-rendered rows) ──────────────
  function applyListFilters() {
    var groups = calList.querySelectorAll('.month-h');
    groups.forEach(function (h) {
      var anyVisible = false;
      var el = h.nextElementSibling;
      while (el && el.classList && el.classList.contains('evrow')) {
        var visible = matchesFilters(el.dataset.type, el.dataset.scope);
        el.hidden = !visible;
        if (visible) anyVisible = true;
        el = el.nextElementSibling;
      }
      h.hidden = !anyVisible;
    });
  }

  // ── filter pills ─────────────────────────────────────────────────────
  function syncPillClasses() {
    if (!pillsEl) return;
    pillsEl.querySelectorAll('.anno-pill').forEach(function (pill) {
      var filter = pill.dataset.filter;
      var value = pill.dataset.value;
      if (filter === 'type') {
        pill.classList.toggle('off', !!state.hiddenTypes[value]);
      } else if (filter === 'scope') {
        pill.classList.toggle('scope-on', state.scope === value);
      }
    });
  }

  if (pillsEl) {
    pillsEl.addEventListener('click', function (e) {
      var pill = e.target.closest ? e.target.closest('.anno-pill') : null;
      if (!pill) return;
      var filter = pill.dataset.filter;
      var value = pill.dataset.value;
      if (filter === 'type') {
        state.hiddenTypes[value] = !state.hiddenTypes[value];
      } else if (filter === 'scope') {
        state.scope = value; // single-select: All ("") or exactly one scope
      }
      syncPillClasses();
      rebuildFilteredIndex();
      renderMonthGrid();
      applyListFilters();
      savePersisted();
    });
  }

  // ── toolbar navigation ───────────────────────────────────────────────
  function shiftMonth(delta) {
    var m = state.month - 1 + delta;
    var y = state.year + Math.floor(m / 12);
    m = ((m % 12) + 12) % 12;
    state.year = y;
    state.month = m + 1;
    // Keep the roving-focus day within the new month (clamped).
    var day = Math.min(parseIso(focusedDate).getDate(), daysInMonth(state.year, state.month));
    focusedDate = state.year + '-' + pad2(state.month) + '-' + pad2(day);
    renderMonthGrid();
    updateTitle();
  }

  if (btnPrev) btnPrev.addEventListener('click', function () { shiftMonth(-1); });
  if (btnNext) btnNext.addEventListener('click', function () { shiftMonth(1); });
  if (btnToday) btnToday.addEventListener('click', function () { setFocusedDate(todayIso, true); updateTitle(); });

  // ── keyboard: roving tabindex on day cells ─────────────────────────────
  calGrid.addEventListener('keydown', function (e) {
    var cell = e.target.closest ? e.target.closest('.day') : null;
    if (!cell) return;
    var current = cell.dataset.date;
    var d = parseIso(current);
    var next = null;

    switch (e.key) {
      case 'ArrowLeft': next = addDays(d, -1); break;
      case 'ArrowRight': next = addDays(d, 1); break;
      case 'ArrowUp': next = addDays(d, -7); break;
      case 'ArrowDown': next = addDays(d, 7); break;
      case 'PageUp':
        e.preventDefault();
        if (e.shiftKey) { shiftMonth(-12); } else { shiftMonth(-1); }
        setFocusedDate(focusedDate, true);
        return;
      case 'PageDown':
        e.preventDefault();
        if (e.shiftKey) { shiftMonth(12); } else { shiftMonth(1); }
        setFocusedDate(focusedDate, true);
        return;
      case 'Home':
        e.preventDefault();
        setFocusedDate(todayIso, true);
        updateTitle();
        return;
      case 'Enter': {
        e.preventDefault();
        var dayEvents = filteredIndex.byDate.get(current) || [];
        if (dayEvents.length === 1) {
          showPopover(cell, dayEvents[0]);
        } else if (dayEvents.length > 1) {
          showDayInList(current);
        }
        return;
      }
      default: return;
    }
    e.preventDefault();
    setFocusedDate(iso(next), true);
    updateTitle();
  });

  // ── boot ─────────────────────────────────────────────────────────────
  syncPillClasses();
  rebuildFilteredIndex();
  applyListFilters();
  renderMonthGrid();
  updateTitle();
  applyView();
})();

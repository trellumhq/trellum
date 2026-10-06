/* Server-rendered context injected by templates/portal/index.html:
   {org, studio, prefix, user:{email,name,role,is_org_admin}, production,
    initial_view} — initial_view is "ops" on the Operations page
   (/s/<org>/<studio>/operations) and "reports" on the dashboard. */
var P = window.PORTAL_CTX;

/* Operations is a PAGE (studio tab bar), not a client-side view mode: the
   same template boots either surface, and this flag is what tells them
   apart. On the ops page the Cards/List toggle never renders and viewMode
   is pinned; navigating between the two surfaces is a normal page load. */
var IS_OPS_PAGE = !!(P && P.initial_view === 'ops');

// Must be `let` -- pollReportRun reassigns this after a report run so
// the freshly-fetched registry (with new validation data) replaces the
// stale initial snapshot. `const` would silently throw on the reassign
// and leave the UI holding onto pre-run validation state.
// Populated by boot() from the studio registry endpoint.
let reports = [];

/* Studio-scoped API paths live under P.prefix (/s/<org>/<studio>);
   global ones (/api/version, /api/me*, /api/assistant/*) do not. */
function apiUrl(path) { return (P && P.prefix ? P.prefix : '') + path; }

/* Reports opened from the catalog retain the console shell. URL handles an
   existing query string and fragment without string-splitting edge cases. */
function consoleReportUrl(path) {
    var url = new URL(path, window.location.href);
    url.searchParams.set('display', 'console');
    return url.pathname + url.search + url.hash;
}

/* apiFetch for a studio-scoped path — resolves it under P.prefix first. */
function studioFetch(path, options) { return apiFetch(apiUrl(path), options); }

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
    return fetch(url, options).then(function(resp) {
        if (resp.status === 401) {
            window.location = '/login';
        }
        return resp;
    });
}

let activeFolder = localStorage.getItem('trellum_portal_tab') || 'all';
let searchQuery = '';
/* The dashboard only knows cards/list now; a stored 'ops'/'health'/'chat'
   from before Operations became its own page falls back to the list default. */
let viewMode = IS_OPS_PAGE ? 'ops'
    : (localStorage.getItem('trellum_portal_view') === 'cards' ? 'cards' : 'list');
let sortCol = localStorage.getItem('trellum_portal_sort_col') || 'last_run';
let sortAsc = localStorage.getItem('trellum_portal_sort_asc') !== null
    ? localStorage.getItem('trellum_portal_sort_asc') === 'true' : true;
let cardSortCol = localStorage.getItem('trellum_portal_card_sort') || 'priority';
// Server-rendered production flag (P.production).
// Gates dev-only affordances (cached-only runs).
const IS_PRODUCTION = !!(P && P.production);
const IS_LOCAL = !IS_PRODUCTION;
var _runningSet = {};

/* ── Error log (session-scoped, seeded from persisted last_error) ── */
var _errorLog = [];
function _seedErrorLog() {
    _errorLog = [];
    reports.forEach(function(r) {
        if (r.last_error) {
            _errorLog.push({
                timestamp: r.last_run || new Date().toISOString(),
                slug: r.slug,
                name: r.name,
                message: r.last_error
            });
        }
    });
    _errorLog.sort(function(a, b) { return new Date(b.timestamp) - new Date(a.timestamp); });
}

/* Registry entries always carry the current studio's slug, so this list is
   normally a single value and renderStudioPills() hides the bar entirely. */
var _allStudios = [];
var activeStudios = new Set();
function _initStudios() {
    _allStudios = reports.map(function(r) { return r.studio; }).filter(Boolean)
        .filter(function(v, i, a) { return a.indexOf(v) === i; });
    var _savedStudios = (function() {
        try { return JSON.parse(localStorage.getItem('trellum_portal_studios')); } catch(e) { return null; }
    })();
    activeStudios = new Set(_savedStudios && _savedStudios.length ? _savedStudios : _allStudios);
}

const CLOCK_SVG = '<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd"/></svg>';


const STAR_FILLED = '<svg viewBox="0 0 20 20" fill="currentColor"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>';
const STAR_EMPTY = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>';
const FOLDER_SVG = '<svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"/></svg>';
const SEARCH_SVG = '<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"/></svg>';
const PLAY_SVG = '<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clip-rule="evenodd"/></svg>';
/* Trigger for the Deliveries drawer (static/report_delivery.js) — the
   same icons report pages use for their header button. Empty/filled is the
   same on/off shape-change pattern as the favorite star: filled + accent
   tint + a count badge is "you have schedules here", same as a filled star
   is "you favorited this". */
const ENVELOPE_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4.5" width="16" height="11" rx="2"/><path d="M2.6 5.5l7.4 5.8 7.4-5.8"/></svg>';
const ENVELOPE_FILLED_SVG = '<svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 5.6a1.6 1.6 0 011.6-1.6h12.8A1.6 1.6 0 0118 5.6v.3l-8 5.4-8-5.4v-.3z"/><path d="M2 7.7v6.7a1.6 1.6 0 001.6 1.6h12.8a1.6 1.6 0 001.6-1.6V7.7l-7.6 5.14a.8.8 0 01-.8 0L2 7.7z"/></svg>';
const EYE_OFF_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8.2 4.2A6.8 6.8 0 0110 4c4 0 7 4 7 6a7.9 7.9 0 01-1.6 2.3M5 5.7C3.1 7 2 8.8 2 10c0 2 3 6 8 6a7 7 0 003-.7"/><path d="M8.6 8.6a2 2 0 002.8 2.8"/><path d="M3 3l14 14"/></svg>';

/* ── Favorites (server-side, per user; /api/me/favorites) ── */
var _favSlugs = new Set();      /* slugs favorited in THIS studio */
var _favIds = {};               /* slug -> report_id */

function isFav(slug) { return _favSlugs.has(slug); }

function _favReportId(slug) {
    if (_favIds[slug] != null) return _favIds[slug];
    var r = reports.find(function(x) { return x.slug === slug; });
    return r && r.id != null ? r.id : null;
}

function loadFavorites() {
    return apiFetch('/api/me/favorites')
        .then(function(r) { return r.ok ? r.json() : { favorites: [] }; })
        .then(function(data) {
            _favSlugs = new Set();
            (data.favorites || []).forEach(function(f) {
                if (P && f.org_slug === P.org.slug && f.studio_slug === P.studio.slug) {
                    _favSlugs.add(f.slug);
                    _favIds[f.slug] = f.report_id;
                }
            });
        })
        .catch(function() {});
}

/* ── Envelope state (server-side, per user; /api/my-subscriptions) ──
   "Subscribed" = this user has an active email schedule on the report --
   same rule static/report_delivery.js uses for the report-page header
   button. Contract: {"reports": {"<slug>": {"schedules": <count>}}},
   sparse (a report with nothing set up is simply absent). */
var _subSlugs = new Set();
var _subCounts = {};   /* slug -> schedule count, only for slugs in _subSlugs */

function isSubscribed(slug) { return _subSlugs.has(slug); }
function subCount(slug) { return _subCounts[slug] || 0; }

function loadSubscriptions() {
    return studioFetch('/api/my-subscriptions')
        .then(function(r) { return r.ok ? r.json() : { reports: {} }; })
        .then(function(data) {
            _subSlugs = new Set();
            _subCounts = {};
            var byReport = data.reports || {};
            Object.keys(byReport).forEach(function(slug) {
                var count = byReport[slug].schedules || 0;
                if (count > 0) {
                    _subSlugs.add(slug);
                    _subCounts[slug] = count;
                }
            });
        })
        .catch(function() {});
}

/* The drawer (static/report_delivery.js) fires this on load and after any
   mutation -- keeps the dashboard's envelope in sync with the drawer's own
   state without a second network round trip per change. */
window.addEventListener('rdw:subscription-change', function(e) {
    var detail = e.detail || {};
    if (!detail.slug) return;
    if (detail.subscribed) {
        _subSlugs.add(detail.slug);
        _subCounts[detail.slug] = detail.count || 0;
    } else {
        _subSlugs.delete(detail.slug);
        delete _subCounts[detail.slug];
    }
    render();
});

/* One-time migration of the legacy localStorage favorites list. */
function migrateLegacyFavorites() {
    var raw = localStorage.getItem('trellum_portal_favs');
    if (raw === null) return Promise.resolve();
    var legacy = null;
    try { legacy = JSON.parse(raw); } catch (e) { legacy = null; }
    if (!Array.isArray(legacy) || legacy.length === 0) {
        localStorage.removeItem('trellum_portal_favs');
        return Promise.resolve();
    }
    return apiFetch('/api/me/favorites/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slugs: legacy })
    }).then(function(r) {
        if (r.ok) localStorage.removeItem('trellum_portal_favs');
    }).catch(function() {});
}

function toggleFav(slug) {
    var id = _favReportId(slug);
    if (id == null) {
        showToast('Cannot favorite ' + slug + ' (unknown report id)', 'error');
        return;
    }
    var wasFav = _favSlugs.has(slug);
    /* Optimistic: flip locally, re-render, roll back if the server refuses. */
    if (wasFav) _favSlugs.delete(slug);
    else { _favSlugs.add(slug); _favIds[slug] = id; }
    render();
    apiFetch('/api/me/favorites/' + id, { method: wasFav ? 'DELETE' : 'PUT' })
        .then(function(r) {
            if (r.ok) return;
            if (wasFav) _favSlugs.add(slug); else _favSlugs.delete(slug);
            showToast('Failed to update favorites', 'error');
            render();
        })
        .catch(function() {
            if (wasFav) _favSlugs.add(slug); else _favSlugs.delete(slug);
            showToast('Failed to update favorites', 'error');
            render();
        });
}

/* ── Helpers ── */
function relativeTime(iso) {
    if (!iso) return 'Never';
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
}
function cronLabel(cron) {
    if (!cron) return '';
    var parts = cron.split(' ');
    if (parts.length < 5) return cron;
    var m = parts[0], h = parts[1], dom = parts[2], mon = parts[3], dow = parts[4];
    if (m.startsWith('*/')) return 'Every ' + m.slice(2) + ' min';
    if (/^\d+$/.test(m) && h.startsWith('*/')) return 'Every ' + h.slice(2) + 'h';
    if (/^\d+$/.test(m) && h === '*') return 'Hourly';
    if (/^\d+$/.test(m) && /^\d+$/.test(h) && dom === '*' && mon === '*') {
        if (dow === '*') return 'Daily';
        return 'Weekly';
    }
    if (/^\d+$/.test(m) && /^\d+$/.test(h) && /^\d+$/.test(dom)) return 'Monthly';
    return cron;
}
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s) { return esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
/* What the Reports dashboard shows. The generated metrics report
   (apps.reports.metrics_catalog.is_generated_metrics_report, flagged
   `metrics_report` in the registry payload) is the build behind the Metrics
   page's charts, not a report anyone wrote — it belongs on that page, not as
   a card among the hand-written ones. Operations keeps using `reports`
   directly: it still has to be runnable, schedulable and diagnosable there. */
function dashboardReports() {
    return reports.filter(function(r) { return !r.metrics_report; });
}

function getCategories() {
    const cats = new Map();
    dashboardReports().forEach(r => {
        const c = r.category || 'Uncategorized';
        cats.set(c, (cats.get(c) || 0) + 1);
    });
    return cats;
}

function matchesSearch(r, q) {
    if (!q) return true;
    return (r.name + ' ' + r.description + ' ' + (r.tags || []).join(' ') + ' ' + r.category + ' ' + r.studio)
        .toLowerCase().includes(q);
}

/* ── Recent reports (localStorage) ── */
function getRecent() {
    try { return JSON.parse(localStorage.getItem('trellum_portal_recent') || '[]'); }
    catch { return []; }
}
function trackRecent(slug) {
    var recent = getRecent();
    recent = recent.filter(function(s) { return s !== slug; });
    recent.unshift(slug);
    if (recent.length > 8) recent = recent.slice(0, 8);
    localStorage.setItem('trellum_portal_recent', JSON.stringify(recent));
}

/* ── Studio filter rendering ── */
function renderStudioPills() {
    var el = document.getElementById('studioFilters');
    if (_allStudios.length <= 1) {
        document.getElementById('studioBar').style.display = 'none';
        return;
    }
    var allActive = activeStudios.size >= _allStudios.length;
    var html = '<button class="studio-pill' + (allActive ? ' active' : '') + '" data-studio="__all__">All</button>';
    _allStudios.forEach(function(s) {
        html += '<button class="studio-pill' + (activeStudios.has(s) ? ' active' : '') + '" data-studio="' + esc(s) + '">'
            + esc(s.toUpperCase()) + '</button>';
    });
    el.innerHTML = html;
}

/* ── Summary bar HTML ── */
/* Cards/List toggle: same icons the shell carried before the toggle moved
   into the launcher's own summary bar. Wired by the [data-setview]
   delegate — this HTML is rebuilt on every render(). */
var CARDS_ICON_SVG = '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M3 4a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm8 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V4zM3 12a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1v-4zm8 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z"/></svg>';
var LIST_ICON_SVG = '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M3 4a1 1 0 011-1h12a1 1 0 010 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 010 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 010 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 010 2H4a1 1 0 01-1-1z" clip-rule="evenodd"/></svg>';

function viewToggleHtml() {
    return '<div class="view-toggle" role="group" aria-label="View mode">'
        + '<button class="view-btn' + (viewMode === 'cards' ? ' active' : '') + '" data-setview="cards" title="Card view" aria-label="Card view">' + CARDS_ICON_SVG + '</button>'
        + '<button class="view-btn' + (viewMode === 'list' ? ' active' : '') + '" data-setview="list" title="List view" aria-label="List view">' + LIST_ICON_SVG + '</button>'
        + '</div>';
}

function renderSearchFeedback(total, q) {
    return '<div class="summary-search-row"><span class="summary-search">' + SEARCH_SVG
        + ' Found <strong>' + total + '</strong> report' + (total !== 1 ? 's' : '')
        + ' matching "' + esc(q) + '"'
        + '<span class="search-clear" onclick="clearSearch()">Clear</span></span></div>';
}

function renderSummary() {
    var html = '<div class="summary-bar">';

    // The right-aligned cluster: sort control (cards only) + the error-log
    // button + the Cards/List toggle. The error-log button used to be a
    // permanent fixture of the global shell (visible on pages it could never
    // affect, since its data is client-state populated only while rendering
    // this view) -- it lives here now, beside the other surface-specific
    // controls (ui-consistency-punchlist.md item 3). The Analytics link that
    // used to sit here is a tab in the studio tab bar now
    // (templates/_studio_nav.html), gated server-side on the same
    // developer/admin role this file used to check.
    var rightHtml = '';
    if (viewMode === 'cards') {
        rightHtml += '<label class="card-sort-label" for="cardSortSelect"><span>Sort</span>'
            + '<select class="card-sort-select" id="cardSortSelect">'
            + '<option value="priority"' + (cardSortCol === 'priority' ? ' selected' : '') + '>Priority</option>'
            + '<option value="name"' + (cardSortCol === 'name' ? ' selected' : '') + '>Name</option>'
            + '<option value="last_run"' + (cardSortCol === 'last_run' ? ' selected' : '') + '>Last Updated</option>'
            + '<option value="status"' + (cardSortCol === 'status' ? ' selected' : '') + '>Status</option>'
            + '</select></label>';
    } else if (viewMode === 'list') {
        rightHtml += '<label class="list-sort-label"><span>Sort</span>'
            + '<select class="list-sort-select" id="listSortSelect">'
            + '<option value="name"' + (sortCol === 'name' ? ' selected' : '') + '>Name</option>'
            + '<option value="category"' + (sortCol === 'category' ? ' selected' : '') + '>Category</option>'
            + '<option value="studio"' + (sortCol === 'studio' ? ' selected' : '') + '>Studio</option>'
            + '<option value="status"' + (sortCol === 'status' ? ' selected' : '') + '>Status</option>'
            + '<option value="schedule"' + (sortCol === 'schedule' ? ' selected' : '') + '>Schedule</option>'
            + '<option value="last_run"' + (sortCol === 'last_run' ? ' selected' : '') + '>Last updated</option>'
            + '</select></label>'
            + '<button class="list-sort-direction" id="listSortDirection" aria-label="Reverse sort order" title="Reverse sort order">'
            + (sortAsc ? '↑' : '↓') + '</button>';
    }
    rightHtml += '<button class="shell-icon-btn" id="errorLogBtn" onclick="toggleErrorLog(this)"'
        + ' title="Error log" aria-label="Error log">'
        + '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>'
        + '<span class="error-log-badge" id="errorLogBadge" style="display:none">0</span>'
        + '</button>';
    rightHtml += viewToggleHtml();
    html += '<span class="summary-right">' + rightHtml + '</span>';

    html += '</div>';
    return html;
}

/* ── URL state for deep linking ── */
function syncToUrl() {
    if (window.TrellumConsoleReportHost && window.TrellumConsoleReportHost.isActive()) return;
    var params = new URLSearchParams();
    if (activeFolder !== 'all') params.set('tab', activeFolder);
    if (searchQuery) params.set('q', searchQuery);
    /* Only the launcher's own cards/list state belongs in the URL — the ops
       surface IS its URL (/operations), so it never writes a view param. */
    if (viewMode === 'list') params.set('view', 'list');
    if (sortCol !== 'last_run') params.set('sort', sortCol);
    if (!sortAsc) params.set('asc', '0');
    if (activeStudios.size < _allStudios.length && _allStudios.length > 1) {
        params.set('studio', Array.from(activeStudios).join(','));
    }
    if (cardSortCol !== 'priority') params.set('csort', cardSortCol);
    var qs = params.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
}
function readFromUrl() {
    var params = new URLSearchParams(location.search);
    if (params.has('tab')) activeFolder = params.get('tab');
    if (params.has('q')) {
        searchQuery = params.get('q');
        document.getElementById('searchInput').value = searchQuery;
    }
    if (params.has('view') && !IS_OPS_PAGE) {
        /* ?view=ops / ?view=health never reach this code: the dashboard view
           redirects them to the Operations page server-side. */
        var v = params.get('view');
        if (v === 'cards' || v === 'list') viewMode = v;
    }
    if (params.has('sort')) {
        var validCols = ['name','category','studio','status','schedule','last_run'];
        var s = params.get('sort');
        if (validCols.indexOf(s) >= 0) sortCol = s;
    }
    if (params.has('asc')) sortAsc = params.get('asc') !== '0';
    if (params.has('studio')) {
        var vals = params.get('studio').split(',').filter(function(v) { return _allStudios.indexOf(v) >= 0; });
        if (vals.length > 0) activeStudios = new Set(vals);
    }
    if (params.has('csort')) {
        var validCardSorts = ['priority','name','last_run','status'];
        var cs = params.get('csort');
        if (validCardSorts.indexOf(cs) >= 0) cardSortCol = cs;
    }
}

/* ── Card sort helper ── */
function sortCardsPool(arr) {
    if (cardSortCol === 'priority') return arr;
    var copy = arr.slice();
    copy.sort(function(a, b) {
        var va, vb;
        if (cardSortCol === 'name') {
            va = (a.name || '').toLowerCase();
            vb = (b.name || '').toLowerCase();
            return va.localeCompare(vb);
        }
        if (cardSortCol === 'last_run') {
            va = a.last_run ? new Date(a.last_run).getTime() : 0;
            vb = b.last_run ? new Date(b.last_run).getTime() : 0;
            return vb - va;
        }
        if (cardSortCol === 'status') {
            var order = { success: 0, error: 1, not_run: 2 };
            va = order[a.last_status] !== undefined ? order[a.last_status] : 3;
            vb = order[b.last_status] !== undefined ? order[b.last_status] : 3;
            return va - vb;
        }
        return 0;
    });
    return copy;
}

/* The star, shared by the card and list renderers. The document-level
   [data-fav] delegate wires the click wherever this lands. */
function favBtnHtml(slug) {
    var fav = isFav(slug);
    return '<button class="fav-btn' + (fav ? ' is-fav' : '') + '" data-fav="' + slug + '" title="'
        + (fav ? 'Remove from favorites' : 'Add to favorites') + '">'
        + (fav ? STAR_FILLED : STAR_EMPTY)
        + '</button>';
}

/* Opens the Deliveries drawer (static/report_delivery.js) for one
   report. The document-level [data-email] delegate wires the click. */
function emailBtnHtml(slug) {
    var subscribed = isSubscribed(slug);
    var count = subCount(slug);
    var title = subscribed
        ? 'Deliveries (' + count + ' schedule' + (count === 1 ? '' : 's') + ')'
        : 'Deliveries';
    return '<button class="email-btn' + (subscribed ? ' is-subscribed' : '') + '" data-email="' + slug + '" title="' + title + '" aria-label="' + title + '">'
        + (subscribed ? ENVELOPE_FILLED_SVG : ENVELOPE_SVG)
        + (subscribed ? '<span class="email-count">' + count + '</span>' : '')
        + '</button>';
}

/* ── View analytics meta line + stale badge (internal planning#4) ── */
function viewsMetaText(r) {
    const views = r.views_30d || 0;
    if (!r.last_viewed) return 'not viewed yet';
    return 'last viewed ' + relativeTime(r.last_viewed) + ' · ' + views + ' view' + (views === 1 ? '' : 's') + ' (30d)';
}

function staleBadgeHtml(r) {
    if (!r.stale) return '';
    return '<span class="ui-badge info" title="Built recently, but no one has viewed it yet">'
        + EYE_OFF_SVG + 'unseen</span>';
}

/* A report that runs live queries when opened — data is fetched at view time,
   not baked into the last build. The pulsing dot separates it from a normal
   status badge. Server-gated: r.live is already false when the org disabled
   live queries (apps/reports/scan.py), so this never has to re-check policy. */
function liveChipHtml(r) {
    if (!r.live) return '';
    return '<span class="live-chip" title="Runs live queries when opened — data is fetched at view time, not from the last build">'
        + '<span class="live-dot"></span>Live</span>';
}

/* ── Card HTML ── */
function cardHtml(r) {
    const disabled = !r.has_output;
    const entry = r.html_entry || 'index.html';
    const href = disabled ? '#' : consoleReportUrl(apiUrl('/r/' + r.slug + '/' + entry));
    const cls = disabled ? ' disabled' : '';
    const tags = (r.tags || []).map(function(t) {
        return '<span class="tag" data-tag="' + esc(t) + '">' + esc(t) + '</span>';
    }).join('');
    const dot = statusDot(r);

    var runBtn = '';

    return '<a class="card' + cls + '" href="' + href + '" data-slug="' + r.slug + '"'
        + ' data-console-report-title="' + escAttr(r.name) + '"'
        + (disabled ? ' onclick="return false"' : '') + '>'
        + '<div class="card-top">'
        +   '<div class="card-name">' + esc(r.name) + '</div>'
        +   '<div class="card-actions">'
        +     runBtn
        +     emailBtnHtml(r.slug)
        +     favBtnHtml(r.slug)
        +     '<div class="card-status ' + dot.cls + '"' + (dot.title ? ' title="' + escAttr(dot.title) + '"' : '') + '></div>'
        +   '</div>'
        + '</div>'
        + '<div class="card-desc ui-desc-clamp">' + esc(r.description || '') + '</div>'
        + '<div class="card-meta">'
        +   liveChipHtml(r)
        +   (r.studio ? '<span class="studio-badge">' + r.studio.toUpperCase() + '</span>' : '')
        +   tags
        +   staleBadgeHtml(r)
        + '</div>'
        + '<div class="card-footer">'
        +   '<span>' + (r.last_run ? 'Updated ' + relativeTime(r.last_run) : 'Not yet run') + '</span>'
        +   '<span>' + cronLabel(r.schedule) + '</span>'
        + '</div>'
        + '<div class="card-views">' + esc(viewsMetaText(r)) + '</div>'
        + '</a>';
}

/* ── List / table HTML ── */
function statusLabel(s) {
    var labels = { success: 'Success', error: 'Error', not_run: 'Not run', waiting: 'Needs setup' };
    return labels[s] || s;
}

/* "Data source '<name>': <message>" -- the attribution the runner puts above
   a build error that a source check explains. */
function sourceFailure(text) {
    var m = /^Data source '([^']+)':\s*(.*)$/.exec((text || '').split('\n')[0]);
    return m ? { name: m[1], message: m[2] } : null;
}

/* The one status dot for cards and the list. Amber when the report is held
   on a data source, or is built but its source fails now (the build still
   serves); red for a failure of the build itself; grey never built; green. */
function statusDot(r) {
    var status = r.last_status || 'not_run';
    if (r.waiting) {
        return { cls: 'waiting', label: 'Needs setup',
                 title: 'Waiting for data source ' + (r.blocked_by || []).join(', ') };
    }
    if (r.has_output && r.failing_source) {
        var built = r.last_built_at || r.last_run;
        return { cls: 'waiting', label: 'Source failing',
                 title: 'Data source ' + r.failing_source.name + ' failing since '
                     + (r.failing_source.since ? relativeTime(r.failing_source.since) : 'its last check')
                     + (built ? ' — showing the build from ' + new Date(built).toLocaleDateString() : '') };
    }
    if (status === 'error') {
        var f = sourceFailure(r.last_error);
        return { cls: 'error', label: f ? 'Error · data source ' + f.name : 'Error',
                 title: r.last_error ? r.last_error.substring(0, 300) : '' };
    }
    return { cls: status, label: statusLabel(status), title: '' };
}

function sortReports(arr) {
    var copy = arr.slice();
    copy.sort(function(a, b) {
        var va, vb;
        if (sortCol === 'name') {
            va = (a.name || '').toLowerCase();
            vb = (b.name || '').toLowerCase();
            return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        if (sortCol === 'category') {
            va = (a.category || '').toLowerCase();
            vb = (b.category || '').toLowerCase();
            return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        if (sortCol === 'studio') {
            va = (a.studio || '').toLowerCase();
            vb = (b.studio || '').toLowerCase();
            return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        if (sortCol === 'status') {
            var order = { success: 0, error: 1, not_run: 2 };
            va = order[a.last_status] !== undefined ? order[a.last_status] : 3;
            vb = order[b.last_status] !== undefined ? order[b.last_status] : 3;
            return sortAsc ? va - vb : vb - va;
        }
        if (sortCol === 'schedule') {
            va = a.schedule || '';
            vb = b.schedule || '';
            return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        if (sortCol === 'last_run') {
            va = a.last_run ? new Date(a.last_run).getTime() : 0;
            vb = b.last_run ? new Date(b.last_run).getTime() : 0;
            return sortAsc ? va - vb : vb - va;
        }
        return 0;
    });
    return copy;
}

function thHtml(col, label) {
    var cls = sortCol === col ? ' sorted' : '';
    var arrow = sortCol === col ? (sortAsc ? '&#9650;' : '&#9660;') : '&#9650;';
    return '<th class="' + cls + '" data-sort="' + col + '">' + label + '<span class="sort-arrow">' + arrow + '</span></th>';
}

function listTableHtml(groups) {
    var html = '<div class="list-view"><table class="list-table"><thead><tr>';
    html += '<th class="list-fav" aria-label="Favorite"></th>';
    html += '<th class="list-email" aria-label="Deliveries"></th>';
    html += thHtml('name', 'Report');
    html += thHtml('category', 'Category');
    html += thHtml('studio', 'Studio');
    html += thHtml('status', 'Status');
    html += thHtml('schedule', 'Schedule');
    html += thHtml('last_run', 'Last Updated');
    html += '</tr></thead><tbody>';
    groups.forEach(function(group) {
        if (group.kind !== 'plain') {
            html += '<tr class="list-group-row"><td class="list-group-cell" colspan="8">'
                + catalogGroupHeaderHtml(group) + '</td></tr>';
        }
        group.items.forEach(function(r) {
        var disabled = !r.has_output;
        var entry = r.html_entry || 'index.html';
        var href = disabled ? '' : consoleReportUrl(apiUrl('/r/' + r.slug + '/' + entry));
        var cls = disabled ? ' class="disabled"' : '';
        var rowTarget = disabled ? '' : ' data-console-report-url="' + escAttr(href) + '"'
            + ' data-console-report-title="' + escAttr(r.name) + '"';
        var dot = statusDot(r);
        var tagHtml = (r.tags || []).map(function(t) {
            return '<span class="tag" data-tag="' + esc(t) + '" style="margin-left:4px">' + esc(t) + '</span>';
        }).join('');
        html += '<tr' + cls + rowTarget + ' data-slug="' + r.slug + '">';
        html += '<td class="list-fav" data-label="Favorite">' + favBtnHtml(r.slug) + '</td>';
        html += '<td class="list-email" data-label="Deliveries">' + emailBtnHtml(r.slug) + '</td>';
        html += '<td class="list-report" data-label="Report"><div class="list-name">'
            + (disabled ? '<span class="list-title">' + esc(r.name) + '</span>'
                : '<a class="list-title" href="' + escAttr(href) + '" data-console-report-title="'
                    + escAttr(r.name) + '">' + esc(r.name) + '</a>')
            + '<span class="list-tags">' + tagHtml + '</span></div>'
            + '<div class="list-desc" title="' + escAttr(r.description || '') + '">' + esc(r.description || '') + '</div></td>';
        html += '<td data-label="Category">' + esc(r.category || '') + '</td>';
        html += '<td data-label="Studio">' + (r.studio ? '<span class="studio-badge">' + r.studio.toUpperCase() + '</span>' : '—') + '</td>';
        html += '<td data-label="Status"><span class="list-status ' + dot.cls + '"' + (dot.title ? ' title="' + escAttr(dot.title) + '"' : '') + '></span><span class="list-status-label">' + esc(dot.label) + '</span></td>';
        html += '<td data-label="Schedule">' + cronLabel(r.schedule) + '</td>';
        html += '<td data-label="Last updated">' + (r.last_run ? relativeTime(r.last_run) : '<span style="color:var(--text3)">Never</span>') + '</td>';
        html += '</tr>';
        });
    });
    html += '</tbody></table></div>';
    return html;
}

/* Cards and rows use the same catalog sections. Favorites and recents stay
   visible above the category catalog on All; focused tabs and search results
   remain one sortable set. */
function catalogGroups(sortedPool, isSearching) {
    if (isSearching || activeFolder !== 'all') {
        return [{ kind: 'plain', items: sortedPool }];
    }

    var groups = [];
    var favorites = sortedPool.filter(function(r) { return isFav(r.slug); });
    if (favorites.length) {
        groups.push({ kind: 'favorites', label: 'Favorites', icon: STAR_FILLED, items: favorites });
    }

    var recent = getRecent().map(function(slug) {
        return sortedPool.find(function(r) { return r.slug === slug; });
    }).filter(Boolean).slice(0, 4);
    if (recent.length) {
        groups.push({ kind: 'recent', label: 'Recently Viewed', icon: CLOCK_SVG, items: recent });
    }

    var categories = Object.create(null);
    sortedPool.forEach(function(r) {
        var category = r.category || 'Uncategorized';
        if (!categories[category]) categories[category] = [];
        categories[category].push(r);
    });
    Object.keys(categories).forEach(function(category) {
        groups.push({ kind: 'category', label: category, items: categories[category] });
    });
    return groups;
}

function catalogGroupHeaderHtml(group) {
    if (group.kind === 'plain') return '';
    if (group.kind === 'category') {
        return '<div class="category-label">' + esc(group.label) + '</div>';
    }
    return '<div class="section-header">' + group.icon
        + '<span class="section-title">' + group.label + '</span></div>';
}

/* ── Folder tabs ── */
/* Categories are user-defined and unbounded — studios in production run 20+.
   They used to render as one long row inside a container whose scrollbar
   portal.css deliberately hides, so most categories were both off-screen and
   undiscoverable, and nothing ever scrolled the active one into view: the
   dashboard could sit filtered by a category the user could not see (and
   activeFolder persists, so that survived reloads).

   Now: tabs that do not fit move into a filterable "More" menu, and the
   active category is always promoted into the visible row. */
function catTabHtml(cat, count) {
    return '<button class="folder-tab' + (activeFolder === cat ? ' active' : '')
        + '" data-cat data-folder="' + escAttr(cat) + '">'
        + esc(cat) + ' <span class="count">' + count + '</span></button>';
}

function categorySelectHtml(cats, favCount, recentCount) {
    var options = [['all', 'All', reports.length]];
    if (favCount > 0) options.push(['_favorites', 'Favorites', favCount]);
    if (recentCount > 0) options.push(['_recent', 'Recent', recentCount]);
    cats.forEach(function(cat) { options.push([cat[0], cat[0], cat[1]]); });
    return '<label class="category-select"><span>Category</span><select aria-label="Report category">'
        + options.map(function(option) {
            return '<option value="' + escAttr(option[0]) + '"'
                + (activeFolder === option[0] ? ' selected' : '') + '>'
                + esc(option[1]) + ' (' + option[2] + ')</option>';
        }).join('') + '</select></label>';
}

function renderTabs() {
    const cats = Array.from(getCategories());
    const tabsEl = document.getElementById('folderTabs');
    const favCount = reports.filter(function(r) { return isFav(r.slug); }).length;
    const recentSlugs = getRecent();
    const recentCount = recentSlugs.filter(function(s) { return dashboardReports().some(function(r) { return r.slug === s; }); }).length;

    if (window.matchMedia('(max-width: 767px)').matches) {
        tabsEl.innerHTML = categorySelectHtml(cats, favCount, recentCount);
        return;
    }

    /* Carried across re-renders: render() runs on every search keystroke. */
    const prev = tabsEl.querySelector('.folder-more');
    const wasOpen = prev ? prev.open : false;
    const filterText = prev && prev.querySelector('.folder-more-filter')
        ? prev.querySelector('.folder-more-filter').value : '';

    /* Pinned tabs never move into the menu — they are the high-traffic ones. */
    let pinned = '<button class="folder-tab' + (activeFolder === 'all' ? ' active' : '') + '" data-folder="all">'
        + FOLDER_SVG + 'All <span class="count">' + dashboardReports().length + '</span></button>';
    if (favCount > 0) {
        pinned += '<button class="folder-tab' + (activeFolder === '_favorites' ? ' active' : '') + '" data-folder="_favorites">'
            + STAR_FILLED + 'Favorites <span class="count">' + favCount + '</span></button>';
    }
    if (recentCount > 0) {
        pinned += '<button class="folder-tab' + (activeFolder === '_recent' ? ' active' : '') + '" data-folder="_recent">'
            + CLOCK_SVG + 'Recent <span class="count">' + recentCount + '</span></button>';
    }

    /* Pass 1: lay every tab out so the browser can measure it. No paint
       happens between the two writes — this is all one synchronous frame. */
    tabsEl.innerHTML = pinned
        + cats.map(function(e) { return catTabHtml(e[0], e[1]); }).join('')
        /* The count is rendered at its widest possible value so the reserved
           width is never an underestimate — measuring "0" here left the row a
           few pixels over once the real count went double-digit. */
        + '<details class="folder-more"><summary class="folder-tab folder-more-summary">'
        + 'More <span class="count">' + cats.length + '</span></summary></details>';

    const split = splitTabs(tabsEl, cats);

    /* Pass 2: the row as it will actually be used. */
    let html = pinned;
    split.visible.forEach(function(i) { html += catTabHtml(cats[i][0], cats[i][1]); });
    if (split.overflow.length) {
        html += '<details class="folder-more"' + (wasOpen ? ' open' : '') + '>'
            + '<summary class="folder-tab folder-more-summary" aria-label="More categories">'
            + 'More <span class="count">' + split.overflow.length + '</span></summary>'
            + '<div class="folder-more-menu" role="menu">'
            + '<input type="text" class="folder-more-filter" placeholder="Filter categories…"'
            + ' aria-label="Filter categories" value="' + escAttr(filterText) + '">'
            + '<div class="folder-more-list">'
            + split.overflow.map(function(i) {
                return '<button class="folder-menu-item" role="menuitem" data-folder="' + escAttr(cats[i][0]) + '">'
                    + '<span class="folder-menu-label">' + esc(cats[i][0]) + '</span>'
                    + '<span class="count">' + cats[i][1] + '</span></button>';
            }).join('')
            + '</div><p class="folder-more-empty" hidden>No category matches.</p>'
            + '</div></details>';
    }
    tabsEl.innerHTML = html;
    if (filterText) filterMoreMenu(tabsEl, filterText);
}

/* Which category tabs fit on one row, given the pinned tabs and (when it is
   needed) the More control. Returns indices into `cats`. */
function splitTabs(tabsEl, cats) {
    const catTabs = Array.from(tabsEl.querySelectorAll('.folder-tab[data-cat]'));
    const avail = tabsEl.clientWidth;
    const all = cats.map(function(_, i) { return i; });
    if (!avail || !catTabs.length) return { visible: all, overflow: [] };

    /* The row is a flex container with a gap, which offsetWidth does not
       include — leaving it out silently overfills the row by gap x tabs. */
    const gap = parseFloat(getComputedStyle(tabsEl).columnGap) || 0;

    let base = 0;
    tabsEl.querySelectorAll('.folder-tab:not([data-cat]):not(.folder-more-summary)')
        .forEach(function(t) { base += t.offsetWidth + gap; });

    const widths = catTabs.map(function(t) { return t.offsetWidth + gap; });
    const total = widths.reduce(function(a, b) { return a + b; }, 0);
    if (base + total <= avail) return { visible: all, overflow: [] };

    const moreWidth = tabsEl.querySelector('.folder-more').offsetWidth + gap;
    let used = base + moreWidth;
    let cut = 0;
    /* Once one tab overflows every later one does too, so the row keeps the
       registry's category order instead of shuffling narrow tabs forward. */
    while (cut < widths.length && used + widths[cut] <= avail) {
        used += widths[cut];
        cut++;
    }

    const visible = all.slice(0, cut);
    const overflow = all.slice(cut);

    /* The active category must always be on screen. If it overflowed, drop
       visible tabs from the end until it fits, then show it last. */
    const activeIdx = cats.findIndex(function(e) { return e[0] === activeFolder; });
    if (activeIdx >= cut) {
        while (visible.length && used + widths[activeIdx] > avail) {
            used -= widths[visible.pop()];
        }
        overflow.splice(overflow.indexOf(activeIdx), 1);
        visible.push(activeIdx);
        overflow.sort(function(a, b) { return a - b; });
    }
    return { visible: visible, overflow: overflow };
}

function filterMoreMenu(tabsEl, query) {
    const q = query.trim().toLowerCase();
    let shown = 0;
    tabsEl.querySelectorAll('.folder-menu-item').forEach(function(item) {
        const label = item.querySelector('.folder-menu-label').textContent.toLowerCase();
        const hit = !q || label.indexOf(q) !== -1;
        item.hidden = !hit;
        if (hit) shown++;
    });
    const empty = tabsEl.querySelector('.folder-more-empty');
    if (empty) empty.hidden = shown > 0;
}

/* ── Empty states ── */
function emptyStateHtml(isSearching) {
    /* First run: the studio has no reports at all. Point an admin at the one
       action that fixes it — connecting the studio's reports repository. */
    if (reports.length === 0) {
        var html = '<div class="empty-state">' + SEARCH_SVG;
        if (P && P.user && P.user.role === 'admin') {
            html += '<p>No reports yet — connect this studio’s git repository and reports appear here.</p>'
                + '<p style="margin-top:12px"><a class="ops-action-btn" href="'
                + escAttr(apiUrl('/settings/repo')) + '">Configure repository</a></p>';
        } else {
            html += '<p>No reports yet — ask a studio admin to connect the reports repository.</p>';
        }
        return html + '</div>';
    }
    return '<div class="empty-state">' + SEARCH_SVG
        + '<p>' + (isSearching ? 'No reports match your search' : 'No reports in this category') + '</p>'
        + '</div>';
}

/* ── Main render ── */
function render() {
    const container = document.getElementById('content');
    const q = searchQuery.toLowerCase();
    const isSearching = q.length > 0;

    /* Validate persisted tab still exists */
    if (activeFolder !== 'all' && activeFolder !== '_favorites' && activeFolder !== '_recent') {
        var cats = getCategories();
        if (!cats.has(activeFolder)) activeFolder = 'all';
    }

    /* Build pool: tab + search filter */
    let pool;
    var shown = dashboardReports();
    if (isSearching) {
        pool = shown.filter(function(r) { return matchesSearch(r, q); });
    } else if (activeFolder === '_favorites') {
        pool = shown.filter(function(r) { return isFav(r.slug); });
    } else if (activeFolder === '_recent') {
        var recentOrder = getRecent();
        pool = recentOrder.map(function(s) { return shown.find(function(r) { return r.slug === s; }); }).filter(Boolean);
    } else if (activeFolder !== 'all') {
        pool = shown.filter(function(r) { return (r.category || 'Uncategorized') === activeFolder; });
    } else {
        pool = shown.slice();
    }

    /* Operations view: show folders for category filtering, hide studios */
    if (viewMode === 'ops') {
        document.getElementById('catalogToolbar').innerHTML = '';
        renderTabs();
        document.getElementById('studioBar').style.display = 'none';
        document.getElementById('folderTabs').parentElement.style.display = '';
        renderOperations();
        _restoreDsState();
        syncToUrl();
        return;
    }

    /* Show studio bar for card/list views */
    {
        renderStudioPills();
        if (_allStudios.length > 1) {
            pool = pool.filter(function(r) { return !r.studio || activeStudios.has(r.studio); });
        }
    }

    /* Controls share the category rail. Populate them first so renderTabs()
       measures the space that is actually left for category buttons. */
    document.getElementById('catalogToolbar').innerHTML = renderSummary();
    renderTabs();

    let html = isSearching ? renderSearchFeedback(pool.length, q) : '';

    if (!pool.length) {
        html += emptyStateHtml(isSearching);
        container.innerHTML = html;
        syncToUrl();
        return;
    }

    if (viewMode === 'list') {
        html += listTableHtml(catalogGroups(sortReports(pool), isSearching));
        container.innerHTML = html;
        syncToUrl();
        return;
    }

    /* Card view: apply card sort */
    var sortedPool = sortCardsPool(pool);

    catalogGroups(sortedPool, isSearching).forEach(function(group) {
        html += catalogGroupHeaderHtml(group) + '<div class="card-grid">';
        group.items.forEach(function(r) { html += cardHtml(r); });
        html += '</div>';
    });

    container.innerHTML = html;

    /* Bind card sort dropdown. The control is replaced whenever render()
       rebuilds the catalog toolbar, so this listener stays single-use. */
    var csel = document.getElementById('cardSortSelect');
    if (csel) {
        csel.addEventListener('change', function() {
            cardSortCol = csel.value;
            localStorage.setItem('trellum_portal_card_sort', cardSortCol);
            render();
        });
    }

    syncToUrl();
}

function clearSearch() {
    document.getElementById('searchInput').value = '';
    searchQuery = '';
    render();
}

/* ── Event delegation ── */
document.getElementById('searchInput').addEventListener('input', function(e) {
    searchQuery = e.target.value;
    if (searchQuery) activeFolder = 'all';
    render();
});

document.getElementById('folderTabs').addEventListener('click', function(e) {
    const tab = e.target.closest('[data-folder]');
    if (!tab) return;
    activeFolder = tab.dataset.folder;
    localStorage.setItem('trellum_portal_tab', activeFolder);
    /* Picking from the menu closes it; renderTabs then promotes the chosen
       category into the visible row. */
    const menu = this.querySelector('.folder-more');
    if (menu) menu.open = false;
    render();
});

document.getElementById('folderTabs').addEventListener('change', function(e) {
    if (!e.target.closest('.category-select')) return;
    activeFolder = e.target.value;
    localStorage.setItem('trellum_portal_tab', activeFolder);
    render();
});

/* Filtering the More menu must not re-render the row — that would blow away
   the input the user is typing in. */
document.getElementById('folderTabs').addEventListener('input', function(e) {
    if (!e.target.classList.contains('folder-more-filter')) return;
    filterMoreMenu(this, e.target.value);
});

/* Focus the filter as soon as the menu opens: with many categories, typing is
   the fast path. 'toggle' does not bubble, so listen in the capture phase. */
document.getElementById('folderTabs').addEventListener('toggle', function(e) {
    if (e.target.classList.contains('folder-more') && e.target.open) {
        const input = e.target.querySelector('.folder-more-filter');
        if (input) input.focus();
    }
}, true);

document.addEventListener('click', function(e) {
    const menu = document.querySelector('.folder-more[open]');
    if (menu && !menu.contains(e.target)) menu.open = false;
});

/* The row is measured, so a resize can change what fits. */
var _tabFitTimer = null;
window.addEventListener('resize', function() {
    clearTimeout(_tabFitTimer);
    _tabFitTimer = setTimeout(renderTabs, 120);
});

/* Studio filter pills */
document.getElementById('studioFilters').addEventListener('click', function(e) {
    var pill = e.target.closest('[data-studio]');
    if (!pill) return;
    var val = pill.dataset.studio;
    if (val === '__all__') {
        activeStudios = new Set(_allStudios);
    } else {
        if (activeStudios.has(val)) {
            activeStudios.delete(val);
            if (activeStudios.size === 0) activeStudios = new Set(_allStudios);
        } else {
            activeStudios.add(val);
        }
    }
    localStorage.setItem('trellum_portal_studios', JSON.stringify(Array.from(activeStudios)));
    render();
});

document.getElementById('content').addEventListener('click', function(e) {
    /* Clickable tags */
    var tagEl = e.target.closest('[data-tag]');
    if (tagEl) {
        e.preventDefault();
        e.stopPropagation();
        searchQuery = tagEl.dataset.tag;
        document.getElementById('searchInput').value = searchQuery;
        activeFolder = 'all';
        localStorage.setItem('trellum_portal_tab', 'all');
        render();
        return;
    }

    const runBtn = e.target.closest('[data-run]');
    if (runBtn) {
        e.preventDefault();
        e.stopPropagation();
        runReport(runBtn.dataset.run);
        return;
    }
    const favBtn = e.target.closest('[data-fav]');
    if (favBtn) {
        e.preventDefault();
        e.stopPropagation();
        toggleFav(favBtn.dataset.fav);
        return;
    }
    const emailBtn = e.target.closest('[data-email]');
    if (emailBtn) {
        e.preventDefault();
        e.stopPropagation();
        var slug = emailBtn.dataset.email;
        var rep = reports.find(function(r) { return r.slug === slug; });
        window.ReportDelivery.open(P.prefix, slug, rep ? rep.name : slug, emailBtn);
        return;
    }
    const th = e.target.closest('[data-sort]');
    if (th) {
        const col = th.dataset.sort;
        if (sortCol === col) { sortAsc = !sortAsc; }
        else { sortCol = col; sortAsc = col === 'last_run' ? false : true; }
        localStorage.setItem('trellum_portal_sort_col', sortCol);
        localStorage.setItem('trellum_portal_sort_asc', String(sortAsc));
        render();
        return;
    }

    /* Track recent on card click */
    var card = e.target.closest('a.card[data-slug]');
    if (card && !card.classList.contains('disabled')) {
        trackRecent(card.dataset.slug);
        return;
    }
    var reportRow = e.target.closest('tr[data-console-report-url]');
    if (reportRow && !e.target.closest('button,a,[data-tag]')) {
        trackRecent(reportRow.dataset.slug);
        openConsoleReport(reportRow.dataset.consoleReportUrl, e, reportRow.dataset.consoleReportTitle);
    }
});

function openConsoleReport(href, event, title) {
    if (event && (event.button !== 0 || event.metaKey || event.ctrlKey
            || event.shiftKey || event.altKey)) return true;
    if (event) event.preventDefault();
    if (window.TrellumConsoleReportHost
            && window.TrellumConsoleReportHost.open(href, { title: title })) return false;
    window.location.assign(href);
    return false;
}

/* ── Toasts ──
   Stacked: each toast is appended to a live region and dismisses on its
   own timer, so a burst of results is readable instead of overwriting
   itself. Oldest is dropped once the stack is full. */
const TOAST_MAX = 4;

function _toastStack() {
    var stack = document.getElementById('toastStack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'toastStack';
        stack.className = 'toast-stack';
        stack.setAttribute('aria-live', 'polite');
        stack.setAttribute('aria-atomic', 'false');
        document.body.appendChild(stack);
    }
    return stack;
}

function showToast(msg, type) {
    var stack = _toastStack();
    while (stack.children.length >= TOAST_MAX) stack.removeChild(stack.firstChild);
    var div = document.createElement('div');
    div.className = 'toast ' + (type || 'info');
    div.setAttribute('role', type === 'error' ? 'alert' : 'status');
    if (type === 'error') {
        var truncated = msg.length > 120 ? msg.substring(0, 120) + '...' : msg;
        div.innerHTML = '<span>' + esc(truncated) + '</span><span class="toast-details">View details</span>';
        div.addEventListener('click', function() { div.remove(); toggleErrorLog(); });
        setTimeout(function() { div.remove(); }, 6000);
    } else {
        div.textContent = msg;
        setTimeout(function() { div.remove(); }, 4000);
    }
    stack.appendChild(div);
}

/* ── Run report ── */
function invalidate_registry_cache() {
    return studioFetch('/api/system/registry/refresh', { method: 'POST' }).catch(function() {});
}

function runReport(slug, cacheMode) {
    if (_runningSet[slug]) return;
    _runningSet[slug] = true;
    render();
    var body = {};
    /* `cache_mode` is what the Django run endpoint reads; `cache` is kept for
       the legacy handler's key so either backend accepts the request. */
    if (cacheMode && cacheMode !== 'normal') { body.cache = cacheMode; body.cache_mode = cacheMode; }
    studioFetch('/api/reports/' + encodeURIComponent(slug) + '/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
            if (data.status === 'already_running') {
                delete _runningSet[slug];
                showToast(slug + ' is already running', 'error');
                render();
                return;
            }
            pollReportRun(slug, cacheMode);
        })
        .catch(function(err) {
            delete _runningSet[slug];
            var r = reports.find(function(x) { return x.slug === slug; });
            var errMsg = 'Failed to start ' + slug + ': ' + err.message;
            if (r) addError(slug, r.name, errMsg);
            showToast(errMsg, 'error');
            render();
        });
}

function pollReportRun(slug, cacheMode) {
    var iv = setInterval(function() {
        studioFetch('/api/reports/' + encodeURIComponent(slug) + '/status')
            .then(function(resp) { return resp.json(); })
            .then(function(st) {
                if (st.state === 'running' || st.state === 'queued') return;
                clearInterval(iv);
                delete _runningSet[slug];
                studioFetch('/api/registry')
                    .then(function(resp) { return resp.json(); })
                    .then(function(reg) {
                        reports = reg.reports;
                        var r = reports.find(function(x) { return x.slug === slug; });
                        var hist = st.history && st.history[0];
                        if (hist && hist.status === 'success') {
                            if (r) { r.last_error = null; r.has_output = true; }
                            var modeLabel = cacheMode === 'fresh' ? ' (fresh)' : cacheMode === 'force' ? ' (cached)' : '';
                            showToast(r ? r.name + ' updated' + modeLabel : slug + ' done', 'success');
                        } else if (hist && hist.status === 'error') {
                            var errMsg = (r && r.last_error) || 'run failed';
                            if (r) r.last_error = errMsg;
                            addError(slug, r ? r.name : slug, errMsg);
                            showToast(errMsg, 'error');
                        } else {
                            showToast(slug + ' finished', 'success');
                        }
                        render();
                    });
            })
            .catch(function() {
                clearInterval(iv);
                delete _runningSet[slug];
                render();
            });
    }, 2000);
}

/* ── Error log panel ── */
function addError(slug, name, message) {
    _errorLog.unshift({
        timestamp: new Date().toISOString(),
        slug: slug,
        name: name || slug,
        message: message
    });
    updateErrorBadge();
}

function updateErrorBadge() {
    var badge = document.getElementById('errorLogBadge');
    var btn = document.getElementById('errorLogBtn');
    // The button now lives in the summary bar (renderSummary), which the
    // Operations view never renders -- an action there can still addError()
    // (the toast already told the user), so this has nothing to update.
    if (!badge || !btn) return;
    if (_errorLog.length > 0) {
        badge.textContent = _errorLog.length;
        badge.style.display = 'flex';
        btn.classList.add('has-errors');
    } else {
        badge.style.display = 'none';
        btn.classList.remove('has-errors');
    }
    var countEl = document.getElementById('errorLogCount');
    if (countEl) countEl.textContent = _errorLog.length > 0 ? '(' + _errorLog.length + ')' : '';
}

var _drawerReturnFocus = {};
function drawerFocusables(drawer) {
    return Array.prototype.filter.call(drawer.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ), function(el) { return el.getClientRects().length > 0; });
}

function setDrawerOpen(drawerId, overlayId, open, trigger) {
    var drawer = document.getElementById(drawerId);
    var overlay = document.getElementById(overlayId);
    if (!drawer || !overlay) return;
    if (open) {
        _drawerReturnFocus[drawerId] = trigger || document.activeElement;
        drawer.removeAttribute('inert');
        drawer.setAttribute('aria-hidden', 'false');
        drawer.classList.add('open');
        overlay.classList.add('open');
        requestAnimationFrame(function() {
            var first = drawerFocusables(drawer)[0];
            if (first) first.focus();
        });
    } else {
        drawer.classList.remove('open');
        overlay.classList.remove('open');
        drawer.setAttribute('inert', '');
        drawer.setAttribute('aria-hidden', 'true');
        var returnFocus = _drawerReturnFocus[drawerId];
        if (returnFocus && returnFocus.isConnected) returnFocus.focus();
        delete _drawerReturnFocus[drawerId];
    }
}

function toggleErrorLog(trigger) {
    var drawer = document.getElementById('errorLogDrawer');
    var isOpen = drawer.classList.contains('open');
    if (isOpen) {
        setDrawerOpen('errorLogDrawer', 'errorLogOverlay', false);
    } else {
        renderErrorLog();
        setDrawerOpen('errorLogDrawer', 'errorLogOverlay', true, trigger);
    }
}

function renderErrorLog() {
    var body = document.getElementById('errorLogBody');
    updateErrorBadge();
    if (_errorLog.length === 0) {
        body.innerHTML = '<div class="error-log-empty">No errors recorded this session.</div>';
        return;
    }
    var html = '';
    _errorLog.forEach(function(entry, idx) {
        html += '<div class="error-log-entry">'
            + '<div class="error-log-entry-header">'
            + '<span class="error-log-entry-name">' + esc(entry.name) + '</span>'
            + '<span style="display:flex;align-items:center;gap:8px;">'
            + '<span class="error-log-entry-time">' + relativeTime(entry.timestamp) + '</span>'
            + '<button class="error-log-entry-dismiss" onclick="event.stopPropagation(); dismissError(' + idx + ')" title="Dismiss">&times;</button>'
            + '</span>'
            + '</div>'
            + '<div class="error-log-entry-msg">' + esc(entry.message) + '</div>'
            + '</div>';
    });
    body.innerHTML = html;
}

function dismissError(idx) {
    _errorLog.splice(idx, 1);
    updateErrorBadge();
    renderErrorLog();
}

function clearErrorLog() {
    _errorLog = [];
    updateErrorBadge();
    renderErrorLog();
}

/* ── Validation drawer ── */
function toggleValidation() {
    var drawer = document.getElementById('validationDrawer');
    var isOpen = drawer.classList.contains('open');
    setDrawerOpen('validationDrawer', 'validationOverlay', !isOpen);
}

function showValidation(slug) {
    var r = reports.find(function(rep) { return rep.slug === slug; });
    if (!r) return;
    if (!r.validation) {
        var title = document.getElementById('validationTitle');
        title.textContent = r.name + ' — Details';
        var body = document.getElementById('validationBody');
        body.innerHTML = '<div class="validation-empty">This report has not been validated yet.<br><br>'
            + '<span style="font-size:12px;color:var(--text3)">Run the report to generate validation data.</span></div>';
        toggleValidation();
        return;
    }
    var v = r.validation;
    var title = document.getElementById('validationTitle');
    title.textContent = r.name + ' — Details';
    var body = document.getElementById('validationBody');

    var s = v.summary || {};
    var summaryHtml = '<div class="validation-summary">';
    if (s.pass) summaryHtml += '<span class="validation-badge pass">' + s.pass + ' pass</span>';
    if (s.fail) summaryHtml += '<span class="validation-badge fail">' + s.fail + ' fail</span>';
    if (s.warn) summaryHtml += '<span class="validation-badge warn">' + s.warn + ' warn</span>';
    if (s.info) summaryHtml += '<span class="validation-badge info">' + s.info + ' info</span>';
    if (s.suppressed) summaryHtml += '<span class="validation-badge suppressed">' + s.suppressed + ' suppressed</span>';
    summaryHtml += '</div>';

    var detailsHtml = renderDetailsBlock(r.details);

    var checks = v.checks || [];
    var groups = { fail: [], warn: [], info: [], pass: [], suppressed: [] };
    checks.forEach(function(c) {
        if (c.suppressed) {
            groups.suppressed.push(c);
            return;
        }
        if (groups[c.level]) groups[c.level].push(c);
    });

    var checksHtml = '';
    ['fail', 'warn', 'info'].forEach(function(level) {
        if (groups[level].length === 0) return;
        var labels = { fail: 'Failures', warn: 'Warnings', info: 'Info' };
        checksHtml += '<div class="validation-group">'
            + '<div class="validation-group-title">' + labels[level] + ' (' + groups[level].length + ')</div>';
        groups[level].forEach(function(c) {
            checksHtml += '<div class="validation-check ' + c.level + '">'
                + '<div class="validation-check-header">'
                + '<span class="validation-check-id">' + esc(c.id) + '</span>'
                + '<span class="validation-check-level ' + c.level + '">' + c.level + '</span>'
                + '</div>'
                + '<div class="validation-check-msg">' + esc(c.message) + '</div>'
                + (c.component || c.section ? '<div class="validation-check-meta">'
                    + (c.component ? 'Component: ' + esc(c.component) : '')
                    + (c.component && c.section ? ' &middot; ' : '')
                    + (c.section ? 'Section: ' + esc(c.section) : '')
                    + '</div>' : '')
                + '</div>';
        });
        checksHtml += '</div>';
    });

    if (groups.suppressed.length > 0) {
        checksHtml += '<div class="validation-group">'
            + '<div class="validation-group-title">Suppressed (' + groups.suppressed.length + ')</div>'
            + '<div class="validation-suppressed-note">Deliberately silenced via report.yaml. Underlying issue is real but accepted.</div>';
        groups.suppressed.forEach(function(c) {
            var dsTag = c.dataset_id ? ' &middot; <code>' + esc(c.dataset_id) + '</code>' : '';
            checksHtml += '<div class="validation-check suppressed">'
                + '<div class="validation-check-header">'
                + '<span class="validation-check-id">' + esc(c.id) + '</span>'
                + '<span class="validation-check-level suppressed">suppressed</span>'
                + '</div>'
                + '<div class="validation-check-msg">' + esc(c.message) + '</div>'
                + (c.component || c.section || c.dataset_id ? '<div class="validation-check-meta">'
                    + (c.component ? 'Component: ' + esc(c.component) : '')
                    + (c.component && c.section ? ' &middot; ' : '')
                    + (c.section ? 'Section: ' + esc(c.section) : '')
                    + dsTag
                    + '</div>' : '')
                + '</div>';
        });
        checksHtml += '</div>';
    }

    if (groups.pass.length > 0) {
        checksHtml += '<button class="validation-passed-toggle" onclick="'
            + 'var el=this.nextElementSibling;el.style.display=el.style.display===\'none\'?\'block\':\'none\';'
            + 'this.textContent=el.style.display===\'none\'?\'Show \'+' + groups.pass.length + '+\' passed checks\':\'Hide passed checks\';'
            + '">Show ' + groups.pass.length + ' passed checks</button>'
            + '<div style="display:none"><div class="validation-group">'
            + '<div class="validation-group-title">Passed (' + groups.pass.length + ')</div>';
        groups.pass.forEach(function(c) {
            checksHtml += '<div class="validation-check pass">'
                + '<div class="validation-check-header">'
                + '<span class="validation-check-id">' + esc(c.id) + '</span>'
                + '<span class="validation-check-level pass">pass</span>'
                + '</div>'
                + '<div class="validation-check-msg">' + esc(c.message) + '</div>'
                + '</div>';
        });
        checksHtml += '</div></div>';
    }

    if (checks.length === 0) {
        checksHtml = '<div class="validation-empty">No validation data available.</div>';
    }

    body.innerHTML = summaryHtml + detailsHtml + checksHtml;
    toggleValidation();
}

function _fmtBytes(n) {
    if (n == null || n === 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = 0; var v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + units[i];
}

function _fmtNumber(n) {
    if (n == null) return '0';
    return Number(n).toLocaleString('en-US');
}

function _statusGlyph(status, suppressed) {
    if (status === 'active') return '✓';
    if (status === 'propagated') return '↗';
    if (status === 'untracked') return '·';
    if (status === 'inactive') return suppressed ? '✗ⓢ' : '✗';
    return '';
}

function renderDetailsBlock(details) {
    if (!details) return '';

    var totals = details.totals || {};
    var mockBanner = '';
    if (details.data_source === 'mock') {
        mockBanner = '<div class="details-mock-banner">'
            + '<strong>Mock data run.</strong> Row counts and sizes are from '
            + 'synthetic test DataFrames (~30 rows each), not the real database. '
            + 'Re-run against the real DB to see production figures.</div>';
    }

    var totalsHtml = mockBanner + '<div class="details-totals">'
        + '<div class="details-totals-pill"><span class="label">Datasets</span>'
        + '<span class="value">' + _fmtNumber(totals.datasource_count || 0) + '</span></div>'
        + '<div class="details-totals-pill"><span class="label">Charts</span>'
        + '<span class="value">' + _fmtNumber(totals.chart_count || 0) + '</span></div>'
        + '<div class="details-totals-pill"><span class="label">'
        + (details.data_source === 'mock' ? 'Mock rows' : 'Total rows')
        + '</span>'
        + '<span class="value">' + _fmtNumber(totals.total_rows || 0) + '</span></div>'
        + '<div class="details-totals-pill"><span class="label">Data size</span>'
        + '<span class="value">' + _fmtBytes(totals.total_size_bytes || 0) + '</span></div>'
        + '</div>';

    var matrix = details.filter_matrix || [];

    // Group matrix rows by scope so multi-scope reports stay readable.
    var byScope = {};
    var scopeOrder = [];
    matrix.forEach(function(row) {
        var key = row.scope || '';
        if (!(key in byScope)) { byScope[key] = []; scopeOrder.push(key); }
        byScope[key].push(row);
    });

    var matrixHtml = '<div class="details-section-title">Filter coverage</div>';
    if (matrix.length === 0) {
        matrixHtml += '<div class="details-empty">No charts in this report.</div>';
    } else {
        scopeOrder.forEach(function(scope) {
            var rows = byScope[scope];
            // Collect filter columns in order of first appearance per scope.
            var cols = []; var seen = {};
            rows.forEach(function(r) {
                (r.filters || []).forEach(function(f) {
                    if (!seen[f.column]) { seen[f.column] = 1; cols.push(f.column); }
                });
            });

            if (scope) matrixHtml += '<div class="details-scope-label">Scope: ' + esc(scope) + '</div>';

            if (cols.length === 0) {
                matrixHtml += '<div class="details-empty">No FilterBar in this view — charts render statically.</div>';
                return;
            }

            matrixHtml += '<div class="details-matrix-wrap"><table class="details-matrix">';
            matrixHtml += '<thead><tr><th class="chart-col">Chart</th>';
            cols.forEach(function(c) {
                matrixHtml += '<th title="' + escAttr(c) + '">' + esc(c) + '</th>';
            });
            matrixHtml += '</tr></thead><tbody>';

            rows.forEach(function(row) {
                var byCol = {};
                (row.filters || []).forEach(function(f) { byCol[f.column] = f; });
                var title = row.chart_title || row.chart_kind || '';
                var ds = row.dataset_id ? ' [' + row.dataset_id + ']' : '';
                matrixHtml += '<tr><td class="chart-col" title="' + escAttr(title + ds) + '">'
                    + esc(title)
                    + '<span class="kind">' + esc(row.chart_kind || '') + '</span>'
                    + '</td>';
                cols.forEach(function(c) {
                    var f = byCol[c];
                    if (!f) {
                        matrixHtml += '<td class="cell"></td>';
                    } else {
                        var cls = 'cell ' + f.status + (f.suppressed ? ' suppressed' : '');
                        var sup = f.suppressed ? ' (suppressed by report.yaml)' : '';
                        matrixHtml += '<td class="' + cls + '" title="'
                            + escAttr(f.column + ' → '
                                + (f.target_column || '(none)')
                                + ' [' + f.status + sup + ']: ' + (f.reason || ''))
                            + '">' + _statusGlyph(f.status, f.suppressed) + '</td>';
                    }
                });
                matrixHtml += '</tr>';
            });
            matrixHtml += '</tbody></table></div>';
        });

        matrixHtml += '<div class="details-legend">'
            + '<span><span class="glyph active">✓</span>active</span>'
            + '<span><span class="glyph propagated">↗</span>propagated</span>'
            + '<span><span class="glyph inactive">✗</span>inactive — filter does not reach this chart</span>'
            + '<span><span class="glyph inactive">✗ⓢ</span>inactive but suppressed in report.yaml (deliberately accepted)</span>'
            + '<span><span class="glyph untracked">·</span>untracked (RawHTML / ABCompare)</span>'
            + '</div>';
    }

    return totalsHtml + matrixHtml;
}

/* ── View toggle (Cards/List) ──
   Lives in the summary bar (renderSummary), rebuilt on every render(), so
   it is wired by delegation rather than by id. Operations is a page now
   (the studio tab bar) — 'ops' is never a value this control can set. */
function setView(mode) {
    viewMode = mode === 'list' ? 'list' : 'cards';
    localStorage.setItem('trellum_portal_view', viewMode);
    render();
}
document.addEventListener('click', function(e) {
    var btn = e.target && e.target.closest ? e.target.closest('[data-setview]') : null;
    if (btn) setView(btn.getAttribute('data-setview'));
});
document.addEventListener('change', function(e) {
    if (e.target.id !== 'listSortSelect') return;
    sortCol = e.target.value;
    localStorage.setItem('trellum_portal_sort_col', sortCol);
    render();
});
document.addEventListener('click', function(e) {
    if (!e.target.closest('#listSortDirection')) return;
    sortAsc = !sortAsc;
    localStorage.setItem('trellum_portal_sort_asc', String(sortAsc));
    render();
});

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', function(e) {
    var activeDrawer = document.querySelector('.error-log-drawer.open, .validation-drawer.open, .server-log-drawer.open');
    if (activeDrawer && e.key === 'Tab') {
        var items = drawerFocusables(activeDrawer);
        if (!items.length) { e.preventDefault(); return; }
        var first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        return;
    }
    if (activeDrawer && e.key !== 'Escape') return;
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        var inp = document.getElementById('searchInput');
        inp.focus();
        inp.select();
    }
    if (e.key === 'Escape') {
        /* Close one layer per Esc, most-recently-layered first. The category
           menu sits above the drawers. */
        var moreMenu = document.querySelector('.folder-more[open]');
        if (moreMenu) {
            moreMenu.open = false;
            return;
        }
        var sDrawer = document.getElementById('serverLogDrawer');
        if (sDrawer && sDrawer.classList.contains('open')) {
            closeServerLog();
            return;
        }
        var vDrawer = document.getElementById('validationDrawer');
        if (vDrawer && vDrawer.classList.contains('open')) {
            toggleValidation();
            return;
        }
        var drawer = document.getElementById('errorLogDrawer');
        if (drawer && drawer.classList.contains('open')) {
            toggleErrorLog();
        }
    }
});

/* fw-theme one-time adoption (retired): theme is a per-studio property now
   (Studio.theme / StudioMembership.theme / Organization.default_theme, see
   apps.core.themes.resolve_studio_theme), not a single per-viewer global a
   browser-local localStorage key could ever have adopted into correctly --
   there is no one "the server preference" left to adopt fw-theme INTO. The
   retired global setter (apps.accounts.views.theme_set, POST
   /account/theme) this used to call is gone along with it. Nothing reads
   localStorage['fw-theme'] any more; a stale key left over from before this
   pass is simply inert.
*/

/* ══════════════════════════════════════════════════
   OPERATIONS VIEW
   ══════════════════════════════════════════════════ */
var _opsFilter = 'all';
var _opsExpandedSlugs = {};
var _opsLivePollers = {};
var _opsUserRoles = null;
var _opsSystemActions = {};
window.matchMedia('(min-width: 768px)').addEventListener('change', function() {
    document.querySelectorAll('.ops-secondary').forEach(function(menu) {
        menu.open = false;
    });
});

/* The user's effective role in THIS studio comes from the server-rendered
   context (P.user.role: viewer|developer|admin); higher roles imply lower. */
function _studioRoleSet() {
    var role = (P && P.user && P.user.role) || 'viewer';
    if (role === 'admin') return new Set(['admin', 'developer', 'viewer']);
    if (role === 'developer') return new Set(['developer', 'viewer']);
    return new Set(['viewer']);
}
function opsEnsureRoles(cb) {
    if (_opsUserRoles === null) _opsUserRoles = _studioRoleSet();
    cb();
}
function opsHasRole() {
    for (var i = 0; i < arguments.length; i++) {
        if (_opsUserRoles && _opsUserRoles.has(arguments[i])) return true;
    }
    return false;
}

function renderOperations() {
    Object.keys(_opsLivePollers).forEach(function(s) { clearInterval(_opsLivePollers[s]); });
    _opsLivePollers = {};
    opsEnsureRoles(function() { _renderOpsContent(); });
}

function _renderOpsContent() {
    var container = document.getElementById('content');
    /* Check git sync status once to determine whether to show the button */
    var gitReady = window._gitSyncChecked
        ? Promise.resolve()
        : studioFetch('/api/system/git/status').then(function(r) { return r.json(); }).then(function(gs) {
            window._gitSyncConfigured = gs && gs.configured;
            window._gitStatus = gs || {};
            window._gitSyncChecked = true;
        }).catch(function() { window._gitSyncConfigured = false; window._gitSyncChecked = true; });

    gitReady.then(function() {
        Promise.all([
            studioFetch('/api/system/status').then(function(r) { return r.json(); }),
            studioFetch('/api/registry').then(function(r) { return r.json(); }),
            _opsFetchRunStats()
        ]).then(function(results) {
            var status = results[0], reg = results[1];
            container.innerHTML = _buildOpsHTML(status, reg.reports || reports);
            _opsBindEvents(status, reg.reports || reports);
            _restoreDsState();
            _opsStartLivePollers(status);
        }).catch(function() {
            container.innerHTML = _buildOpsHTML({running:{},queue:[],max_concurrent:10}, reports);
            _opsBindEvents({running:{},queue:[]}, reports);
            _restoreDsState();
        });
    });
}

/* Per-slug run aggregates (duration/memory) for the Operations table.
   Kept in a module-level cache so table cells can be built synchronously;
   a fetch failure keeps the previous snapshot and cells degrade to em-dash. */
var _opsRunStats = {};
function _opsFetchRunStats() {
    return studioFetch('/api/system/run-stats')
        .then(function(r) { return r.json(); })
        .then(function(data) { _opsRunStats = (data && data.stats) || {}; })
        .catch(function() {});
}

function _buildOpsHTML(status, reportList) {
    /* Apply folder/category filter (same as cards/list views) */
    if (activeFolder === '_favorites') {
        reportList = reportList.filter(function(r) { return isFav(r.slug); });
    } else if (activeFolder === '_recent') {
        var recentOrder = getRecent();
        reportList = recentOrder.map(function(s) { return reportList.find(function(r) { return r.slug === s; }); }).filter(Boolean);
    } else if (activeFolder && activeFolder !== 'all') {
        reportList = reportList.filter(function(r) { return (r.category || 'Uncategorized') === activeFolder; });
    }

    /* Apply top search bar filter */
    if (searchQuery) {
        var q = searchQuery.toLowerCase();
        reportList = reportList.filter(function(r) {
            return (r.name || '').toLowerCase().indexOf(q) >= 0
                || (r.slug || '').toLowerCase().indexOf(q) >= 0
                || (r.description || '').toLowerCase().indexOf(q) >= 0;
        });
    }

    var running = status.running || {};
    var queue = status.queue || [];
    var queueSlugs = queue.map(function(q) { return q.slug || q; });
    var counts = {total:reportList.length, running:Object.keys(running).length, queued:queue.length, ok:0, error:0, not_run:0, waiting:0};
    reportList.forEach(function(r) {
        if (running[r.slug] || queueSlugs.indexOf(r.slug) >= 0) return;
        var s = r.last_status || 'not_run';
        if (r.waiting) counts.waiting++;
        else if (s === 'success') counts.ok++;
        else if (s === 'error') counts.error++;
        else counts.not_run++;
    });
    var uptime = status.server_uptime_seconds ? _fmtDuration(status.server_uptime_seconds) : '-';

    /* Data sources that need someone's attention sit above everything else;
       otherwise the section keeps its place under the system actions. */
    _dsSources = status.source_states || [];
    var dsAttention = _dsSources.filter(function(s) { return _DS_ATTENTION.indexOf(s.state) >= 0; }).length;
    if (dsAttention && !_dsTouched) _dsExpanded = true;
    var dsHtml = _dsSectionHTML(dsAttention);

    var html = dsAttention ? dsHtml : '';
    html += '<div class="ops-summary">';
    html += _opsStat(counts.total, 'Reports', '');
    html += _opsStat(counts.running, 'Running', 'running');
    html += _opsStat(counts.queued, 'Queued', '');
    html += _opsStat(counts.waiting, 'Waiting for data source', 'warn');
    html += _opsStat(counts.ok, 'Succeeded', 'ok');
    html += _opsStat(counts.error, 'Failed', 'error');
    html += _opsStat(uptime, 'Uptime', '');
    html += '</div>';

    if (opsHasRole('developer', 'admin')) {
        html += '<div class="ops-actions"><span class="ops-actions-label">System</span>';
        if (opsHasRole('admin')) {
            html += '<div class="ops-run-dropdown">'
                + '<button class="ops-action-btn" onclick="event.stopPropagation(); opsToggleSysRunMenu()">&#9654; Run All Reports &#9662;</button>'
                + '<div class="ops-run-menu" id="opsSysRunMenu" style="left:0;right:auto;bottom:auto;top:100%;margin-top:4px">'
                + '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsCloseSysRunMenu(); _opsDoAction(\'run-all\')">&#9654; Run All<span class="ops-run-menu-hint">default cache</span></button>'
                + '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsCloseSysRunMenu(); _opsDoAction(\'run-all-fresh\')">&#8635; Run All Fresh<span class="ops-run-menu-hint">no cache</span></button>'
                + (IS_LOCAL ? '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsCloseSysRunMenu(); _opsDoAction(\'run-all-cached\')">&#9889; Run All Cached<span class="ops-run-menu-hint">fast</span></button>' : '')
                + '</div></div>';
            html += _opsActionBtn('stop-all', '&#9632; Stop All Running', 'danger');
            html += _opsActionBtn('clear-cache', 'Clear Query Cache');
        }
        var gs = window._gitStatus || {};
        if (window._gitSyncConfigured) {
            html += _opsActionBtn('git-sync', '&#8635; ' + (gs.publish_mode === 'manual' ? 'Check for changes' : 'Sync now'));
        }
        html += _opsActionBtn('refresh', '&#8635; Reload Registry');
        html += '<button class="ops-action-btn" data-ops-action="server-log">&#128196; Server Log</button>';
        html += '</div>';
        if (window._gitSyncConfigured) html += _opsRepoCard(gs);
    }

    if (!dsAttention) html += dsHtml;

    html += '<div class="ops-filters">';
    /* Count validation states */
    var vCounts = {healthy:0, v_warn:0, v_fail:0};
    reportList.forEach(function(r) {
        var v = r.validation && r.validation.summary;
        if (!v) return;
        if (v.fail > 0) vCounts.v_fail++;
        else if (v.warn > 0) vCounts.v_warn++;
        else vCounts.healthy++;
    });

    [['all','All',counts.total],['running','Running',counts.running],['queued','Queued',counts.queued],
     ['waiting','Waiting',counts.waiting],
     ['error','Errors',counts.error],['ok','OK',counts.ok],['not_run','Not Run',counts.not_run],
     ['v_fail','Validation Fails',vCounts.v_fail],['v_warn','Warnings',vCounts.v_warn],['healthy','No issues',vCounts.healthy]].forEach(function(f) {
        html += '<button class="ops-filter-pill' + (_opsFilter === f[0] ? ' active' : '') + '" data-ops-filter="' + f[0] + '">'
            + f[1] + '<span class="ops-filter-count">' + f[2] + '</span></button>';
    });
    html += '</div>';

    var filtered = _opsFilterReports(reportList, running, queueSlugs);
    html += '<div class="ops-table-wrap"><table class="ops-table"><thead><tr><th>Report</th><th>Data</th><th>Schedule</th><th>Status</th><th>Duration</th><th>Memory</th><th>Details</th><th>Actions</th></tr></thead><tbody>';
    filtered.forEach(function(r) {
        var isRunning = !!running[r.slug], isQueued = queueSlugs.indexOf(r.slug) >= 0;
        var expanded = !!_opsExpandedSlugs[r.slug];
        html += '<tr' + (expanded ? ' class="expanded"' : '') + ' data-ops-row="' + r.slug + '">';
        html += '<td class="ops-report-cell" data-label="Report"><div class="ops-report-name">' + esc(r.name) + '</div><div class="ops-report-slug">' + esc(r.slug) + '</div></td>';
        html += '<td data-label="Data">' + _opsDataCell(r) + '</td>';
        html += '<td data-label="Schedule">' + _opsCronCell(r, status) + '</td>';
        html += '<td data-label="Status">' + _opsStatusCell(r, running, isQueued) + '</td>';
        html += '<td data-label="Duration">' + _opsDurationCell(r.slug) + '</td>';
        html += '<td data-label="Memory">' + _opsMemoryCell(r.slug) + '</td>';
        html += '<td data-label="Details">' + _opsDetailsCell(r) + '</td>';
        html += '<td class="ops-actions-cell" data-label="Actions">' + _opsActionsCell(r, isRunning, isQueued) + '</td>';
        html += '</tr>';
        if (expanded) {
            var mode = isRunning ? 'live' : ((r.last_status || '') === 'error' ? 'error' : 'log');
            var title = isRunning ? 'Running' : ((r.last_status || '') === 'error' ? 'Failed' : 'Last run');
            /* Report meta info shown in the expanded row */
            var metaInfo = '<div style="padding:8px 14px;font-size:11px;color:var(--text3);display:flex;gap:16px;flex-wrap:wrap">';
            if (r.framework_version) metaInfo += '<span>Framework: <strong style="color:var(--text2)">' + esc(r.framework_version) + '</strong></span>';
            if (r.last_run) metaInfo += '<span>Last run: <strong style="color:var(--text2)">' + esc(r.last_run.replace('T',' ').substring(0,19)) + ' UTC</strong></span>';
            if (r.description) metaInfo += '<span>' + esc(r.description) + '</span>';
            metaInfo += '</div>';
            html += '<tr class="ops-expand-row"><td colspan="8">'
                + metaInfo
                + '<div class="ops-run-history" id="opsHist_' + r.slug + '">Loading run history\u2026</div>'
                + _opsTerminal(r.slug, mode, title + ' \u2014 ' + r.slug)
                + '</td></tr>';
        }
    });
    html += '</tbody></table></div>';
    return html;
}

/* Repository card: the remote head against what is published, from the
   git/status payload the sync button already gates on (fetched once per
   page; a git-sync action invalidates it so the next render re-reads). */
function _opsRepoCard(gs) {
    var manual = gs.publish_mode === 'manual', pending = gs.pending || null;
    var n = pending ? (pending.commits_total || (pending.commits || []).length) : 0;
    var state = gs.publishing ? ['running', 'Publishing…']
        : pending ? ['queued', n + ' commit' + (n === 1 ? '' : 's') + ' ahead']
        : ['success', 'Up to date'];
    var html = '<div class="ops-actions"><span class="ops-actions-label">Repository &middot; '
        + (manual ? 'Manual' : 'Automatic') + ' publishing</span>'
        + '<span class="ops-status ' + state[0] + '"><span class="ops-status-dot"></span>' + esc(state[1]) + '</span>';
    if (manual && pending && !gs.publishing) {
        html += '<a class="ops-action-btn" href="' + apiUrl('/settings/repo') + '">Review &amp; publish</a>';
    } else if (!manual && gs.last_published) {
        html += '<span class="ops-status-text">Last published ' + esc(_timeAgo(gs.last_published)) + '</span>';
    }
    return html + '</div>';
}
function _opsStat(v, label, cls) {
    return '<div class="ops-stat ' + cls + '"><div class="ops-stat-value">' + v + '</div><div class="ops-stat-label">' + label + '</div></div>';
}
function _opsActionBtn(id, label, cls) {
    return '<button class="ops-action-btn ' + (cls||'') + (_opsSystemActions[id]?' running':'') + '" data-ops-action="' + id + '">' + label + '</button>';
}
function _opsDataCell(r) {
    if (r.live) {
        return '<span class="ops-data live" title="Runs live queries when opened — data is fetched at view time">'
            + '<span class="live-dot"></span>Live at view</span>';
    }
    return '<span class="ops-data static" title="Serves the snapshot baked into the last build">Static build</span>';
}
function _opsCronCell(r, status) {
    var cron = r.schedule || '';
    if (!cron) return '<span class="ops-cron">\u2014</span>';
    var label = cronLabel(cron);
    var next = '';
    (status.scheduled_jobs || []).forEach(function(j) {
        if (j.slug === r.slug && j.next_run) {
            var mins = Math.round((new Date(j.next_run) - new Date()) / 60000);
            if (mins > 0) next = 'next in ' + mins + 'm';
        }
    });
    return '<div class="ops-report-name" style="font-size:12px;font-weight:500">' + esc(label) + '</div>'
        + '<div class="ops-cron">' + esc(cron) + '</div>'
        + (next ? '<div class="ops-next-run">' + next + '</div>' : '');
}
function _opsStatusCell(r, running, isQueued) {
    if (running[r.slug]) {
        var el = Math.round(running[r.slug].elapsed_seconds || 0);
        return '<div class="ops-status running"><span class="ops-status-dot"></span><span class="ops-status-text">Running</span></div>'
            + '<div class="ops-status-detail">' + el + 's elapsed</div>';
    }
    if (isQueued) return '<div class="ops-status queued"><span class="ops-status-dot"></span><span class="ops-status-text">Queued</span></div>';
    var s = r.last_status || 'not_run';
    if (r.waiting) {
        /* Held before it started: not a failure of the build. */
        return '<div class="ops-status waiting"><span class="ops-status-dot"></span><span class="ops-status-text">Waiting for data source</span></div>'
            + '<div class="ops-status-detail"><code>' + esc((r.blocked_by || []).join(', ')) + '</code></div>';
    }
    var label = s === 'success' ? 'OK' : s === 'error' ? 'Error' : 'Not run';
    var failure = s === 'error' ? sourceFailure(r.last_error) : null;
    if (failure) label += ' \u00b7 data source <code>' + esc(failure.name) + '</code>';
    var detail = '';
    if (r.last_run) {
        detail = _timeAgo(r.last_run);
        if (s === 'error' && r.last_error) detail += ' \u2014 ' + (failure ? failure.message : r.last_error).substring(0, 60);
    }
    return '<div class="ops-status ' + s + '"><span class="ops-status-dot"></span><span class="ops-status-text">' + label + '</span></div>'
        + (detail ? '<div class="ops-status-detail">' + esc(detail) + '</div>' : '');
}
function _opsDurationCell(slug) {
    var st = _opsRunStats[slug];
    if (!st || !st.agg || !st.agg.runs) return '<span class="ops-metric">\u2014</span>';
    var last = st.last || {};
    var main = last.duration_s != null ? _fmtDuration(last.duration_s) : '\u2014';
    var sub = st.agg.p95_duration_s != null ? 'p95 ' + _fmtDuration(st.agg.p95_duration_s) : '';
    return '<div class="ops-metric">' + main + '</div>'
        + (sub ? '<div class="ops-metric-sub">' + sub + '</div>' : '');
}
function _opsMemoryCell(slug) {
    var st = _opsRunStats[slug];
    if (!st || !st.agg || !st.agg.runs) return '<span class="ops-metric">\u2014</span>';
    var last = st.last || {};
    var main = _fmtMB(last.peak_memory_mb);
    var sub = st.agg.max_peak_memory_mb != null ? 'max ' + _fmtMB(st.agg.max_peak_memory_mb) : '';
    return '<div class="ops-metric">' + main + '</div>'
        + (sub ? '<div class="ops-metric-sub">' + sub + '</div>' : '');
}
function _opsDetailsCell(r) {
    if (!r.validation || !r.validation.summary) return '<span style="color:var(--text3);font-size:11px">\u2014</span>';
    var v = r.validation.summary;
    var html = '<div class="ops-details" onclick="event.stopPropagation(); showValidation(\'' + r.slug + '\')" style="cursor:pointer" title="View report details">';
    if (v.pass) html += '<span class="ops-details-badge pass">\u2713 ' + v.pass + '</span>';
    if (v.warn) html += '<span class="ops-details-badge warn">\u26A0 ' + v.warn + '</span>';
    if (v.fail) html += '<span class="ops-details-badge fail">\u2717 ' + v.fail + '</span>';
    html += '</div>';
    /* Show top 2 issues inline */
    // Suppressed checks are excluded from the ops-view chip list —
    // they're deliberately accepted, visible in the validation drawer only.
    var checks = (r.validation.checks || []).filter(function(c) {
        return (c.level === 'fail' || c.level === 'warn') && !c.suppressed;
    });
    if (checks.length > 0) {
        html += '<div style="margin-top:3px">';
        checks.slice(0, 2).forEach(function(c) {
            var cls = c.level === 'fail' ? 'fail' : 'warn';
            html += '<span class="ops-details-badge ' + cls + '" style="font-size:9px;padding:1px 4px;margin-right:2px">' + esc(c.id) + '</span>';
        });
        if (checks.length > 2) html += '<span style="font-size:9px;color:var(--text3)">+' + (checks.length - 2) + '</span>';
        html += '</div>';
    }
    return html;
}
function _opsActionsCell(r, isRunning, isQueued) {
    if (!opsHasRole('developer', 'admin')) return '';
    var h = '<div class="ops-row-actions">';
    if (isRunning || isQueued) {
        h += '<button class="ops-row-btn stop" onclick="event.stopPropagation(); opsStop(\'' + r.slug + '\')">Stop</button>';
    } else {
        h += '<div class="ops-run-dropdown">'
            + '<button class="ops-row-btn" onclick="event.stopPropagation(); opsToggleRunMenu(\'' + r.slug + '\')">&#9654; Run</button>'
            + '<div class="ops-run-menu" id="opsRunMenu_' + r.slug + '">'
            + '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsRun(\'' + r.slug + '\',\'normal\')">&#9654; Generate<span class="ops-run-menu-hint">default</span></button>'
            + '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsRun(\'' + r.slug + '\',\'fresh\')">&#8635; Fresh data<span class="ops-run-menu-hint">no cache</span></button>'
            + (IS_LOCAL ? '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsRun(\'' + r.slug + '\',\'force\')">&#9889; Cached only<span class="ops-run-menu-hint">fast</span></button>' : '')
            + '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsRun(\'' + r.slug + '\',\'debug\')">&#128269; Debug run<span class="ops-run-menu-hint">verbose + cache</span></button>'
            + '<button class="ops-run-menu-item" onclick="event.stopPropagation(); opsRun(\'' + r.slug + '\',\'debug-fresh\')">&#128269; Debug fresh<span class="ops-run-menu-hint">verbose + no cache</span></button>'
            + '</div></div>';
    }
    h += '<details class="ops-secondary" onclick="event.stopPropagation()">'
        + '<summary class="ops-row-btn">More</summary><div class="ops-secondary-menu">';
    if (r.has_output) {
        var entry = r.html_entry || 'index.html';
        var href = consoleReportUrl(apiUrl('/r/' + r.slug + '/' + entry));
        h += '<a class="ops-row-btn" href="' + escAttr(href) + '" data-console-report-title="'
            + escAttr(r.name) + '" onclick="return openConsoleReport(this.href,event,this.dataset.consoleReportTitle)"'
            + ' title="Open report">Open</a>';
    }
    h += '<button class="ops-row-btn" onclick="event.stopPropagation(); opsToggleExpand(\'' + r.slug + '\')" title="Toggle log">Log</button>';
    if (r.validation && r.validation.summary) {
        var vs = r.validation.summary;
        var hStyle = '';
        if (vs.fail > 0) hStyle = ' style="color:var(--red);border-color:var(--red)"';
        else if (vs.warn > 0) hStyle = ' style="color:var(--warn);border-color:var(--warn)"';
        else hStyle = ' style="color:var(--green);border-color:var(--green)"';
        h += '<button class="ops-row-btn"' + hStyle + ' onclick="event.stopPropagation(); showValidation(\'' + r.slug + '\')" title="Report details">Details</button>';
    }
    return h + '</div></details></div>';
}
function _opsTerminal(slug, mode, title) {
    var cls = mode === 'error' ? ' error' : '';
    var bodyClass = mode === 'live' ? ' live' : '';
    return '<div class="ops-terminal' + cls + '" id="opsTerm_' + slug + '">'
        + '<div class="ops-terminal-header"><div class="ops-terminal-dots"><span></span><span></span><span></span></div>'
        + '<span class="ops-terminal-title">' + esc(title) + '</span></div>'
        + '<div class="ops-terminal-body' + bodyClass + '" id="opsTermBody_' + slug + '">Loading...</div></div>';
}
function _opsFilterReports(list, running, queueSlugs) {
    return list.filter(function(r) {
        if (_opsFilter !== 'all') {
            var isR = !!running[r.slug], isQ = queueSlugs.indexOf(r.slug)>=0, s = r.last_status||'not_run';
            if (_opsFilter==='running' && !isR) return false;
            if (_opsFilter==='queued' && !isQ) return false;
            if (_opsFilter==='waiting' && (isR||isQ||!r.waiting)) return false;
            if (_opsFilter==='error' && (isR||isQ||s!=='error'||r.waiting)) return false;
            if (_opsFilter==='ok' && (isR||isQ||s!=='success')) return false;
            if (_opsFilter==='not_run' && (isR||isQ||s!=='not_run')) return false;
            /* Validation filters */
            var v = r.validation && r.validation.summary;
            if (_opsFilter==='v_fail' && !(v && v.fail > 0)) return false;
            if (_opsFilter==='v_warn' && !(v && v.warn > 0 && (!v.fail || v.fail === 0))) return false;
            if (_opsFilter==='healthy' && !(v && (!v.fail || v.fail === 0) && (!v.warn || v.warn === 0))) return false;
        }
        return true;
    });
}
function _opsBindEvents(status, reportList) {
    document.querySelectorAll('[data-ops-filter]').forEach(function(b) {
        b.addEventListener('click', function() { _opsFilter = this.dataset.opsFilter; renderOperations(); });
    });
    document.querySelectorAll('[data-ops-action]').forEach(function(b) {
        b.addEventListener('click', function() { _opsDoAction(this.dataset.opsAction); });
    });
    document.querySelectorAll('[data-ops-row]').forEach(function(tr) {
        tr.addEventListener('click', function() { opsToggleExpand(this.dataset.opsRow); });
    });
    Object.keys(_opsExpandedSlugs).forEach(function(slug) {
        if ((status.running || {})[slug]) _opsStartLivePoll(slug);
        else _opsLoadLog(slug);
        _opsLoadHistory(slug);
    });
}
function _opsLoadHistory(slug) {
    studioFetch('/api/reports/' + encodeURIComponent(slug) + '/status')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var el = document.getElementById('opsHist_' + slug);
            if (el) el.innerHTML = _opsHistoryHTML(data.aggregates || {}, data.history || []);
        })
        .catch(function() {
            var el = document.getElementById('opsHist_' + slug);
            if (el) el.innerHTML = '';
        });
}
function _opsHistoryHTML(agg, history) {
    if (!history.length) return '<div class="ops-history-empty">No completed runs yet.</div>';
    var rateCls = '';
    if (agg.success_rate != null) {
        rateCls = agg.success_rate >= 0.9 ? 'ok' : (agg.success_rate < 0.5 ? 'error' : '');
    }
    var html = '<div class="ops-summary ops-history-stats">';
    html += _opsStat(agg.runs != null ? agg.runs : '—', 'Runs (' + (agg.window_days || 90) + 'd)', '');
    html += _opsStat(_fmtPct(agg.success_rate), 'Success rate', rateCls);
    html += _opsStat(agg.avg_duration_s != null ? _fmtDuration(agg.avg_duration_s) : '—', 'Avg duration', '');
    html += _opsStat(agg.p95_duration_s != null ? _fmtDuration(agg.p95_duration_s) : '—', 'P95 duration', '');
    html += _opsStat(_fmtMB(agg.avg_peak_memory_mb), 'Avg peak mem', '');
    html += _opsStat(_fmtMB(agg.max_peak_memory_mb), 'Max peak mem', '');
    html += '</div>';
    html += '<div class="ops-history-scroll"><table class="ops-history-table"><thead><tr>'
        + '<th>Started</th><th>Trigger</th><th>Status</th><th>Queue wait</th><th>Duration</th><th>Peak mem</th>'
        + '</tr></thead><tbody>';
    history.forEach(function(h) {
        var started = h.started_at || h.created_at;
        html += '<tr>'
            + '<td>' + (started ? _timeAgo(started) : '—') + '</td>'
            + '<td>' + esc(h.trigger || '') + '</td>'
            + '<td><span class="ops-history-status ' + esc(h.status || '') + '">' + esc(_opsStatusLabel(h.status)) + '</span></td>'
            + '<td>' + (h.queue_wait_seconds != null ? _fmtDuration(h.queue_wait_seconds) : '—') + '</td>'
            + '<td>' + (h.duration_seconds != null ? _fmtDuration(h.duration_seconds) : '—') + '</td>'
            + '<td>' + _fmtMB(h.peak_memory_mb) + '</td>'
            + '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
}
function _opsStatusLabel(s) {
    if (s === 'success') return 'OK';
    if (s === 'oom_killed') return 'OOM killed';
    if (s === 'timeout') return 'Timed out';
    if (!s) return '?';
    return s.charAt(0).toUpperCase() + s.slice(1);
}
/* Close run menus on outside click — registered once, not per render */
document.addEventListener('click', function(e) {
    if (!e.target.closest('.ops-run-dropdown') && !e.target.closest('.ops-action-btn')) {
        document.querySelectorAll('.ops-run-menu.open').forEach(function(m) { m.classList.remove('open'); });
    }
});
function opsToggleSysRunMenu() {
    document.querySelectorAll('.ops-run-menu.open').forEach(function(m) { m.classList.remove('open'); });
    var menu = document.getElementById('opsSysRunMenu');
    if (menu) menu.classList.toggle('open');
}
function opsCloseSysRunMenu() {
    var menu = document.getElementById('opsSysRunMenu');
    if (menu) menu.classList.remove('open');
}
function opsToggleRunMenu(slug) {
    document.querySelectorAll('.ops-run-menu.open').forEach(function(m) { m.classList.remove('open'); });
    var menu = document.getElementById('opsRunMenu_' + slug);
    if (menu) menu.classList.toggle('open');
}
/* Immediately update a row's status + actions after an action */
function _opsRefreshAfterAction(slug, needsExpand) {
    if (needsExpand && !_opsExpandedSlugs[slug]) {
        _opsExpandedSlugs[slug] = true;
        renderOperations();
        return;
    }
    /* Quick poll to update just this row */
    setTimeout(function() {
        studioFetch('/api/system/status').then(function(r) { return r.json(); }).then(function(status) {
            var running = status.running || {};
            var queue = status.queue || [];
            var queueSlugs = queue.map(function(q) { return q.slug || q; });
            var tr = document.querySelector('[data-ops-row="' + slug + '"]');
            if (!tr) return;
            var tds = tr.querySelectorAll('td');
            if (tds.length < 7) return;

            var r = null;
            reports.forEach(function(rep) { if (rep.slug === slug) r = rep; });

            var isRunning = !!running[slug];
            var isQueued = queueSlugs.indexOf(slug) >= 0;

            /* Update status */
            if (isRunning) {
                var el = Math.round(running[slug].elapsed_seconds || 0);
                tds[3].innerHTML = '<div class="ops-status running"><span class="ops-status-dot"></span><span class="ops-status-text">Running</span></div>'
                    + '<div class="ops-status-detail">' + el + 's elapsed</div>';
            } else if (isQueued) {
                tds[3].innerHTML = '<div class="ops-status queued"><span class="ops-status-dot"></span><span class="ops-status-text">Queued</span></div>';
            } else {
                tds[3].innerHTML = '<div class="ops-status success"><span class="ops-status-dot"></span><span class="ops-status-text">Stopped</span></div>'
                    + '<div class="ops-status-detail">just now</div>';
            }

            /* Update actions */
            if (r) tds[7].innerHTML = _opsActionsCell(r, isRunning, isQueued);

            /* Update summary stats */
            var stats = document.querySelectorAll('.ops-stat-value');
            if (stats.length >= 6) {
                stats[1].textContent = Object.keys(running).length;
                stats[2].textContent = queue.length;
            }
        });
    }, 300); /* Short delay so the server registers the state change */
}

function opsRun(slug, mode) {
    document.querySelectorAll('.ops-run-menu.open').forEach(function(m) { m.classList.remove('open'); });
    /* Optimistic UI update — show running immediately */
    var tr = document.querySelector('[data-ops-row="' + slug + '"]');
    if (tr) {
        var tds = tr.querySelectorAll('td');
        if (tds.length >= 7) {
            tds[3].innerHTML = '<div class="ops-status running"><span class="ops-status-dot"></span><span class="ops-status-text">Starting...</span></div>';
            var r = null;
            reports.forEach(function(rep) { if (rep.slug === slug) r = rep; });
            if (r) tds[7].innerHTML = _opsActionsCell(r, true, false);
        }
    }

    /* Kill old live poller if exists, reset terminal content */
    if (_opsLivePollers[slug]) {
        clearInterval(_opsLivePollers[slug]);
        delete _opsLivePollers[slug];
    }
    /* Mark this slug as freshly started — live poller won't treat empty response as "finished" */
    if (!window._opsRecentlyStarted) window._opsRecentlyStarted = {};
    window._opsRecentlyStarted[slug] = Date.now();

    var termBody = document.getElementById('opsTermBody_' + slug);
    if (termBody) {
        termBody.textContent = '(starting...)';
        termBody.className = 'ops-terminal-body live';
    }

    studioFetch('/api/reports/' + encodeURIComponent(slug) + '/run', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cache:mode||'normal', cache_mode:mode||'normal'})
    }).then(function() {
        if (!_opsExpandedSlugs[slug]) {
            _opsExpandedSlugs[slug] = true;
            renderOperations();
        }
        /* Always start live poller after a short delay (server needs to register the subprocess) */
        setTimeout(function() {
            _opsStartLivePoll(slug);
            _opsRefreshAfterAction(slug, false);
        }, 500);
    });
}
function opsStop(slug) {
    /* Optimistic UI update — swap buttons immediately, don't wait for server */
    var tr = document.querySelector('[data-ops-row="' + slug + '"]');
    if (tr) {
        var tds = tr.querySelectorAll('td');
        if (tds.length >= 7) {
            tds[3].innerHTML = '<div class="ops-status not_run"><span class="ops-status-dot"></span><span class="ops-status-text">Stopping...</span></div>';
            var r = null;
            reports.forEach(function(rep) { if (rep.slug === slug) r = rep; });
            if (r) tds[7].innerHTML = _opsActionsCell(r, false, false);
        }
    }
    studioFetch('/api/reports/' + encodeURIComponent(slug) + '/stop', {method:'POST'}).then(function() {
        /* Confirm with server after 500ms */
        setTimeout(function() { _opsRefreshAfterAction(slug, false); }, 500);
    });
}
function opsToggleExpand(slug) {
    if (_opsExpandedSlugs[slug]) {
        delete _opsExpandedSlugs[slug];
        if (_opsLivePollers[slug]) { clearInterval(_opsLivePollers[slug]); delete _opsLivePollers[slug]; }
    } else { _opsExpandedSlugs[slug] = true; }
    renderOperations();
}
function _opsDoAction(action) {
    /* Server log toggle — no API call, no re-render */
    if (action === 'server-log') {
        var drawer = document.getElementById('serverLogDrawer');
        if (drawer.classList.contains('open')) {
            closeServerLog();
        } else {
            setDrawerOpen('serverLogDrawer', 'serverLogOverlay', true);
            opsRefreshServerLog();
        }
        return;
    }

    if (_opsSystemActions[action]) return;
    _opsSystemActions[action] = true;
    renderOperations();
    var url, body = {};
    switch (action) {
        case 'run-all': url='/api/system/run-all'; break;
        case 'run-all-fresh': url='/api/system/run-all'; body={cache:'fresh', cache_mode:'fresh'}; break;
        case 'run-all-cached': url='/api/system/run-all'; body={cache:'force', cache_mode:'force'}; break;
        case 'stop-all': url='/api/system/stop-all'; break;
        case 'clear-cache': url='/api/system/cache/clear'; break;
        case 'refresh': url='/api/system/registry/refresh'; break;
        case 'git-sync': url='/api/system/git/sync'; break;
    }
    studioFetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
        .then(function(r) { return r.json(); })
        .then(function(data) {
            _opsSystemActions[action] = false;
            if (action === 'git-sync') window._gitSyncChecked = false;  /* re-read the repository card */
            if (data.ok) {
                showToast(data.message || action.replace(/-/g,' ') + ' done', 'success');
            } else {
                var errMsg = data.message || data.error || action.replace(/-/g,' ') + ' failed';
                showToast(errMsg, 'error');
                addError('system', action.replace(/-/g,' '), errMsg);
            }
            renderOperations();
        }).catch(function(e) {
            _opsSystemActions[action] = false;
            var errMsg = 'System action failed: ' + (e.message || action);
            showToast(errMsg, 'error');
            addError('system', action.replace(/-/g,' '), errMsg);
            renderOperations();
        });
}
function _opsStartLivePollers(status) {
    Object.keys(_opsExpandedSlugs).forEach(function(slug) {
        if ((status.running || {})[slug]) _opsStartLivePoll(slug);
    });
}
function _opsStartLivePoll(slug) {
    if (_opsLivePollers[slug]) return;
    _opsFetchLive(slug);
    _opsLivePollers[slug] = setInterval(function() { _opsFetchLive(slug); }, 2000);
}
function _opsFetchLive(slug) {
    studioFetch('/api/reports/' + encodeURIComponent(slug) + '/log/live')
        .then(function(r) { return r.json(); }).then(function(data) {
            var el = document.getElementById('opsTermBody_' + slug);
            if (!el) return;
            var text = typeof data === 'string' ? data : (data.stdout_tail || data.stdout || data.output || '');

            /* If empty response, report may have finished OR may not have started yet */
            if (!text || !text.trim()) {
                /* Grace period: don't treat empty as "finished" within 10s of starting */
                var started = (window._opsRecentlyStarted || {})[slug];
                if (started && (Date.now() - started) < 10000) {
                    return; /* Still starting up — keep polling */
                }
                if (_opsLivePollers[slug]) {
                    clearInterval(_opsLivePollers[slug]);
                    delete _opsLivePollers[slug];
                }
                el.classList.remove('live');
                _opsLoadLog(slug);
                delete (window._opsRecentlyStarted || {})[slug];
                return;
            }
            /* Got output — clear the recently-started flag */
            if (window._opsRecentlyStarted) delete window._opsRecentlyStarted[slug];

            var wasAtBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 50;
            el.innerHTML = _opsColorize(text);
            if (wasAtBottom) el.scrollTop = el.scrollHeight;
        }).catch(function() {
            var el = document.getElementById('opsTermBody_' + slug);
            if (el && el.textContent === 'Loading...') el.textContent = '(waiting for output...)';
        });
}
function _opsLoadLog(slug) {
    studioFetch('/api/reports/' + encodeURIComponent(slug) + '/log?run=0')
        .then(function(r) { return r.json(); }).then(function(data) {
            var el = document.getElementById('opsTermBody_' + slug);
            if (!el) return;
            var text = '';
            if (data && data.stdout) text = data.stdout;
            if (data && data.stderr) text += (text ? '\n\n--- stderr ---\n' : '') + data.stderr;
            if (!text && data) text = 'Status: ' + (data.status||'unknown') + ' (exit ' + (data.exit_code||'?') + ')';
            if (!text) text = 'No log available. Run the report to generate output.';
            el.innerHTML = _opsColorize(text);
        }).catch(function() {
            var el = document.getElementById('opsTermBody_' + slug);
            if (el) el.textContent = 'No log available.';
        });
}
function _opsColorize(text) {
    return esc(text)
        .replace(/(\[\d{2}:\d{2}:\d{2}\])/g, '<span class="t-time">$1</span>')
        .replace(/(CACHE HIT [a-f0-9]+)/g, '<span class="t-cache">$1</span>')
        .replace(/(CACHE MISS [a-f0-9]+)/g, '<span class="t-warn">$1</span>')
        .replace(/(Done in [\d.]+s.*)/g, '<span class="t-done">$1</span>')
        .replace(/(ERROR|Error|FAIL|Traceback)/g, '<span class="t-err">$1</span>')
        .replace(/(\[PASS\])/g, '<span class="t-ok">$1</span>');
}
/* Server-log drawer close: the X, an overlay click, or Esc (see the
   keydown handler above) all funnel through here. */
function closeServerLog() {
    setDrawerOpen('serverLogDrawer', 'serverLogOverlay', false);
}
document.addEventListener('click', function(e) {
    if (e.target.closest('#opsServerLogRefresh')) opsRefreshServerLog();
    if (e.target.closest('#opsServerLogCopy')) opsCopyServerLog();
    if (e.target.closest('#serverLogClose') || e.target.id === 'serverLogOverlay') {
        closeServerLog();
    }
});
function opsRefreshServerLog() {
    studioFetch('/api/system/log?lines=500').then(function(r) { return r.json(); }).then(function(data) {
        var el = document.getElementById('opsServerLogBody');
        if (!el) return;
        el.innerHTML = _opsColorize(data.log || '(empty)');
        el.scrollTop = el.scrollHeight;
    });
}
function opsCopyServerLog() {
    var el = document.getElementById('opsServerLogBody');
    if (el) navigator.clipboard.writeText(el.textContent).then(function() { showToast('Server log copied', 'success'); });
}

function _timeAgo(iso) {
    var d = (new Date() - new Date(iso)) / 1000;
    if (d < 60) return Math.round(d) + 's ago';
    if (d < 3600) return Math.round(d/60) + 'm ago';
    if (d < 86400) return Math.round(d/3600) + 'h ago';
    return Math.round(d/86400) + 'd ago';
}
function _fmtDuration(s) {
    if (s < 60) return Math.round(s) + 's';
    if (s < 3600) return Math.floor(s/60) + 'm ' + Math.round(s%60) + 's';
    return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}
function _fmtMB(mb) {
    if (mb == null) return '—';
    if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
    return Math.round(mb) + ' MB';
}
function _fmtPct(x) {
    return x == null ? '—' : Math.round(x * 100) + '%';
}

/* ── Data Sources section ── */
/* Rows come with /api/system/status (source_states), so the section renders
   with the rest of the tab; _dsExpanded survives render() calls, and
   _dsTouched records that the person chose, so attention no longer forces
   the section open once they have collapsed it. */
var _dsExpanded = false;
var _dsTouched = false;
var _dsSources = [];
var _dsSearch = '';
var _DS_ATTENTION = ['needs_credentials', 'needs_upload', 'failing'];
/* Same badge mapping as the settings page: [class, label]. */
var _DS_BADGES = {
    needs_credentials: ['ds-fail', 'Needs credentials'],
    needs_upload: ['ds-warn', 'Needs upload'],
    checking: ['ds-warn', 'Checking…'],
    connected: ['ds-ok', 'Connected'],
    failing: ['ds-fail', 'Failing'],
    not_in_repo: ['ds-warn', 'Not in repository'],
    unknown: ['ds-fail', 'Not declared']
};

function _dsSectionHTML(attention) {
    var html = '<div class="ds-section" id="dsSection">';
    html += '<div class="ds-header" onclick="toggleDsSection()">';
    html += '<span class="ds-header-title">Data Sources</span>';
    if (attention) html += '<span class="ds-header-count">' + attention + ' need' + (attention === 1 ? 's' : '') + ' attention</span>';
    html += '<span class="ds-header-toggle" id="dsToggle">' + (_dsExpanded ? '&#9660;' : '&#9654;') + '</span>';
    html += '</div>';
    html += '<div class="ds-body" id="dsBody" style="display:' + (_dsExpanded ? 'block' : 'none') + '"></div></div>';
    return html;
}

function toggleDsSection() {
    _dsExpanded = !_dsExpanded;
    _dsTouched = true;
    var body = document.getElementById('dsBody');
    var toggle = document.getElementById('dsToggle');
    if (!body || !toggle) return;
    body.style.display = _dsExpanded ? 'block' : 'none';
    toggle.innerHTML = _dsExpanded ? '&#9660;' : '&#9654;';
    if (_dsExpanded) renderDsTable();
}

/* Post-render hook: renderOperations() rebuilt the DOM with an empty body. */
function _restoreDsState() {
    if (_dsExpanded) renderDsTable();
}

function renderDsTable() {
    var body = document.getElementById('dsBody');
    if (!body) return;
    var sources = _dsSources;

    if (sources.length === 0) {
        body.innerHTML = '<div class="ds-empty">No data sources declared. Add them to <code>data-sources/config.yaml</code> in your repository and publish.</div>';
        return;
    }

    /* Filter by search */
    var filtered = sources;
    if (_dsSearch) {
        var q = _dsSearch.toLowerCase();
        filtered = sources.filter(function(s) {
            return (s.name || '').toLowerCase().indexOf(q) >= 0
                || (s.type || '').toLowerCase().indexOf(q) >= 0;
        });
    }

    var html = '<input class="ds-search" type="text" placeholder="Search data sources..." value="' + esc(_dsSearch) + '" id="dsSearchInput">';
    html += '<table class="ds-table"><thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Used By</th><th>Actions</th></tr></thead><tbody>';
    filtered.forEach(function(s) {
        var badge = _DS_BADGES[s.state] || ['ds-warn', s.state];
        var needs = s.state === 'needs_credentials' || s.state === 'needs_upload';
        html += '<tr>';
        html += '<td><div class="ds-name">' + esc(s.name) + '</div></td>';
        html += '<td><span class="ds-type-badge">' + esc(s.type || '—') + '</span></td>';
        html += '<td><span class="ds-status ' + badge[0] + '">' + esc(badge[1]) + '</span>'
            + (s.detail && s.state !== 'connected' ? '<div class="ds-desc">' + esc(s.detail.substring(0, 80)) + '</div>' : '') + '</td>';
        html += '<td>' + (s.blocks && s.blocks.length
            ? 'blocks ' + s.blocks.map(function(slug) { return '<code>' + esc(slug) + '</code>'; }).join(', ')
            : 'used by ' + (s.used_by || []).length) + '</td>';
        html += '<td><div class="ds-actions">';
        if (needs && opsHasRole('admin')) {
            html += '<a class="ds-action-btn" href="' + apiUrl('/settings/datasources?configure=' + encodeURIComponent(s.name)) + '#configure">Configure</a>';
        }
        if (s.upload && opsHasRole('admin') && s.binding_scope !== 'org') {
            html += '<label class="ds-action-btn" title="Upload new version">'
                + '<input class="ds-upload-input" type="file" onchange="dsSendUpload(\'' + escAttr(s.name) + '\', this)">'
                + '↑ Upload</label>';
        }
        if (s.file && opsHasRole('developer', 'admin')) {
            html += '<button class="ds-action-btn" onclick="dsDownload(\'' + escAttr(s.name) + '\')" title="Download">↓ Download</button>';
        }
        html += '</div></td>';
        html += '</tr>';
    });
    html += '</tbody></table>';
    body.innerHTML = html;

    /* Bind search */
    var si = document.getElementById('dsSearchInput');
    if (si) si.addEventListener('input', function() { _dsSearch = this.value; renderDsTable(); });
}

function dsSendUpload(name, input) {
    if (!input.files || !input.files.length) return;
    var file = input.files[0];
    var fd = new FormData();
    fd.append('file', file);
    studioFetch('/api/datasources/' + encodeURIComponent(name) + '/upload', {
        method: 'POST', body: fd
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (data.ok) {
            showToast('Uploaded ' + data.filename + ' (' + data.size + ' bytes)', 'success');
            renderOperations();
        } else {
            showToast('Upload failed: ' + (data.error || 'Unknown error'), 'error');
        }
    }).catch(function(err) {
        showToast('Upload failed: ' + err, 'error');
    });
    input.value = '';
}

function dsDownload(name) {
    studioFetch('/api/datasources/' + encodeURIComponent(name) + '/download')
        .then(function(r) {
            if (!r.ok) {
                return r.json().then(function(d) { throw new Error(d.error || r.statusText); });
            }
            var cd = r.headers.get('Content-Disposition') || '';
            var m = cd.match(/filename="?([^"]+)"?/);
            var filename = m ? m[1] : name;
            return r.blob().then(function(blob) { return { blob: blob, filename: filename }; });
        })
        .then(function(result) {
            var url = URL.createObjectURL(result.blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = result.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        })
        .catch(function(err) { showToast('Download failed: ' + err, 'error'); });
}

/* Auto-refresh Operations: update numbers, status, and action buttons in-place */
setInterval(function() {
    if (viewMode !== 'ops') return;
    studioFetch('/api/system/status').then(function(r) { return r.json(); }).then(function(status) {
        var running = status.running || {};
        var queue = status.queue || [];
        var queueSlugs = queue.map(function(q) { return q.slug || q; });

        /* Update summary stat values in-place */
        var stats = document.querySelectorAll('.ops-stat-value');
        if (stats.length >= 7) {
            stats[1].textContent = Object.keys(running).length;
            stats[2].textContent = queue.length;
            if (status.server_uptime_seconds) stats[stats.length - 1].textContent = _fmtDuration(status.server_uptime_seconds);
        }

        /* Detect rows that just transitioned out of running/queued so we can
           pull their real last_status from the registry rather than guessing. */
        var transitioned = [];

        /* Update each row's status + actions in-place */
        document.querySelectorAll('[data-ops-row]').forEach(function(tr) {
            var slug = tr.dataset.opsRow;
            var tds = tr.querySelectorAll('td');
            if (tds.length < 7) return;
            var statusTd = tds[3];
            var actionsTd = tds[7];

            var r = null;
            reports.forEach(function(rep) { if (rep.slug === slug) r = rep; });

            var isRunning = !!running[slug];
            var isQueued = queueSlugs.indexOf(slug) >= 0;
            var wasRunning = !!tr.querySelector('.ops-status.running');
            var wasQueued = !!tr.querySelector('.ops-status.queued');

            /* Update status cell */
            if (isRunning) {
                var el = Math.round(running[slug].elapsed_seconds || 0);
                statusTd.innerHTML = '<div class="ops-status running"><span class="ops-status-dot"></span><span class="ops-status-text">Running</span></div>'
                    + '<div class="ops-status-detail">' + el + 's elapsed</div>';
            } else if (isQueued) {
                statusTd.innerHTML = '<div class="ops-status queued"><span class="ops-status-dot"></span><span class="ops-status-text">Queued</span></div>';
            } else if (wasRunning || wasQueued) {
                /* Just finished — defer until we get the real outcome from
                   the registry refresh below. Show a transient "Finishing…"
                   so the user sees something happening and we don't lie. */
                statusTd.innerHTML = '<div class="ops-status"><span class="ops-status-dot"></span><span class="ops-status-text">Finishing…</span></div>';
                transitioned.push(slug);
            }

            /* Update action buttons when state changes */
            if ((isRunning || isQueued) !== (wasRunning || wasQueued)) {
                if (r) actionsTd.innerHTML = _opsActionsCell(r, isRunning, isQueued);
            }
        });

        /* Pull fresh last_status / last_error from the registry for any row
           that just finished, then redraw the table so the row reflects the
           real outcome (success / error / oom_killed). */
        if (transitioned.length) {
            /* render() re-enters renderOperations(), which refetches the
               run-stats cache and reloads any open drawer's history — so the
               finished run's duration/memory appear without extra plumbing. */
            studioFetch('/api/registry')
                .then(function(resp) { return resp.json(); })
                .then(function(reg) {
                    reports = reg.reports;
                    transitioned.forEach(function(slug) {
                        var r = reports.find(function(x) { return x.slug === slug; });
                        if (r && (r.last_status === 'error' || r.last_status === 'oom_killed')) {
                            addError(slug, r.name || slug, r.last_error || (r.last_status + ': ' + slug));
                        }
                    });
                    render();
                })
                .catch(function() {});
        }
    }).catch(function() {});
}, 2000);

/* ── Boot ──
   The registry is no longer inlined into this file: fetch it (plus the
   user's server-side favorites) first, then do the legacy init sequence. */
function boot() {
    /* Chrome affordances that depend only on the server context.
       Logout lives in the shell's user menu only — the dashboard's duplicate
       icon button went when the two header bars merged. */
    var searchInput = document.getElementById('searchInput');
    if (searchInput && /Mac|iPhone|iPad/.test(navigator.platform || '')) {
        searchInput.placeholder = 'Search reports... (Cmd+K)';
    }

    /* Studio tab bar: on narrow screens the rail scrolls horizontally, so
       bring the active tab into view (a no-op when everything fits).
       base.html carries the same snippet for the mgmt surface. */
    var tabRail = document.querySelector('.studio-tabs-rail');
    if (tabRail && tabRail.scrollWidth > tabRail.clientWidth) {
        var activeTab = tabRail.querySelector('.active');
        if (activeTab && activeTab.scrollIntoView) {
            activeTab.scrollIntoView({ block: 'nearest', inline: 'center' });
        }
    }

    var registryLoaded = studioFetch('/api/registry')
        .then(function(r) { return r.ok ? r.json() : { reports: [] }; })
        .then(function(reg) { reports = reg.reports || []; })
        .catch(function() { reports = []; });

    var favsLoaded = migrateLegacyFavorites().then(function() { return loadFavorites(); });
    var subsLoaded = loadSubscriptions();

    Promise.all([registryLoaded, favsLoaded, subsLoaded]).then(function() {
        _seedErrorLog();
        _initStudios();

        /* ── Read URL state (overrides localStorage) then render ── */
        readFromUrl();
        // render() first: the error-log button now lives in the summary bar
        // it builds (renderSummary), not static shell markup, so it must
        // exist before updateErrorBadge() looks it up.
        render();
        updateErrorBadge();
    });
}
boot();
/* (The footer deploy-hash indicator was removed with the global shell;
   the build version remains available on /api/version.) */

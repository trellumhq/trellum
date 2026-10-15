/* Studio analytics table (internal planning#4 follow-up): client-side relative-time
 * formatting, name filtering, and click-to-sort for templates/reports/
 * analytics.html. A studio of up to a few hundred reports gets every row
 * server-rendered, so filtering and sorting only read and reorder DOM nodes
 * already on the page -- nothing is fetched.
 *
 * A much larger studio is rendered one page at a time (table[data-server-side]).
 * There the same controls search and sort on the server instead: typing
 * applies after a short pause, a header click sorts at once, and only the
 * table region is swapped, so the input keeps focus.
 *
 * A plain {% static %} file, not an inline <script>, because the management
 * surface's CSP is script-src 'self' with no 'unsafe-inline' (see
 * trellum_portal.settings.base.CONTENT_SECURITY_POLICY and
 * apps.core.middleware.ContentSecurityPolicyMiddleware) -- "everything the
 * management surface runs is a static file" is a stated invariant, not a
 * suggestion.
 */
(function () {
    'use strict';

    var DEBOUNCE_MS = 400;
    var RETRY_MS = 1500;
    var MAX_RETRIES = 3;

    function relativeTime(iso) {
        if (!iso) return 'Never';
        var diff = (Date.now() - new Date(iso).getTime()) / 1000;
        if (diff < 60) return 'Just now';
        if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return Math.floor(diff / 86400) + 'd ago';
    }

    // The server renders an absolute timestamp; this swaps it for a relative
    // one, same convention as the dashboard cards and the report page's
    // Activity panel.
    function paintRelativeTimes(table) {
        table.querySelectorAll('tbody tr[data-last-viewed]').forEach(function (tr) {
            var iso = tr.getAttribute('data-last-viewed');
            var cell = tr.querySelector('.analytics-last-viewed');
            if (!cell) return;
            cell.textContent = iso ? relativeTime(iso) : 'Not viewed yet';
            if (iso) cell.title = new Date(iso).toLocaleString();
        });
    }

    // Give every sortable header a visible affordance -- an inactive
    // double-chevron that becomes a single directional arrow on the active
    // column -- so the columns read as sortable at a glance rather than only
    // revealing it on hover.
    function paintIndicators(table, sortState) {
        table.querySelectorAll('th[data-sort]').forEach(function (th) {
            var ind = th.querySelector('.analytics-sort-ind');
            if (!ind) {
                ind = document.createElement('span');
                ind.className = 'analytics-sort-ind';
                th.appendChild(ind);
            }
            var active = th.getAttribute('data-sort') === sortState.col;
            ind.textContent = active ? (sortState.asc ? ' ↑' : ' ↓') : ' ↕';
            ind.classList.toggle('active', active);
            th.setAttribute('aria-sort', active
                ? (sortState.asc ? 'ascending' : 'descending') : 'none');
        });
    }

    // Everything is on the page: filter and sort the existing rows.
    function initClientSide(table, search) {
        var rows = Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-name]'));

        if (search) {
            search.addEventListener('input', function () {
                var q = search.value.trim().toLowerCase();
                rows.forEach(function (tr) {
                    tr.hidden = q.length > 0 && tr.getAttribute('data-name').indexOf(q) === -1;
                });
            });
        }

        // Click-to-sort: toggles asc/desc on repeat clicks of the same column.
        var sortState = { col: null, asc: true };
        var sortHeaders = table.querySelectorAll('th[data-sort]');
        paintIndicators(table, sortState);

        sortHeaders.forEach(function (th) {
            th.addEventListener('click', function () {
                var col = th.getAttribute('data-sort');
                var asc = sortState.col === col ? !sortState.asc : true;
                sortState = { col: col, asc: asc };
                var body = table.querySelector('tbody');
                var sorted = rows.slice().sort(function (a, b) {
                    var va, vb;
                    if (col === 'report') {
                        va = a.getAttribute('data-name');
                        vb = b.getAttribute('data-name');
                        return asc ? va.localeCompare(vb) : vb.localeCompare(va);
                    }
                    if (col === 'last_viewed') {
                        va = a.getAttribute('data-last-viewed') ? new Date(a.getAttribute('data-last-viewed')).getTime() : 0;
                        vb = b.getAttribute('data-last-viewed') ? new Date(b.getAttribute('data-last-viewed')).getTime() : 0;
                    } else {
                        va = parseInt(a.getAttribute('data-' + col), 10) || 0;
                        vb = parseInt(b.getAttribute('data-' + col), 10) || 0;
                    }
                    return asc ? va - vb : vb - va;
                });
                sorted.forEach(function (tr) { body.appendChild(tr); });
                paintIndicators(table, sortState);
            });
        });
    }

    // A studio too large for one response: search and sort on the server, one
    // request in flight, stale answers dropped.
    function initServerSide(region, search) {
        var state = {
            q: search ? search.value.trim() : '',
            sort: 'report',
            asc: true
        };
        var timer = null;
        var controller = null;
        var serial = 0;

        function readTable() {
            var table = region.querySelector('#analyticsTable');
            if (!table) return null;
            state.sort = table.getAttribute('data-sort-col') || 'report';
            state.asc = table.getAttribute('data-sort-dir') !== 'desc';
            // The server says which order the page is in; show it.
            paintIndicators(table, { col: state.sort, asc: state.asc });
            paintRelativeTimes(table);
            return table;
        }

        function urlFor(next) {
            var params = new URLSearchParams(window.location.search);
            params.delete('page');
            ['q', 'sort', 'direction'].forEach(function (key) { params.delete(key); });
            if (next.q) params.set('q', next.q);
            if (next.sort !== 'report' || !next.asc) {
                params.set('sort', next.sort);
                params.set('direction', next.asc ? 'asc' : 'desc');
            }
            var query = params.toString();
            return window.location.pathname + (query ? '?' + query : '');
        }

        function load(next, attempt) {
            var url = urlFor(next);
            var mine = ++serial;
            if (controller) controller.abort();
            controller = typeof AbortController === 'function' ? new AbortController() : null;
            var options = { credentials: 'same-origin', headers: { 'Accept': 'text/html' } };
            if (controller) options.signal = controller.signal;
            fetch(url, options).then(function (response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.text();
            }).then(function (html) {
                if (mine !== serial) return;
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var fresh = doc.getElementById('analyticsRegion');
                if (!fresh) throw new Error('missing region');
                region.innerHTML = fresh.innerHTML;
                state.q = next.q;
                window.history.replaceState(window.history.state, '', url);
                readTable();
            }).catch(function (error) {
                if (mine !== serial || (error && error.name === 'AbortError')) return;
                // Nothing for the person to do: keep the rows on screen and
                // quietly try again.
                if ((attempt || 0) < MAX_RETRIES) {
                    setTimeout(function () {
                        if (mine === serial) load(next, (attempt || 0) + 1);
                    }, RETRY_MS);
                }
            });
        }

        if (search) {
            search.addEventListener('input', function () {
                clearTimeout(timer);
                timer = setTimeout(function () {
                    var q = search.value.trim();
                    if (q === state.q) return;
                    load({ q: q, sort: state.sort, asc: state.asc });
                }, DEBOUNCE_MS);
            });
        }

        region.addEventListener('click', function (event) {
            var th = event.target.closest ? event.target.closest('th[data-sort]') : null;
            if (!th || !region.contains(th)) return;
            var col = th.getAttribute('data-sort');
            var asc = state.sort === col ? !state.asc : true;
            // Remember the intent now, so a quick second click toggles the
            // direction even before the first answer arrives.
            state.sort = col;
            state.asc = asc;
            clearTimeout(timer);
            load({ q: search ? search.value.trim() : state.q, sort: col, asc: asc });
        });

        readTable();
    }

    document.addEventListener('DOMContentLoaded', function () {
        var table = document.getElementById('analyticsTable');
        if (!table) return;
        var search = document.getElementById('analyticsSearch');
        paintRelativeTimes(table);
        if (table.hasAttribute('data-server-side')) {
            initServerSide(document.getElementById('analyticsRegion'), search);
        } else {
            initClientSide(table, search);
        }
    });
})();

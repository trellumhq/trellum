/* Studio analytics table (internal planning#4 follow-up): client-side relative-time
 * formatting, name filtering, and click-to-sort for templates/reports/
 * analytics.html. Every row is already server-rendered -- this only reads
 * and reorders DOM nodes already on the page, it never fetches anything.
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

    function relativeTime(iso) {
        if (!iso) return 'Never';
        var diff = (Date.now() - new Date(iso).getTime()) / 1000;
        if (diff < 60) return 'Just now';
        if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return Math.floor(diff / 86400) + 'd ago';
    }

    document.addEventListener('DOMContentLoaded', function () {
        var table = document.getElementById('analyticsTable');
        if (!table) return;

        // Server renders an absolute timestamp (readable with JS off); this
        // swaps it for a relative one, same convention as the dashboard
        // cards and the report page's Activity panel.
        table.querySelectorAll('tbody tr[data-last-viewed]').forEach(function (tr) {
            var iso = tr.getAttribute('data-last-viewed');
            var cell = tr.querySelector('.analytics-last-viewed');
            if (!cell) return;
            cell.textContent = iso ? relativeTime(iso) : 'Not viewed yet';
            if (iso) cell.title = new Date(iso).toLocaleString();
        });

        var rows = Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-name]'));

        // Filter: pure client-side (every row is already on the page), so
        // this never round-trips the server -- unlike base.html's generic
        // form.ui-list-toolbar search, which this input is deliberately not
        // wrapped in.
        var search = document.getElementById('analyticsSearch');
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

        // Give every sortable header a visible affordance — an inactive
        // double-chevron that becomes a single directional arrow on the
        // active column — so the columns read as sortable at a glance
        // rather than only revealing it on hover.
        function paintIndicators() {
            sortHeaders.forEach(function (th) {
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
        paintIndicators();

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
                paintIndicators();
            });
        });
    });
})();

/* Relative timestamps enhance server-rendered global analytics. */
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

    });
})();

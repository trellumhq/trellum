    // ── Public API namespace ─────────────────────────────
    function _fwDownloadCsv(columns, rows, filename) {
        var csv = columns.join(',') + '\n';
        for (var i = 0; i < rows.length; i++) {
            csv += rows[i].map(function(v) {
                var s = v == null ? '' : String(v);
                return s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0
                    ? '"' + s.replace(/"/g, '""') + '"' : s;
            }).join(',') + '\n';
        }
        var blob = new Blob([csv], {type: 'text/csv;charset=utf-8;'});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
    }
    window._fwDownloadCsv = _fwDownloadCsv;

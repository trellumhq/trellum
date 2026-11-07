    // ── Public API namespace ─────────────────────────────
    function _fwDownloadCsv(columns, rows, filename) {
        function cell(value) {
            var text = value == null ? '' : String(value);
            return /[,"\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
        }
        var csv = columns.map(cell).join(',') + '\n';
        for (var i = 0; i < rows.length; i++) {
            csv += rows[i].map(cell).join(',') + '\n';
        }
        var blob = new Blob([csv], {type: 'text/csv;charset=utf-8;'});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
    }
    window._fwDownloadCsv = _fwDownloadCsv;

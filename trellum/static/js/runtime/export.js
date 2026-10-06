    // ── Export helpers (html2canvas + jsPDF) ──
    var _exportHideSelectors = [
        '.fw-filter-bar', '.fw-anno-bar', '.fw-table-search',
        '.fw-table-count', '.fw-csv-btn', '.fw-chart-dl', '.fw-export-wrap',
    ];
    function _fwExportCapture(cb) {
        var container = document.querySelector('.fw-container');
        if (!container) return;
        var btn = document.getElementById('fwExportBtn');
        if (btn) btn.classList.add('running');
        var slug = document.body.getAttribute('data-report-slug') || 'report';

        var hidden = [];
        _exportHideSelectors.forEach(function(sel) {
            document.querySelectorAll(sel).forEach(function(el) {
                if (el.style.display !== 'none') {
                    hidden.push({el: el, prev: el.style.display});
                    el.style.display = 'none';
                }
            });
        });

        html2canvas(container, {
            backgroundColor: getComputedStyle(document.body).backgroundColor,
            scale: 2,
            useCORS: true,
            logging: false,
            windowWidth: container.scrollWidth,
        }).then(function(canvas) {
            hidden.forEach(function(h) { h.el.style.display = h.prev; });
            if (btn) btn.classList.remove('running');
            cb(canvas, slug);
        }).catch(function(err) {
            hidden.forEach(function(h) { h.el.style.display = h.prev; });
            if (btn) btn.classList.remove('running');
            console.error('Export failed:', err);
        });
    }

    function _exportPNG() {
        _fwExportCapture(function(canvas, slug) {
            var link = document.createElement('a');
            link.download = slug + '.png';
            link.href = canvas.toDataURL('image/png');
            link.click();
        });
    }

    function _exportPDF() {
        _fwExportCapture(function(canvas, slug) {
            var imgData = canvas.toDataURL('image/png');
            var w = canvas.width;
            var h = canvas.height;
            var pxPerMm = 96 / 25.4 * 2;
            var pdfW = w / pxPerMm;
            var pdfH = h / pxPerMm;
            var orientation = pdfW > pdfH ? 'l' : 'p';
            var pdf = new jspdf.jsPDF(orientation, 'mm', [pdfW, pdfH]);
            pdf.addImage(imgData, 'PNG', 0, 0, pdfW, pdfH);
            pdf.save(slug + '.pdf');
        });
    }

    // ── Export helpers (html2canvas + jsPDF) ──
    var _exportHideSelectors = [
        '.fw-filter-bar', '.fw-anno-bar', '.fw-table-search',
        '.fw-table-count', '.fw-csv-btn', '.fw-chart-dl', '.fw-export-wrap',
    ];
    // Export-only libraries load on first use; `_fwVendor` maps name -> URL (see cdn.py).
    var _exportGlobals = {html2canvas: function() { return typeof html2canvas === 'function'; },
        jspdf: function() { return typeof jspdf !== 'undefined' && !!jspdf.jsPDF; }};
    var _exportLoads = {};
    function _fwLoadExportLib(name) {
        if (_exportGlobals[name]()) return Promise.resolve();
        var url = (window._fwVendor || {})[name];
        if (!url) return Promise.reject(new Error('Image capture is unavailable because the capture library did not load.'));
        if (!_exportLoads[name]) _exportLoads[name] = new Promise(function(resolve, reject) {
            var tag = document.createElement('script');
            tag.src = url;
            tag.onload = resolve;
            tag.onerror = function() { delete _exportLoads[name]; tag.remove(); reject(new Error('Image capture is unavailable because the capture library did not load.')); };
            document.head.appendChild(tag);
        });
        return _exportLoads[name];
    }
    function _fwLoadExportLibs(names) { return Promise.all(names.map(_fwLoadExportLib)); }

    function _fwExportCapture(cb, libs) {
        var btnLoad = document.getElementById('fwExportBtn');
        if (btnLoad) btnLoad.classList.add('running');
        _fwLoadExportLibs(libs || ['html2canvas']).then(function() {
            if (btnLoad) btnLoad.classList.remove('running');
            _fwExportCaptureNow(cb);
        }).catch(function(err) {
            if (btnLoad) btnLoad.classList.remove('running');
            console.error('Export failed:', err);
        });
    }

    function _fwExportCaptureNow(cb) {
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
        }, ['html2canvas', 'jspdf']);
    }

    // Selection UI is temporary and is removed before html2canvas runs.
    var _analysisSelection = null;
    var _analysisNotice = null;
    var _analysisNoticeTimer = null;
    var _captureHideSelectors = _exportHideSelectors.concat(['.fw-analysis-capture-ui']);

    function _analysisMessage(text, isError) {
        if (!_analysisNotice || !_analysisNotice.isConnected) {
            _analysisNotice = document.createElement('div');
            _analysisNotice.className = 'fw-analysis-capture-ui';
            _analysisNotice.setAttribute('role', 'status');
            _analysisNotice.setAttribute('aria-live', 'polite');
            _analysisNotice.style.cssText = 'position:fixed;z-index:99999;top:12px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:12px;max-width:calc(100vw - 32px);box-sizing:border-box;padding:10px 16px;background:var(--bg-card,#fff);color:var(--text-main,#222);border:1px solid var(--border-color,#ccc);border-radius:8px;font:14px/1.4 sans-serif;box-shadow:0 2px 12px rgba(0,0,0,.16)';
            document.body.appendChild(_analysisNotice);
        }
        _analysisNotice.textContent = '';
        var label = document.createElement('span');
        label.textContent = text;
        _analysisNotice.appendChild(label);
        _analysisNotice.style.borderColor = isError
            ? 'var(--accent-red)' : 'var(--border-color)';
        _analysisNotice.style.borderLeftColor = isError
            ? 'var(--accent-red)' : 'var(--accent-yellow)';
        _analysisNotice.style.borderLeftWidth = '4px';
        clearTimeout(_analysisNoticeTimer);
        _analysisNotice.classList.toggle('fw-analysis-error', !!isError);
        if (isError) {
            var dismiss = document.createElement('button');
            dismiss.type = 'button';
            dismiss.setAttribute('aria-label', 'Dismiss capture message');
            dismiss.textContent = 'x';
            dismiss.style.cssText = 'padding:0 4px;border:0;background:transparent;color:var(--text-main);font:inherit;font-size:18px;cursor:pointer';
            dismiss.addEventListener('click', function() {
                clearTimeout(_analysisNoticeTimer);
                _analysisNotice.remove(); _analysisNotice = null;
            });
            _analysisNotice.appendChild(dismiss);
        }
        _analysisNoticeTimer = setTimeout(function() {
            if (_analysisNotice) _analysisNotice.remove();
            _analysisNotice = null;
        }, isError ? 15000 : 5000);
    }

    function _analysisVisible(el) {
        var style = getComputedStyle(el);
        return el.getClientRects().length > 0 && style.display !== 'none'
            && style.visibility !== 'hidden' && style.visibility !== 'collapse'
            && style.opacity !== '0';
    }

    function _analysisTargets() {
        var out = [];
        document.querySelectorAll('.fw-chart-container').forEach(function(el) {
            if (el.querySelector('canvas') && _analysisVisible(el)) out.push(el);
        });
        document.querySelectorAll('.fw-section').forEach(function(el) {
            if (_analysisVisible(el)) out.push(el);
        });
        return out;
    }

    function _analysisPending() {
        var loading = document.getElementById('fwLoading');
        if (loading && _analysisVisible(loading)) return 'The report is still loading.';
        var chunk = document.getElementById('fwChunkBanner');
        if (chunk && _analysisVisible(chunk))
            return 'The report is still loading data.';
        var liveStatus = window._fwLiveQuery && window._fwLiveQuery.getCaptureStatus
            ? window._fwLiveQuery.getCaptureStatus() : null;
        if (liveStatus && liveStatus.pending)
            return 'A live data query is still running. Wait for it to finish, then capture again.';
        if (liveStatus && liveStatus.error)
            return 'Live data is out of date because its query failed: ' + liveStatus.error;
        var statuses = document.querySelectorAll('.fw-live-status');
        for (var i = 0; i < statuses.length; i++) {
            if (/updating|retrying/i.test(statuses[i].textContent || ''))
                return 'A live data query is still running. Wait for it to finish, then capture again.';
        }
        if (document.querySelector('[data-live-state="loading"],[data-live-state="updating"],[data-live-state="debounce"]'))
            return 'A live data query is still running. Wait for it to finish, then capture again.';
        return '';
    }

    function _analysisCaptureError(message) {
        _analysisMessage(message, true);
        return Promise.resolve(false);
    }

    function _analysisCaptureSuccess(fileName, imageUrl) {
        clearTimeout(_analysisNoticeTimer);
        if (_analysisNotice) _analysisNotice.remove();
        _analysisNotice = document.createElement('div');
        _analysisNotice.className = 'fw-analysis-capture-ui';
        _analysisNotice.setAttribute('role', 'status');
        _analysisNotice.setAttribute('aria-live', 'polite');
        _analysisNotice.style.cssText = 'position:fixed;z-index:99999;top:12px;left:50%;transform:translateX(-50%);width:min(620px,calc(100vw - 32px));max-height:calc(100vh - 24px);overflow:auto;box-sizing:border-box;padding:16px;background:var(--bg-card,#fff);color:var(--text-main,#222);border:1px solid var(--border-color,#ccc);border-left:4px solid var(--accent-yellow,#e3b341);border-radius:8px;font:14px sans-serif;box-shadow:0 2px 12px rgba(0,0,0,.16)';
        var close = document.createElement('button');
        close.type = 'button'; close.textContent = 'Close';
        close.style.cssText = 'float:right;padding:4px 8px;cursor:pointer;background:var(--bg-main,var(--bg-card,#fff));color:var(--text-main,#222);border:1px solid var(--border-color,#ccc);border-radius:6px';
        close.addEventListener('click', function() {
            clearTimeout(_analysisNoticeTimer);
            _analysisNotice.remove(); _analysisNotice = null;
        });
        _analysisNotice.appendChild(close);
        var title = document.createElement('strong');
        title.textContent = 'Your capture is ready';
        title.style.cssText = 'display:block;line-height:1.4;margin:4px 84px 10px 0';
        _analysisNotice.appendChild(title);
        var detail = document.createElement('p');
        detail.textContent = 'One file, everything together: the chart image (PNG) is inside ' + fileName + ', along with its filters, source and dates. You do not need a separate image. Nothing has been published.';
        detail.style.cssText = 'line-height:1.4;margin:0 0 10px;overflow-wrap:anywhere';
        _analysisNotice.appendChild(detail);
        var preview = document.createElement('img');
        preview.src = imageUrl; preview.alt = 'Preview of the captured report view';
        preview.style.cssText = 'display:block;max-width:100%;max-height:180px;object-fit:contain;margin:10px 0;border:1px solid var(--border-color,#ccc)';
        _analysisNotice.appendChild(preview);
        var next = document.createElement('p');
        next.textContent = 'Give this downloaded file to the AI tool you use to write your Trellum reports, with the finding you want to explain. It can turn the capture into an analysis for you to review.';
        next.style.cssText = 'line-height:1.4;margin:10px 0';
        _analysisNotice.appendChild(next);
        var steps = document.createElement('ol');
        ['Attach the capture file to your AI authoring tool.',
            'Ask it to write an analysis using this evidence.',
            'Review the text and image, then publish to your chosen audience.'].forEach(function(text) {
            var step = document.createElement('li'); step.textContent = text;
            step.style.cssText = 'padding:4px 0;line-height:1.4'; steps.appendChild(step);
        });
        _analysisNotice.appendChild(steps);
        var prompt = document.createElement('p');
        prompt.textContent = 'Try: “Create a Trellum analysis from this capture. Explain the finding, include the image and source, and show me a draft before publishing.”';
        prompt.style.cssText = 'line-height:1.4;padding:10px;border:1px solid var(--border-color,#ccc);border-radius:6px';
        _analysisNotice.appendChild(prompt);
        var limit = document.createElement('p');
        limit.textContent = 'The capture is a fixed picture, not raw data or a finished article. Use an AI tool with access to your report repository; the portal AI assistant cannot import captures or write articles yet.';
        limit.style.cssText = 'line-height:1.4;margin:10px 0';
        _analysisNotice.appendChild(limit);
        var advanced = document.createElement('details');
        var summary = document.createElement('summary');
        summary.textContent = 'Advanced: command-line steps';
        summary.style.cursor = 'pointer'; advanced.appendChild(summary);
        var guidance = document.createElement('p');
        guidance.textContent = 'Replace the example names and file path with yours. Import extracts the PNG and source details into evidence files and prints the image reference to add to content.md. Build and review before publishing.';
        advanced.appendChild(guidance);
        var commands = [
            'trellum analysis new september-findings',
            'trellum analysis import reports/september-findings path/to/capture.trellum-capture.json --name conversion',
            'python -m trellum.run reports/september-findings --no-serve'
        ];
        commands.forEach(function(command) {
            var row = document.createElement('div');
            row.style.cssText = 'display:flex;align-items:center;gap:8px;margin:6px 0';
            var code = document.createElement('code');
            code.textContent = command;
            code.style.cssText = 'flex:1;overflow-wrap:anywhere';
            var copy = document.createElement('button');
            copy.type = 'button'; copy.textContent = 'Copy';
            copy.style.cssText = 'flex:none;padding:4px 8px;cursor:pointer;background:var(--bg-main,var(--bg-card,#fff));color:var(--text-main,#222);border:1px solid var(--border-color,#ccc);border-radius:6px';
            copy.addEventListener('click', function() {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(command).then(function() { copy.textContent = 'Copied'; }, function() { copy.textContent = 'Copy unavailable'; });
                } else copy.textContent = 'Copy unavailable';
            });
            row.appendChild(code); row.appendChild(copy); advanced.appendChild(row);
        });
        _analysisNotice.appendChild(advanced);
        var doc = document.createElement('a');
        doc.href = document.documentElement.getAttribute('data-analysis-guide-url')
            || 'https://trellum.dev/docs/latest/workflow/published-analyses/';
        doc.textContent = 'Read the publish an analysis guide';
        doc.style.cssText = 'color:var(--accent-blue,#4c8bf5);text-decoration:underline';
        _analysisNotice.appendChild(doc);
        document.body.appendChild(_analysisNotice);
    }

    function _analysisComponents() {
        var data = window._reportData || {};
        var scope = window._currentScope || data.defaultScope || '';
        var cache = window._scopeCache || {};
        var components = cache[scope];
        if (!components && (!scope || scope === data.defaultScope)) components = data.components || {};
        if (!components) throw new Error('The active report section is still loading. Wait and try again.');
        return {scope: scope, components: components, data: data};
    }

    function _analysisFilters(target, components) {
        var filters = {};
        var roots = [];
        if (target.matches('[data-fw-kind]')) roots.push(target);
        Array.prototype.push.apply(roots, target.querySelectorAll('[data-fw-kind]'));
        if (target.matches('canvas')) roots.push(target);
        Array.prototype.push.apply(roots, target.querySelectorAll('canvas'));
        var parents = {};
        Object.keys(components).forEach(function(id) {
            var cfg = components[id];
            if (cfg && cfg.type === 'scoped_data_source' && cfg.dataset_id && cfg.parent_id)
                parents[cfg.dataset_id] = cfg.parent_id;
        });
        var datasets = {};
        roots.forEach(function(root) {
            if (!_analysisVisible(root)) return;
            var cfg = components[root.id];
            var dsId = cfg && cfg.dataset_id;
            var seen = {};
            while (dsId && !seen[dsId]) {
                seen[dsId] = true;
                datasets[dsId] = true;
                dsId = parents[dsId];
            }
        });
        Object.keys(datasets).forEach(function(dsId) {
            if (window.fw.filterEngine && window.fw.filterEngine.isReady(dsId))
                filters[dsId] = window.fw.filterEngine.getFilterState(dsId);
        });
        return filters;
    }

    function _analysisSnapshot(target) {
        var active = _analysisComponents();
        var data = active.data;
        var builtAt = data._freshness && data._freshness.generated_at || null;
        var liveStatus = window._fwLiveQuery && window._fwLiveQuery.getCaptureStatus
            ? window._fwLiveQuery.getCaptureStatus() : {revision: 0};
        return {scope: active.scope, builtAt: builtAt, data: data,
            components: active.components,
            liveRevision: liveStatus.revision || 0,
            filters: _analysisFilters(target, active.components)};
    }

    function _analysisSnapshotChanged(target, snapshot) {
        var current = _analysisSnapshot(target);
        return current.scope !== snapshot.scope || current.builtAt !== snapshot.builtAt
            || current.data !== snapshot.data || current.components !== snapshot.components
            || current.liveRevision !== snapshot.liveRevision
            || JSON.stringify(current.filters) !== JSON.stringify(snapshot.filters);
    }

    function _analysisReady(target, components) {
        var roots = [];
        if (target.matches('[data-fw-kind]')) roots.push(target);
        Array.prototype.push.apply(roots, target.querySelectorAll('[data-fw-kind]'));
        if (target.matches('canvas')) roots.push(target);
        Array.prototype.push.apply(roots, target.querySelectorAll('canvas'));
        var chartTypes = {chart: true, line: true, doughnut: true, funnel: true,
            scatter: true, heatmap: true, treemap: true};
        var nonVisual = {data_source: true, scoped_data_source: true,
            live_data_source: true, raw_html: true};
        for (var i = 0; i < roots.length; i++) {
            var root = roots[i];
            if (!_analysisVisible(root)) continue;
            var cfg = components[root.id];
            if (!cfg || !cfg.type || nonVisual[cfg.type]) continue;
            if (cfg.dataset_id && window.fw.filterEngine && !window.fw.filterEngine.isReady(cfg.dataset_id))
                return 'This section is still loading its data. Wait and try again.';
            if (chartTypes[cfg.type] && !(window._chartInstances || {})[root.id])
                return 'A chart in this section has not finished rendering. Scroll it into view, wait, and try again.';
            if (!chartTypes[cfg.type] && !root.childElementCount && !(root.textContent || '').trim())
                return 'Content in this section has not finished rendering. Wait and try again.';
        }
        var canvases = target.matches('canvas') ? [target] : target.querySelectorAll('canvas');
        for (var c = 0; c < canvases.length; c++) {
            var canvas = canvases[c];
            if (!_analysisVisible(canvas)) continue;
            var canvasConfig = components[canvas.id];
            if (!canvasConfig || !chartTypes[canvasConfig.type]) continue;
            var chart = (window._chartInstances || {})[canvas.id];
            if (!chart || !canvas.width || !canvas.height)
                return 'A chart in this section has not finished rendering. Wait for it to appear, then capture again.';
        }
        return '';
    }

    function _analysisFinalRender(target) {
        var canvases = target.matches('canvas') ? [target] : target.querySelectorAll('canvas');
        var instances = window._chartInstances || {};
        Array.prototype.forEach.call(canvases, function(canvas) {
            if (!_analysisVisible(canvas)) return;
            var chart = instances[canvas.id];
            if (!chart) return;
            if (chart.stop) chart.stop();
            if (chart.update) chart.update('none');
        });
        return new Promise(function(resolve) {
            requestAnimationFrame(function() { requestAnimationFrame(resolve); });
        });
    }

    function _analysisTitle(target) {
        if (target.classList.contains('fw-section')) {
            var sectionTitle = target.getAttribute('data-fw-section-title');
            var sectionHeading = target.querySelector('h2');
            return sectionTitle || (sectionHeading ? sectionHeading.textContent.trim() : '');
        }
        var title = target.querySelector('.fw-chart-title');
        if (!title && target.classList.contains('fw-chart-container')) {
            var previous = target.previousElementSibling;
            if (previous && previous.classList.contains('fw-chart-title')) title = previous;
        }
        if (title) return title.textContent.trim();
        var section = target.closest('.fw-section');
        var heading = section && section.querySelector('h2');
        return heading ? heading.textContent.trim() : '';
    }

    function _analysisSourceUrl() {
        try {
            var url = new URL(window.location.href);
            if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
            if (/\/share\/[^/]+(?:\/|$)/i.test(url.pathname)) return '';
            return url.origin + url.pathname;
        } catch (e) { return ''; }
    }

    function _analysisDownloadPayload(payload, filename) {
        var blob = new Blob([JSON.stringify(payload)], {type: 'application/json'});
        var link = document.createElement('a');
        link.download = filename;
        link.href = URL.createObjectURL(blob); link.click();
        setTimeout(function() { URL.revokeObjectURL(link.href); }, 1000);
        _analysisCaptureSuccess(filename, 'data:image/png;base64,' + payload.image.data_base64);
        return true;
    }

    function _analysisDeliver(payload, filename) {
        // The same-origin server decides whether an authenticated analysis
        // session exists, including access through a protected review proxy.
        if (!/^https?:$/.test(location.protocol))
            return Promise.resolve(_analysisDownloadPayload(payload, filename));
        return fetch('/_fw/review/analysis', {cache: 'no-store', credentials: 'same-origin'})
            .then(function(response) { return response.ok ? response.json() : null; })
            .catch(function() { return null; }).then(function(session) {
                if (!session || !session.active || !session.token || !window.crypto || !window.crypto.getRandomValues)
                    return _analysisDownloadPayload(payload, filename);
                return new Promise(function(resolve) {
                    var previousFocus = document.activeElement;
                    var dialog = document.createElement('dialog');
                    dialog.className = 'fw-analysis-capture-ui';
                    dialog.setAttribute('aria-labelledby', 'fw-analysis-delivery-title');
                    dialog.style.cssText = 'width:min(420px,calc(100vw - 48px));max-height:85vh;overflow:auto;margin:auto;padding:24px;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-card);color:var(--text-main)';
                    var title = document.createElement('h2');
                    title.id = 'fw-analysis-delivery-title'; title.textContent = 'Capture for analysis';
                    var destination = document.createElement('p');
                    destination.textContent = 'Send this frozen evidence to ' + session.slug + '.';
                    var label = document.createElement('label');
                    label.textContent = 'Note for your agent (optional)';
                    var note = document.createElement('textarea');
                    note.maxLength = 2000; note.rows = 3;
                    note.style.cssText = 'display:block;width:100%;box-sizing:border-box;margin:8px 0 16px;padding:8px;background:var(--bg-main);color:var(--text-main);border:1px solid var(--border-color);border-radius:4px';
                    label.appendChild(note);
                    var status = document.createElement('p'); status.setAttribute('role', 'status');
                    var sending = false;
                    var requestBytes = new Uint8Array(16); window.crypto.getRandomValues(requestBytes);
                    var requestId = Array.from(requestBytes).map(function(n) { return n.toString(16).padStart(2, '0'); }).join('');
                    var finish = function(result) {
                        dialog.close(); dialog.remove();
                        if (previousFocus && previousFocus.isConnected) previousFocus.focus();
                        resolve(result);
                    };
                    var button = function(text, callback) {
                        var el = document.createElement('button'); el.type = 'button'; el.textContent = text;
                        el.style.cssText = 'padding:8px 12px;margin:4px 8px 4px 0;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-card);color:var(--text-main);cursor:pointer';
                        el.addEventListener('click', callback); return el;
                    };
                    var send = button('Send to agent', function() {
                        if (sending) return;
                        sending = true; send.disabled = true; download.disabled = true; cancel.disabled = true; note.disabled = true;
                        // A retry must carry the same note when delivery was unconfirmed.
                        note.readOnly = true;
                        status.textContent = 'Sending capture…';
                        fetch('/_fw/review/analysis/capture', {method: 'POST', credentials: 'same-origin',
                            headers: {'Content-Type': 'application/json', 'X-Trellum-Analysis-Token': session.token},
                            body: JSON.stringify({request_id: requestId, note: note.value, capture: payload})})
                            .then(function(response) { return response.json().then(function(result) {
                                if (!response.ok) throw new Error(result.error || 'Capture was not accepted.');
                                finish(true); _analysisMessage('Capture sent. Your agent will import it into the analysis.');
                            }); }).catch(function(err) {
                                status.textContent = (err && err.message || 'Capture delivery could not be confirmed.') + ' Retry or download the capture.';
                            }).then(function() {
                                sending = false; send.disabled = false; download.disabled = false; cancel.disabled = false; note.disabled = false;
                            });
                    });
                    var download = button('Download capture', function() { finish(_analysisDownloadPayload(payload, filename)); });
                    var cancel = button('Cancel', function() { finish(false); });
                    dialog.addEventListener('cancel', function(event) { event.preventDefault(); if (!sending) finish(false); });
                    [title, destination, label, status, send, download, cancel].forEach(function(el) { dialog.appendChild(el); });
                    document.body.appendChild(dialog); dialog.showModal(); note.focus();
                });
            });
    }

    function _analysisRender(target) {
        var pending = _analysisPending();
        if (pending) return Promise.reject(new Error(pending));
        if (!document.fonts || !document.fonts.ready)
            return Promise.reject(new Error('This browser cannot wait for fonts before capture.'));
        if (!target.isConnected || !_analysisVisible(target))
            return Promise.reject(new Error('Choose a visible chart or report section.'));
        var slug = document.body.getAttribute('data-report-slug') || '';
        var heading = document.querySelector('.fw-header h1');
        var canvas = target.matches('canvas') ? target : target.querySelector('canvas');
        var componentId = target.classList.contains('fw-section')
            ? (target.id || 'section') : (canvas ? canvas.id : (target.id || 'section'));
        var componentTitle = _analysisTitle(target);
        return _fwLoadExportLib('html2canvas').then(function() { return document.fonts.ready; }).then(function() { return new Promise(function(resolve) {
                requestAnimationFrame(function() { requestAnimationFrame(resolve); });
            }); }).then(function() { return _analysisFinalRender(target); }).then(function() {
                var recheck = _analysisPending();
                if (recheck) throw new Error(recheck);
                if (!target.isConnected || !_analysisVisible(target))
                    throw new Error('The selected chart or section is no longer visible. Select it again.');
                var notReady = _analysisReady(target, _analysisComponents().components);
                if (notReady) throw new Error(notReady);
                var snapshot = _analysisSnapshot(target);
                var hidden = [];
                _captureHideSelectors.forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        hidden.push([el, el.style.display]); el.style.display = 'none';
                    });
                });
                var restore = function() { hidden.forEach(function(row) { row[0].style.display = row[1]; }); };
                return Promise.resolve().then(function() {
                    return html2canvas(target, {backgroundColor: getComputedStyle(document.body).backgroundColor,
                        scale: 2, useCORS: true, logging: false});
                }).then(function(image) {
                    var pending = _analysisPending();
                    if (pending) throw new Error(pending);
                    if (!target.isConnected || _analysisSnapshotChanged(target, snapshot))
                        throw new Error('The report changed while capture was rendering. Capture the updated view again.');
                    var dataUrl = image.toDataURL('image/png');
                    var base64 = dataUrl.split(',')[1] || '';
                    var bytes = Math.floor(base64.length * 3 / 4) - (base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0);
                    if (bytes > 20 * 1024 * 1024) throw new Error('The image is larger than the 20 MiB capture limit. Select a smaller chart or section.');
                    var source = {report_slug: slug, report_name: heading ? heading.textContent.trim() : '',
                        url: _analysisSourceUrl(), component_id: componentId, component_title: componentTitle,
                        captured_at: new Date().toISOString(),
                        source_built_at: snapshot.builtAt,
                        filters: snapshot.filters};
                    var payload = {format: 'trellum-analysis-capture', version: 1,
                        image: {mime_type: 'image/png', data_base64: base64}, source: source};
                    restore();
                    return payload;
                }).then(function(value) { restore(); return value; }, function(err) { restore(); throw err; });
            });
    }

    function _fwCaptureElementSnapshotForAnalysis(target) {
        if (!target || !target.nodeType || target.nodeType !== 1)
            return Promise.reject(new Error('Choose a visible chart or report section.'));
        return _analysisRender(target);
    }

    function _fwCaptureElementForAnalysis(target) {
        if (!target || !target.nodeType || target.nodeType !== 1)
            return _analysisCaptureError('Choose a visible chart or report section.');
        return _analysisRender(target).then(function(payload) {
            var slug = payload.source.report_slug;
            var componentId = payload.source.component_id;
            return _analysisDeliver(payload, (slug || 'report') + '-' + (componentId || 'component') + '.trellum-capture.json');
        }).catch(function(err) {
            return _analysisCaptureError((err && err.message) || 'Capture failed.');
        });
    }

    function _fwCaptureForAnalysis() {
        if (_analysisSelection) return Promise.resolve(false);
        var pending = _analysisPending();
        if (pending) return _analysisCaptureError(pending);
        var targets = _analysisTargets();
        if (!targets.length) return _analysisCaptureError('There are no visible charts or report sections to capture.');
        return new Promise(function(resolve) {
            var oldTabIndex = [];
            var style = document.createElement('style');
            style.className = 'fw-analysis-capture-ui';
            style.textContent = '.fw-analysis-selectable{outline:3px solid var(--accent-yellow)!important;outline-offset:3px;cursor:pointer!important}.fw-analysis-selectable:hover,.fw-analysis-selectable:focus{outline-color:var(--accent-red)!important}';
            document.head.appendChild(style);
            targets.forEach(function(el, i) {
                oldTabIndex.push(el.getAttribute('tabindex'));
                el.setAttribute('tabindex', '0'); el.classList.add('fw-analysis-selectable');
                el.setAttribute('data-fw-capture-index', String(i));
            });
            _analysisSelection = {targets: targets, oldTabIndex: oldTabIndex, style: style,
                resolve: resolve, previousFocus: document.activeElement};
            _analysisMessage('Choose a highlighted chart or section. The download is a frozen PNG with filters and source times, not raw rows or a written analysis. Press Escape to cancel.');
            clearTimeout(_analysisNoticeTimer);
            _analysisNoticeTimer = null;
            _analysisNotice.style.width = 'min(620px,calc(100vw - 32px))';
            var cancel = document.createElement('button');
            cancel.className = 'fw-analysis-capture-ui'; cancel.type = 'button'; cancel.textContent = 'Cancel';
            cancel.style.cssText = 'flex:none;align-self:flex-start;padding:6px 10px;cursor:pointer;background:var(--bg-main,var(--bg-card,#fff));color:var(--text-main,#222);border:1px solid var(--border-color,#ccc);border-radius:6px';
            cancel.addEventListener('click', function() { _analysisFinish(false); });
            _analysisNotice.appendChild(cancel);
            document.addEventListener('click', _analysisClick, true);
            document.addEventListener('keydown', _analysisKey, true);
            targets[0].focus();
        });
    }

    function _analysisFinish(result) {
        var state = _analysisSelection;
        if (!state) return;
        document.removeEventListener('click', _analysisClick, true);
        document.removeEventListener('keydown', _analysisKey, true);
        state.targets.forEach(function(el, i) {
            el.classList.remove('fw-analysis-selectable');
            el.removeAttribute('data-fw-capture-index');
            if (state.oldTabIndex[i] === null) el.removeAttribute('tabindex');
            else el.setAttribute('tabindex', state.oldTabIndex[i]);
        });
        state.style.remove();
        document.querySelectorAll('.fw-analysis-capture-ui').forEach(function(el) { el.remove(); });
        _analysisNotice = null;
        _analysisSelection = null;
        if (state.previousFocus && state.previousFocus.isConnected) state.previousFocus.focus();
        state.resolve(result);
    }

    function _analysisClick(event) {
        var target = event.target.closest && event.target.closest('.fw-analysis-selectable');
        if (!target) return;
        event.preventDefault(); event.stopPropagation();
        var capture = Promise.resolve().then(function() {
            return _fwCaptureElementForAnalysis(target);
        });
        _analysisFinish(capture);
    }

    function _analysisKey(event) {
        if (event.key === 'Escape') {
            event.preventDefault(); event.stopPropagation(); _analysisFinish(false); return;
        }
        if (event.key === 'Enter' || event.key === ' ') {
            var target = document.activeElement;
            if (target && target.classList.contains('fw-analysis-selectable')) {
                event.preventDefault(); event.stopPropagation();
                var capture = Promise.resolve().then(function() {
                    return _fwCaptureElementForAnalysis(target);
                });
                _analysisFinish(capture);
            }
        }
    }

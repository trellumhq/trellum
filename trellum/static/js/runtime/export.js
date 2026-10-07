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
            _analysisNotice.style.cssText = 'position:fixed;z-index:99999;top:12px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:12px;padding:10px 16px;background:var(--bg-card);color:var(--text-main);border:1px solid var(--border-color);border-radius:8px;font:14px sans-serif;box-shadow:0 2px 12px rgba(0,0,0,.16)';
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

    function _analysisDownload(target) {
        var pending = _analysisPending();
        if (pending) return _analysisCaptureError(pending);
        if (typeof html2canvas !== 'function')
            return _analysisCaptureError('Image capture is unavailable because the capture library did not load.');
        var slug = document.body.getAttribute('data-report-slug') || '';
        var heading = document.querySelector('.fw-header h1');
        var canvas = target.matches('canvas') ? target : target.querySelector('canvas');
        var componentId = target.classList.contains('fw-section')
            ? (target.id || 'section') : (canvas ? canvas.id : (target.id || 'section'));
        var componentTitle = _analysisTitle(target);
        if (document.fonts && document.fonts.ready) {
            return document.fonts.ready.then(function() { return new Promise(function(resolve) {
                requestAnimationFrame(function() { requestAnimationFrame(resolve); });
            }); }).then(function() { return _analysisFinalRender(target); }).then(function() {
                var recheck = _analysisPending();
                if (recheck) throw new Error(recheck);
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
                    var blob = new Blob([JSON.stringify(payload)], {type: 'application/json'});
                    var link = document.createElement('a');
                    link.download = (slug || 'report') + '-' + (componentId || 'component') + '.trellum-capture.json';
                    link.href = URL.createObjectURL(blob); link.click();
                    setTimeout(function() { URL.revokeObjectURL(link.href); }, 1000);
                    return true;
                }).then(function(value) { restore(); return value; }, function(err) { restore(); throw err; });
            }).then(function(ok) {
                if (ok) _analysisMessage('Capture downloaded.');
                return ok;
            }).catch(function(err) {
                return _analysisCaptureError((err && err.message) || 'Capture failed.');
            });
        }
        return Promise.resolve(_analysisCaptureError('This browser cannot wait for fonts before capture.'));
    }

    function _fwCaptureElementForAnalysis(target) {
        if (!target || !target.nodeType || target.nodeType !== 1)
            return _analysisCaptureError('Choose a visible chart or report section.');
        return _analysisDownload(target);
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
            _analysisMessage('Choose a highlighted chart or section. Press Escape to cancel.');
            var cancel = document.createElement('button');
            cancel.className = 'fw-analysis-capture-ui'; cancel.type = 'button'; cancel.textContent = 'Cancel';
            cancel.style.cssText = 'position:fixed;z-index:100000;top:12px;right:16px;padding:9px 14px;cursor:pointer;background:var(--bg-card);color:var(--text-main);border:1px solid var(--border-color);border-radius:6px';
            cancel.addEventListener('click', function() { _analysisFinish(false); });
            document.body.appendChild(cancel);
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

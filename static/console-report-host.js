(function () {
    'use strict';

    var root = document.querySelector('[data-console-report-host]');
    var frame = root && root.querySelector('[data-console-report-frame]');
    var status = root && root.querySelector('[data-console-report-status]');
    var catalog = document.querySelector('[data-console-catalog]');
    var shell = document.querySelector('[data-console-shell]');
    var prefix = window.PORTAL_CTX && window.PORTAL_CTX.prefix;
    if (!root || !frame || !status || !catalog || !shell || !prefix) return;

    var reportBase = prefix.replace(/\/$/, '') + '/r/';
    /* A surviving child browsing context participates in WebKit's native
       Back/Forward restoration. Keep only its markup as a template so each
       hosted report starts in a new context that history cannot overwrite. */
    var frameTemplate = frame.cloneNode(false);
    frame.remove();
    frame = null;
    var catalogPath = window.location.pathname;
    var catalogTitle = document.title;
    var catalogPageTitle = (shell.querySelector('[data-console-page-title]') || {}).textContent || '';
    var active = null;
    var serial = 0;
    var timeout = null;
    var scrollSavePending = false;
    var catalogContent = document.getElementById('content');
    var catalogReady = !catalogContent || catalogContent.dataset.catalogReady === 'true';
    var restoringCatalogScroll = false;
    var pendingCatalogScroll = null;
    var restoreGeneration = 0;
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

    function saveCatalogScroll() {
        scrollSavePending = false;
        if (active || restoringCatalogScroll || pendingCatalogScroll) return;
        var state = Object.assign({}, history.state || {});
        state.tlConsoleCatalog = Object.assign({}, state.tlConsoleCatalog || {}, {
            url: window.location.pathname + window.location.search + window.location.hash,
            x: window.scrollX,
            y: window.scrollY
        });
        history.replaceState(state, '', window.location.href);
    }

    function restoreCatalogScroll(state) {
        if (state) pendingCatalogScroll = state;
        if (!pendingCatalogScroll || !catalogReady) return;
        var generation = ++restoreGeneration;
        restoringCatalogScroll = true;
        window.requestAnimationFrame(function () {
            if (generation !== restoreGeneration) return;
            var target = pendingCatalogScroll;
            if (target) window.scrollTo(target.x || 0, target.y || 0);
            window.requestAnimationFrame(function () {
                if (generation !== restoreGeneration) return;
                restoringCatalogScroll = false;
                pendingCatalogScroll = null;
            });
        });
    }

    window.addEventListener('trellum:catalog-ready', function () {
        catalogReady = true;
        restoreCatalogScroll();
    });

    window.addEventListener('scroll', function () {
        if (active || restoringCatalogScroll || pendingCatalogScroll || scrollSavePending) return;
        scrollSavePending = true;
        window.requestAnimationFrame(saveCatalogScroll);
    }, { passive: true });

    function reportTarget(value) {
        var url;
        try { url = new URL(value, window.location.href); } catch (error) { return null; }
        if (url.origin !== window.location.origin || url.pathname.indexOf(reportBase) !== 0) return null;
        var rest = url.pathname.slice(reportBase.length).split('/').filter(Boolean);
        if (rest.length < 2 || !/\.html?$/i.test(rest[rest.length - 1])) return null;
        try { return { url: url, slug: decodeURIComponent(rest[0]) }; }
        catch (error) { return null; }
    }

    function canonicalUrl(url) {
        var next = new URL(url.href);
        next.searchParams.delete('_console_host');
        next.searchParams.delete('_console_host_token');
        next.searchParams.set('display', 'console');
        return next.pathname + next.search + next.hash;
    }

    function frameUrl(url, token) {
        var next = new URL(url.href);
        next.searchParams.set('display', 'focus');
        next.searchParams.set('_console_host', '1');
        next.searchParams.set('_console_host_token', token);
        return next.pathname + next.search + next.hash;
    }

    function setPageTitle(title) {
        if (window.TrellumConsoleShell && window.TrellumConsoleShell.setPageTitle) {
            window.TrellumConsoleShell.setPageTitle(title, shell);
        }
    }

    function notifyAssistant(report, reportDocument) {
        window.dispatchEvent(new CustomEvent('trellum:assistant-context', {
            detail: { report: report || null, document: reportDocument || null }
        }));
    }

    function freshFrame(url, title) {
        if (frame) frame.remove();
        frame = frameTemplate.cloneNode(false);
        frame.hidden = true;
        frame.title = title;
        frame.src = url;
        root.appendChild(frame);
    }

    function showFailure() {
        if (!active) return;
        frame.hidden = true;
        status.hidden = false;
        status.textContent = '';
        var message = document.createElement('span');
        message.textContent = 'We could not open this report here.';
        var fallback = document.createElement('a');
        fallback.href = active.canonical;
        fallback.textContent = 'Open report normally';
        fallback.setAttribute('data-console-report-native', '');
        status.appendChild(message);
        status.appendChild(fallback);
    }

    function showReport(target, title, token, depth) {
        window.clearTimeout(timeout);
        active = {
            canonical: canonicalUrl(target.url),
            slug: target.slug,
            title: title || target.slug,
            token: token,
            depth: depth || 1
        };
        root.hidden = false;
        catalog.toggleAttribute('inert', true);
        document.body.classList.add('tl-console-report-hosting');
        status.hidden = false;
        status.textContent = 'Loading report…';
        freshFrame(frameUrl(target.url, token), active.title);
        setPageTitle(active.title);
        document.title = active.title + ' - ' + (window.PORTAL_CTX.studio.name || 'trellum');
        notifyAssistant(active.slug);
        timeout = window.setTimeout(showFailure, 10000);
    }

    function hideReport() {
        if (!active) return;
        window.clearTimeout(timeout);
        active = null;
        serial += 1;
        if (frame) frame.remove();
        frame = null;
        root.hidden = true;
        catalog.removeAttribute('inert');
        document.body.classList.remove('tl-console-report-hosting');
        setPageTitle(catalogPageTitle);
        document.title = catalogTitle;
        notifyAssistant(null);
        var state = history.state && history.state.tlConsoleCatalog;
        restoreCatalogScroll(state);
    }

    function open(value, options) {
        var target = reportTarget(value);
        if (!target) return false;
        var title = options && options.title;
        var token = 'h' + Date.now().toString(36) + (++serial).toString(36);
        if (active) {
            var depth = active.depth + 1;
            var nextReportState = Object.assign({}, history.state || {}, {
                tlConsoleReport: {
                    url: canonicalUrl(target.url), title: title || target.slug,
                    token: token, depth: depth
                }
            });
            history.pushState(nextReportState, '', canonicalUrl(target.url));
            showReport(target, title, token, depth);
            return true;
        }
        var current = Object.assign({}, history.state || {}, {
            tlConsoleCatalog: {
                url: window.location.pathname + window.location.search + window.location.hash,
                x: window.scrollX,
                y: window.scrollY
            }
        });
        history.replaceState(current, '', window.location.href);
        var reportState = Object.assign({}, history.state || {}, {
            tlConsoleReport: {
                url: canonicalUrl(target.url), title: title || target.slug,
                token: token, depth: 1
            }
        });
        delete reportState.tlConsoleCatalog;
        history.pushState(reportState, '', canonicalUrl(target.url));
        showReport(target, title, token, 1);
        return true;
    }

    function close() {
        if (active) history.go(-active.depth);
    }

    function validMessage(event) {
        return active && frame && event.origin === window.location.origin
            && event.source === frame.contentWindow
            && event.data && event.data.hostToken === active.token
            && event.data.report === active.slug;
    }

    window.addEventListener('message', function (event) {
        if (!validMessage(event)) return;
        if (event.data.type === 'trellum:report-ready') {
            window.clearTimeout(timeout);
            status.hidden = true;
            frame.hidden = false;
            notifyAssistant(active.slug, frame.contentDocument);
        } else if (event.data.type === 'trellum:report-state') {
            var current = reportTarget(active.canonical);
            if (!current) return;
            var next = new URL(current.url.href);
            next.search = event.data.search || '';
            next.hash = event.data.hash || '';
            active.canonical = canonicalUrl(next);
            var state = Object.assign({}, history.state || {});
            state.tlConsoleReport = Object.assign({}, state.tlConsoleReport, { url: active.canonical });
            history.replaceState(state, '', active.canonical);
        } else if (event.data.type === 'trellum:report-close') {
            close();
        } else if (event.data.type === 'trellum:report-open') {
            open(event.data.url, { title: event.data.title });
        } else if (event.data.type === 'trellum:report-display'
                && (event.data.display === 'focus' || event.data.display === 'monitor')) {
            var destination = new URL(active.canonical, window.location.href);
            destination.searchParams.set('display', event.data.display);
            window.location.assign(destination.pathname + destination.search + destination.hash);
        }
    });

    window.addEventListener('popstate', function (event) {
        var report = event.state && event.state.tlConsoleReport;
        if (report) {
            // Opening/closing the assistant's mobile history entry preserves
            // this report state. Do not replace a healthy iframe for it.
            if (active && report.token === active.token && report.url === active.canonical) return;
            var target = reportTarget(report.url);
            if (target) {
                var token = 'h' + Date.now().toString(36) + (++serial).toString(36);
                var state = Object.assign({}, event.state);
                state.tlConsoleReport = Object.assign({}, report, { token: token });
                history.replaceState(state, '', report.url);
                showReport(target, report.title, token, report.depth);
            }
        } else {
            var catalogState = event.state && event.state.tlConsoleCatalog;
            hideReport();
            restoreCatalogScroll(catalogState);
        }
    });

    window.addEventListener('pageshow', function () {
        var catalogState = history.state && history.state.tlConsoleCatalog;
        if (catalogState && !(history.state && history.state.tlConsoleReport)) {
            restoreCatalogScroll(catalogState);
        }
    });

    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
                || event.shiftKey || event.altKey) return;
        var link = event.target.closest && event.target.closest('a[href]');
        if (!link || link.hasAttribute('download') || link.hasAttribute('data-console-report-native')) return;
        var targetName = link.getAttribute('target');
        if (targetName && targetName.toLowerCase() !== '_self') return;
        var destination = new URL(link.href, window.location.href);
        if (active && destination.origin === window.location.origin
                && destination.pathname === catalogPath) {
            event.preventDefault();
            var drawerClose = shell.querySelector('[data-console-close]');
            if (shell.classList.contains('is-open') && drawerClose) drawerClose.click();
            close();
            return;
        }
        var target = reportTarget(link.href);
        if (!target) return;
        event.preventDefault();
        open(target.url.href, { title: link.dataset.consoleReportTitle || target.slug });
    });

    window.TrellumConsoleReportHost = {
        open: open,
        close: close,
        isActive: function () { return !!active; },
        contextFor: function (source) {
            if (!active || !frame || source !== frame.contentWindow) return null;
            return { token: active.token, report: active.slug };
        }
    };
}());

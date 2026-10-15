/* Explicit console route adapters. Report hosts retain their own history owner. */
(function () {
    'use strict';
    var adapters = Object.create(null), generation = 0, request = null, cleanup = null, lifecycle = null;
    var currentURL = location.href, pending = false, restoringHistory = false;
    var entryIndex = (history.state && history.state.trellumNavigation && history.state.trellumNavigation.index) || 0;
    var main = document.querySelector('main[data-console-page]');
    if (!main || document.querySelector('[data-console-report-host]')) return;
    var initialController = main.dataset.consoleController;
    if (initialController === 'native') return;
    var ready = Promise.resolve();
    function state(position) {
        return Object.assign({}, history.state || {}, { trellumNavigation: { url: currentURL, index: entryIndex, position: position || [scrollX, scrollY] } });
    }
    function savePosition() { if (!pending && !restoringHistory) history.replaceState(state(), '', location.href); }
    function disposition(url) {
        if (url.origin !== location.origin || url.hash) return null;
        // Studio identity comes from existing authorized links; do not guess names.
        var match = url.pathname.match(/^\/s\/([^/]+)\/([^/]+)\/metrics(?:\/comparison)?\/?$/);
        if (!match || url.search) return null;
        var source = new URL(currentURL).pathname.match(/^\/s\/([^/]+)\/([^/]+)/);
        if (!source || source[1] !== match[1] || source[2] !== match[2]) return null;
        return { controller: 'metrics', title: 'Metrics' };
    }
    function leave() {
        return !window.TrellumSettingsForms || window.TrellumSettingsForms.confirmLeave(document);
    }
    function mark(title, url) {
        if (window.TrellumConsoleShell) window.TrellumConsoleShell.setPageTitle(title);
        document.querySelectorAll('.tl-console-nav-link').forEach(function (link) {
            var target = new URL(link.href);
            var active = target.pathname === url.pathname ||
                (/\/metrics\/?$/.test(target.pathname) && /\/metrics\/comparison\/?$/.test(url.pathname));
            link.classList.toggle('active', active);
            if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
        });
        var close = document.querySelector('[data-console-close]');
        if (close) close.click();
    }
    function unmount() {
        if (lifecycle) lifecycle.abort();
        if (cleanup) cleanup();
        lifecycle = null; cleanup = null;
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.unmount();
    }
    function mount(name) {
        lifecycle = new AbortController();
        var result = adapters[name].mount({ root: main, signal: lifecycle.signal, url: new URL(currentURL) });
        // mount may return cleanup or {unmount, ready}; ready resolves primary layout.
        cleanup = typeof result === 'function' ? result : result && result.unmount;
        ready = Promise.resolve(result && result.ready);
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.init(main);
        if (window.TrellumChoices) window.TrellumChoices.init(main);
        if (window.TrellumRecipients) window.TrellumRecipients.init(main);
    }
    function focusHeading() {
        var heading = main.querySelector('h1');
        if (!heading || !heading.getClientRects().length) heading = document.querySelector('[data-console-page-title]');
        if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    }
    function loading(title) {
        main.replaceChildren();
        var heading = document.createElement('h1'); heading.textContent = title; heading.tabIndex = -1;
        var status = document.createElement('p'); status.className = 'tl-console-navigation-status';
        status.setAttribute('role', 'status'); status.textContent = 'Loading ' + title + '…';
        main.append(heading, status); main.setAttribute('aria-busy', 'true');
        focusHeading(); window.scrollTo(0, 0);
    }
    function navigate(url, route, options) {
        options = options || {};
        if (!options.guarded && !leave()) return;
        if (!options.pop) savePosition();
        if (request) request.abort();
        var id = ++generation; request = new AbortController();
        unmount(); pending = true; currentURL = url.href;
        if (!options.pop) { ++entryIndex; history.pushState(state([0, 0]), '', url.href); }
        mark(route.title, url); document.title = route.title + ' — trellum'; loading(route.title);
        var timeout = setTimeout(function () { if (id === generation) request.abort(); }, 30000);
        fetch(url.href, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: request.signal,
            headers: { 'X-Trellum-Navigation': '1', Accept: 'application/json' }
        }).then(function (response) {
            if (!response.ok || !(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('Navigation failed');
            return response.json();
        }).then(function (data) {
            if (id !== generation) return;
            if (data.version !== 1 || data.controller !== route.controller || !adapters[data.controller] ||
                new URL(data.url, location.origin).href !== url.href || typeof data.html !== 'string') throw new Error('Invalid fragment');
            var fragment = document.createElement('template'); fragment.innerHTML = data.html;
            fragment.content.querySelectorAll('script').forEach(function (node) { node.remove(); });
            fragment.content.querySelectorAll('*').forEach(function (node) {
                Array.from(node.attributes).forEach(function (attribute) {
                    if (/^on/i.test(attribute.name) || (/^(href|src|action)$/i.test(attribute.name) && /^\s*javascript:/i.test(attribute.value))) node.removeAttribute(attribute.name);
                });
            });
            // Static, server-authored template markup only; scripts are never evaluated.
            main.replaceChildren(fragment.content); main.dataset.consoleController = data.controller;
            main.removeAttribute('aria-busy'); mark(data.title, url); document.title = data.title + ' — trellum';
            mount(data.controller);
            return ready.then(function () {
                if (id !== generation) return;
                pending = false;
                requestAnimationFrame(function () {
                    if (id !== generation) return;
                    if (options.position) window.scrollTo(options.position[0], options.position[1]);
                    else focusHeading();
                    document.dispatchEvent(new CustomEvent('console:navigated', {detail: {url: url.href}}));
                });
            });
        }).catch(function () {
            if (id !== generation) return;
            pending = false; main.removeAttribute('aria-busy');
            var loadingStatus = main.querySelector('.tl-console-navigation-status'); if (loadingStatus) loadingStatus.remove();
            var status = document.createElement('p'); status.setAttribute('role', 'alert');
            status.textContent = 'This page could not load. Open it directly to retry or sign in again.'; main.appendChild(status);
            var link = document.createElement('a'); link.href = url.href; link.dataset.consoleNative = '1';
            link.textContent = 'Open page directly'; main.appendChild(link);
        }).finally(function () { clearTimeout(timeout); });
    }
    document.addEventListener('click', function (event) {
        var link = event.target.closest('a[href]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey ||
            link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
        var url = new URL(link.href);
        if (!/^https?:$/.test(url.protocol) || (url.origin === location.origin && url.pathname === location.pathname && url.search === location.search && url.hash)) return;
        if (!event.trellumLeaveConfirmed && !leave()) { event.preventDefault(); return; }
        var route = disposition(url);
        if (route && adapters[route.controller] && main.dataset.consoleController === 'metrics' && !link.hasAttribute('data-console-native')) {
            event.preventDefault(); navigate(url, route, { guarded: true });
        } else {
            // Native/auth/download boundaries keep browser semantics and truthful feedback.
            if (window.TrellumSettingsForms) window.TrellumSettingsForms.nativeLeave();
            var status = document.querySelector('[data-console-navigation-native-status]');
            if (status) status.textContent = 'Opening page…';
        }
    });
    window.addEventListener('popstate', function (event) {
        if (restoringHistory) { restoringHistory = false; return; }
        var entry = event.state && event.state.trellumNavigation;
        if (!entry || entry.url !== location.href) return; // another history owner
        var url = new URL(entry.url), route = disposition(url);
        if (!route || !adapters[route.controller]) { location.reload(); return; }
        if (!leave()) { restoringHistory = true; history.go(entryIndex - entry.index); return; }
        entryIndex = entry.index;
        navigate(url, route, {pop: true, guarded: true, position: entry.position});
    });
    window.addEventListener('scroll', savePosition, {passive: true});
    window.addEventListener('pagehide', function () { savePosition(); ++generation; if (request) request.abort(); unmount(); });
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) return;
        if (pending) { var url = new URL(currentURL), route = disposition(url); if (route) navigate(url, route, {pop: true, guarded: true}); }
        else if (adapters[main.dataset.consoleController]) mount(main.dataset.consoleController);
    });
    window.TrellumNavigation = { register: function (name, adapter) {
        if (!adapter || typeof adapter.mount !== 'function') throw new Error('A navigation adapter needs mount(context)');
        adapters[name] = adapter;
        if (name === initialController) { initialController = null; mount(name); history.replaceState(state(), '', currentURL); }
    }, navigate: function (href) { var url = new URL(href, location.href), route = disposition(url); if (route && adapters[route.controller] && adapters[main.dataset.consoleController]) navigate(url, route); }, disposition: disposition };
}());

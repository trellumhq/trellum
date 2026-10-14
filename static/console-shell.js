(function () {
    'use strict';

    var desktop = window.matchMedia('(min-width: 1024px)');
    var dark = window.matchMedia('(prefers-color-scheme: dark)');

    function stored(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    function focusable(root) {
        return Array.prototype.filter.call(root.querySelectorAll(
            'a[href], button:not([disabled]), summary, input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ), function (el) { return el.getClientRects().length > 0; });
    }

    function themeFor(root) {
        var preference = stored('mgmt-theme');
        var mode = preference === 'light' || preference === 'dark' ? preference
            : preference === 'auto' ? 'auto' : root.dataset.consoleDefaultMode;
        return mode === 'light' || mode === 'dark' ? mode : (dark.matches ? 'dark' : 'light');
    }

    function setPageTitle(title, root) {
        root = root || document.querySelector('[data-console-shell]');
        var node = root && root.querySelector('[data-console-page-title]');
        title = title == null ? '' : String(title).trim();
        if (node) node.textContent = title;
        document.body.classList.toggle('tl-console-title-known', !!title);
    }

    function setTheme(preference, root) {
        if (preference) {
            try { localStorage.setItem('mgmt-theme', preference); } catch (e) {}
        }
        root = root || document.querySelector('[data-console-shell]');
        if (!root) return;
        var mode = themeFor(root);
        root.dataset.consoleTheme = mode;
        if (document.body.classList.contains('mgmt')) document.documentElement.dataset.theme = mode;
        root.dispatchEvent(new CustomEvent('tl-console-theme-change', {
            bubbles: true, detail: { preference: stored('mgmt-theme') || 'default', mode: mode }
        }));
    }

    /* Safe before append: mounted report shells can resolve their console
       palette and saved width before the browser paints the new subtree. */
    function prepare(root) {
        if (!root) return root;
        root.dataset.consoleTheme = themeFor(root);
        root.classList.toggle('is-collapsed', desktop.matches && stored('tl-console-collapsed') === '1');
        root.dataset.consoleReady = 'false';
        var title = root.querySelector('[data-console-page-title]');
        if (title) setPageTitle(title.textContent, root);
        return root;
    }

    function updateHealth(root, summary) {
        var alert = root && root.querySelector('[data-console-health-alert]');
        if (!alert) return;
        var status = summary && summary.status;
        var text = alert.querySelector('[data-console-health-message]');
        if (status === 'ok') {
            alert.hidden = true;
            alert.dataset.healthStatus = 'ok';
            alert.title = '';
            alert.setAttribute('aria-label', 'System health');
            if (text) text.textContent = '';
            return;
        }
        var message = summary && summary.message || 'System health unavailable';
        var detail = summary && summary.detail || 'Open System health for details';
        alert.hidden = false;
        alert.dataset.healthStatus = status === 'error' ? 'error' : 'unknown';
        alert.title = detail;
        alert.setAttribute('aria-label', message + '. ' + detail);
        if (text) text.textContent = message;
    }

    function pollHealth() {
        if (window.__trellumHealthInFlight) return window.__trellumHealthInFlight;
        var root = document.querySelector('[data-console-shell][data-console-health-url]');
        if (!root) return Promise.resolve();
        var controller = new AbortController();
        var timeout = window.setTimeout(function () { controller.abort(); }, 10000);
        window.__trellumHealthInFlight = fetch(root.dataset.consoleHealthUrl, {
            credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
            headers: { Accept: 'application/json' }
        }).then(function (response) {
            if (!response.ok) throw new Error('health request failed');
            return response.json();
        }).then(function (summary) {
            updateHealth(document.querySelector('[data-console-shell]'), summary);
        }).catch(function () {
            updateHealth(document.querySelector('[data-console-shell]'), {
                status: 'unknown', message: 'System health unavailable',
                detail: 'Open System health for details'
            });
        }).finally(function () {
            window.clearTimeout(timeout);
            window.__trellumHealthInFlight = null;
        });
        return window.__trellumHealthInFlight;
    }

    function startHealthPolling(root) {
        if (!root.dataset.consoleHealthUrl || window.__trellumHealthTimer) return;
        window.__trellumHealthTimer = window.setInterval(pollHealth, 30000);
    }

    function init(root) {
        if (!root || root.dataset.consoleInitialized) return;
        prepare(root);
        startHealthPolling(root);
        root.dataset.consoleInitialized = 'true';
        var body = document.body;
        var sidebar = root.querySelector('.tl-console-sidebar');
        var navScroller = root.querySelector('.tl-console-nav');
        var scrollKeyPrefix = root.dataset.consoleContext && 'tl-console-sidebar:' + root.dataset.consoleContext + ':';
        var toggle = root.querySelector('[data-console-toggle]');
        var collapse = root.querySelector('[data-console-collapse]');
        var backdrop = root.querySelector('[data-console-backdrop]');
        var close = root.querySelector('[data-console-close]');
        var searchToggle = root.querySelector('[data-console-search-toggle]');
        var search = root.querySelector('[data-console-search]');
        var searchShortcut = search && search.querySelector('kbd');
        var inertTargets = document.querySelectorAll('[data-console-inert]');
        var returnFocus = null;

        if (searchShortcut && /Mac|iPhone|iPad/.test(navigator.platform || '')) {
            searchShortcut.textContent = '⌘K';
        }

        if (scrollKeyPrefix) {
            var scrollKey = scrollKeyPrefix + window.location.pathname;
            try {
                var savedScroll = sessionStorage.getItem(scrollKey);
                if (savedScroll !== null) {
                    navScroller.scrollTop = Number(savedScroll) || 0;
                }
            } catch (e) {}
            sidebar.addEventListener('click', function (event) {
                var link = event.target.closest('.tl-console-nav-link[href]');
                if (!link || link.target === '_blank' || link.hasAttribute('download') || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                var destination = new URL(link.href, window.location.href);
                if (destination.origin !== window.location.origin) return;
                try {
                    sessionStorage.setItem(scrollKeyPrefix + window.location.pathname, String(navScroller.scrollTop));
                    sessionStorage.setItem(scrollKeyPrefix + destination.pathname, String(navScroller.scrollTop));
                } catch (e) {}
            });
        }

        function closeMenus(except) {
            root.querySelectorAll('details[open]').forEach(function (item) {
                if (item !== except) item.open = false;
            });
        }

        function syncTheme() {
            setTheme(null, root);
        }

        function setInert(value) {
            inertTargets.forEach(function (el) { el.toggleAttribute('inert', value); });
        }

        function syncSidebarState() {
            var hidden = !desktop.matches && !root.classList.contains('is-open');
            sidebar.toggleAttribute('inert', hidden);
            sidebar.setAttribute('aria-hidden', hidden ? 'true' : 'false');
        }

        function closeSearch() {
            root.classList.remove('is-search-open');
            if (searchToggle) searchToggle.setAttribute('aria-expanded', 'false');
        }

        function closeDrawer(restore) {
            var wasOpen = root.classList.contains('is-open');
            root.classList.remove('is-open');
            body.classList.remove('tl-console-drawer-open');
            if (toggle) toggle.setAttribute('aria-expanded', 'false');
            setInert(false);
            syncSidebarState();
            if (restore !== false && wasOpen && returnFocus) {
                try { returnFocus.focus({ preventScroll: true }); } catch (e) { returnFocus.focus(); }
            }
            returnFocus = null;
        }

        function openDrawer() {
            if (desktop.matches) return;
            closeMenus();
            closeSearch();
            returnFocus = document.activeElement;
            root.classList.add('is-open');
            body.classList.add('tl-console-drawer-open');
            if (toggle) toggle.setAttribute('aria-expanded', 'true');
            setInert(true);
            syncSidebarState();
            var first = focusable(sidebar)[0];
            if (first) {
                try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); }
            }
        }

        function setCollapsed(collapsed) {
            collapsed = !!collapsed && desktop.matches;
            document.documentElement.dataset.consoleCollapsed = collapsed ? 'true' : 'false';
            body.classList.toggle('tl-console-collapsed', collapsed);
            root.classList.toggle('is-collapsed', collapsed);
            if (collapse) {
                collapse.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
                collapse.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
            }
            root.dispatchEvent(new CustomEvent('tl-console-resize', {
                bubbles: true, detail: { width: collapsed ? 64 : 240 }
            }));
        }

        function openSearch() {
            if (!search || !searchToggle) return;
            closeMenus();
            closeDrawer(false);
            root.classList.add('is-search-open');
            searchToggle.setAttribute('aria-expanded', 'true');
            var input = search.querySelector('input');
            if (input) {
                try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
                if (input.select) input.select();
            }
        }

        syncTheme();
        setCollapsed(stored('tl-console-collapsed') === '1');
        syncSidebarState();
        var title = root.querySelector('[data-console-page-title]');
        setPageTitle(title ? title.textContent : '', root);

        if (toggle) toggle.addEventListener('click', function () {
            if (root.classList.contains('is-open')) closeDrawer(); else openDrawer();
        });
        if (backdrop) backdrop.addEventListener('click', function () { closeDrawer(); });
        if (close) close.addEventListener('click', function () { closeDrawer(); });
        if (searchToggle) searchToggle.addEventListener('click', function () {
            if (root.classList.contains('is-search-open')) closeSearch(); else openSearch();
        });
        if (collapse) collapse.addEventListener('click', function () {
            var collapsed = !root.classList.contains('is-collapsed');
            try { localStorage.setItem('tl-console-collapsed', collapsed ? '1' : '0'); } catch (e) {}
            setCollapsed(collapsed);
        });
        root.addEventListener('click', function (event) {
            var button = event.target.closest('[data-console-theme-choice]');
            if (!button) return;
            setTheme(button.dataset.consoleThemeChoice, root);
        });
        root.addEventListener('input', function (event) {
            var input = event.target.closest('[data-scope-menu] .tl-console-scope-filter');
            if (!input) return;
            var query = input.value.trim().toLocaleLowerCase();
            var shown = 0;
            input.parentElement.querySelectorAll('a').forEach(function (link) {
                link.hidden = query && link.textContent.trim().toLocaleLowerCase().indexOf(query) === -1;
                if (!link.hidden) shown++;
            });
            var empty = input.parentElement.querySelector('.tl-console-scope-empty');
            if (empty) empty.hidden = shown > 0;
        });
        root.addEventListener('toggle', function (event) {
            if (event.target.open) {
                closeMenus(event.target);
                closeSearch();
            }
        }, true);
        document.addEventListener('click', function (event) {
            var details = event.target.closest && event.target.closest('details');
            if (!details || !root.contains(details)) closeMenus();
        });
        document.addEventListener('keydown', function (event) {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && search) {
                event.preventDefault();
                openSearch();
                return;
            }
            if (event.key === 'Escape') {
                if (root.classList.contains('is-open')) { event.preventDefault(); closeDrawer(); return; }
                if (root.classList.contains('is-search-open')) { event.preventDefault(); closeSearch(); return; }
                if (root.querySelector('details[open]')) { event.preventDefault(); closeMenus(); }
            }
            if (!root.classList.contains('is-open') || event.key !== 'Tab') return;
            var items = focusable(sidebar);
            if (!items.length) return;
            var first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        desktop.addEventListener('change', function () {
            closeDrawer(false);
            closeSearch();
            setCollapsed(stored('tl-console-collapsed') === '1');
            syncSidebarState();
        });
        dark.addEventListener('change', syncTheme);
        requestAnimationFrame(function () {
            root.dataset.consoleReady = 'true';
            document.documentElement.dataset.consoleReady = 'true';
        });
    }

    window.TrellumConsoleShell = {
        init: init, prepare: prepare, setPageTitle: setPageTitle, setTheme: setTheme,
        refreshHealth: pollHealth
    };
    document.querySelectorAll('[data-console-shell]').forEach(init);
}());

/* A select that reveals one of several sections (the SSO page's identity
   source): sections carry data-reveal="<option value>". Without scripting
   every section renders, so the form still works. Lives here rather than
   inline because the management CSP is script-src 'self'. */
(function () {
    var sel = document.querySelector('select[data-reveals]');
    if (!sel) return;
    function show() {
        document.querySelectorAll('[data-reveal]').forEach(function (el) {
            el.hidden = el.getAttribute('data-reveal') !== sel.value;
        });
    }
    sel.addEventListener('change', show);
    show();
}());

(function () {
    var nativeSnapshot = null, nativeTimeout = null;
    function clear(quiet) {
        clearTimeout(nativeTimeout); nativeTimeout = null;
        var restored = nativeSnapshot;
        if (nativeSnapshot) {
            window.TrellumConsoleShell.setPageTitle(nativeSnapshot.title);
            nativeSnapshot.content.forEach(function (entry) {
                entry.node.inert = entry.inert;
                if (entry.hidden === null) entry.node.removeAttribute('hidden');
                else entry.node.setAttribute('hidden', entry.hidden);
            });
            nativeSnapshot.links.forEach(function (entry) { entry.node.classList.toggle('active', entry.active); if (entry.current) entry.node.setAttribute('aria-current', entry.current); else entry.node.removeAttribute('aria-current'); });
            nativeSnapshot = null;
        }
        document.querySelectorAll('[data-console-native-pending]').forEach(function (node) { node.remove(); });
        var main = document.querySelector('main,[data-console-catalog]'); if (main) main.classList.remove('tl-console-native-pending');
        var status = document.querySelector('[data-console-navigation-native-status]'); if (status) status.textContent = '';
        if (restored) {
            window.scrollTo(restored.position[0], restored.position[1]);
            var focus = restored.focus;
            if (!focus || !focus.isConnected || !focus.getClientRects().length) focus = document.querySelector('[data-console-toggle]');
            if (focus) focus.focus({preventScroll:true});
            if (quiet !== true) document.dispatchEvent(new CustomEvent('console:native-restored', {detail: {url: location.href}}));
        }
    }
    function cancel(message) {
        if (!nativeSnapshot) return;
        window.stop(); clear();
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.resume();
        var status = document.querySelector('[data-console-navigation-native-status]');
        if (status && message) status.textContent = message;
    }
    function pageURL(url) {
        if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return false;
        if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;
        // Only console documents. Assets, report hosts, APIs, auth and downloads
        // keep their own lifecycle; a pending document timeout must not stop them.
        var path = url.pathname;
        if (/\.(?:csv|json|zip|pdf|xlsx?|gz|txt|log)\/?$/i.test(path) ||
            /^\/(?:api\/|s\/[^/]+\/[^/]+\/api\/|orgs\/[^/]+\/api\/)/.test(path) ||
            /\/(?:export|download|choices)\/?$/.test(path) ||
            url.searchParams.has('download') || url.searchParams.has('export')) return false;
        return path === '/' || /^\/scopes(?:\/open)?\/?$/.test(path) ||
            /^\/system\/email-connections\/(?:new|[0-9]+)\/?$/.test(path) ||
            /^\/s\/[^/]+\/[^/]+\/r\/[^/]+\/access\/?$/.test(path) || /^\/(?:account(?:\/security\/mfa\/(?:setup|recovery-codes))?|system|instance\/settings)\/?$/.test(path) ||
            /^\/(?:me\/(?:deliveries|api-keys(?:\/new)?)|deliveries(?:\/[0-9]+\/edit)?|operator(?:\/(?:orgs(?:\/[^/]+)?|logs(?:\/runs\/[0-9a-f-]+)?))?)\/?$/.test(path) ||
            /^\/orgs\/(?:new|[^/]+(?:\/settings(?:\/[^/]+)*|\/studios\/new)?)\/?$/.test(path) ||
            /^\/s\/[^/]+\/[^/]+\/(?:analyses|operations|analytics|metrics(?:\/(?:comparison|compare|[0-9]+\/claims))?|experiments|annotations|alerts(?:\/[^/]+)*|deliveries(?:\/[^/]+)*|settings(?:\/[^/]+)*)?\/?$/.test(path);
    }
    function begin(url, label, link) {
        var main = document.querySelector('main,[data-console-catalog]');
        var pageTitle = document.querySelector('[data-console-page-title]');
        if (!main || !pageTitle) return false;
        clear(true);
        nativeSnapshot = { content: Array.from(main.children).map(function (node) {
            return {node:node, inert:node.inert, hidden:node.getAttribute('hidden')};
        }), position: [scrollX,scrollY], focus: document.activeElement, title: pageTitle.textContent, links: Array.from(document.querySelectorAll('.tl-console-nav-link')).map(function (node) { return {node: node, active: node.classList.contains('active'), current: node.getAttribute('aria-current')}; }) };
        history.replaceState(Object.assign({}, history.state || {}, {
            trellumNativePosition: {url:location.href, position:nativeSnapshot.position}
        }), '', location.href);
        var drawerClose = document.querySelector('[data-console-close]');
        if (drawerClose && drawerClose.closest('[data-console-shell]').classList.contains('is-open')) drawerClose.click();
        window.TrellumConsoleShell.setPageTitle(label);
        if (link && link.matches('.tl-console-nav-link')) document.querySelectorAll('.tl-console-nav-link').forEach(function (node) {
            var active = node === link; node.classList.toggle('active', active);
            if (active) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
        });
        var panel = document.createElement('section'); panel.dataset.consoleNativePending = ''; panel.className = 'page-head';
        var heading = document.createElement('h1'); heading.textContent = label; heading.tabIndex = -1;
        var status = document.createElement('p'); status.setAttribute('role', 'status'); status.textContent = 'Opening ' + label + '…';
        var stop = document.createElement('button'); stop.type = 'button'; stop.className = 'ui-btn ghost'; stop.textContent = 'Cancel loading';
        stop.addEventListener('click', function () { cancel(); });
        // Prevent keyboard focus from entering the previous page while loading.
        nativeSnapshot.content.forEach(function (entry) { entry.node.inert = true; entry.node.hidden = true; });
        panel.append(heading, status, stop); main.prepend(panel); main.classList.add('tl-console-native-pending');
        heading.focus({preventScroll:true});
        var sharedStatus = document.querySelector('[data-console-navigation-native-status]');
        if (sharedStatus) sharedStatus.textContent = 'Opening page…';
        window.scrollTo(0,0);
        nativeTimeout = setTimeout(function () { cancel('The page did not finish loading. Try opening it again when ready.'); }, 15000);
        document.dispatchEvent(new CustomEvent('console:native-start', {detail: {url: url.href}}));
        return true;
    }
    window.addEventListener('click', function (event) {
        var link = event.target.closest && event.target.closest('a[href]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey ||
            link.hasAttribute('download') || link.hasAttribute('data-console-no-feedback') || (link.target && link.target !== '_self')) return;
        if (!link.closest('[data-console-shell],main[data-console-page],[data-console-catalog]')) return;
        var url = new URL(link.href);
        if (!pageURL(url)) return;
        // Runs after delegated dirty guards and enhanced/report-host owners.
        if (!event.trellumLeaveConfirmed && window.TrellumSettingsForms && !window.TrellumSettingsForms.confirmLeave(document)) {
            event.preventDefault(); return;
        }
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.nativeLeave();
        var heading = document.querySelector('main h1,[data-console-page-title]');
        var title = link.querySelector('.tl-console-label');
        var label = title ? title.textContent : url.pathname === location.pathname && heading ? heading.textContent : link.textContent;
        begin(url, label.trim() || 'page', link);
    });
    window.addEventListener('submit', function (event) {
        var form = event.target, submitter = event.submitter;
        if (event.defaultPrevented || !form.matches('form') || form.hasAttribute('data-console-no-feedback')) return;
        var method = (submitter && submitter.getAttribute('formmethod')) || form.method;
        var target = (submitter && submitter.getAttribute('formtarget')) || form.target;
        if (method.toLowerCase() !== 'get' || (target && target !== '_self')) return;
        if (!form.closest('[data-console-shell],main[data-console-page],[data-console-catalog]')) return;
        var url = new URL((submitter && submitter.getAttribute('formaction')) || form.action, location.href);
        if (!pageURL(url)) return;
        if (!event.trellumLeaveConfirmed && window.TrellumSettingsForms && !window.TrellumSettingsForms.confirmLeave(document, form)) {
            event.preventDefault(); return;
        }
        event.trellumLeaveConfirmed = true;
        url.search = new URLSearchParams(new FormData(form)).toString();
        if (submitter && submitter.name) url.searchParams.append(submitter.name, submitter.value);
        if (!pageURL(url)) return;
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.nativeLeave();
        var heading = document.querySelector('main h1,[data-console-page-title]');
        begin(url, heading ? heading.textContent.trim() : 'page');
    });
    window.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && nativeSnapshot) { event.preventDefault(); cancel(); }
    });
    window.addEventListener('pageshow', function () {
        clear();
        var saved = history.state && history.state.trellumNativePosition;
        if (saved && saved.url === location.href && Array.isArray(saved.position)) {
            requestAnimationFrame(function () { window.scrollTo(saved.position[0], saved.position[1]); });
        }
    });
}());

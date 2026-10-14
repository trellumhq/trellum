/* Bounded Reports and Analyses catalog. The native page remains the fallback. */
(function () {
    'use strict';
    var ctx = window.PORTAL_CTX;
    var root = document.querySelector('[data-c14-catalog]');
    if (!ctx || !root) return;
    var prefix = ctx.prefix;
    var kind = root.dataset.kind;
    var path = root.dataset.path;
    var active = true;
    var epoch = 0;
    var current = { primary: null, personal: null, indicators: null, categories: null, detail: null };
    var state = new URL(window.location.href);
    var primary = null;
    var personal = null;
    var indicators = {};
    var indicatorRequested = new Set();
    var indicatorPending = [];
    var searchTimer;
    var hidden = false;
    var interrupted = {};
    var renderedHref = state.pathname + state.search;
    var detailTrigger;
    var detailId;
    var detailParams = new URLSearchParams();
    var status = root.querySelector('[data-catalog-status]');
    var detailOverlay = document.getElementById('validationOverlay');
    var detailDrawer = document.getElementById('validationDrawer');
    var detailBody = document.getElementById('validationBody');
    var detailTitle = document.getElementById('validationTitle');
    var errorOverlay = document.getElementById('errorLogOverlay');
    var errorDrawer = document.getElementById('errorLogDrawer');
    var errorBody = document.getElementById('errorLogBody');

    function el(tag, className, value) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (value !== undefined) node.textContent = String(value);
        return node;
    }
    function message(value, error) {
        status.textContent = value || '';
        status.toggleAttribute('data-error', !!error);
    }
    function recent() {
        try {
            var slugs = JSON.parse(localStorage.getItem('trellum_portal_recent') || '[]');
            return Array.isArray(slugs) ? slugs.filter(function (v) { return typeof v === 'string' && v.length <= 200; }).slice(0, 8) : [];
        } catch (_) { return []; }
    }
    function recordRecent(slug) {
        try { localStorage.setItem('trellum_portal_recent', JSON.stringify([slug].concat(recent().filter(function (v) { return v !== slug; })).slice(0, 8))); }
        catch (_) { /* Private browsing may disable local storage. */ }
    }
    function apiParams() {
        var params = new URLSearchParams(state.search);
        params.set('kind', kind);
        params.delete('recent');
        recent().forEach(function (slug) { params.append('recent', slug); });
        return params;
    }
    function read(domain, url, commit, failed) {
        if (!active) return Promise.resolve();
        if (current[domain]) current[domain].abort();
        var controller = new AbortController();
        var generation = epoch;
        current[domain] = controller;
        return fetch(url, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
            .then(function (response) {
                if (!response.ok) throw new Error('Catalog request failed');
                return response.json();
            }).then(function (body) {
                if (!active || generation !== epoch || controller !== current[domain] || controller.signal.aborted) return;
                current[domain] = null;
                if (body.state === 'oversized' || body.state === 'invalid') throw new Error(body.message || 'Catalog result unavailable');
                commit(body);
            }).catch(function (error) {
                if (controller === current[domain]) current[domain] = null;
                if (error.name === 'AbortError' || !active || generation !== epoch) return;
                failed(error);
            });
    }
    function announceLoading() {
        root.dataset.catalogReady = 'false';
        window.dispatchEvent(new Event('trellum:catalog-loading'));
    }
    function announceReady() {
        root.dataset.catalogReady = 'true';
        window.dispatchEvent(new Event('trellum:catalog-ready'));
    }
    /* Hidden catalogs keep their rendered rows; only unfinished work is
       cancelled and remembered so return repeats it once. */
    function suspend() {
        if (hidden) return;
        hidden = true;
        active = false;
        epoch++;
        interrupted = {};
        Object.keys(current).forEach(function (name) {
            if (current[name]) { interrupted[name] = true; current[name].abort(); }
            current[name] = null;
        });
        clearTimeout(searchTimer);
        indicatorPending = [];
        closeDetail();
        closeErrors();
    }
    function resume() {
        if (!hidden) return;
        hidden = false;
        active = true;
        var location = new URL(window.location.href);
        var wanted = location.pathname + location.search;
        var redo = interrupted;
        interrupted = {};
        if (wanted !== renderedHref || location.pathname !== path) {
            state = location;
            refreshPrimary();
            refreshPersonal();
            return;
        }
        if (redo.primary) refreshPrimary();
        if (redo.personal) refreshPersonal();
        refreshIndicators();
    }
    function invalidate() {
        epoch++;
        Object.keys(current).forEach(function (name) {
            if (current[name]) current[name].abort();
            current[name] = null;
        });
        clearTimeout(searchTimer);
        indicatorRequested.clear();
        indicatorPending = [];
        indicators = {};
    }
    function baseReportUrl(row) {
        var base = prefix + '/r/' + encodeURIComponent(row.slug) + '/';
        if (row.has_output === true && row.html_entry) return base + encodeURIComponent(row.html_entry);
        return base;
    }
    function makeAction(tag, label, attr, value) {
        var node = el(tag, '', label);
        node.setAttribute(attr, value);
        if (tag === 'button') node.type = 'button';
        return node;
    }
    function makeItem(row) {
        var card = el('article', 'c14-item');
        card.dataset.catalogId = row.id;
        card.dataset.catalogSlug = row.slug;
        var main = el('div', 'c14-item-main');
        var heading = el('div', 'c14-item-heading');
        var link = el('a', 'c14-report-link', row.name || row.slug);
        link.href = baseReportUrl(row);
        link.dataset.consoleReportTitle = row.name || row.slug;
        heading.appendChild(link);
        var output = el('span', 'c14-state', row.output_state === 'known' ? (row.has_output ? 'Ready' : 'No output') : 'Output unknown · open normally');
        output.dataset.catalogOutput = '';
        heading.appendChild(output);
        main.appendChild(heading);
        if (row.description) main.appendChild(el('p', 'c14-description', row.description));
        var tags = el('div', 'c14-tags');
        (row.tags || []).forEach(function (tag) {
            var a = makeAction('a', tag, 'data-catalog-tag', tag);
            a.href = path + '?q=' + encodeURIComponent(tag);
            tags.appendChild(a);
        });
        main.appendChild(tags);
        var meta = el('div', 'c14-meta');
        [row.category, row.studio, row.schedule || 'Schedule unknown'].forEach(function (value) { meta.appendChild(el('span', '', value)); });
        main.appendChild(meta);
        card.appendChild(main);
        var actions = el('div', 'c14-item-actions');
        var indicator = el('span', '', 'Indicators pending');
        indicator.dataset.catalogIndicator = row.id;
        indicator.setAttribute('role', 'status');
        actions.appendChild(indicator);
        var fav = makeAction('button', row.is_favorite ? '★' : '☆', 'data-catalog-favorite', row.id);
        fav.setAttribute('aria-pressed', row.is_favorite ? 'true' : 'false');
        fav.setAttribute('aria-label', (row.is_favorite ? 'Remove ' : 'Add ') + (row.name || row.slug) + (row.is_favorite ? ' from favorites' : ' to favorites'));
        actions.appendChild(fav);
        var more = makeAction('a', 'More details', 'data-catalog-detail', row.id);
        more.href = prefix + '/catalog/details/' + row.id;
        actions.appendChild(more);
        var delivery = makeAction('button', 'Delivery', 'data-catalog-delivery', row.slug);
        delivery.dataset.catalogName = row.name || row.slug;
        actions.appendChild(delivery);
        if (ctx.user.role === 'developer' || ctx.user.role === 'admin' || ctx.user.is_org_admin) actions.appendChild(makeAction('button', 'Run', 'data-catalog-run', row.slug));
        card.appendChild(actions);
        applyIndicator(card, row.id);
        return card;
    }
    function rows(container, values) {
        if (!container) return;
        container.replaceChildren();
        if (!values || !values.length) { container.appendChild(el('p', 'c14-empty', 'No matching items.')); return; }
        values.forEach(function (row) { container.appendChild(makeItem(row)); });
    }
    function applyIndicator(card, id) {
        var target = card.querySelector('[data-catalog-indicator]');
        if (!target) return;
        var info = indicators[id];
        if (!info) { target.textContent = 'Indicators pending'; return; }
        if (info.error) {
            target.textContent = 'Indicators unavailable · ';
            var retry = makeAction('button', 'Retry', 'data-catalog-indicator-retry', id);
            target.appendChild(retry);
            return;
        }
        var parts = [];
        parts.push(info.source_status === 'ready' ? (info.waiting ? 'Waiting for source' : 'Source ready') : 'Source ' + (info.source_status || 'unknown'));
        parts.push(info.subscriptions && info.subscriptions.state === 'ready' ? info.subscriptions.count + ' deliveries' : 'Deliveries unknown');
        parts.push(info.views_30d === null || info.views_30d === undefined ? 'Views unknown' : info.views_30d + ' views');
        target.textContent = parts.join(' · ');
    }
    function paintIndicators() {
        root.querySelectorAll('[data-catalog-id]').forEach(function (card) { applyIndicator(card, card.dataset.catalogId); });
    }
    function pageUrl(page) {
        var next = new URL(state.href);
        next.searchParams.set('page', page);
        return next.pathname + next.search;
    }
    function paintPrimary(data) {
        primary = data;
        renderedHref = state.pathname + state.search;
        var list = root.querySelector('[data-catalog-rows]');
        list.classList.toggle('is-list', state.searchParams.get('view') === 'list');
        rows(list, data.rows);
        var count = root.querySelector('[data-catalog-count]');
        if (count) count.textContent = data.pagination.total + ' matching · ' + data.coverage.total + ' authorized';
        var pages = root.querySelector('.c14-pages');
        pages.replaceChildren();
        if (data.pagination.has_previous) { var prev = el('a', '', 'Previous'); prev.href = pageUrl(data.pagination.page - 1); pages.appendChild(prev); }
        pages.appendChild(el('span', '', 'Page ' + data.pagination.page));
        if (data.pagination.has_next) { var next = el('a', '', 'Next'); next.href = pageUrl(data.pagination.page + 1); pages.appendChild(next); }
        paintCategories(data.categories);
        message('');
        announceReady();
        refreshIndicators();
    }
    function paintCategories(data) {
        if (!data || data.state === 'oversized') return;
        var box = root.querySelector('[data-catalog-categories]');
        if (!box) return;
        var details = box.querySelector('details');
        if (!details) return;
        var oldOpen = details.open;
        details.replaceChildren();
        details.appendChild(el('summary', '', 'Categories'));
        var form = el('form'); form.action = path; form.method = 'get';
        var input = el('input'); input.name = 'category_q'; input.type = 'search'; input.placeholder = 'Find a category'; input.value = state.searchParams.get('category_q') || ''; form.appendChild(input);
        var button = el('button', '', 'Find'); button.type = 'submit'; form.appendChild(button); details.appendChild(form);
        (data.categories || []).forEach(function (category) {
            var a = el('a', '', category.display_category + ' (' + category.count + ')');
            var target = new URL(state.href); target.searchParams.set('tab', category.display_category); target.searchParams.set('page', '1'); a.href = target.pathname + target.search;
            details.appendChild(a);
        });
        if (data.selected) {
            var pinned = el('a', '', data.selected.display_category + ' (' + data.selected.count + ')');
            var target = new URL(state.href); target.searchParams.set('tab', data.selected.display_category); target.searchParams.set('page', '1'); pinned.href = target.pathname + target.search; details.appendChild(pinned);
        }
        if (data.pagination.has_previous || data.pagination.has_next) {
            var nav = el('div');
            [-1, 1].forEach(function (offset) {
                if (offset < 0 && !data.pagination.has_previous || offset > 0 && !data.pagination.has_next) return;
                var a = el('a', '', offset < 0 ? 'Previous categories' : 'Next categories');
                var target = new URL(state.href); target.searchParams.set('category_page', data.pagination.page + offset); a.href = target.pathname + target.search; nav.appendChild(a);
            }); details.appendChild(nav);
        }
        details.open = oldOpen;
    }
    function paintPersonal(data) {
        personal = data;
        var box = root.querySelector('[data-catalog-personal]');
        if (!box) return;
        box.dataset.state = 'ready';
        rows(box.querySelector('[data-catalog-favorites]'), data.favorites);
        rows(box.querySelector('[data-catalog-recent]'), (data.recent || []).slice(0, 4));
        var count = box.querySelector('h2 small');
        if (count) count.textContent = '(' + data.pagination.total + ')';
        var tab = root.querySelector('[data-catalog-categories] a[href*="_favorites"]');
        if (tab) tab.textContent = 'Favorites (' + data.pagination.total + ')';
        refreshIndicators();
    }
    function showErrors() {
        errorBody.replaceChildren();
        var seen = new Set();
        var count = 0;
        root.querySelectorAll('[data-catalog-id]').forEach(function (card) {
            var id = card.dataset.catalogId;
            if (seen.has(id)) return;
            seen.add(id);
            var info = indicators[id];
            if (!info || !info.last_error) return;
            count++;
            var item = el('p', '', card.querySelector('.c14-report-link').textContent + ': ' + info.last_error);
            errorBody.appendChild(item);
        });
        if (!count) errorBody.appendChild(el('p', '', 'No known errors on this page. Unavailable indicators remain unknown.'));
        errorOverlay.classList.add('open'); errorDrawer.classList.add('open');
        errorDrawer.setAttribute('aria-hidden', 'false'); errorDrawer.removeAttribute('inert');
        errorDrawer.querySelector('.error-log-close').focus();
    }
    function closeErrors() {
        errorOverlay.classList.remove('open'); errorDrawer.classList.remove('open');
        errorDrawer.setAttribute('aria-hidden', 'true'); errorDrawer.setAttribute('inert', '');
        var trigger = root.querySelector('[data-catalog-errors]'); if (trigger) trigger.focus();
    }
    function refreshPrimary() {
        message('Loading catalog…');
        announceLoading();
        return read('primary', prefix + '/api/catalog?' + apiParams(), paintPrimary, function () {
            message('Catalog update failed. The current page remains available. Retry.', true);
            announceReady();
        });
    }
    function refreshPersonal() {
        var box = root.querySelector('[data-catalog-personal]');
        if (box) box.dataset.state = 'loading';
        return read('personal', prefix + '/api/catalog/personal?' + apiParams(), paintPersonal, function () {
            if (box) box.dataset.state = 'error';
            message('Favorites and recent reports are unavailable. Retry.', true);
        });
    }
    function refreshIndicators() {
        var ids = [];
        root.querySelectorAll('[data-catalog-id]').forEach(function (card) {
            var id = Number(card.dataset.catalogId);
            if (Number.isInteger(id) && !ids.includes(id) && ids.length < 64) ids.push(id);
        });
        var missing = ids.filter(function (id) { return !indicatorRequested.has(id) && !indicatorPending.includes(id); });
        if (!missing.length) return;
        ids = indicatorPending.concat(missing).filter(function (id, index, all) { return all.indexOf(id) === index; }).slice(0, 64);
        indicatorPending = ids;
        var params = new URLSearchParams();
        ids.forEach(function (id) { params.append('id', id); });
        return read('indicators', prefix + '/api/catalog/indicators?' + params, function (data) {
            Object.assign(indicators, data.reports || {});
            ids.forEach(function (id) { indicatorRequested.add(id); });
            indicatorPending = [];
            paintIndicators();
        }, function () {
            ids.forEach(function (id) { indicators[id] = { error: true }; });
            ids.forEach(function (id) { indicatorRequested.add(id); });
            indicatorPending = [];
            paintIndicators();
        });
    }
    function navigate(url, options) {
        var next = new URL(url, window.location.href);
        if (next.origin !== window.location.origin || next.pathname !== path) { window.location.href = next.href; return; }
        invalidate();
        active = true;
        state = next;
        var historyState = Object.assign({}, history.state || {});
        history.pushState(historyState, '', next.pathname + next.search);
        if (!(options && options.keepScroll)) window.scrollTo(0, 0);
        refreshPrimary();
        refreshPersonal();
    }
    function csrf() {
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : '';
    }
    function toggleFavorite(button) {
        var id = button.dataset.catalogFavorite;
        var next = button.getAttribute('aria-pressed') !== 'true';
        root.querySelectorAll('[data-catalog-favorite="' + id + '"]').forEach(function (node) {
            node.setAttribute('aria-pressed', next ? 'true' : 'false'); node.textContent = next ? '★' : '☆'; node.disabled = true;
        });
        fetch('/api/me/favorites/' + id, { method: next ? 'PUT' : 'DELETE', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
            .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
            .then(function () { if (active) { refreshPersonal(); if (state.searchParams.get('tab') === '_favorites') refreshPrimary(); } })
            .catch(function () {
                root.querySelectorAll('[data-catalog-favorite="' + id + '"]').forEach(function (node) { node.setAttribute('aria-pressed', next ? 'false' : 'true'); node.textContent = next ? '☆' : '★'; });
                message('Favorite could not be saved. Retry if needed.', true);
            }).finally(function () { root.querySelectorAll('[data-catalog-favorite="' + id + '"]').forEach(function (node) { node.disabled = false; }); });
    }
    function runReport(button) {
        button.disabled = true;
        message('Run requested…');
        fetch(prefix + '/api/reports/' + encodeURIComponent(button.dataset.catalogRun) + '/run', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/json' }, body: '{}' })
            .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
            .then(function (data) { message(data.status === 'already_running' ? 'Report is already running.' : 'Run accepted. Refresh to see the latest state.'); })
            .catch(function () { message('Run outcome unconfirmed. Check status before retrying.', true); })
            .finally(function () { button.disabled = false; });
    }
    function renderDetail(data) {
        detailBody.replaceChildren();
        if (data.state && data.state !== 'ready') detailBody.appendChild(el('p', 'c14-message', 'Details are ' + data.state + '. The normal report link remains available.'));
        var declaration = data.declaration || {};
        detailBody.appendChild(el('h3', '', 'Description'));
        detailBody.appendChild(el('p', '', declaration.description_text || 'No description'));
        if (declaration.description_pagination) {
            if (declaration.description_pagination.has_previous) { var dPrev = makeAction('button', 'Previous description', 'data-catalog-detail-page', 'description_page'); dPrev.dataset.page = declaration.description_pagination.page - 1; detailBody.appendChild(dPrev); }
            if (declaration.description_pagination.has_next) { var dNext = makeAction('button', 'Next description', 'data-catalog-detail-page', 'description_page'); dNext.dataset.page = declaration.description_pagination.page + 1; detailBody.appendChild(dNext); }
        }
        detailBody.appendChild(el('h3', '', 'Tags (' + (declaration.tag_count || 0) + ')'));
        (declaration.tag_rows || []).forEach(function (tag) {
            var item = el('div', '', tag.text);
            if (tag.length > 80) { var more = makeAction('button', 'Read full tag', 'data-catalog-tag-index', tag.index); item.appendChild(more); }
            detailBody.appendChild(item);
        });
        if (declaration.tag_text) {
            detailBody.appendChild(el('p', '', declaration.tag_text.text));
            if (declaration.tag_text.page > 1) { var textPrev = makeAction('button', 'Previous tag text', 'data-catalog-detail-page', 'text_page'); textPrev.dataset.page = declaration.tag_text.page - 1; detailBody.appendChild(textPrev); }
            if (declaration.tag_text.has_next) { var textNext = makeAction('button', 'Next tag text', 'data-catalog-detail-page', 'text_page'); textNext.dataset.page = declaration.tag_text.page + 1; detailBody.appendChild(textNext); }
        }
        if (declaration.tag_pagination) {
            if (declaration.tag_pagination.has_previous) { var tPrev = makeAction('button', 'Previous tags', 'data-catalog-detail-page', 'tag_page'); tPrev.dataset.page = declaration.tag_pagination.page - 1; detailBody.appendChild(tPrev); }
            if (declaration.tag_pagination.has_next) { var tNext = makeAction('button', 'Next tags', 'data-catalog-detail-page', 'tag_page'); tNext.dataset.page = declaration.tag_pagination.page + 1; detailBody.appendChild(tNext); }
        }
        if (data.entries && data.entries.length) {
            detailBody.appendChild(el('h3', '', (data.section || 'checks') + ' (' + data.pagination.total + ')'));
            data.entries.forEach(function (entry) { detailBody.appendChild(el('pre', '', JSON.stringify(entry, null, 2))); });
            if (data.pagination.has_previous) { var ePrev = makeAction('button', 'Previous entries', 'data-catalog-detail-page', 'page'); ePrev.dataset.page = data.pagination.page - 1; detailBody.appendChild(ePrev); }
            if (data.pagination.has_next) { var eNext = makeAction('button', 'Next entries', 'data-catalog-detail-page', 'page'); eNext.dataset.page = data.pagination.page + 1; detailBody.appendChild(eNext); }
        }
        var native = el('a', '', 'Open full details page');
        native.href = prefix + '/catalog/details/' + detailId + '?' + detailParams;
        detailBody.appendChild(native);
    }
    function fetchDetail() {
        detailBody.replaceChildren(el('p', '', 'Loading details…'));
        return read('detail', prefix + '/api/catalog/details/' + detailId + '?' + detailParams, renderDetail, function () { detailBody.replaceChildren(el('p', '', 'Details unavailable. Open the full details page or retry.')); });
    }
    function openDetail(link) {
        detailTrigger = link;
        detailId = link.dataset.catalogDetail;
        detailParams = new URLSearchParams();
        detailTitle.textContent = 'Report details';
        detailOverlay.classList.add('open');
        detailDrawer.classList.add('open');
        detailDrawer.setAttribute('aria-hidden', 'false');
        detailDrawer.removeAttribute('inert');
        detailDrawer.querySelector('button').focus();
        fetchDetail();
    }
    function closeDetail() {
        if (!detailDrawer.classList.contains('open')) return;
        if (current.detail) current.detail.abort();
        detailOverlay.classList.remove('open');
        detailDrawer.classList.remove('open');
        detailDrawer.setAttribute('aria-hidden', 'true');
        detailDrawer.setAttribute('inert', '');
        if (detailTrigger && detailTrigger.isConnected) detailTrigger.focus();
    }
    function start() {
        if (!active) return;
        announceReady();
        /* A history restore may reuse a cached copy of the server-rendered page
           (no bfcache pageshow); ask the bounded endpoint for the current page. */
        var navigation = window.performance && performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
        if (navigation && navigation.type === 'back_forward') refreshPrimary();
        refreshPersonal();
        refreshIndicators();
    }
    root.addEventListener('click', function (event) {
        var target = event.target.closest('a,button');
        if (!target) return;
        if (target.matches('[data-catalog-detail]')) { event.preventDefault(); openDetail(target); return; }
        if (target.matches('[data-catalog-favorite]')) { toggleFavorite(target); return; }
        if (target.matches('[data-catalog-delivery]')) { if (window.ReportDelivery) window.ReportDelivery.open(prefix, target.dataset.catalogDelivery, target.dataset.catalogName, target); return; }
        if (target.matches('[data-catalog-run]')) { runReport(target); return; }
        if (target.matches('[data-catalog-indicator-retry]')) {
            var retryId = Number(target.dataset.catalogIndicatorRetry);
            indicatorRequested.delete(retryId);
            delete indicators[retryId];
            refreshIndicators();
            return;
        }
        if (target.matches('[data-catalog-errors]')) { showErrors(); return; }
        if (target.matches('[data-catalog-tag]')) { event.preventDefault(); navigate(target.href); return; }
        if (target.matches('.c14-catalog a[href]')) {
            if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || target.target && target.target !== '_self') return;
            var destination = new URL(target.href, window.location.href);
            if (destination.pathname === path) { event.preventDefault(); navigate(target.href); return; }
            if (target.classList.contains('c14-report-link')) recordRecent(target.closest('[data-catalog-slug]').dataset.catalogSlug);
        }
    });
    root.addEventListener('submit', function (event) {
        var form = event.target;
        if (form.method.toLowerCase() !== 'get') return;
        event.preventDefault();
        var destination = new URL(form.action || path, window.location.href);
        new FormData(form).forEach(function (value, key) { destination.searchParams.set(key, value); });
        destination.searchParams.set('page', '1');
        if (form.classList.contains('c14-search')) destination.searchParams.delete('tab');
        navigate(destination.href);
    });
    var search = root.querySelector('#catalogSearch');
    if (search) search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
            var destination = new URL(state.href); destination.searchParams.set('q', search.value); destination.searchParams.delete('tab'); destination.searchParams.set('page', '1'); navigate(destination.href, { keepScroll: true });
        }, 150);
    });
    detailOverlay.addEventListener('click', closeDetail);
    errorOverlay.addEventListener('click', closeErrors);
    errorDrawer.querySelector('.error-log-close').addEventListener('click', closeErrors);
    errorDrawer.querySelector('.error-log-btn').addEventListener('click', function () { errorBody.replaceChildren(el('p', '', 'The visible log was cleared.')); });
    detailDrawer.querySelector('.validation-close').addEventListener('click', closeDetail);
    detailDrawer.addEventListener('click', function (event) {
        var page = event.target.closest('[data-catalog-detail-page]');
        var tag = event.target.closest('[data-catalog-tag-index]');
        if (page) { detailParams.set(page.dataset.catalogDetailPage, page.dataset.page); fetchDetail(); }
        if (tag) { detailParams.set('tag_index', tag.dataset.catalogTagIndex); fetchDetail(); }
    });
    detailDrawer.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { closeDetail(); return; }
        if (event.key !== 'Tab') return;
        var focusable = Array.from(detailDrawer.querySelectorAll('a[href],button:not([disabled])'));
        if (!focusable.length) return;
        if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
    });
    errorDrawer.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { closeErrors(); return; }
        if (event.key !== 'Tab') return;
        var focusable = Array.from(errorDrawer.querySelectorAll('button:not([disabled]),a[href]'));
        if (!focusable.length) return;
        if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
    });
    document.addEventListener('console:native-start', function () { active = false; invalidate(); closeDetail(); closeErrors(); });
    document.addEventListener('console:native-restored', function () { active = true; state = new URL(window.location.href); refreshPrimary(); refreshPersonal(); });
    document.addEventListener('trellum:catalog-hide', suspend);
    document.addEventListener('trellum:catalog-return', resume);
    window.addEventListener('pagehide', function () { active = false; invalidate(); });
    window.addEventListener('pageshow', function (event) { if (event.persisted) { active = true; state = new URL(window.location.href); refreshPrimary(); refreshPersonal(); } });
    window.addEventListener('popstate', function () {
        if (!active) return;
        var next = new URL(window.location.href);
        /* Report-host history entries and states the host already resumed are not catalog navigations. */
        if (next.pathname !== path || next.pathname + next.search === state.pathname + state.search) return;
        state = next; invalidate(); refreshPrimary(); refreshPersonal();
    });
    window.addEventListener('rdw:subscription-change', function () {
        if (!active) return;
        if (current.indicators) current.indicators.abort();
        current.indicators = null;
        indicatorPending = [];
        indicatorRequested.clear();
        refreshIndicators();
    });
    window.TrellumCatalog = { boot: start };
    start();
})();

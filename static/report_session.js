/* Portal-owned session handling for authenticated report pages.
 *
 * The portal splices this script into the <head> of every signed-in report
 * response (apps.reports.views._inject_report_chrome), ahead of the report's
 * own scripts, so builds from before it existed need no rebuild. Public share
 * and embed pages never receive it.
 *
 * A report that stays open past its session keeps polling data.json and its
 * live queries. The portal answers those with 401 (API paths) or a redirect to
 * /login (everything else); the framework runtime treats either as a failed
 * refresh and logs a warning. This wrapper recognises a portal answer saying
 * "the session ended" and sends the whole window to the login page once, with
 * the report's display URL as the return page. A 403, a 5xx or a network error
 * is an ordinary failure and passes through untouched.
 *
 * A report shown inside the console shell lives in an iframe, so it navigates
 * the shell instead -- but only when that shell vouches for this exact frame
 * (TrellumConsoleReportHost.contextFor), the same check static/report_menu.js
 * uses for its own host calls.
 *
 * window.__trellumReportAccess is written by the portal for this response and
 * carries the viewer's resolved capabilities; see report_share.js. */
(function () {
    'use strict';

    if (window.TrellumReportSession) return;
    var access = window.__trellumReportAccess || {};
    var loginPath = typeof access.login === 'string' && access.login.charAt(0) === '/' ? access.login : '/login';
    var nativeFetch = window.fetch;
    var redirecting = false;

    function isLoginPath(pathname) {
        return pathname === loginPath || pathname === loginPath + '/';
    }

    function requestUrl(input) {
        try {
            var raw = typeof input === 'string' ? input : (input && (input.url || input.href));
            return new URL(raw, document.baseURI);
        } catch (error) { return null; }
    }

    // The shell window when this frame is the console host's live report frame,
    // null when it is not framed, and false when a host exists but no longer
    // vouches for this frame (a replaced report must not move the shell).
    function consoleHost() {
        try {
            if (window.parent === window) return null;
            var host = window.parent.TrellumConsoleReportHost;
            if (!host || typeof host.contextFor !== 'function') return null;
            return host.contextFor(window) ? window.parent : false;
        } catch (error) { return null; }
    }

    function returnPage(target) {
        var page = '/';
        try {
            var url = new URL(target.location.href);
            if (url.origin === window.location.origin) {
                url.searchParams.delete('_console_host');
                url.searchParams.delete('_console_host_token');
                page = url.pathname + url.search + url.hash;
            }
        } catch (error) {}
        return page.charAt(0) === '/' && page.charAt(1) !== '/' && !isLoginPath(page.split(/[?#]/)[0]) ? page : '/';
    }

    // A session-policy redirect names why the session ended ("idle", ...);
    // keep that so the login page can say so.
    function reasonOf(response) {
        try {
            var reason = new URL(response.url).searchParams.get('reason');
            return reason && /^[a-z_]{1,32}$/.test(reason) ? reason : '';
        } catch (error) { return ''; }
    }

    function expired(response) {
        if (redirecting) return;
        var host = consoleHost();
        if (host === false) return;
        redirecting = true;
        var target = host || window;
        var query = 'next=' + encodeURIComponent(returnPage(target));
        var reason = response ? reasonOf(response) : '';
        if (reason) query = 'reason=' + reason + '&' + query;
        target.location.assign(loginPath + '?' + query);
    }

    function sessionEnded(url, response) {
        if (!url || url.origin !== window.location.origin || isLoginPath(url.pathname)) return false;
        if (response.status === 401) return true;
        if (!response.redirected) return false;
        try {
            var landed = new URL(response.url);
            return landed.origin === window.location.origin && isLoginPath(landed.pathname);
        } catch (error) { return false; }
    }

    // The caller must neither consume the dead response nor retry it while the
    // page is leaving, so its promise stays pending -- except that an abort
    // still rejects it, as it would a real request.
    function parked(init, input) {
        var signal = (init && init.signal) || (input && input.signal);
        return new Promise(function (resolve, reject) {
            if (!signal) return;
            var abort = function () { reject(new DOMException('The operation was aborted.', 'AbortError')); };
            if (signal.aborted) abort();
            else signal.addEventListener('abort', abort, { once: true });
        });
    }

    if (typeof nativeFetch === 'function') {
        window.fetch = function (input, init) {
            var url = requestUrl(input);
            var local = !!url && url.origin === window.location.origin;
            if (redirecting && local) return parked(init, input);
            return nativeFetch.apply(window, arguments).then(function (response) {
                if (!sessionEnded(url, response)) return response;
                expired(response);
                return parked(init, input);
            });
        };
    }

    // Coming back from the login page through the back/forward cache restores
    // this frozen page; let it try again.
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) redirecting = false;
    });

    window.TrellumReportSession = { expired: function () { expired(null); } };
})();

/* Report-page theme picker POST-back (theming redesign).
 *
 * The report's own picker (trellum/components/header.py, <select
 * id="fwThemeSelect">, wired by trellum's own runtime JS) writes data-theme
 * + localStorage['fw-theme'] only -- the framework knows nothing about
 * studios, orgs, or the portal's URL scheme, so it has no way to reach the
 * portal's per-studio setter. This file, served at the frozen URL
 * /api/reports/theme-widget.js (apps.reports.views.theme_widget_js) and
 * injected into every report (apps.runner.executor.portal_extensions, plus
 * a serve-time retrofit in apps.reports.views._inject_report_chrome for
 * builds from before this landed), attaches an ADDITIONAL change listener
 * on the same <select> that POSTs the pick to apps.studios.views.theme_set
 * -- it never removes or replaces the framework's own handler, only adds
 * to it, so the framework's own localStorage/data-theme behavior (and
 * whatever `another agent` does with header.py) keeps working untouched.
 *
 * Only wires up on a session-authenticated report page
 * (/s/<org>/<studio>/r/<slug>/...): public share links (/share/<token>/...)
 * have no viewer identity to persist a per-studio override for, so the
 * pattern below simply does not match there and this becomes a no-op.
 */
(function () {
    'use strict';

    var m = window.location.pathname.match(/^\/s\/([a-z0-9-]+)\/([a-z0-9-]+)\/r\//);
    if (!m) return;
    var themeUrl = '/s/' + m[1] + '/' + m[2] + '/theme';

    function getCookie(name) {
        var mm = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return mm ? decodeURIComponent(mm[1]) : '';
    }

    function post(theme) {
        var body = 'theme=' + encodeURIComponent(theme) +
            '&next=' + encodeURIComponent(window.location.pathname + window.location.search);
        // Fire-and-forget: this tab already shows the theme the picker just
        // applied client-side (the framework's own handler did that); the
        // POST only persists the choice server-side for next time / other
        // surfaces (studio chrome, other tabs) to pick up. `redirect:
        // 'manual'` skips following the setter's 302 back to `next` -- there
        // is nothing useful to do with that response body here.
        fetch(themeUrl, {
            method: 'POST',
            credentials: 'same-origin',
            redirect: 'manual',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: body
        }).catch(function () {});
    }

    function wire() {
        var select = document.getElementById('fwThemeSelect');
        if (!select) return;
        select.addEventListener('change', function () {
            post(select.value);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }
})();

    // ── Suppress password-manager autofill scans ─────────
    // Bitwarden / 1Password / LastPass walk every <input> on the page to
    // decide whether to offer autofill. On a dense report (FilterBar,
    // DataTable search, Slim Select search fields) the scan can burn
    // hundreds of ms on every DOM mutation. We don't want autofill on any
    // framework-generated input — none of them are credential fields —
    // so tag them all with the attributes each manager respects. A
    // MutationObserver catches inputs that Slim Select and other UI libs
    // inject after initial render.
    (function _fwSuppressAutofill() {
        function _tag(inp) {
            if (!inp || inp._fwAfTagged) return;
            inp._fwAfTagged = true;
            inp.setAttribute('autocomplete', 'off');
            inp.setAttribute('data-bwignore', 'true');     // Bitwarden
            inp.setAttribute('data-1p-ignore', 'true');    // 1Password
            inp.setAttribute('data-lpignore', 'true');     // LastPass
        }
        function _tagAll(root) {
            (root || document).querySelectorAll('input,textarea').forEach(_tag);
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() { _tagAll(); });
        } else {
            _tagAll();
        }
        var mo = new MutationObserver(function(mutations) {
            for (var i = 0; i < mutations.length; i++) {
                var added = mutations[i].addedNodes;
                for (var j = 0; j < added.length; j++) {
                    var n = added[j];
                    if (!n || n.nodeType !== 1) continue;
                    if (n.tagName === 'INPUT' || n.tagName === 'TEXTAREA') _tag(n);
                    else if (n.querySelectorAll) _tagAll(n);
                }
            }
        });
        mo.observe(document.body || document.documentElement,
            { childList: true, subtree: true });
    })();

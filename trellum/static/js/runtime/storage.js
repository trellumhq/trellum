    // ── Safe storage ──────────────────────────────────────
    // localStorage access THROWS in sandboxed iframes and other locked-down
    // contexts; an unguarded call would kill this whole script block.
    function _lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

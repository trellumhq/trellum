/* <trellum-report src="https://portal.example.com/share/<token>/"> — the host
 * page's half of the embed protocol as a custom element, so a site can frame a
 * report without writing the iframe, the resize listener or the origin checks
 * itself (internal planning ticket #12). Served byte-for-byte at /api/reports/embed-element.js.
 *
 * Attributes: src (required), theme, min-height (default 480), title.
 * Events on the element: trellum:ready, trellum:height {detail:{height}}.
 * Method: reload().
 *
 * Classic script, no module, no dependencies, no shadow DOM: the frame is the
 * isolation boundary already, and light DOM lets the host style the wrapper.
 */
(function () {
    'use strict';
    if (!window.customElements || !window.CustomEvent) return;

    var MIN_HEIGHT = 480;

    class TrellumReport extends HTMLElement {
        static get observedAttributes() { return ['src', 'theme', 'min-height', 'title']; }

        connectedCallback() {
            if (!this._frame) this._build();
            window.addEventListener('message', this._onMessage);
            this.appendChild(this._frame);
        }

        disconnectedCallback() {
            if (this._onMessage) window.removeEventListener('message', this._onMessage);
        }

        attributeChangedCallback(name) {
            if (!this._frame) return;
            if (name === 'theme') this._pushTheme();
            else this._apply();
        }

        /** Post trellum:reload, so the frame refetches now instead of waiting
         *  for its once-a-minute build watch. No-op before the frame exists. */
        reload() { this._post({ type: 'trellum:reload' }); }

        _build() {
            var self = this;
            this.style.display = 'block';
            var frame = this._frame = document.createElement('iframe');
            frame.setAttribute('loading', 'lazy');
            frame.style.cssText = 'width:100%;border:0;display:block';
            this._apply();
            this._onMessage = function (e) {
                if (e.source !== frame.contentWindow || e.origin !== self._origin()) return;
                var d = e.data || {};
                if (d.type === 'trellum:height') {
                    var h = Number(d.height);
                    if (!h) return;
                    frame.style.height = h + 'px';
                    self.dispatchEvent(new CustomEvent('trellum:height', { detail: { height: h } }));
                } else if (d.type === 'trellum:ready') {
                    self._ready = true;
                    self._pushTheme();
                    self.dispatchEvent(new CustomEvent('trellum:ready'));
                }
            };
        }

        _apply() {
            var frame = this._frame, src = this.getAttribute('src');
            frame.title = this.getAttribute('title') || 'Embedded report';
            frame.style.minHeight = (Number(this.getAttribute('min-height')) || MIN_HEIGHT) + 'px';
            if (src && frame.getAttribute('src') !== src) {
                this._ready = false;
                frame.setAttribute('src', src);
            }
        }

        /** The frame's own origin, derived from src — both the only address we
         *  post to and the only origin we accept messages from. '' when src is
         *  missing or unparseable, which no e.origin ever equals. */
        _origin() {
            try { return new URL(this.getAttribute('src'), location.href).origin; }
            catch (err) { return ''; }
        }

        _pushTheme() {
            var theme = this.getAttribute('theme');
            if (this._ready && theme) this._post({ type: 'trellum:theme', theme: theme });
        }

        _post(msg) {
            var origin = this._origin();
            if (origin && this._frame && this._frame.contentWindow) {
                this._frame.contentWindow.postMessage(msg, origin);
            }
        }
    }

    if (!customElements.get('trellum-report')) customElements.define('trellum-report', TrellumReport);
})();

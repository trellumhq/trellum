/* Document reading is the default; slide view is an explicit manifest choice. */
(function () {
    function init() {
        var deck = document.querySelector('article.fw-presentation');
        if (!deck || deck.querySelector('.fw-presentation-controls')) return;
        var slides = Array.from(deck.querySelectorAll('section.fw-slide'));
        if (!slides.length) return;
        var slideView = deck.dataset.presentationView === 'slides';
        var paged = slideView;
        if (slideView) deck.tabIndex = 0;
        var index = 0, presenting = false, opener = null, nativeEntered = false;
        var links = Array.from(deck.querySelectorAll('.fw-presentation-toc a'));
        var controls = document.createElement('nav');
        controls.className = 'fw-presentation-controls';
        controls.setAttribute('aria-label', 'Presentation controls');
        function button(label, action) {
            var el = document.createElement('button');
            el.type = 'button'; el.textContent = label;
            el.addEventListener('click', action); controls.appendChild(el);
            return el;
        }
        var previous = button('Previous', function () { show(index - 1, true, true); });
        var position = document.createElement('span');
        position.className = 'fw-presentation-position';
        position.setAttribute('role', 'status'); position.setAttribute('aria-live', 'polite');
        controls.appendChild(position);
        var next = button('Next', function () { show(index + 1, true, true); });
        var present = button('Present', enter);
        var exit = button('Exit presentation', leave);
        exit.hidden = true;
        var reading = slideView ? button('Read all slides', function () {
            paged = !paged;
            var linked = linkedIndex();
            show(linked >= 0 ? linked : index, false, false);
        }) : null;
        var notice = document.createElement('span');
        notice.className = 'fw-presentation-notice'; notice.setAttribute('role', 'status');
        controls.appendChild(notice);
        deck.insertBefore(controls, slideView ? deck.querySelector('.fw-presentation-toc') : deck.firstChild);
        slides.forEach(function (slide) { slide.querySelector('h2').tabIndex = -1; });

        function hostState(value) {
            try {
                var host = window.parent !== window && window.parent.TrellumConsoleReportHost;
                var context = host && host.contextFor(window);
                if (context) window.parent.postMessage({type: 'trellum:presentation',
                    report: context.report, hostToken: context.token, presenting: value}, window.location.origin);
            } catch (error) { /* Cross-origin embeds use the in-frame fallback. */ }
        }
        function linkedIndex() {
            var id;
            try { id = decodeURIComponent(window.location.hash.slice(1)); }
            catch (error) { return -1; }
            var target = document.getElementById(id);
            return target ? slides.indexOf(target.closest('.fw-slide')) : -1;
        }
        function show(value, write, focus) {
            index = Math.max(0, Math.min(slides.length - 1, value));
            slides.forEach(function (slide, i) { slide.hidden = (presenting || paged) && i !== index; });
            deck.classList.toggle('fw-presentation--paged', paged);
            links.forEach(function (link, i) {
                if (i === index) link.setAttribute('aria-current', 'step');
                else link.removeAttribute('aria-current');
            });
            if (reading) {
                reading.textContent = paged ? 'Read all slides' : 'Slide view';
                reading.hidden = presenting;
            }
            position.textContent = 'Slide ' + (index + 1) + ' of ' + slides.length;
            previous.disabled = index === 0; next.disabled = index === slides.length - 1;
            if (write && window.location.hash !== '#' + slides[index].id) {
                history.pushState(history.state, '', '#' + slides[index].id);
            }
            if (focus) {
                slides[index].querySelector('h2').focus({preventScroll: presenting || paged});
                if (!presenting && !paged) slides[index].scrollIntoView({block: 'start', behavior: 'instant'});
            }
        }
        links.forEach(function (link) {
            link.addEventListener('click', function (event) {
                if (!(paged || presenting) || event.defaultPrevented || event.button !== 0 ||
                        event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
                event.preventDefault();
                show(links.indexOf(link), true, true);
            });
        });
        function enter(event) {
            if (presenting) return;
            opener = event.currentTarget;
            presenting = true; nativeEntered = false;
            if (slideView) paged = true;
            document.body.classList.add('fw-presenting');
            deck.setAttribute('role', 'dialog'); deck.setAttribute('aria-modal', 'true');
            deck.setAttribute('aria-label', 'Presentation');
            present.hidden = true; exit.hidden = false; notice.textContent = '';
            hostState(true); show(index, true, true);
            // Keep this call synchronous with the Present button's user gesture.
            if (deck.requestFullscreen) {
                try {
                    Promise.resolve(deck.requestFullscreen()).catch(fallback);
                } catch (error) { fallback(); }
            } else fallback();
        }
        function fallback() {
            if (presenting) notice.textContent = 'Fullscreen unavailable. Presenting in this window.';
        }
        function leave() {
            if (!presenting) return;
            presenting = false;
            document.body.classList.remove('fw-presenting');
            deck.removeAttribute('role'); deck.removeAttribute('aria-modal'); deck.removeAttribute('aria-label');
            present.hidden = false; exit.hidden = true; notice.textContent = '';
            hostState(false); show(index, false, false);
            if (document.fullscreenElement === deck && document.exitFullscreen) {
                Promise.resolve(document.exitFullscreen()).catch(function () {});
            }
            if (opener && opener.isConnected) opener.focus({preventScroll: true});
            if (!slideView) slides[index].scrollIntoView({block: 'start', behavior: 'instant'});
        }
        document.addEventListener('fullscreenchange', function () {
            if (document.fullscreenElement === deck) {
                nativeEntered = true;
                if (!presenting && document.exitFullscreen) document.exitFullscreen().catch(function () {});
            } else if (nativeEntered && presenting) leave();
        });
        window.addEventListener('hashchange', fromLocation);
        window.addEventListener('popstate', fromLocation);
        function fromLocation() {
            var linked = linkedIndex();
            if (linked >= 0) show(linked, false, presenting || paged);
            else if (!window.location.hash) show(0, false, presenting || paged);
        }
        document.addEventListener('keydown', function (event) {
            if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
            if (presenting && event.key === 'Escape') { event.preventDefault(); leave(); return; }
            if (!(presenting || paged) || !deck.contains(event.target)) return;
            var editing = event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="slider"],[role="spinbutton"],[role="combobox"]');
            // Arrow keys belong to authored links/widgets, except our own navigation.
            var authoredControl = event.target.closest('a,button,[role="button"]') &&
                !event.target.closest('.fw-presentation-controls,.fw-presentation-toc');
            var target = {ArrowLeft: index - 1, ArrowUp: index - 1,
                ArrowRight: index + 1, ArrowDown: index + 1, Home: 0, End: slides.length - 1}[event.key];
            if (target !== undefined && !event.shiftKey && !editing && !authoredControl) {
                event.preventDefault(); show(target, true, true);
            }
            if (presenting && event.key === 'Tab') {
                var focusable = Array.from(deck.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"],[contenteditable]:not([contenteditable="false"])'))
                    .filter(function (el) { return el.getClientRects().length > 0; });
                var first = focusable[0], last = focusable[focusable.length - 1];
                if (event.shiftKey && (document.activeElement === first || document.activeElement.tagName === 'H2')) {
                    event.preventDefault(); last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault(); first.focus();
                }
            }
        });
        var touch = null;
        deck.addEventListener('touchstart', function (event) {
            touch = null;
            if (!(presenting || paged) || event.touches.length !== 1 || event.target.closest('a,button,input,textarea,select,[contenteditable],table,pre')) return;
            touch = {x: event.touches[0].clientX, y: event.touches[0].clientY};
        }, {passive: true});
        deck.addEventListener('touchend', function (event) {
            if (!touch || !(presenting || paged) || !event.changedTouches.length) return;
            var dx = event.changedTouches[0].clientX - touch.x;
            var dy = event.changedTouches[0].clientY - touch.y;
            touch = null;
            if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 2) show(index + (dx < 0 ? 1 : -1), true, true);
        }, {passive: true});
        deck.addEventListener('touchcancel', function () { touch = null; });
        var linked = linkedIndex(); show(linked >= 0 ? linked : 0, false, false);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
}());

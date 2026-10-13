    /* ── identity capture ───────────────────────────────────── */
    var SNAP = WORKSPACE && WORKSPACE.kind === 'analysis'
        ? 'h1,h2,h3,h4,h5,h6,[data-fw-kind],.fw-kpi-card,.fw-section'
        : WORKSPACE
            ? '.fw-chart-container,.fw-kpi-card,[data-fw-kind]:not([data-fw-kind="Passage"]):not([data-fw-kind="passage"]),.fw-section'
            : '.fw-kpi-card, [data-fw-kind], .fw-section';

    function cssPath(node) {
        if (node.id) return '#' + node.id;
        var parts = [];
        var cur = node;
        while (cur && cur.nodeType === 1 && parts.length < 5) {
            if (cur.id) { parts.unshift('#' + cur.id); break; }
            var tag = cur.tagName.toLowerCase();
            var nth = 1, sib = cur;
            while ((sib = sib.previousElementSibling)) {
                if (sib.tagName === cur.tagName) nth++;
            }
            parts.unshift(tag + ':nth-of-type(' + nth + ')');
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    }

    function identify(node) {
        if (WORKSPACE && WORKSPACE.kind === 'analysis') {
            // Text headings inside a chart/section are the specific target;
            // only a direct component surface falls through to its component.
            var heading = node.closest('h1,h2,h3,h4,h5,h6');
            var passage = node.closest('[data-fw-kind="Passage"],[data-fw-kind="passage"]');
            if (heading || passage) {
                var textTarget = heading || passage;
                var enclosingSection = textTarget.closest('.fw-section');
                return {
                    el: textTarget, kind: heading ? 'Heading' : 'Passage',
                    title: (textTarget.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120),
                    id: textTarget.id || '', selector: cssPath(textTarget),
                    tag: textTarget.tagName.toLowerCase(),
                    text: (textTarget.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 240),
                    section: enclosingSection ? {
                        id: enclosingSection.id || '',
                        title: enclosingSection.getAttribute('data-fw-section-title') ||
                            (enclosingSection.querySelector('h2') ? enclosingSection.querySelector('h2').textContent.trim() : '')
                    } : null
                };
            }
        }
        var card = node.closest('.fw-kpi-card');
        var comp = node.closest('[data-fw-kind]');
        var sec = node.closest('.fw-section');
        var target, kind, title;
        if (card) {
            target = card; kind = 'KPI';
            var lbl = card.querySelector('.fw-kpi-label');
            title = lbl ? lbl.textContent.trim() : '';
        } else if (comp) {
            target = comp;
            kind = comp.getAttribute('data-fw-kind');
            title = comp.getAttribute('data-fw-title') || '';
        } else if (sec) {
            target = sec; kind = 'Section';
            title = sec.getAttribute('data-fw-section-title') || '';
        } else {
            return null;
        }
        var secInfo = null;
        if (sec) {
            secInfo = {
                id: sec.id || '',
                title: sec.getAttribute('data-fw-section-title') ||
                       (sec.querySelector('h2') ? sec.querySelector('h2').textContent.trim() : '')
            };
        }
        return {
            el: target,
            kind: kind,
            title: title,
            id: target.id || '',
            selector: cssPath(target),
            tag: target.tagName.toLowerCase(),
            text: (target.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 240),
            section: secInfo
        };
    }

    function chipHtml(t) {
        var loc = t.section && t.section.title && t.kind !== 'Section'
            ? ' in “' + esc(t.section.title) + '”' : '';
        var purpose = WORKSPACE && WORKSPACE.kind === 'report' ? 'Add to analysis · ' : '';
        return '<span class="fwrv-k">' + purpose + esc(t.kind) + '</span><span>' +
            (t.title ? '“' + esc(t.title) + '”' : '') + loc +
            '</span><span class="fwrv-sel">' + esc(t.selector) + '</span>';
    }

    function docRect(node) {
        var r = node.getBoundingClientRect();
        return { top: r.top + window.scrollY, left: r.left + window.scrollX, w: r.width, h: r.height };
    }

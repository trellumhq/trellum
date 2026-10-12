(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
    var controls = document.getElementById('fwMetricsWorkspace');
    if (!controls) return;
    var focused = document.body.classList.contains('fw-only');
    if (new URLSearchParams(location.search).get('workspace') === '1' && !focused) {
        document.body.classList.add('fw-metrics-embedded');
    }
    var choices = Array.from(controls.querySelectorAll('input[type=checkbox]'));
    var blocks = choices.map(function (choice) { return document.getElementById(choice.value); });
    var key = 'trellum:metrics-visible:' + location.pathname;
    var hidden = [];
    try { hidden = JSON.parse(localStorage.getItem(key) || '[]'); } catch (e) {}
    if (!Array.isArray(hidden)) hidden = [];
    var stamp = null;

    function charts() {
        return Object.values(window._chartInstances || {}).filter(function (chart) {
            return chart.canvas && chart.canvas.closest('.fw-metric-block');
        });
    }
    function clear() {
        stamp = null;
        charts().forEach(function (chart) {
            if (chart.$metricGuide != null) { chart.$metricGuide = null; chart.draw(); }
        });
    }
    function applyVisibility(save) {
        clear();
        choices.forEach(function (choice, i) {
            blocks[i].hidden = !choice.checked && !focused;
            var canvas = blocks[i].querySelector('canvas');
            var chart = canvas && (window._chartInstances || {})[canvas.id];
            if (choice.checked && chart) chart.resize();
        });
        var count = choices.filter(function (choice) { return choice.checked; }).length;
        document.getElementById('fwMetricsCount').textContent = count + ' of ' + choices.length + ' shown';
        document.getElementById('fwMetricsEmpty').hidden = count !== 0 || focused;
        if (save) {
            try { localStorage.setItem(key, JSON.stringify(choices.filter(function (choice) {
                return !choice.checked;
            }).map(function (choice) { return choice.value; }))); } catch (e) {}
        }
    }
    choices.forEach(function (choice, i) {
        choice.checked = focused || hidden.indexOf(choice.value) === -1;
        choice.addEventListener('change', function () { applyVisibility(true); });
        var category = document.createElement('div');
        category.className = 'fw-metric-category';
        category.textContent = choice.dataset.category;
        blocks[i].prepend(category);
        var link = document.createElement('a');
        link.className = 'fw-metric-focus'; link.textContent = 'Open metric page →';
        var url = new URL(location.href);
        url.searchParams.delete('workspace'); url.searchParams.set('only', choice.value); url.hash = '';
        link.href = url.href;
        if (document.body.classList.contains('fw-metrics-embedded')) link.target = '_top';
        link.addEventListener('click', function () {
            var current = new URL(location.href);
            current.searchParams.delete('workspace'); current.searchParams.set('only', choice.value); current.hash = '';
            link.href = current.href;
        });
        blocks[i].appendChild(link);
        if (blocks[i].hasAttribute('data-metric-snapshot')) {
            var note = document.createElement('p'); note.className = 'fw-metric-snapshot-note';
            note.textContent = 'Latest total is a build-time snapshot. Use the trend for the selected dates.';
            blocks[i].insertBefore(note, blocks[i].querySelector('.fw-kpi-grid'));
        }
    });
    document.getElementById('fwMetricsShowAll').addEventListener('click', function () {
        choices.forEach(function (choice) { choice.checked = true; });
        applyVisibility(true);
    });
    document.getElementById('fwMetricsHideAll').addEventListener('click', function () {
        choices.forEach(function (choice) { choice.checked = false; });
        applyVisibility(true);
    });
    applyVisibility(false);
    if (focused) return;

    // Time values, never array positions: a missing date has no guide or invented tooltip.
    function time(value) {
        if (!/^\d{4}-\d{2}-\d{2}(?:$|T| )/.test(String(value))) return null;
        var parsed = Date.parse(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    function dates(chart) {
        var block = chart.canvas.closest('.fw-metric-block');
        return block && block.dataset.metricTime && !block.hidden
            ? (chart.data.labels || []).map(time) : [];
    }
    function align(source) {
        charts().forEach(function (chart) {
            var values = dates(chart), index = stamp == null ? -1 : values.indexOf(stamp);
            var x = index === -1 ? null : chart.scales.x.getPixelForValue(index);
            var area = chart.chartArea;
            chart.$metricGuide = x != null && x >= area.left && x <= area.right ? x : null;
            if (chart !== source) chart.draw();
        });
    }
    Chart.register({
        id: 'fwMetricsGuide',
        afterInit: clear,
        beforeDestroy: clear,
        afterEvent: function (chart, args) {
            var values = dates(chart), ev = args.event;
            if (!values.length) return;
            if (ev.type === 'mouseout') { clear(); args.changed = true; return; }
            if (ev.type !== 'mousemove' || (ev.native &&
                    (ev.native.pointerType === 'touch' || /^touch/.test(ev.native.type)))) return;
            var area = chart.chartArea;
            if (ev.x < area.left || ev.x > area.right || ev.y < area.top || ev.y > area.bottom) {
                clear(); args.changed = true; return;
            }
            // Native nearest-point tooltips can choose a different X when the pointer is far above the line.
            var active = chart.tooltip && chart.tooltip.getActiveElements();
            var index = active && active.length ? active[0].index : chart.scales.x.getValueForPixel(ev.x);
            stamp = values[index] == null ? null : values[index];
            align(chart); args.changed = true;
        },
        afterDraw: function (chart) {
            if (chart.$metricGuide == null) return;
            var ctx = chart.ctx, area = chart.chartArea;
            ctx.save(); ctx.strokeStyle = getComputedStyle(chart.canvas).getPropertyValue('--text-secondary').trim();
            ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(chart.$metricGuide, area.top); ctx.lineTo(chart.$metricGuide, area.bottom);
            ctx.stroke(); ctx.restore();
        }
    });
    document.addEventListener('pointerleave', function (ev) {
        if (ev.target.tagName === 'CANVAS') clear();
    }, true);
    document.addEventListener('change', clear, true);
    document.addEventListener('click', clear, true);
    window.addEventListener('blur', clear);
    });
})();

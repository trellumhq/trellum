    // Opt-in daily category context. Calendar validation never changes plot data.
    // A replacement canvas may inherit a description from an older instance.
    window._fwClearMonthContext = function(canvas) {
        var id = canvas.id + '-month-context';
        var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/);
        if (tokens.indexOf(id) === -1) return;
        tokens = tokens.filter(function(token) { return token && token !== id; });
        if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
        else canvas.removeAttribute('aria-describedby');
        var row = document.getElementById(id);
        if (row && row.classList.contains('fw-month-context')) row.remove();
        if (canvas.parentElement) canvas.parentElement.classList.remove('fw-has-month-context');
    };
    window._fwMonthContextPlugin = function() {
        var row = null, container = null, boundaries = [];
        var months = ['January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'];
        function describe(chart, enabled) {
            var id = chart.canvas.id + '-month-context';
            var tokens = (chart.canvas.getAttribute('aria-describedby') || '').split(/\s+/)
                .filter(function(token) { return token && token !== id; });
            if (enabled) tokens.push(id);
            if (tokens.length) chart.canvas.setAttribute('aria-describedby', tokens.join(' '));
            else chart.canvas.removeAttribute('aria-describedby');
        }
        function clear(chart) {
            if (row) row.remove();
            if (container) container.classList.remove('fw-has-month-context');
            row = null; container = null; boundaries = [];
            describe(chart, false);
        }
        function calendar(labels) {
            if (!Array.isArray(labels) || !labels.length) return {reason: 'empty'};
            var previous = '', groups = [];
            for (var i = 0; i < labels.length; i++) {
                var label = labels[i];
                if (typeof label !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(label))
                    return {reason: 'invalid-date'};
                var year = +label.slice(0, 4), month = +label.slice(5, 7), day = +label.slice(8, 10);
                var leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
                var days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
                if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1])
                    return {reason: 'invalid-date'};
                if (label === previous) return {reason: 'duplicate-date'};
                if (label < previous) return {reason: 'unsorted-dates'};
                var key = label.slice(0, 7), last = groups[groups.length - 1];
                if (!last || last.key !== key) groups.push({key: key, month: month, year: label.slice(0, 4), start: i, end: i});
                else last.end = i;
                previous = label;
            }
            return {groups: groups};
        }
        function layout(chart) {
            var result = calendar(chart.data.labels), x = chart.scales.x, area = chart.chartArea;
            if (result.reason || !x || x.type !== 'category' || !area || area.width <= 0) {
                clear(chart);
                chart._fwMonthContext = {status: 'omitted', reason: result.reason || 'unavailable-scale'};
                return;
            }
            container = chart.canvas.parentElement;
            if (!row) {
                row = document.createElement('div');
                row.className = 'fw-month-context';
                row.id = chart.canvas.id + '-month-context';
                row.setAttribute('role', 'group');
                row.setAttribute('aria-label', 'Calendar months of supplied daily observations');
            }
            if (container.nextElementSibling !== row) container.after(row);
            container.classList.add('fw-has-month-context');
            describe(chart, true);
            var count = chart.data.labels.length;
            function center(i) { return x.getPixelForValue(i); }
            function edge(i) {
                // Outer months extend to the plot edge, including a single category.
                if (i === 0) return -Infinity;
                if (i === count) return Infinity;
                return (center(i - 1) + center(i)) / 2;
            }
            var css = getComputedStyle(row), ctx = chart.ctx;
            ctx.save();
            ctx.font = css.font;
            boundaries = [];
            var fragment = document.createDocumentFragment(), visible = [];
            result.groups.forEach(function(group, index) {
                var start = edge(group.start), end = edge(group.end + 1);
                if (index && start >= area.left && start <= area.right) boundaries.push(start);
                var left = Math.max(area.left, start), right = Math.min(area.right, end);
                if (right <= left) return;
                var full = months[group.month - 1] + ' ' + group.year;
                var candidates = [full, months[group.month - 1].slice(0, 3) + ' ' + group.year, group.key];
                var text = candidates.find(function(candidate) { return ctx.measureText(candidate).width + 8 <= right - left; }) || '';
                var span = document.createElement('span');
                span.className = 'fw-month-span';
                span.style.left = left + 'px'; span.style.width = (right - left) + 'px';
                span.title = full;
                var accessible = document.createElement('span');
                accessible.className = 'fw-month-accessible'; accessible.textContent = full;
                var visual = document.createElement('span');
                visual.setAttribute('aria-hidden', 'true'); visual.textContent = text;
                span.append(accessible, visual); fragment.appendChild(span);
                visible.push({key: group.key, left: left, right: right, label: text, full: full});
            });
            ctx.restore();
            row.replaceChildren(fragment);
            chart._fwMonthContext = {status: 'eligible', reason: '', groups: visible, boundaries: boundaries.slice()};
        }
        return {
            id: 'fwMonthContext',
            afterUpdate: layout,
            beforeDatasetsDraw: function(chart) {
                if (!boundaries.length) return;
                var ctx = chart.ctx, area = chart.chartArea;
                ctx.save();
                ctx.beginPath(); ctx.rect(area.left, area.top, area.width, area.height); ctx.clip();
                ctx.strokeStyle = getComputedStyle(chart.canvas).getPropertyValue('--border-color').trim();
                ctx.lineWidth = 1;
                boundaries.forEach(function(pixel) {
                    ctx.beginPath(); ctx.moveTo(pixel, area.top); ctx.lineTo(pixel, area.bottom); ctx.stroke();
                });
                ctx.restore();
            },
            beforeDestroy: function(chart) { clear(chart); delete chart._fwMonthContext; }
        };
    };

// Only new standalone choices opt into this enhancer. SlimSelect owns native
// value/change semantics; do not dispatch a second change from afterChange.
(function() {
    function init() {
        if (typeof SlimSelect === 'undefined') return;
        document.querySelectorAll('select[data-fw-choice]').forEach(function(select) {
            if (select.slim) return;
            var picker = new SlimSelect({
                select: select,
                settings: {
                    showSearch: true, allowDeselect: false,
                    searchPlaceholder: 'Search choices',
                    placeholderText: select.disabled ? 'No choices available' : 'Choose an option'
                }
            });
            // A visible label should focus the enhanced control too.
            var label = select.parentElement.querySelector('label');
            if (label) label.addEventListener('click', function(e) {
                e.preventDefault();
                if (!select.disabled) picker.render.main.main.focus();
            });
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();

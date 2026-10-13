(function () {
    if (!window.TrellumNavigation) return;
    window.TrellumNavigation.register('metrics', {
        mount: function (context) {
            var overlay = window.TrellumMetricsOverlay.mount(context.root, context.signal);
            var workspace = window.TrellumMetricsWorkspace.mount(context.root, context.signal);
            return { unmount: function () { overlay(); if (workspace) workspace(); }, ready: workspace && workspace.ready };
        }
    });
}());

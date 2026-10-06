window._fwRenderers['live_data_source'] = function(id, cfg) {
    // Load the build-time snapshot exactly like an ordinary DataSource --
    // isReady()/getFilterState()/subscribe() all work identically whether
    // this dataset ever goes live or stays a snapshot forever (e.g. served
    // standalone). registerDataset() is what actually makes it "live": it
    // tells the filter engine to route setFilter() to the binder instead of
    // re-filtering locally, and it's safe to call even when the binder will
    // never arm (no host, or a share link) -- it just never fires anything.
    var raw = window._reportData['_ds_' + cfg.dataset_id];
    window._fwFilterEngine.initColumnar(cfg.dataset_id, raw || {_cols: [], _data: [], _dict: {}});
    if (window._fwLiveQuery) window._fwLiveQuery.registerDataset(cfg.dataset_id, cfg.live || {});
};

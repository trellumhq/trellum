window._fwRenderers['live_data_source'] = function(id, cfg) {
    var owner = document.getElementById(id);
    if (!owner) return;
    var raw = window._reportData['_ds_' + cfg.dataset_id];
    if (window._fwLiveQuery) {
        // Snapshot installation and query identity share one owner. Re-entering
        // this renderer (including theme changes) cannot rewind queried rows.
        window._fwLiveQuery.registerDataset(cfg.dataset_id, cfg.live || {}, {
            raw: raw, report: window._reportData, owner: owner
        });
    } else {
        window._fwFilterEngine.initColumnar(cfg.dataset_id, raw || {_cols: [], _data: [], _dict: {}});
    }
};

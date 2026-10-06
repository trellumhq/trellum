window._fwRenderers['data_source'] = function(id, cfg) {
    var raw = window._reportData['_ds_' + cfg.dataset_id];
    if (!raw) return;
    window._fwFilterEngine.initColumnar(cfg.dataset_id, raw);
};

window._fwRenderers['scoped_data_source'] = function(id, cfg) {
    window._fwFilterEngine.addScopedChild(cfg.dataset_id, cfg.parent_id);
};

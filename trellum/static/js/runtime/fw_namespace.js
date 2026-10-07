    // Stable entry point for custom RawHTML JS.  Old bare
    // globals (fmtCompact$, getThemeColors, etc.) remain as
    // aliases for backward compatibility.
    window.fw = {
        fmtCompact: fmtCompact,
        'fmtCompact$': fmtCompact$,
        fmtChips: fmtChips,
        fmtPercent: fmtPercent,
        'fmt$': fmt$,
        getFormatter: window.getFormatter,

        getActiveTheme: getActiveTheme,
        getThemeColors: getThemeColors,
        switchTheme: switchTheme,
        themes: window._themes,

        chartInstances: window._chartInstances,
        registerChart: function(id, chart) {
            window._chartInstances[id] = chart;
        },

        buildAnnotations: _buildAnnotations,
        downloadCsv: _fwDownloadCsv,

        exportPNG: _exportPNG,
        exportPDF: _exportPDF,
        captureForAnalysis: _fwCaptureForAnalysis,
        captureElementForAnalysis: _fwCaptureElementForAnalysis,

        filterEngine: window._fwFilterEngine,
        aggregate: window._fwAggregate,
        liveWrap: window._fwLiveWrap,
        urlSync: window._fwUrlSync,
        annoVisible: window._annoVisible,

        get data() { return window._reportData; },
        get events() { return (window._reportData || {})._events || []; },
        get currentScope() { return window._currentScope; },
    };

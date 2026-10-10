(function(){
  function _measureSticky() {
    var root = document.documentElement;
    var chrome = document.querySelector('.fw-report-chrome');
    var hdr = chrome || document.querySelector('.fw-header');
    var meta = document.getElementById('fwReportMetadata');
    var ab = document.getElementById('fwAnnoBar');
    var hh = hdr ? hdr.getBoundingClientRect().height : 0;
    var mh = chrome ? 0 : (meta ? meta.getBoundingClientRect().height : 0);
    var ah = ab ? ab.getBoundingClientRect().height : 0;
    root.style.setProperty('--fw-header-h', hh + 'px');
    root.style.setProperty('--fw-metadata-h', mh + 'px');
    root.style.setProperty('--fw-sticky-offset', (hh + mh + ah) + 'px');
  }
  function _watchSticky() {
    _measureSticky();
    window.addEventListener('resize', _measureSticky);
    new MutationObserver(_measureSticky).observe(document.body, {
      attributes: true, attributeFilter: ['class']
    });
    new MutationObserver(function(records) {
      for (var i = 0; i < records.length; i++) {
        var record = records[i];
        if (record.target.closest && record.target.closest('#fwAnnoBar')) {
          _measureSticky();
          return;
        }
        for (var j = 0; j < record.addedNodes.length; j++) {
          var node = record.addedNodes[j];
          if (node.nodeType === 1 && (node.id === 'fwAnnoBar' ||
              (node.querySelector && node.querySelector('#fwAnnoBar')))) {
            _measureSticky();
            return;
          }
        }
      }
    }).observe(document.body, {
      childList: true, subtree: true
    });
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', _watchSticky);
  else _watchSticky();

  function _updateFreshness() {
    var el = document.getElementById('fwFreshness');
    var d = window._reportData && window._reportData._freshness;
    if (!el || !d || !d.generated_at) return;
    var age = (Date.now() - new Date(d.generated_at).getTime()) / 1000;
    var fullTxt;
    if (age < 60) {
      fullTxt = 'Updated just now';
    } else if (age < 3600) {
      var m = Math.floor(age / 60);
      fullTxt = 'Updated ' + m + 'm ago';
    } else if (age < 86400) {
      var h = Math.floor(age / 3600);
      fullTxt = 'Updated ' + h + 'h ago';
    } else {
      var dAge = Math.floor(age / 86400);
      fullTxt = 'Updated ' + dAge + 'd ago';
    }
    el.innerHTML = '<span class="fw-fresh-dot"></span>' + fullTxt;
    el.title = fullTxt;
    el.className = 'fw-freshness visible';
    var rs = d.refresh_seconds || 0;
    if (rs > 0 && age > rs * 4) el.classList.add('very-stale');
    else if (rs > 0 && age > rs * 2) el.classList.add('stale');
    else el.classList.add('fresh');
    _measureSticky();
  }
  var _poll = setInterval(function() {
    if (window._reportData && document.getElementById('fwFreshness')) {
      clearInterval(_poll);
      _updateFreshness();
      setInterval(_updateFreshness, 60000);
    }
  }, 200);
})();

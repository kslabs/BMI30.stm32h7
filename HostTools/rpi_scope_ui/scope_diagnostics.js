// Opt-in, read-only performance observations; no acquisition commands.
(function() {
  if (!new URLSearchParams(window.location.search).has('scope_diag')) return;
  let started = performance.now(), paints = [], arrivals = [], resources = [], animationGaps = [], longTasks = [];
  let udp = null;
  let lastSource = null, lastAnimation = null, active = true;
  let node = document.createElement('script');
  node.id = 'scope-diagnostics'; node.type = 'application/json';
  document.body.appendChild(node);
  function retain(items, value) { items.push(value); if (items.length > 240) items.shift(); }
  let enqueue = queueScopeFrame;
  queueScopeFrame = function(f, held, generation) {
    if (f && f.available) retain(arrivals, {at:performance.now()-started,source:f.last_frame_t});
    return enqueue(f, held, generation);
  };
  let render = renderFrame;
  renderFrame = function(f, force) {
    let at = performance.now(), source = f && f.last_frame_t;
    let result = render(f, force);
    let revision=f&&f._scopeRevision||source;
    if (f && f.available && revision !== lastSource) {
      retain(paints, {at: at - started, source: source,revision:f._scopeRevision||null,
        channelTimes:[f._channel_frame_t0||source,f._channel_frame_t1||source],renderMs: performance.now() - at});
      lastSource = revision;
    }
    return result;
  };
  function animation(at) {
    if (!active) return;
    if (lastAnimation !== null) retain(animationGaps, at - lastAnimation);
    lastAnimation = at; requestAnimationFrame(animation);
  }
  requestAnimationFrame(animation);
  let observers = [];
  if (window.PerformanceObserver) {
    for (let kind of ['resource', 'longtask']) {
      try {
        let observer = new PerformanceObserver(function(list) {
          for (let entry of list.getEntries()) {
            if (kind === 'longtask') retain(longTasks, {at: entry.startTime - started, ms: entry.duration});
            else if (/\/api\/(frame|status)/.test(entry.name)) retain(resources, {
              path: entry.name.split('/api/')[1].split('?')[0], at: entry.startTime - started,
              ms: entry.duration, end: entry.responseEnd - started});
          }
        });
        observer.observe({type: kind, buffered: false}); observers.push(observer);
      } catch (e) {}
    }
  }
  function publish() {
    node.textContent = JSON.stringify({elapsedMs: performance.now() - started,
      paints: paints, arrivals:arrivals, resources: resources, animationGaps: animationGaps, longTasks: longTasks,
      stream: scopeRtcHealthy() ? {transport:'webrtc',state:scopeRtc.pc.connectionState,
        lastAgeMs:performance.now()-scopeRtc.last,queued:liveFrames.length,udp:udp,events:scopeSocketEvents} : typeof scopeSocket !== 'undefined' ? {transport:'websocket',state: scopeSocket && scopeSocket.readyState,
        lastAgeMs: performance.now() - scopeSocketLast, queued: liveFrames.length,
        events: typeof scopeSocketEvents !== 'undefined' ? scopeSocketEvents : []} : null});
  }
  publish(); let timer = setInterval(function(){
    publish();
    if (scopeRtcHealthy()) scopeRtc.pc.getStats().then(function(stats){
      for (let item of stats.values()) if(item.type==='transport' && item.selectedCandidatePairId) {
        let pair=stats.get(item.selectedCandidatePairId),candidate=pair&&stats.get(pair.localCandidateId);
        if(candidate)udp={protocol:candidate.protocol,rttMs:pair.currentRoundTripTime*1000};
      }
    }).catch(()=>{});
  }, 1000);
  window.addEventListener('pagehide', function() {
    active = false; clearInterval(timer); for (let observer of observers) observer.disconnect();
  });
}());

// Independent, bounded display requests. Acquisition stays on the core worker.
const SCOPE_FRAME_INTERVAL_MS = 40;
const SCOPE_STATUS_INTERVAL_MS = 500;
const SCOPE_REQUEST_TIMEOUT_MS = 2000;
let frameTimer = null;
let frameInFlight = false;
let currentFrameController = null;
let framePaint = null;
let pendingFrame = null;
let displayGeneration = 0;
let lastFrameReadoutAt = -Infinity;
let lastScopeLayoutKey = '';

function cancelScopeFrames() {
  displayGeneration++;
  pendingFrame = null;
  if (framePaint !== null) cancelAnimationFrame(framePaint);
  framePaint = null;
  if (currentFrameController) currentFrameController.abort();
}

function stopScopeDisplay() {
  leavingPage = true;
  if (refreshTimer !== null) clearInterval(refreshTimer);
  if (frameTimer !== null) clearInterval(frameTimer);
  refreshTimer = frameTimer = null;
  cancelScopeFrames();
  if (currentRefreshController) currentRefreshController.abort();
}

function queueScopeFrame(f, allowFrozen, generation) {
  pendingFrame = {frame: f, allowFrozen: allowFrozen, generation: generation};
  if (framePaint !== null) return;
  framePaint = requestAnimationFrame(function() {
    framePaint = null;
    let pending = pendingFrame;
    pendingFrame = null;
    if (!pending || pending.generation !== displayGeneration || leavingPage ||
        document.hidden || playerState.playback) return;
    applyFrame(pending.frame, pending.allowFrozen);
  });
}

function refreshFrames() {
  if (leavingPage || document.hidden || frameInFlight || !lastStatus ||
      playerState.playback || freezeMode === 'freeze' ||
      (freezeMode === 'auto' && !autoFreezeRequest)) return Promise.resolve();
  frameInFlight = true;
  let generation = displayGeneration;
  let channels = frameChannels();
  let allowFrozen = freezeMode === 'auto';
  let ctl = window.AbortController ? new AbortController() : null;
  currentFrameController = ctl;
  let timeout = ctl ? setTimeout(function() { ctl.abort(); }, SCOPE_REQUEST_TIMEOUT_MS) : null;
  let opts = ctl ? {signal: ctl.signal, cache: 'no-store'} : {cache: 'no-store'};
  return fetchFrame(opts).then(function(f) {
    if (generation !== displayGeneration || channels !== frameChannels() ||
        leavingPage || document.hidden || playerState.playback || f.busy) return;
    queueScopeFrame(f, allowFrozen, generation);
  }).catch(function(e) {
    if (generation !== displayGeneration || leavingPage || e.name === 'AbortError') return;
    setText('serviceState', 'error');
    setText('conn', String(e));
  }).finally(function() {
    if (timeout !== null) clearTimeout(timeout);
    frameInFlight = false;
    if (currentFrameController === ctl) currentFrameController = null;
  });
}

function refreshStatus() {
  if (leavingPage || refreshInFlight || document.hidden) return Promise.resolve();
  refreshInFlight = true;
  let ctl = window.AbortController ? new AbortController() : null;
  currentRefreshController = ctl;
  let timeout = ctl ? setTimeout(function() { ctl.abort(); }, SCOPE_REQUEST_TIMEOUT_MS) : null;
  let opts = ctl ? {signal: ctl.signal, cache: 'no-store'} : {cache: 'no-store'};
  return fetch('/api/status', opts).then(function(r) {
    if (!r.ok) throw new Error('status ' + r.status);
    return r.json();
  }).then(function(s) {
    if (leavingPage || document.hidden) return;
    applyStatus(s);
    if (!playerState.playback && freezeMode === 'auto') autoFreezeNeedsFrame((s || {}).detector || {});
    if (freezeMode === 'live' || (freezeMode === 'auto' && autoFreezeRequest)) refreshFrames();
    else redrawHeldFrameIfNeeded();
  }).catch(function(e) {
    if (leavingPage || e.name === 'AbortError') return;
    setText('serviceState', 'error');
    setText('conn', String(e));
  }).finally(function() {
    if (timeout !== null) clearTimeout(timeout);
    refreshInFlight = false;
    if (currentRefreshController === ctl) currentRefreshController = null;
  });
}

function refresh() {
  return Promise.all([refreshStatus(), refreshFrames()]);
}

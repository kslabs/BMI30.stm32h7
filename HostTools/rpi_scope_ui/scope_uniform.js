// One live transport, bounded jitter buffer and a steady display clock.
const SCOPE_FRAME_INTERVAL_MS = 40;
const SCOPE_STATUS_INTERVAL_MS = 500;
const SCOPE_REQUEST_TIMEOUT_MS = 2000;
const SCOPE_PLAYOUT_MS = 120;
const SCOPE_JITTER_MS = 180;
const SCOPE_QUEUE_MAX = 8;
let frameTimer = null, frameInFlight = false, currentFrameController = null;
let framePaint = null, framePaintWake = null, pendingFrame = null, displayGeneration = 0;
let lastFrameReadoutAt = -Infinity, lastScopeLayoutKey = '';
let liveFrames = [], liveOffset = null, livePlayAt = 0, liveSource = 0, liveArrival = 0;
let scopeSocket = null, scopeSocketChannels = '', scopeSocketLast = 0, scopeRetryAt = 0;
let scopeSocketEvents = [];
let scopeRtc = null, scopeRtcRetryAt = 0;
let scopeHttpStatusAt = -Infinity;

async function decodeScopeRtc(data, status) {
  if (!(data instanceof ArrayBuffer)) return data;
  let magic=new TextDecoder().decode(new Uint8Array(data,0,Math.min(4,data.byteLength)));
  if (magic==='BMC1' || magic==='BMS1' || magic==='BMD1') {
    data=await new Response(new Blob([data.slice(4)]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer();
    if(status)return new TextDecoder().decode(data);
    if(magic==='BMD1') {
      let bytes=new Uint8Array(data),view=new DataView(data),length=view.getUint32(4,true),offset=(8+length+1)&~1;
      let header=JSON.parse(new TextDecoder().decode(bytes.slice(8,8+length))),n=header.count*header.arrays.length;
      if(data.byteLength-offset!==n*2)throw new Error('Invalid channel packet');
      let planes=bytes.slice(offset);
      for(let i=0;i<n;i++){bytes[offset+i*2]=planes[i];bytes[offset+i*2+1]=planes[n+i];}
      let f=parseFrameBinary(data);
      for(let name of f.arrays){let a=f[name];for(let i=1;i<a.length;i++)a[i]=(a[i]+a[i-1])&65535;for(let i=1;i<a.length;i++)a[i]=(a[i]+a[i-1])&65535;}
      return f;
    }
  }
  return status?data:parseFrameBinary(data);
}

function scopeLiveEligible() {
  return !leavingPage && !document.hidden && !playerState.playback && freezeMode === 'live';
}
function scopeLiveHealthy() {
  return scopeRtcHealthy() || (scopeSocket && scopeSocket.readyState === 1 && performance.now() - scopeSocketLast < 1500);
}
function scopeLiveStop() {
  scopeRtcStop(false);
  let socket = scopeSocket;
  scopeSocket = null; scopeSocketLast = 0; scopeSocketChannels = '';
  if (socket) socket.close();
}
function scopeWebSocketConnect() {
  if (!scopeLiveEligible()) { scopeLiveStop(); return; }
  let channels = frameChannels(), now = performance.now();
  if (scopeSocket && scopeSocketChannels !== channels) scopeLiveStop();
  if (scopeSocket && scopeSocket.readyState < 2) {
    if (scopeSocket.readyState === 1 && now - scopeSocketLast > 3000) scopeLiveStop();
    else return;
  }
  if (!window.WebSocket || now < scopeRetryAt) return;
  let url = new URL('api/scope/live', window.location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('channels', channels); url.searchParams.set('max_points', FRAME_MAX_POINTS);
  if (new URL(window.location.href).searchParams.has('scope_diag')) url.searchParams.set('scope_diag', '1');
  let socket;
  try { socket = new WebSocket(url.href); } catch (e) { scopeRetryAt = now + 2000; return; }
  socket.binaryType = 'arraybuffer';
  scopeSocket = socket; scopeSocketChannels = channels; scopeSocketLast = now;
  socket.onmessage = function(event) {
    if (scopeSocket !== socket || !scopeLiveEligible()) return;
    scopeSocketLast = performance.now();
    try {
      if (typeof event.data === 'string') {
        let status = JSON.parse(event.data); applyStatus(status);
        if (scopeSocketChannels !== frameChannels()) { scopeLiveStop(); scopeLiveConnect(); }
      } else {
        let f = parseFrameBinary(event.data); f._channels = channels; f._reviewQuery = '';
        if (f.available) queueScopeFrame(f, false, displayGeneration);
      }
    } catch (e) {
      scopeSocketEvents.push({at: performance.now(), kind: 'decode', error: String(e)});
      if (scopeSocketEvents.length > 24) scopeSocketEvents.shift();
      socket.close();
    }
  };
  socket.onclose = function(event) {
    if (scopeSocket !== socket) return;
    scopeSocketEvents.push({at: performance.now(), code: event && event.code, kind: 'close'});
    if (scopeSocketEvents.length > 24) scopeSocketEvents.shift();
    scopeSocket = null; scopeSocketLast = 0; scopeRetryAt = performance.now() + 1000;
  };
  socket.onerror = function() { socket.close(); };
}

function scopeRtcHealthy() {
  return scopeRtc && scopeRtc.frames.readyState === 'open' && performance.now() - scopeRtc.last < 1500;
}
function scopeRtcStop(failed) {
  let rtc = scopeRtc; scopeRtc = null;
  scopeRtcRetryAt = failed ? performance.now() + 30000 : 0;
  if (!rtc) return;
  if (rtc.leaseTimer !== null) clearInterval(rtc.leaseTimer);
  rtc.controller.abort(); rtc.pc.close();
}
async function scopeRtcLease(rtc) {
  if (scopeRtc !== rtc || !rtc.lease) return;
  let ctl = new AbortController(), timer = setTimeout(()=>ctl.abort(), 2000);
  try {
    await fetch(new URL('api/scope/lease', window.location.href), {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({lease:rtc.lease}), signal:ctl.signal});
  } catch(e) {} finally { clearTimeout(timer); }
}
async function scopeRtcStart(channels) {
  let pc = new RTCPeerConnection({iceServers:[]});
  let rtc = {pc:pc, channels:channels, frames:pc.createDataChannel('scope-frames', {ordered:false,maxRetransmits:0}),
    status:pc.createDataChannel('scope-status', {ordered:false,maxRetransmits:0}), last:performance.now(), started:performance.now(),
    controller:new AbortController(), lease:null, leaseTimer:null, statusSource:0, latest:{}, channelTimes:[0,0], revision:0};
  scopeRtc = rtc;
  async function message(event, status) {
    if (scopeRtc !== rtc || !scopeLiveEligible() || channels !== frameChannels()) return;
    rtc.last = performance.now();
    try {
      let data=await decodeScopeRtc(event.data,status);
      if (scopeRtc!==rtc || !scopeLiveEligible() || channels!==frameChannels()) return;
      if (status) {
        let s=JSON.parse(data), stamp=Number(s._scopeStatusSeq)||0;
        if (stamp && stamp<=rtc.statusSource) return;
        rtc.statusSource=stamp;applyStatus(s);
      }
      else {
        let f = data;
        if (f._scopeChannel===0 || f._scopeChannel===1) {
          let channel=f._scopeChannel,source=Number(f.last_frame_t)||0;
          if(source<=rtc.channelTimes[channel])return;
          rtc.channelTimes[channel]=source;
          f=Object.assign({},rtc.latest,f);
          f.last_frame_t=Math.max(...rtc.channelTimes);f._channel_frame_t0=rtc.channelTimes[0];f._channel_frame_t1=rtc.channelTimes[1];
          f._scopeRevision=++rtc.revision;rtc.latest=f;
        }
        f._channels=channels; f._reviewQuery='';
        if (f.available) queueScopeFrame(f, false, displayGeneration);
      }
    } catch(e) { if(scopeRtc===rtc)scopeRtcStop(true); }
  }
  rtc.frames.binaryType='arraybuffer';
  rtc.status.binaryType='arraybuffer';
  rtc.frames.onmessage=event=>message(event,false); rtc.status.onmessage=event=>message(event,true);
  rtc.frames.onopen=function() {
    if (scopeRtc !== rtc) return;
    rtc.last=performance.now();
    let socket=scopeSocket;scopeSocket=null;scopeSocketLast=0;scopeSocketChannels='';if(socket)socket.close();
  };
  pc.onconnectionstatechange=function() {
    if (scopeRtc === rtc && (pc.connectionState==='failed' || pc.connectionState==='closed')) scopeRtcStop(true);
  };
  let timer=setTimeout(()=>rtc.controller.abort(),10000);
  try {
    await pc.setLocalDescription(await pc.createOffer());
    if (pc.iceGatheringState !== 'complete') {
      await new Promise((resolve,reject)=> {
        let wait=setTimeout(()=>{pc.onicegatheringstatechange=null;reject(new Error('ICE timeout'));},4000);
        pc.onicegatheringstatechange=function(){if(pc.iceGatheringState==='complete'){clearTimeout(wait);pc.onicegatheringstatechange=null;resolve();}};
      });
    }
    if (scopeRtc !== rtc) return;
    let response=await fetch(new URL('api/scope/offer',window.location.href), {method:'POST',
      headers:{'Content-Type':'application/json'},signal:rtc.controller.signal,
      body:JSON.stringify({sdp:pc.localDescription.sdp,type:'offer',channels:channels,max_points:FRAME_MAX_POINTS,compression:!!window.DecompressionStream})});
    if (!response.ok) throw new Error('Display negotiation '+response.status);
    let answer=await response.json();
    if (scopeRtc !== rtc) return;
    rtc.lease=answer.lease;
    await pc.setRemoteDescription({sdp:answer.sdp,type:answer.type});
    rtc.leaseTimer=setInterval(()=>scopeRtcLease(rtc),10000);
  } catch(e) {
    if(scopeRtc===rtc){scopeSocketEvents.push({at:performance.now(),kind:'rtc',error:String(e)});if(scopeSocketEvents.length>24)scopeSocketEvents.shift();scopeRtcStop(true);}
  } finally { clearTimeout(timer); }
}
function scopeLiveConnect() {
  if (!scopeLiveEligible()) {scopeLiveStop();return;}
  let channels=frameChannels(),now=performance.now();
  if (scopeRtc && (scopeRtc.channels!==channels || now-scopeRtc.last>10000)) scopeRtcStop(false);
  if (!scopeRtc && window.RTCPeerConnection && now>=scopeRtcRetryAt) scopeRtcStart(channels).catch(()=>scopeRtcStop(true));
  if (scopeRtcHealthy()) return;
  if (scopeRtc && now-scopeRtc.started<5000) return;
  scopeWebSocketConnect();
}

function cancelScopeFrames() {
  displayGeneration++; pendingFrame = null; liveFrames = [];
  liveOffset = null; liveSource = 0; livePlayAt = 0;
  if (framePaint !== null) cancelAnimationFrame(framePaint);
  framePaint = null;
  clearScopePaintWake();
  if (currentFrameController) currentFrameController.abort();
}
function clearScopePaintWake() {
  if (framePaintWake !== null) clearTimeout(framePaintWake);
  framePaintWake = null;
}
function requestScopePaint() {
  if (framePaint === null) framePaint = requestAnimationFrame(scopePaintTick);
  if (framePaintWake !== null) return;
  let now = performance.now(), due = pendingFrame ? now : livePlayAt;
  framePaintWake = setTimeout(function() {
    framePaintWake = null;
    if (framePaint !== null) cancelAnimationFrame(framePaint);
    framePaint = null;
    scopePaintTick(performance.now());
  }, Math.max(1, due - now + 4));
}
function stopScopeDisplay() {
  leavingPage = true;
  if (refreshTimer !== null) clearInterval(refreshTimer);
  if (frameTimer !== null) clearInterval(frameTimer);
  refreshTimer = frameTimer = null;
  cancelScopeFrames(); scopeLiveStop();
  if (currentRefreshController) currentRefreshController.abort();
}
function scopePaintTick(now) {
  framePaint = null;
  if (leavingPage || document.hidden || playerState.playback) { clearScopePaintWake(); return; }
  if (pendingFrame) {
    let item = pendingFrame; pendingFrame = null;
    if (item.generation === displayGeneration) applyFrame(item.frame, item.allowFrozen);
  }
  if (freezeMode !== 'live' || !liveFrames.length) { clearScopePaintWake(); return; }
  if (!livePlayAt) livePlayAt = now + SCOPE_JITTER_MS;
  if (now >= livePlayAt) {
    // Keep a short reserve to cover uneven delivery, dropping excess history.
    while (liveFrames.length > 3) liveFrames.shift();
    let ready = liveFrames.shift();
    if (ready && ready.generation === displayGeneration) {
      applyFrame(ready.frame, false);
      livePlayAt = now + SCOPE_PLAYOUT_MS;
      clearScopePaintWake();
    }
  }
  if (liveFrames.length) requestScopePaint();
  else clearScopePaintWake();
}
function queueScopeFrame(f, allowFrozen, generation) {
  if (generation !== displayGeneration || !f || f.busy) return;
  if (allowFrozen || !f.available || !Number.isFinite(Number(f.last_frame_t)) || !f.last_frame_t) {
    pendingFrame = {frame: f, allowFrozen: allowFrozen, generation: generation};
  } else {
    let now = performance.now(), source = Number(f.last_frame_t);
    // Unordered UDP may deliver an older frame after a newer one.
    if (source < liveSource || (source===liveSource && !f._scopeRevision)) return;
    if(source===liveSource && liveFrames.length && liveFrames[liveFrames.length-1].frame.last_frame_t===source) {
      liveFrames[liveFrames.length-1].frame=f;return;
    }
    if (now - liveArrival > 1000) {
      liveFrames = []; liveOffset = null; livePlayAt = 0;
    }
    liveSource = source; liveArrival = now;
    if (!livePlayAt) livePlayAt = now + SCOPE_JITTER_MS;
    let offset = now - source * 1000;
    liveOffset = liveOffset === null ? offset : Math.min(liveOffset, offset);
    liveFrames.push({frame: f, generation: generation});
    while (liveFrames.length > SCOPE_QUEUE_MAX) liveFrames.shift();
  }
  requestScopePaint();
}

function refreshFrames() {
  if (leavingPage || document.hidden || frameInFlight || playerState.playback ||
      freezeMode === 'freeze' || (freezeMode === 'auto' && !autoFreezeRequest)) return Promise.resolve();
  if (freezeMode === 'live') {
    scopeLiveConnect();
    if (scopeLiveHealthy()) return Promise.resolve();
  }
  if (!lastStatus) return Promise.resolve();
  frameInFlight = true;
  let generation = displayGeneration, channels = frameChannels(), allowFrozen = freezeMode === 'auto';
  let ctl = window.AbortController ? new AbortController() : null;
  currentFrameController = ctl;
  let timeout = ctl ? setTimeout(function() { ctl.abort(); }, SCOPE_REQUEST_TIMEOUT_MS) : null;
  let opts = ctl ? {signal: ctl.signal, cache: 'no-store'} : {cache: 'no-store'};
  return fetchFrame(opts).then(function(f) {
    if (generation !== displayGeneration || channels !== frameChannels() ||
        leavingPage || document.hidden || playerState.playback || f.busy || scopeLiveHealthy()) return;
    queueScopeFrame(f, allowFrozen, generation);
  }).catch(function(e) {
    if (generation !== displayGeneration || leavingPage || e.name === 'AbortError' || scopeLiveHealthy()) return;
    setText('serviceState', 'error'); setText('conn', String(e));
  }).finally(function() {
    if (timeout !== null) clearTimeout(timeout);
    frameInFlight = false;
    if (currentFrameController === ctl) currentFrameController = null;
  });
}
function refreshStatus(force) {
  let rtc=scopeRtcHealthy();
  if (leavingPage || refreshInFlight || document.hidden || (!force && scopeLiveHealthy() && !rtc) ||
      (!force && rtc && performance.now()-scopeHttpStatusAt<1000)) return Promise.resolve();
  scopeHttpStatusAt=performance.now();
  refreshInFlight = true;
  let ctl = window.AbortController ? new AbortController() : null;
  currentRefreshController = ctl;
  let timeout = ctl ? setTimeout(function() { ctl.abort(); }, SCOPE_REQUEST_TIMEOUT_MS) : null;
  let opts = ctl ? {signal: ctl.signal, cache: 'no-store'} : {cache: 'no-store'};
  return fetch('/api/status', opts).then(function(r) {
    if (!r.ok) throw new Error('status ' + r.status);
    return r.json();
  }).then(function(s) {
    if (leavingPage || document.hidden || (!force && scopeLiveHealthy() && !scopeRtcHealthy())) return;
    applyStatus(s);
    if (!playerState.playback && freezeMode === 'auto') autoFreezeNeedsFrame((s || {}).detector || {});
    if (freezeMode === 'live' || (freezeMode === 'auto' && autoFreezeRequest)) refreshFrames();
    else redrawHeldFrameIfNeeded();
  }).catch(function(e) {
    if (leavingPage || e.name === 'AbortError' || scopeLiveHealthy()) return;
    setText('serviceState', 'error'); setText('conn', String(e));
  }).finally(function() {
    if (timeout !== null) clearTimeout(timeout);
    refreshInFlight = false;
    if (currentRefreshController === ctl) currentRefreshController = null;
  });
}
function refresh() { return Promise.all([refreshStatus(true), refreshFrames()]); }

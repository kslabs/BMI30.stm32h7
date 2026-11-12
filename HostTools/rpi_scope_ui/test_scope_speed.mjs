import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
}
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness(scheduler) {
  const status = [], frames = [], painted = [], texts = [], raf = new Map(), timers = new Map();
  let handle = 0;
  const context = vm.createContext({
    window: {AbortController}, AbortController, document: {hidden: false},
    leavingPage: false, refreshTimer: null, refreshInFlight: false, currentRefreshController: null,
    lastStatus: {mode: {selected: 6}}, playerState: {playback: false}, freezeMode: 'live',
    autoFreezeRequest: null, channels: 'both', redraws: 0,
    frameChannels() { return context.channels; },
    fetch(url, opts) { const d = deferred(); status.push({url, opts, ...d}); return d.promise; },
    fetchFrame(opts) { const d = deferred(); frames.push({opts, ...d});
      opts.signal?.addEventListener('abort', () => d.reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
      return d.promise;
    },
    applyStatus(s) { context.lastStatus = s; },
    autoFreezeNeedsFrame() { return !!context.autoFreezeRequest; },
    applyFrame(f, allowFrozen) { painted.push({f, allowFrozen}); },
    redrawHeldFrameIfNeeded() { context.redraws++; },
    setText(...args) { texts.push(args); },
    requestAnimationFrame(fn) { raf.set(++handle, fn); return handle; },
    cancelAnimationFrame(id) { raf.delete(id); },
    setTimeout(fn) { timers.set(++handle, fn); return handle; },
    clearTimeout(id) { timers.delete(id); }, clearInterval(id) { timers.delete(id); },
  });
  vm.runInContext(scheduler, context);
  return {context, status, frames, painted, texts, raf, timers,
    run(code) { return vm.runInContext(code, context); },
    paint() { for (const [id, fn] of [...raf]) { raf.delete(id); fn(); } },
  };
}

export default async function testScopeSpeed(schedulerPath, pageScriptPath) {
  const scheduler = await readFile(schedulerPath, 'utf8');
  const page = await readFile(pageScriptPath, 'utf8');
  new vm.Script(page); // Parse the actual decoded SERVICE_HTML script.
  const cases = [];
  let h = harness(scheduler);
  h.run('refresh()');
  assert.equal(h.status.length, 1);
  assert.equal(h.frames.length, 1);
  h.frames[0].resolve({available: true, last_frame_t: 1}); await flush(); h.paint();
  h.run('refreshFrames()');
  h.frames[1].resolve({available: true, last_frame_t: 2}); await flush(); h.paint();
  assert.equal(h.painted.length, 2, 'a stalled status response must not stall the waveform');
  assert.equal(h.status.length, 1);
  cases.push('waveform continues while status is slow');

  h = harness(scheduler);
  for (let i = 0; i < 50; i++) h.run('refreshFrames(); refreshStatus()');
  assert.equal(h.frames.length, 1); assert.equal(h.status.length, 1);
  cases.push('one in-flight request per endpoint');

  h = harness(scheduler);
  h.run('refreshFrames()'); h.frames[0].resolve({last_frame_t: 1}); await flush();
  h.run('refreshFrames()'); h.frames[1].resolve({last_frame_t: 2}); await flush();
  assert.equal(h.raf.size, 1); h.paint(); assert.equal(h.painted[0].f.last_frame_t, 2);
  cases.push('draw newest frame once per animation callback');

  h = harness(scheduler);
  h.run('refreshFrames(); cancelScopeFrames(); freezeMode="freeze"; refreshFrames()');
  await flush(); h.paint(); assert.equal(h.painted.length, 0); assert.equal(h.frames.length, 1);
  cases.push('Freeze discards late live response');

  h = harness(scheduler);
  h.run('refreshFrames()'); h.context.channels = 'upper';
  h.frames[0].resolve({last_frame_t: 1}); await flush(); h.paint();
  assert.equal(h.painted.length, 0);
  cases.push('mode change discards previous channel response');

  h = harness(scheduler); h.context.freezeMode = 'auto';
  h.run('refreshFrames()'); assert.equal(h.frames.length, 0);
  h.context.autoFreezeRequest = {seq: 7}; h.run('refreshFrames()');
  h.frames[0].resolve({exact_event_frame: true, event_snapshot: {seq: 7}}); await flush(); h.paint();
  assert.equal(h.painted[0].allowFrozen, true);
  cases.push('AutoFreeze requests only pending exact event');

  h = harness(scheduler);
  h.run('refreshFrames()'); h.frames[0].resolve({last_frame_t: 1}); await flush();
  h.context.playerState.playback = true; h.paint(); h.run('refreshFrames()');
  assert.equal(h.painted.length, 0); assert.equal(h.frames.length, 1);
  cases.push('Player prevents queued live frames replacing playback');

  h = harness(scheduler); h.context.document.hidden = true;
  h.run('refresh()'); assert.equal(h.frames.length, 0); assert.equal(h.status.length, 0);
  h.context.document.hidden = false; h.run('refreshFrames()');
  h.frames[0].resolve({last_frame_t: 1}); await flush();
  h.run('stopScopeDisplay(); refresh()'); h.paint();
  assert.equal(h.painted.length, 0); assert.equal(h.frames.length, 1); assert.equal(h.raf.size, 0);
  cases.push('hidden tab and navigation stop polling and pending paints');

  h = harness(scheduler); h.run('refreshFrames()');
  [...h.timers.values()][0](); await flush(); h.run('refreshFrames()');
  assert.equal(h.frames.length, 2); assert.equal(h.texts.length, 0);
  cases.push('request timeout permits recovery without request backlog');

  h = harness(scheduler); h.context.lastStatus = null;
  h.run('refresh()'); assert.equal(h.frames.length, 0);
  h.status[0].resolve({ok: true, json: async () => ({mode: {selected: 6}})}); await flush();
  assert.equal(h.frames.length, 1);
  cases.push('first frame waits for initial mode then starts immediately');

  // Confirm the page actually wires separate loops and slower numeric readouts.
  assert.match(page, /setInterval\(refreshFrames,SCOPE_FRAME_INTERVAL_MS\)/);
  assert.match(page, /setInterval\(refreshStatus,SCOPE_STATUS_INTERVAL_MS\)/);
  assert.match(page, /readoutNow-lastFrameReadoutAt>=SCOPE_STATUS_INTERVAL_MS/);
  assert.match(page, /pagehide',stopScopeDisplay/);

  // Exercise the real renderer rather than a duplicate implementation.
  let now = 0;
  const rendering = vm.createContext({
    window: {}, document: {getElementById: id => ({id})},
    performance: {now: () => now}, draws: [], readouts: [], layouts: [], legends: [],
  });
  const pageWithoutStartup = page.slice(page.indexOf('const MODE_LABELS')).split('\n')
    .filter(line => !line.trimStart().startsWith('initTheme();') &&
      !line.startsWith("document.addEventListener('visibilitychange'") &&
      !line.startsWith("window.addEventListener('pagehide'")) .join('\n');
  vm.runInContext(pageWithoutStartup, rendering);
  vm.runInContext(`
    drawPlot=(...args)=>draws.push(args);
    updateNormReadouts=(...args)=>readouts.push(args);
    applyModeVisibility=(mode)=>layouts.push(mode);
    setScopeLegend=(...args)=>legends.push(args);
    paintAutoFreezeSources=()=>{};
    lastStatus={mode:{selected:6},detector:{},normalization:{enabled:false}};
  `, rendering);
  for (let i = 0; i < 25; i++) {
    now = i * 40;
    vm.runInContext(`renderFrame({available:true,last_frame_t:${i+1},
      data0_even:[1,2,3],data0_odd:[3,2,1],prod0:[0,20,0],
      data1_even:[4,5,6],data1_odd:[6,5,4],prod1:[0,30,0]},false)`, rendering);
  }
  assert.equal(rendering.draws.length, 50, 'both waveforms keep the fast cadence');
  assert.equal(rendering.readouts.length, 2, 'numeric readouts use the slower cadence');
  assert.equal(rendering.layouts.length, 1); assert.equal(rendering.legends.length, 2);
  vm.runInContext('renderFrame(lastFrame,true)', rendering);
  assert.equal(rendering.readouts.length, 3, 'manual redraw updates numbers immediately');
  cases.push('real renderer draws 25 times while readouts update twice');

  const previousDraws = rendering.draws.length;
  vm.runInContext('freezeMode="freeze"; applyFrame({available:true,last_frame_t:90})', rendering);
  assert.equal(rendering.draws.length, previousDraws);
  vm.runInContext(`freezeMode="auto"; autoFreezeRequest={kind:'detection',ch:'upper',seq:7,
    baseline:{upper:7,lower:0},reviewBaseline:0};
    applyFrame({available:true,last_frame_t:91,exact_event_frame:true,
      _reviewQuery:'&fire_seq0=7',event_snapshot:{seq:6}},true);`, rendering);
  assert.equal(rendering.draws.length, previousDraws, 'wrong event is never painted');
  vm.runInContext(`applyFrame({available:true,last_frame_t:92,exact_event_frame:true,
    _reviewQuery:'&fire_seq0=7',event_snapshot:{seq:7},data0_even:[1,2,3]},true)`, rendering);
  assert.equal(rendering.draws.length, previousDraws + 2);
  assert.equal(vm.runInContext('autoFreezeRequest', rendering), null);
  cases.push('real Freeze and AutoFreeze reject live and mismatched event frames');
  return {passed: cases.length, cases, frameIntervalMs: 40, statusIntervalMs: 500};
}

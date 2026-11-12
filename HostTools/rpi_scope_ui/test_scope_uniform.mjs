import vm from 'node:vm';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
function harness(code) {
  let now=0, serial=0; const raf=new Map(), timers=new Map(), deadlines=new Map(), sockets=[], requests=[], paints=[];
  class Socket {
    constructor(url) { this.url=url;this.readyState=0;sockets.push(this); }
    close() { this.readyState=3;this.onclose?.(); }
    push(data) { this.readyState=1;this.onmessage?.({data}); }
  }
  const context=vm.createContext({
    URL,WebSocket:Socket,AbortController,window:{WebSocket:Socket,AbortController,location:{href:'http://device/portal-oscilloscope'}},
    document:{hidden:false},performance:{now:()=>now},
    leavingPage:false,refreshTimer:null,refreshInFlight:false,currentRefreshController:null,
    lastStatus:{mode:{selected:6}},playerState:{playback:false},freezeMode:'live',autoFreezeRequest:null,FRAME_MAX_POINTS:600,
    frameChannels(){return context.lastStatus?.mode?.selected===1?'upper':'both';},
    applyFrame(f){paints.push({at:now,frame:f});},applyStatus(s){context.lastStatus=s;},
    parseFrameBinary:f=>f,autoFreezeNeedsFrame(){return !!context.autoFreezeRequest;},redrawHeldFrameIfNeeded(){},setText(){},
    fetchFrame(opts){requests.push('frame');return new Promise((resolve,reject)=>opts.signal?.addEventListener('abort',()=>reject(Object.assign(new Error('abort'),{name:'AbortError'}))));},
    fetch(){requests.push('status');return Promise.resolve({ok:true,json:async()=>({mode:{selected:6}})});},
    requestAnimationFrame(fn){raf.set(++serial,fn);return serial;},cancelAnimationFrame:id=>raf.delete(id),
    setTimeout(fn,ms=0){timers.set(++serial,fn);deadlines.set(serial,now+ms);return serial;},
    clearTimeout(id){timers.delete(id);deadlines.delete(id);},clearInterval(){},
  });
  vm.runInContext(code,context);
  return {context,sockets,paints,requests,timers,raf,
    run:s=>vm.runInContext(s,context),
    tick(t){now=t;for(const [id,fn] of [...raf]){raf.delete(id);fn(now);}},
    clock(t){now=t;for(const [id,fn] of [...timers])if(deadlines.get(id)<=now){timers.delete(id);deadlines.delete(id);fn();}},
    put(f){context.incoming=f;vm.runInContext('queueScopeFrame(incoming,false,displayGeneration)',context);},
  };
}
export default async function runUniformTests(file,pageFile) {
  const code=await readFile(file,'utf8');new vm.Script(await readFile(pageFile,'utf8'));
  const cases=[];
  let h=harness(code), arrivals=[];
  for(let i=0;i<64;i++) arrivals.push({at:i*62.5+[20,60,30,70,40][i%5],f:{available:true,last_frame_t:100+i*.0625,prod0:[i,0,i]}});
  arrivals.sort((a,b)=>a.at-b.at);
  for(let t=0;t<4300;t+=4){while(arrivals.length&&arrivals[0].at<=t)h.put(arrivals.shift().f);h.tick(t);}
  const gaps=h.paints.slice(1).map((p,i)=>p.at-h.paints[i].at);
  assert(h.paints.length>=30,`too few paints: ${h.paints.length}`);
  assert(gaps.every(x=>x>=116&&x<=124),`uneven buffered cadence: ${gaps}`);
  for(const p of h.paints)assert.equal(p.frame.prod0[0],Math.round((p.frame.last_frame_t-100)/.0625));
  cases.push('jittery arrival produces regular 116-124 ms paints of exact received arrays');

  h=harness(code);arrivals=[];
  for(let i=0;i<36;i++)arrivals.push({at:i*120+[20,60,30,70,40][i%5],f:{available:true,last_frame_t:100+i*.120,prod0:[i]}});
  for(let t=0;t<4300;t+=4){while(arrivals.length&&arrivals[0].at<=t)h.put(arrivals.shift().f);h.tick(t);}
  assert(h.paints.length>=33);
  assert(h.paints.slice(1).every((p,i)=>p.at-h.paints[i].at>=116&&p.at-h.paints[i].at<=124));
  cases.push('120 ms source cadence stays uniform with arrival jitter');

  h=harness(code);h.put({available:true,last_frame_t:100,prod0:[1]});h.put({available:true,last_frame_t:101,prod0:[2]});
  h.put({available:true,last_frame_t:100.5,prod0:[3]});
  assert.equal(h.run('liveSource'),101);assert.equal(h.run('liveFrames.length'),2);
  cases.push('unordered old UDP frame cannot replace newer data or reset the display clock');

  h=harness(code);h.put({available:true,last_frame_t:100,_scopeRevision:1,prod0:[7]});
  h.put({available:true,last_frame_t:100,_scopeRevision:2,prod0:[7],prod1:[8]});
  assert.equal(h.run('liveFrames.length'),1);h.tick(4);h.tick(200);
  assert.equal(h.paints[0].frame.prod1[0],8);
  h.put({available:true,last_frame_t:100,_scopeRevision:3,prod0:[7],prod1:[9]});h.tick(400);
  assert.equal(h.paints[1].frame.prod1[0],9);
  cases.push('independent channel arrival coalesces pending frame and can update a previously painted companion channel');

  h=harness(code);h.tick(2000);
  for(let i=0;i<64;i++)h.put({available:true,last_frame_t:100+i*.025,prod0:[i]});
  assert.equal(h.run('liveFrames.length'),8);
  for(let t=2000;t<2500;t+=4)h.tick(t);
  assert(h.paints.slice(1).every((p,i)=>p.at-h.paints[i].at>=116));
  assert(h.paints.length<=3,'a transport burst must not replay a long backlog');
  cases.push('large transport burst stays bounded and never causes a catch-up burst');

  h=harness(code);
  for(let t=0;t<2000;t+=4){if(t%60===0)h.put({available:true,last_frame_t:100+t/1000,prod0:[t]});if(t<600||t>900)h.tick(t);}
  assert(h.paints.slice(1).every((p,i)=>p.at-h.paints[i].at>=120));
  cases.push('late browser animation tick cannot produce rapid catch-up paints');

  h=harness(code);
  for(let t=0;t<3000;t+=4){h.clock(t);if(t%120===0)h.put({available:true,last_frame_t:100+t/1000,prod0:[t]});}
  assert(h.paints.length>=22,'received data must paint even when animation callbacks are suspended');
  assert(h.paints.slice(1).every((p,i)=>p.at-h.paints[i].at>=120&&p.at-h.paints[i].at<=128));
  assert(h.raf.size<=1&&h.timers.size<=1,'only one animation request and one deadline timer may wait');
  for(const p of h.paints)assert.equal(p.frame.prod0[0],Math.round((p.frame.last_frame_t-100)*1000));
  h.run('cancelScopeFrames();freezeMode="freeze"');h.clock(5000);
  assert.equal(h.timers.size,0);assert.equal(h.raf.size,0);
  cases.push('deadline timer preserves bounded cadence of exact frames while Chrome delays animation callbacks; Freeze cancels both clocks');

  h=harness(code);h.put({available:true,last_frame_t:1});h.run('cancelScopeFrames();freezeMode="freeze"');h.tick(200);
  assert.equal(h.paints.length,0);assert.equal(h.raf.size,0);
  cases.push('Freeze invalidates buffered waveform');

  h=harness(code);h.context.freezeMode='auto';h.context.incoming={available:true,last_frame_t:1,exact_event_frame:true};
  h.run('queueScopeFrame(incoming,true,displayGeneration)');h.tick(4);assert.equal(h.paints.length,1);
  cases.push('AutoFreeze bypasses live buffer for exact event');

  h=harness(code);h.run('scopeLiveConnect()');let socket=h.sockets[0];
  socket.push(JSON.stringify({mode:{selected:6}}));h.run('refreshFrames();refreshStatus()');await flush();
  assert.deepEqual(h.requests,[]);
  assert.equal(new URL(socket.url).protocol,'ws:');assert.equal(new URL(socket.url).pathname,'/api/scope/live');
  socket.push({available:true,last_frame_t:100,prod0:[7]});
  for(let t=0;t<250;t+=4)h.tick(t);
  assert.equal(h.paints[0].frame.prod0[0],7);
  cases.push('healthy WebSocket supplies status and waveform without repeated HTTP requests');

  h=harness(code);h.run('scopeLiveConnect()');socket=h.sockets[0];h.context.lastStatus={mode:{selected:1}};h.run('scopeLiveConnect()');
  assert.equal(socket.readyState,3);assert.equal(h.sockets.length,2);
  socket.push({available:true,last_frame_t:100});h.tick(500);assert.equal(h.paints.length,0);
  assert.equal(new URL(h.sockets[1].url).searchParams.get('channels'),'upper');
  cases.push('channel change reconnects stream and rejects late previous socket messages');

  h=harness(code);h.run('scopeLiveConnect()');socket=h.sockets[0];socket.push(JSON.stringify({mode:{selected:6}}));
  h.context.document.hidden=true;h.run('cancelScopeFrames();scopeLiveStop();refreshFrames();refreshStatus()');
  assert.equal(socket.readyState,3);assert.deepEqual(h.requests,[]);
  cases.push('hidden page closes live transport and stops polling');

  h=harness(code);h.run('scopeLiveConnect()');socket=h.sockets[0];socket.close();h.run('refreshFrames()');
  assert.deepEqual(h.requests,['frame']);for(const fn of [...h.timers.values()])fn();await flush();
  h.run('refreshFrames()');assert.equal(h.requests.length,2);
  cases.push('closed stream falls back to bounded recoverable HTTP');

  h=harness(code);h.run('scopeLiveConnect();stopScopeDisplay()');assert.equal(h.sockets[0].readyState,3);
  h.run('refreshFrames();refreshStatus()');assert.deepEqual(h.requests,[]);
  cases.push('navigation closes socket and both display loops');
  return {passed:cases.length,cases,playoutMs:120,jitterBufferMs:180};
}

export async function replayArrivals(file, arrivals) {
  let h=harness(await readFile(file,'utf8')), items=arrivals.map(x=>({...x}));
  const start=items[0].at, end=items[items.length-1].at;
  for(let at=start;at<end;at+=4){while(items.length&&items[0].at<=at){let x=items.shift();h.put({available:true,last_frame_t:x.source});}h.tick(at);}
  let gaps=h.paints.slice(1).map((p,i)=>p.at-h.paints[i].at);
  return {paints:h.paints.length,fps:h.paints.length/(end-start)*1000,regularPercent:100*gaps.filter(x=>x>=116&&x<=128).length/gaps.length,
    gaps:gaps.sort((a,b)=>a-b),maxMs:Math.max(...gaps)};
}

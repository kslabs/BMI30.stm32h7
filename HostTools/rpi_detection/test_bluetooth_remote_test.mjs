import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';

const source=await fs.readFile(new URL('./bluetooth_remote_test.js',import.meta.url),'utf8');
const mac='FF:24:01:26:A0:4A';
const device={address:mac,name:'T33',connected:true,paired:true,hid_capable:true,uuid_codes:['1812']};
const flush=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};

function harness() {
  const w=new Window({url:'http://bmi30.test/portal#bluetooth'}),d=w.document;
  d.write('<html lang="en"><body><section id="panel-bluetooth" class="portal-panel is-active" data-bluetooth-root><div data-bt-devices></div></section></body></html>');
  let now=0,hidden=false,serial=0;
  const timers=new Map(),requests=[];
  Object.defineProperty(d,'hidden',{get:()=>hidden,configurable:true});
  Object.defineProperty(w.performance,'now',{value:()=>now,configurable:true});
  w.setTimeout=(fn,delay=0)=>{const id=++serial;timers.set(id,{fn,at:now+Number(delay)});return id;};
  w.clearTimeout=id=>timers.delete(id);
  w.fetch=(url,opts={})=>{
    const item={url:String(url),opts,done:false};requests.push(item);
    if(item.url.endsWith('/stop')) {item.done=true;return Promise.resolve({ok:true,json:async()=>({ok:true,status:'stopped'})});}
    return new Promise((resolve,reject)=>{
      item.resolve=(data,status=200)=>{item.done=true;resolve({ok:status>=200&&status<300,json:async()=>data});};
      opts.signal?.addEventListener('abort',()=>{item.done=true;reject(Object.assign(new Error('aborted'),{name:'AbortError'}));});
    });
  };
  w.eval(source);
  const list=d.querySelector('[data-bt-devices]');
  function render(input=device){list.innerHTML='<article class="bluetooth-device bluetooth-device--remote"><div class="bluetooth-device-main">T33</div>'+w.BMI30RemoteTest.card(input)+'</article>';w.BMI30RemoteTest.refresh();}
  render();
  const key=name=>d.querySelector('[data-bt-remote-key="'+name+'"]');
  const click=name=>d.querySelector('[data-bt-remote-action="'+name+'"]').click();
  const polls=()=>requests.filter(r=>r.url.includes('/events?'));
  const stopRequests=()=>requests.filter(r=>r.url.endsWith('/stop'));
  async function advance(ms) {
    const target=now+ms;
    for(let guard=0;guard<1000;guard++){
      const next=[...timers].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();
    }
    now=target;await flush();
  }
  async function begin(data={}){click('start');await flush();requests.at(-1).resolve({ok:true,test_id:'test-1',cursor:0,connected:true,ready:true,status:'ready',server_at:1000,...data});await flush();}
  async function reply(data={}){const p=polls().filter(r=>!r.done).at(-1);assert.ok(p,'pending poll');p.resolve({ok:true,test_id:'test-1',cursor:0,connected:true,ready:true,status:'ready',server_at:1000,events:[],...data});await flush();}
  const event=(seq,button='center',extra={})=>({seq,button,phase:'complete',complete:true,pressed:false,at:999.95,...extra});
  return {w,d,requests,render,key,click,polls,stopRequests,advance,begin,reply,event,
    async hidden(value){hidden=value;d.dispatchEvent(new w.Event('visibilitychange'));await flush();},
    async close(){w.dispatchEvent(new w.Event('pagehide'));await flush();await w.happyDOM.abort();}};
}

const passed=[];
async function test(name,run){const h=harness();try{await run(h);passed.push(name);}finally{await h.close();}}

await test('selected Test or Stop mode replaces the stopped caption',async h=>{
  const control=name=>h.d.querySelector('[data-bt-remote-action="'+name+'"]');
  const selected=(testing)=>{
    assert.equal(control('start').classList.contains('is-selected'),testing);
    assert.equal(control('stop').classList.contains('is-selected'),!testing);
    assert.equal(control('start').getAttribute('aria-pressed'),String(testing));
    assert.equal(control('stop').getAttribute('aria-pressed'),String(!testing));
  };
  selected(false);
  assert.equal(h.d.querySelector('[data-bt-remote-status]').textContent,'');
  await h.begin();selected(true);
  await h.reply({cursor:1,events:[h.event(1,'center')]});selected(true);
  assert.ok(h.key('center').classList.contains('is-received'));
  h.click('stop');await flush();selected(false);
  assert.equal(h.d.querySelector('[data-bt-remote-status]').textContent,'');
  h.render();selected(false);
  await h.begin();selected(true);
  await h.reply({connected:false,ready:false,status:'disconnected'});selected(false);
});

await test('local indicator clicks never create device events',async h=>{
  h.key('center').dispatchEvent(new h.w.MouseEvent('click',{bubbles:true}));await flush();
  assert.equal(h.requests.length,0);assert.equal(h.key('center').classList.contains('is-received'),false);
  assert.equal(h.key('center').querySelector('small').textContent,'0');
});
await test('startup opening race waits for readiness',async h=>{
  await h.begin({connected:false,ready:false,status:'starting'});
  await h.reply({connected:false,ready:false,status:'starting'});
  assert.equal(h.stopRequests().length,0);assert.match(h.d.querySelector('[data-bt-remote-status]').textContent,/Starting/);
  await h.advance(100);await h.reply({connected:true,ready:true,status:'ready'});
  assert.equal(h.stopRequests().length,0);assert.match(h.d.querySelector('[data-bt-remote-status]').textContent,/Press buttons/);
});
await test('real completed events flash and duplicate cursors do not recount',async h=>{
  await h.begin();await h.reply({cursor:1,events:[h.event(1,'up')]});
  assert.ok(h.key('up').classList.contains('is-received'));assert.equal(h.key('up').querySelector('small').textContent,'1');
  await h.advance(100);await h.reply({cursor:1,events:[h.event(1,'up')]});
  assert.equal(h.key('up').querySelector('small').textContent,'1');
  await h.advance(219);assert.ok(h.key('up').classList.contains('is-received'));
  await h.advance(1);assert.equal(h.key('up').classList.contains('is-received'),false);
});
await test('a fast first press in the start response is retained exactly once',async h=>{
  await h.begin({cursor:1,events:[h.event(1,'center')]});
  assert.equal(h.key('center').querySelector('small').textContent,'1');
  assert.ok(h.key('center').classList.contains('is-received'));
  assert.match(h.polls()[0].url,/cursor=1$/);
  await h.reply({cursor:1,events:[h.event(1,'center')]});
  assert.equal(h.key('center').querySelector('small').textContent,'1');
});
await test('one poll in flight and authenticated cursor advances',async h=>{
  await h.begin();await h.advance(900);assert.equal(h.polls().length,1);
  assert.equal(h.polls()[0].opts.headers['X-BMI30-Bluetooth-Test'],'1');
  assert.equal(h.polls()[0].opts.credentials,'same-origin');
  await h.reply({cursor:1,events:[h.event(1)]});await h.advance(100);
  assert.match(h.polls().at(-1).url,/cursor=1$/);
});
await test('stale complete events count history without flashing now',async h=>{
  await h.begin();await h.reply({cursor:1,events:[h.event(1,'left',{at:980})]});
  assert.equal(h.key('left').classList.contains('is-received'),false);
  assert.equal(h.key('left').querySelector('small').textContent,'1');
  assert.match(h.d.querySelector('[data-bt-remote-age]').textContent,/20\.0/);
});
await test('unmapped ConsumerControl and incomplete held input never guess an arrow',async h=>{
  await h.begin();await h.reply({cursor:1,events:[h.event(1,null,{complete:false,phase:'diagnostic',source:'raw',code:115,value:1})]});
  assert.equal(h.d.querySelectorAll('.is-received').length,0);
  assert.match(h.d.querySelector('[data-bt-remote-other]').textContent,/1/);
  await h.advance(100);await h.reply({cursor:2,events:[h.event(2,'right',{complete:false,phase:'diagnostic',pressed:true})]});
  await h.advance(1000);assert.equal(h.d.querySelectorAll('.is-received').length,0);
});
await test('input not ready cannot highlight even a complete event',async h=>{
  await h.begin({connected:false,ready:false,status:'starting'});
  await h.reply({ready:false,status:'starting',cursor:1,events:[h.event(1,'down')]});
  assert.equal(h.d.querySelectorAll('.is-received').length,0);assert.equal(h.key('down').querySelector('small').textContent,'0');
});
await test('card refresh preserves session, counts, flashes and a single handler',async h=>{
  await h.begin();await h.reply({cursor:1,events:[h.event(1,'center')]});h.render();
  assert.equal(h.key('center').querySelector('small').textContent,'1');assert.ok(h.key('center').classList.contains('is-received'));
  assert.equal(h.d.querySelector('[data-bt-remote-action="start"]').disabled,true);
  h.click('stop');await flush();assert.equal(h.stopRequests().length,1);
  h.render();h.click('start');await flush();assert.equal(h.requests.filter(r=>r.url.endsWith('/start')).length,2);
});
await test('overflow fails closed instead of silently accepting missed events',async h=>{
  await h.begin();await h.reply({cursor:3,lost_events:1,history_start_seq:3,events:[h.event(3)]});
  assert.equal(h.d.querySelectorAll('.is-received').length,0);assert.equal(h.stopRequests().length,1);
  assert.match(h.d.querySelector('[data-bt-remote-status]').textContent,/lost/);
});
await test('expired ok response releases and stops polling',async h=>{
  await h.begin();await h.reply({status:'expired',connected:true,ready:false});await h.advance(2000);
  assert.equal(h.stopRequests().length,1);assert.equal(h.polls().length,1);
});
await test('page hidden stops with keepalive and stale poll cannot flash',async h=>{
  await h.begin();const old=h.polls()[0];await h.hidden(true);
  old.resolve({ok:true,test_id:'test-1',cursor:1,connected:true,ready:true,server_at:1000,events:[h.event(1)]});await flush();
  assert.equal(h.d.querySelectorAll('.is-received').length,0);assert.equal(h.stopRequests().length,1);
  assert.equal(h.stopRequests()[0].opts.keepalive,true);
  await h.hidden(false);await h.advance(1000);assert.equal(h.polls().length,1);
});
await test('same-page panel navigation releases the input session',async h=>{
  await h.begin();h.d.getElementById('panel-bluetooth').classList.remove('is-active');await flush();
  await h.w.happyDOM.whenAsyncComplete();await flush();assert.equal(h.stopRequests().length,1);
});
await test('disconnected HID remains visible and non-HID does not get a widget',async h=>{
  h.render({...device,connected:false});assert.equal(h.d.querySelector('[data-bt-remote-action="start"]').disabled,true);
  assert.equal(h.w.BMI30RemoteTest.card({address:mac,connected:true,audio_capable:false}), '');
  assert.equal(h.w.BMI30RemoteTest.card({address:'11:22:33:44:55:66',hid_capable:true,connected:true}), '');
});
await test('RU and EN switch with existing portal language event',async h=>{
  h.d.documentElement.lang='ru';h.w.dispatchEvent(new h.w.Event('bmi30-language-change'));await flush();
  assert.equal(h.d.querySelector('[data-bt-remote-action="start"]').textContent,'Тест');
  h.d.documentElement.lang='en';h.w.dispatchEvent(new h.w.Event('bmi30-language-change'));await flush();
  assert.equal(h.d.querySelector('[data-bt-remote-action="start"]').textContent,'Test');
});
console.log(JSON.stringify({passed:true,count:passed.length,tests:passed},null,2));

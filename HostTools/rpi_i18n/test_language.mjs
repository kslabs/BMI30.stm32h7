import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';
const dir=new URL('./',import.meta.url);
const work=new URL('../../.tmp/language-20261016/',import.meta.url);
const catalogs={};
for(const code of ['uk','es','de','cs','ja','fr','pt','zh-Hans','it','ru']) catalogs[code]=JSON.parse(await fs.readFile(new URL(`translations.${code}.json`,dir),'utf8'));
const script=(await fs.readFile(new URL('ui_language.js',dir),'utf8')).replace('__BMI30_CATALOGS__',JSON.stringify(catalogs));
const settle=()=>new Promise(resolve=>setTimeout(resolve,15));
function page(html,preference) {
  const window=new Window({url:'http://bmi30.test/portal',settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
  if(preference) window.localStorage.setItem('bmi30.ui.language',preference);
  window.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  let time=0,id=0;const timers=new Map();
  window.setTimeout=(fn,delay)=>{timers.set(++id,{fn,at:time+delay});return id;};
  window.clearTimeout=id=>timers.delete(id);
  const advance=ms=>{time+=ms;for(const [key,value] of timers) if(value.at<=time){timers.delete(key);value.fn();}};
  window.eval(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  return {window,document:window.document,advance};
}
const html='<html><head><title>BMI30 Portal</title></head><body><p class="device-meta"><span>RPI</span><span class="identity-value">Tag</span><span>STM32</span></p><h2>Tag Detection</h2><p id="dynamic">Connected</p><button id="tip" title="Noise measurement area">Save</button><input value="Tag"><select name="visible_ssid"><option>Tag</option></select><div translate="no">Tag</div></body></html>';
let p=page(html);
assert.equal(p.document.documentElement.lang,'en');
assert.equal(p.document.querySelector('h2').textContent,'Tag Detection');
assert.equal(p.document.querySelector('.device-meta').firstElementChild.className,'bmi30-language');
assert.equal(p.document.querySelectorAll('[role=menuitemradio]').length,11);
let button=p.document.querySelector('#bmi30-language-button'), menu=p.document.querySelector('#bmi30-language-menu');
button.parentElement.dispatchEvent(new p.window.PointerEvent('pointerenter'));
p.advance(1999);assert.equal(menu.hidden,true);p.advance(1);assert.equal(menu.hidden,false);
p.document.body.dispatchEvent(new p.window.PointerEvent('pointerdown',{bubbles:true}));assert.equal(menu.hidden,true);
button.parentElement.dispatchEvent(new p.window.PointerEvent('pointerenter'));p.advance(1000);
button.parentElement.dispatchEvent(new p.window.PointerEvent('pointerleave'));p.advance(2000);assert.equal(menu.hidden,true);
button.click();assert.equal(menu.hidden,false);
button.dispatchEvent(new p.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(menu.hidden,true);
button.dispatchEvent(new p.window.PointerEvent('pointerdown'));p.advance(2000);assert.equal(menu.hidden,false);button.click();assert.equal(menu.hidden,false);
for(const code of p.window.BMI30I18n.languages) {
  p.window.BMI30I18n.setLanguage(code);
  assert.equal(p.document.documentElement.lang,code);
  assert.equal(p.document.querySelector('h2').textContent,code==='en'?'Tag Detection':catalogs[code]['Tag Detection']);
  assert.equal(p.document.querySelector('#tip').title,code==='en'?'Noise measurement area':catalogs[code]['Noise measurement area']);
  assert.equal(p.document.querySelector('input').value,'Tag');
  assert.equal(p.document.querySelector('.identity-value').textContent,'Tag');
  const tip=p.window.BMI30I18n.t('n (noise) — noise estimate: max(mean, P95) over 5 s. P95 is the 95th percentile of measurement history.');
  assert.ok(tip.includes('5'));assert.ok(tip.includes('P95'));assert.ok(!tip.includes('{seconds}'));
}
p.window.BMI30I18n.setLanguage('ru');
p.document.querySelector('#dynamic').textContent='Disconnected';await settle();
assert.equal(p.document.querySelector('#dynamic').textContent,'Отключено');
p.window.BMI30I18n.setLanguage('en');assert.equal(p.document.querySelector('#dynamic').textContent,'Disconnected');
p.window.BMI30I18n.setLanguage('ja');p.window.BMI30I18n.text(p.document.querySelector('#dynamic'),'Connected');
p.window.BMI30I18n.setLanguage('en');assert.equal(p.document.querySelector('#dynamic').textContent,'Connected');
assert.equal(p.document.querySelector('select option').textContent,'Tag');
p.window.BMI30I18n.setLanguage('ru');
assert.equal(p.window.localStorage.getItem('bmi30.ui.language'),'ru');assert.ok(p.document.cookie.includes('bmi30_language=ru'));
let q=page(html,'cs');assert.equal(q.document.documentElement.lang,'cs');
let invalid=page(html,'xx');assert.equal(invalid.document.documentElement.lang,'en');
// Rendered pages exercise the real portal's split text nodes, option values, and tooltips.
const coverage={};
for(const name of ['portal-user','portal-engineer','scope']) {
  const original=await fs.readFile(new URL(`${name}.html`,work),'utf8');
  const r=page(original);const controls=[...r.document.querySelectorAll('input,select,option')].map(x=>[x.name,x.value]);
  r.window.BMI30I18n.setLanguage('ru');await settle();
  assert.deepEqual([...r.document.querySelectorAll('input,select,option')].map(x=>[x.name,x.value]),controls,name+' control values changed');
  coverage[name]={localizedButtons:[...r.document.querySelectorAll('button')].length};
  await r.window.happyDOM.abort();
}
await fs.writeFile(new URL('language-tests.json',work),JSON.stringify({passed:true,languages:11,hoverDelayMs:2000,persistence:true,dynamicText:true,coverage},null,2));
for(const r of [p,q,invalid]) await r.window.happyDOM.abort();
console.log('PASS: 11 languages; EN default; 1999/2000ms hover; cancel/hold/click/Escape; persistence; dynamic updates; reversible translation; control values unchanged.');

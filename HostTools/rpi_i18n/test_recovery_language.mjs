import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';
const work=new URL('../../.tmp/local-ui-20261028/',import.meta.url);
const script=await fs.readFile(new URL('i18n.js',work),'utf8');
const catalogs=JSON.parse(await fs.readFile(new URL('catalogs.json',work),'utf8'));
const settle=()=>new Promise(resolve=>setTimeout(resolve,25));
function page(html,lang='en'){
 const window=new Window({url:'http://bmi30.test/portal-recovery',settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
 window.localStorage.setItem('bmi30.ui.language',lang);
 window.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
 window.eval(script);window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
 return window;
}
const recovery=await fs.readFile(new URL('recovery.html',work),'utf8');
const diagnostic=await fs.readFile(new URL('network.html',work),'utf8');
let total=0;const leftovers={};
for(const language of ['en',...Object.keys(catalogs).filter(x=>x!=='_aliases')]){
 const w=page(recovery,language);await settle();
 const d=w.document,expected=language==='en'?'USB emergency system':catalogs[language]['USB emergency system'];
 assert.equal(d.querySelector('h1').textContent,expected,language+' title');
 assert.equal(d.querySelector('#cancel').textContent,language==='en'?'Cancel':catalogs[language].Cancel);
 assert.equal(d.documentElement.lang,language);assert.equal(d.querySelectorAll('[role=menuitemradio]').length,11);
 d.querySelector('#operation').textContent='72% · Повторная сверка изменившихся файлов.';await settle();
 assert.equal(d.querySelector('#operation').textContent,'72% · '+(language==='en'?'Verifying changed files…':catalogs[language]['Verifying changed files…']));
 const inputs=[...d.querySelectorAll('input,select')].map(e=>[e.id,e.value,e.checked]);
 w.BMI30I18n.setLanguage('en');await settle();
 assert.deepEqual([...d.querySelectorAll('input,select')].map(e=>[e.id,e.value,e.checked]),inputs);
 const walker=d.createTreeWalker(d.body,w.NodeFilter.SHOW_TEXT);let n,residual=[];
 while((n=walker.nextNode()))if(!n.parentElement.closest('script,style,[translate="no"],.bmi30-language')&&/[А-Яа-яЁё]/.test(n.nodeValue))residual.push(n.nodeValue.trim());
 if(residual.length)leftovers[language]=residual;
 await w.happyDOM.abort();total++;
}
assert.deepEqual(leftovers,{},'Untranslated Russian source on English recovery page');
const backend=JSON.parse(await fs.readFile(new URL('backend-messages.json',work),'utf8'));
const aliases=page('<html><head></head><body></body></html>');
for(const message of backend)assert.ok(!/[А-Яа-яЁё]/.test(aliases.BMI30I18n.t(message)),message);
await aliases.happyDOM.abort();
for(const language of ['en','ru','cs','de','ja','uk','es','fr','pt','zh-Hans','it']){
 const w=page(diagnostic,language);await settle();
 assert.match(w.document.querySelector('h1').textContent,new RegExp(language==='en'?'Device network':catalogs[language]['Device network']));
 await w.happyDOM.abort();
}
// Manual firmware check uses the shared locale and never calls an install API.
const fw=await fs.readFile(new URL('../../HostTools/usb_recovery/firmware_check.js',import.meta.url),'utf8');
const w=page('<html><head></head><body><button id="firmware-check-btn"></button><p id="firmware-check-status"></p></body></html>');
w.fetch=async()=>({ok:true,json:async()=>({cloud:{checked_at:1793190000,error:'gaierror: operation failed'},operation:{}})});
w.eval(fw);await settle();
for(const language of w.BMI30I18n.languages){
 w.BMI30I18n.setLanguage(language);await settle();
 assert.equal(w.document.querySelector('#firmware-check-btn').textContent,'↻ '+(language==='en'?'Check updates':catalogs[language]['Check updates']));
 assert.ok(w.document.querySelector('#firmware-check-status').textContent.includes(language==='en'?'Server name lookup failed.':catalogs[language]['Server name lookup failed.']));
}
await w.happyDOM.abort();
console.log('PASS: recovery, network diagnostics and manual update UI in '+total+' languages; legacy progress and reversible selection.');

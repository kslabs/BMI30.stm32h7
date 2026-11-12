import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';
const html=await fs.readFile(process.argv[2]||new URL('wifi_profiles.html',import.meta.url),'utf8');
const script=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].at(-1)[1];
const i18n=html.match(/<script id="bmi30-i18n">([\s\S]*?)<\/script>/)?.[1];
const settle=()=>new Promise(r=>setTimeout(r,20));
const id='11111111-2222-3333-4444-555555555555';
const malicious='invite:<img src=x onerror="window.injected=true">';
async function fixture(){
 const w=new Window({url:'http://bmi30.test/portal-wifi-profiles'});const calls=[];let copied='';
 w.document.execCommand=name=>{assert.equal(name,'copy');copied=w.document.querySelector('textarea').value;return true;};
 Object.defineProperty(w.document,'hidden',{configurable:true,get:()=>false});
 w.fetch=async(url,options={})=>{calls.push({url,options});const value=String(url).endsWith('/password')?
  {ok:true,available:true,password:'ui-test-only<&secret'}:{ok:true,networks:[{uuid:id,ssid:malicious,name:'guest',security:'wpa-psk',active:true,autoconnect:true,password_supported:true}]};
  return {ok:true,status:200,text:async()=>JSON.stringify(value)};};
 w.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,''));w.eval(script);await settle();
 const button=text=>[...w.document.querySelectorAll('button')].find(b=>b.textContent===text);
 return {w,calls,button,copied:()=>copied,close:()=>w.happyDOM.abort()};
}
{
 const f=await fixture();assert.equal(f.calls.length,1);assert.equal(f.w.document.querySelector('img'),null);
 assert.equal(f.w.document.querySelector('h2').textContent,malicious);
 const input=f.w.document.querySelector('input');assert.equal(input.hidden,true);assert.equal(input.value,'');
 f.button('Copy password').click();await settle();assert.equal(f.calls.length,1);
 f.button('Show password').click();await settle();assert.equal(input.type,'text');assert.equal(input.value,'ui-test-only<&secret');
 const request=f.calls.at(-1);assert.equal(request.options.method,'POST');assert.equal(request.options.headers['X-BMI30-WIFI'],'1');
 assert.deepEqual(JSON.parse(request.options.body),{uuid:id});
 f.button('Copy password').click();await settle();assert.equal(f.copied(),'ui-test-only<&secret');assert.equal(f.w.document.querySelector('textarea'),null);
 f.button('Hide password').click();await settle();assert.equal(input.hidden,true);assert.equal(input.value,'');
 f.button('Show password').click();await settle();f.w.dispatchEvent(new f.w.Event('pagehide'));assert.equal(input.value,'');assert.equal(input.hidden,true);
 assert.equal(f.w.localStorage.length,0);await f.close();
}
{
 const f=await fixture();f.button('Copy network name').click();await settle();assert.equal(f.copied(),malicious);assert.equal(f.calls.length,1);await f.close();
}
console.log('Saved Wi-Fi UI: explicit reveal, copy fallback, masking, no injection, no local secret storage passed.');
if(i18n){
 for(const code of ['en','ru','uk','es','de','cs','ja','fr','pt','zh-Hans','it']){
  const f=await fixture();f.w.localStorage.setItem('bmi30.ui.language',code);f.w.eval(i18n);
  f.w.document.dispatchEvent(new f.w.Event('DOMContentLoaded'));await settle();
  const expected=f.w.BMI30I18n.t('Saved Wi-Fi networks and passwords');
  assert.equal(f.w.document.querySelector('h1').textContent,expected);
  if(code!=='en')assert.notEqual(expected,'Saved Wi-Fi networks and passwords');
  assert.equal(f.w.document.querySelector('h2').textContent,malicious,'Network name must never be translated');
  const shown=f.w.BMI30I18n.t('Show password');
  assert.ok([...f.w.document.querySelectorAll('button')].some(b=>b.textContent===shown));
  await f.close();
 }
 console.log('Saved Wi-Fi page: all 11 languages passed.');
}

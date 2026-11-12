import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';
const html=await fs.readFile(new URL('recovery.html',import.meta.url),'utf8');
const script=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].at(-1)[1];
const i18nScript=await fs.readFile(new URL('../../.tmp/local-ui-20261028/i18n.js',import.meta.url),'utf8');
const settle=()=>new Promise(resolve=>setTimeout(resolve,15));
const reply=(status,value)=>({status,ok:status>=200&&status<300,text:async()=>typeof value==='string'?value:JSON.stringify(value)});
async function fixture({planFailure=false,emptyPlan=false,emptyStart=false,usbBoot=false,emmcAvailable=true,needsReboot=false,unknownBoot=false,language=null,initialOperation=null}={}){
 const w=new Window({url:'http://bmi30.test/portal-recovery',settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
 const state={ok:true,source:'BMI30-TEST',boot:{root:'/dev/mmcblk0p2',from_recovery_usb:false},emmc:{visible:false},copy:{kind:'usb',target:null},can_write:true,
  disks:[{path:'/dev/test-only',model:'TEST FIXTURE',serial:'NOT A REAL USB',size:32e9,eligible:true}],operation:{stage:'idle',progress:0,message:'Запись не запущена.'}};
 if(usbBoot){state.boot={root:'/dev/sda2',boot:'/dev/sda1',from_recovery_usb:true};state.copy={kind:'emmc-restore',target:emmcAvailable?'/dev/mmcblk0':null};state.emmc={visible:true,available:emmcAvailable,requires_reboot_to_enable:needsReboot,reason:emmcAvailable?'':'eMMC unavailable'};state.disks=[];}
 if(unknownBoot){state.copy={kind:null,target:null};state.can_write=false;state.blocked_reason='Unknown boot drive';}
 if(initialOperation){state.operation=initialOperation;state.disks[0].eligible=false;state.disks[0].reason='Drive is in use';}
 const plan={id:'a'.repeat(32),target_path:usbBoot?'/dev/mmcblk0':'/dev/test-only',kind:usbBoot?'emmc-restore':'usb',serial:'NOT A REAL USB',model:'TEST FIXTURE',size:32e9,required_bytes:8e9,confirmation:'FIXTURE-ONLY'};
 const calls=[];let poll,meter,now=0;
 Object.defineProperty(w.performance,'now',{value:()=>now,configurable:true});
 w.setInterval=(fn,ms)=>{if(ms===3000)poll=fn;if(ms===1000)meter=fn;return 1;};
 w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.fetch=async(url,options={})=>{
  const action=String(url).split('/').at(-1);calls.push({action,options});
  if(action==='status')return reply(200,state);
  if(action==='plan'){const body=JSON.parse(options.body);assert.equal(body.kind,plan.kind);assert.equal(body.target,plan.target_path);return emptyPlan?reply(200,''):planFailure?reply(409,{ok:false,error:'Not enough free space.'}):reply(200,{ok:true,plan});}
  if(action==='enable-emmc'){assert.equal(JSON.parse(options.body).confirm_reboot,true);return reply(200,{ok:true,rebooting:true,message:'Rebooting to enable eMMC'});}
  if(action==='start'){
   const body=JSON.parse(options.body);assert.equal(body.id,plan.id);assert.equal(body.confirmation,plan.confirmation);assert.equal(body.erase_confirmed,true);
   state.operation={id:plan.id,stage:'queued',progress:0,message:'Queued',target_path:plan.target_path,target_model:plan.model,target_serial:plan.serial,target_size:plan.size};
   return emptyStart?reply(202,''):reply(202,{ok:true,started:true,operation:state.operation});
  }
  throw Error('Unexpected action '+action);
 };
 w.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,''));
 if(language){w.localStorage.setItem('bmi30.ui.language',language);w.eval(i18nScript);w.document.dispatchEvent(new w.Event('DOMContentLoaded'));}
 w.eval(script);await settle();
 const d=w.document;if(!initialOperation){d.querySelector('#disk').value='/dev/test-only';d.querySelector('#disk').dispatchEvent(new w.Event('change'));}
 return {w,d,state,calls,plan,poll:async()=>{poll();await settle();},tick:seconds=>{now+=seconds*1000;meter();},prepare:async()=>{d.querySelector('#prepare').click();await settle();},close:()=>w.happyDOM.abort()};
}
{
 const op={id:'running-copy',stage:'copying',progress:30,target_path:'/dev/test-only',target_model:'TEST FIXTURE',target_serial:'NOT A REAL USB',target_size:32e9};
 const f=await fixture({initialOperation:op});
 assert.equal(f.d.querySelector('#disk').value,op.target_path,'Page reload must show the actual active destination');
 assert.equal(f.d.querySelector('#disk').disabled,true);assert.equal(f.d.querySelector('#copy-destination').hidden,false);
 assert.match(f.d.querySelector('#copy-destination-value').textContent,/TEST FIXTURE.*\/dev\/test-only/);
 f.state.disks=[];await f.poll();
 assert.equal(f.d.querySelector('#disk').value,op.target_path,'Formatting or temporarily missing inventory must not erase the active destination');
 assert.equal(f.d.querySelector('#start').disabled,true);await f.close();
}
{
 const f=await fixture();await f.prepare();f.d.querySelector('#erase').checked=true;f.d.querySelector('#erase').dispatchEvent(new f.w.Event('change'));
 f.d.querySelector('#start').click();await settle();
 f.state.disks[0].eligible=false;f.state.disks[0].reason='Drive is in use';await f.poll();
 assert.equal(f.d.querySelector('#disk').value,'/dev/test-only','A mounted copy destination remains selected');
 assert.equal(f.d.querySelector('#copy-destination').hidden,false);await f.close();
}
{
 const f=await fixture();f.state.disks.push({path:'/dev/other-test',model:'OTHER TEST USB',size:16e9,serial:'OTHER',eligible:true});await f.poll();
 f.state.operation={id:'other-browser-copy',stage:'queued',progress:0,target_path:'/dev/other-test',target_model:'OTHER TEST USB',target_size:16e9};await f.poll();
 assert.equal(f.d.querySelector('#disk').value,'/dev/other-test','A job started elsewhere must override this page\'s old selection');
 assert.match(f.d.querySelector('#copy-destination-value').textContent,/OTHER TEST USB/);await f.close();
}
{
 const f=await fixture();f.state.operation={id:'metrics',stage:'copying',progress:50,elapsed_s:60,bytes_copied:120e6};await f.poll();
 assert.equal(f.d.querySelector('#elapsed').textContent,'00:01:00');assert.match(f.d.querySelector('#average-speed').textContent,/2.*MB\/s/);
 f.tick(30);assert.equal(f.d.querySelector('#elapsed').textContent,'00:01:30');
 f.state.operation={id:'metrics',stage:'failed',progress:97,elapsed_s:120,bytes_copied:120e6};await f.poll();f.tick(3600);
 assert.equal(f.d.querySelector('#elapsed').textContent,'00:02:00','Failed copy duration must stop');assert.equal(f.d.querySelector('#failed-note').hidden,false);
 f.state.operation={id:'next',stage:'queued',progress:0,elapsed_s:0,bytes_copied:0};await f.poll();
 assert.equal(f.d.querySelector('#elapsed').textContent,'00:00:00');assert.equal(f.d.querySelector('#failed-note').hidden,true);await f.close();
}
{
 const f=await fixture({planFailure:true});await f.prepare();
 assert.equal(f.d.querySelector('#error').textContent,'Not enough free space.');await f.poll();await f.poll();
 assert.equal(f.d.querySelector('#error').textContent,'Not enough free space.','Background refresh erased the failed action');
 assert.equal(f.calls.filter(x=>x.action==='start').length,0);await f.close();
}
{
 const f=await fixture({emptyPlan:true});await f.prepare();await f.poll();
 assert.match(f.d.querySelector('#error').textContent,/empty response.*HTTP 200/);assert.equal(f.d.querySelector('#confirmation').hidden,true);await f.close();
}
for(const emptyStart of [false,true]){
 const f=await fixture({emptyStart});await f.prepare();
 assert.equal(f.d.querySelector('#confirmation').hidden,false);await f.poll();assert.equal(f.d.querySelector('#confirmation').hidden,false);
 assert.equal(f.d.querySelector('#start').disabled,true,'Erase consent required');
 const consent=f.d.querySelector('#erase');consent.checked=true;consent.dispatchEvent(new f.w.Event('change'));
 f.d.querySelector('#start').click();await settle();
 assert.equal(f.calls.filter(x=>x.action==='start').length,1);assert.equal(f.d.querySelector('#error').textContent,'');
 assert.equal(f.d.querySelector('#confirmation').hidden,true);
 f.state.operation={id:f.plan.id,stage:'copying',progress:70,message:'Copying'};await f.poll();
 assert.equal(f.d.querySelector('#progress').value,70);
 f.state.operation.progress=65;await f.poll();assert.equal(f.d.querySelector('#progress').value,70);assert.match(f.d.querySelector('#operation').textContent,/^70%/);
 f.state.operation.progress=99;await f.poll();assert.equal(f.d.querySelector('#progress').value,99);
 f.state.operation.stage='complete';f.state.operation.progress=100;await f.poll();assert.equal(f.d.querySelector('#progress').value,100);
 await f.close();
}
{
 const f=await fixture({usbBoot:true});const card=f.d.querySelector('#copy-card');
 assert.equal(f.d.querySelector('#copy-direction').textContent,'USB → eMMC');
 assert.equal(f.d.querySelector('#usb-target').hidden,true);assert.equal(f.d.querySelector('#emmc-target').hidden,false);
 assert.equal(f.d.querySelector('#restore-emmc'),null,'There must not be a separate restore button');
 assert.ok(card.contains(f.d.querySelector('#prepare'))&&card.contains(f.d.querySelector('#progress')));
 assert.equal(f.d.querySelector('#prepare').disabled,false,'Active source USB need not appear as a destination');
 await f.prepare();assert.equal(f.d.querySelector('#confirmation').hidden,false);await f.poll();
 assert.equal(f.d.querySelector('#confirmation').hidden,false);assert.equal(f.d.querySelector('#start').disabled,true);
 assert.match(f.d.querySelector('#start').textContent,/USB → eMMC/);
 const consent=f.d.querySelector('#erase');consent.checked=true;consent.dispatchEvent(new f.w.Event('change'));
 f.d.querySelector('#start').click();await settle();assert.equal(f.calls.filter(x=>x.action==='start').length,1);
 f.state.operation={id:f.plan.id,kind:'emmc-restore',stage:'complete',progress:100};await f.poll();
 assert.match(f.d.querySelector('#result').textContent,/eMMC.*Выключите питание/);await f.close();
}
for(const options of [{usbBoot:true,emmcAvailable:false},{unknownBoot:true}]){
 const f=await fixture(options);assert.equal(f.d.querySelector('#prepare').disabled,true);
 f.d.querySelector('#prepare').click();await settle();assert.equal(f.calls.filter(x=>x.action==='plan').length,0);await f.close();
}
{
 const f=await fixture({usbBoot:true,emmcAvailable:false,needsReboot:true});f.w.confirm=()=>false;
 await f.prepare();assert.equal(f.calls.filter(x=>x.action==='enable-emmc').length,0);
 f.w.confirm=()=>true;await f.prepare();assert.equal(f.calls.filter(x=>x.action==='enable-emmc').length,1);
 assert.equal(f.calls.filter(x=>x.action==='plan'||x.action==='start').length,0,'Enabling eMMC never starts erasure');await f.close();
}
{
 const f=await fixture();await f.prepare();f.state.copy={kind:null,target:null};f.state.can_write=false;await f.poll();
 assert.equal(f.d.querySelector('#confirmation').hidden,true);assert.equal(f.d.querySelector('#start').disabled,true);await f.close();
}
{
 const f=await fixture();await f.prepare();f.state.version='changed-release';await f.poll();
 assert.equal(f.d.querySelector('#confirmation').hidden,true,'New firmware must invalidate the old copy confirmation');await f.close();
}
{
 const f=await fixture();f.state.operation={id:'new-job',stage:'complete',kind:'usb',progress:100,wifi_seed:{active_wifi_saved:true,source_hotspot_fallback_saved:true}};await f.poll();
 assert.equal(f.d.querySelector('#wifi-seed-status').hidden,false);assert.match(f.d.querySelector('#wifi-seed-status').textContent,/fallback connection/);await f.close();
}
for(const usbBoot of [false,true]){
 const f=await fixture({usbBoot,language:'ja'});const key=usbBoot?'Booted from USB.':'Booted from internal eMMC.';
 for(const language of f.w.BMI30I18n.languages){
  f.w.BMI30I18n.setLanguage(language);await settle();
  assert.equal(f.d.querySelector('#boot').textContent,f.w.BMI30I18n.t(key),'Dynamic boot text retained a previous language');
  assert.equal(f.d.querySelector('#copy-direction').textContent,usbBoot?'USB → eMMC':'eMMC → USB');
  assert.equal(f.d.querySelector('#prepare').disabled,false);
  for(const text of ['Elapsed time','Copied data','Average speed (whole operation)',
                    'Average speed includes checking and final saving.',
                    'Copying has stopped. The progress bar shows the failed attempt.',
                    'Finishing the write timed out. The drive is not ready for removal.']){
   assert.ok(f.w.BMI30I18n.t(text));
   if(language!=='en')assert.notEqual(f.w.BMI30I18n.t(text),text,'Missing '+language+' translation: '+text);
  }
 }
 await f.close();
}
console.log('PASS: one copy panel, boot-selected directions, erase consent, unavailable media, persistent errors, nondecreasing progress, elapsed timer and average speed, frozen failure duration, all 11 languages. No hardware requests.');

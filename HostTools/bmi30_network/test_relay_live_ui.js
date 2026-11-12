// Exercise the actual relay controls and WS handler without issuing commands.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {URL}=require('node:url');
let now=10000,revision=0;
const intervals=[],els={};
['root','enable','duration','test','level'].forEach(key=>els[key]={disabled:false,checked:false,value:'',textContent:'',classList:{toggle(){}},setAttribute(){},addEventListener(){}});
class Socket {constructor(){this.readyState=1;} close(){this.readyState=3;}}
const context=vm.createContext({Date:{now:()=>now},performance:{now:()=>now},URL,WebSocket:Socket,
  document:{hidden:false,activeElement:null,querySelector:s=>els[s.match(/data-relay-(\w+)/)?.[1]]||null,addEventListener(){}},
  window:{location:{href:'https://device.test/portal#group'},setInterval:fn=>intervals.push(fn),addEventListener(){}},
  setInterval:fn=>intervals.push(fn),fetch(){throw Error('Display updates must never command the relay');},
  _groupLatest:{},_updateGroupMatrix(){},_updateSensorTextEl(){},_formatBool:String,_pollSensors(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../rpi_relay_web/portal_relay.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(process.argv[2] || path.join(__dirname,'live_ui.js'),'utf8'),context);
context.groupLiveDevice={};
function push(seq,observed,enabled=true,pc1=true) {
  context.groupLiveApply({type:'group',revision:++revision,group:{checked_at:now/1000,local_id:'A',nodes:[{id:'A',available:true,age_s:0,
    sensors:{detadc1:!!(revision%2)},relay:{sequence:seq,observed_at:observed,age_s:now/1000-observed,available:true,enabled,saved_enabled:enabled,
      pc1,active:pc1,duration_ms:800,test_enabled:false}}]}});
}
for(let seq=1;seq<=20;seq++) {
  push(seq,now/1000-.4);intervals[0]();
  assert.equal(els.level.textContent,'Relay: ON','fresh WS reads keep the relay available between five-second HTTP baselines');
  assert.equal(els.enable.checked,true);assert.equal(els.enable.disabled,false);
  now+=1000;
}
context.document.activeElement=els.duration;els.duration.value='1234';push(21,now/1000);
assert.equal(els.duration.value,'1234','WS preserves an in-progress edit');
context.updateRelay({sequence:20,available:true,enabled:false,saved_enabled:false,pc1:false,age_s:0,duration_ms:1},false);
assert.equal(els.enable.checked,true,'a delayed HTTP baseline cannot rewind the newer relay state');
const stamp=now/1000;
for(let i=0;i<40;i++){now+=100;push(21,stamp);intervals[0]();}
assert.equal(els.level.textContent,'Relay: unavailable','ADC events and group heartbeats must not renew a stopped relay source');
assert.equal(els.enable.checked,true,'expiry preserves the saved permission');assert.equal(els.enable.disabled,true);
now+=100;push(22,now/1000-3);
assert.equal(els.level.textContent,'Relay: unavailable','a newly received stale sequence is still stale');
now+=100;push(23,now/1000-.1,false,false);
assert.equal(els.level.textContent,'Relay: OFF');assert.equal(els.enable.checked,false);assert.equal(els.enable.disabled,false);
console.log('Relay UI: WS continuity, real source expiry, preserved edits/permission, late HTTP rejection and recovery passed');

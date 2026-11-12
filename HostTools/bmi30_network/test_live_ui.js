const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {URL}=require('node:url');
let now=10000, calendar=10000, watchdog, visibility, polls=0;
const changes=[],status={textContent:''},sockets=[];
class Socket {
  constructor(url){this.url=url;this.readyState=0;sockets.push(this);}
  close(){this.readyState=3;this.onclose();}
  emit(revision,on){this.readyState=1;this.onmessage({data:JSON.stringify({type:'group',revision,group:{local_id:'A',nodes:[{id:'A',available:true,age_s:0,sensors:{detadc1:on}}]}})});}
}
const context=vm.createContext({Date:{now:()=>calendar},performance:{now:()=>now},URL,WebSocket:Socket,
  document:{hidden:false,querySelector:()=>status,addEventListener:(name,fn)=>{visibility=fn;}},
  window:{location:{href:'https://device.test/portal#group'},addEventListener(){},setInterval:fn=>{watchdog=fn;}},
  _pollSensors(){polls++;},_updateGroupMatrix:d=>changes.push(d.bmi30_group.nodes[0].sensors.detadc1),
  _groupLatest:{},
  _updateSensorTextEl(){},_formatBool:v=>String(v)});
vm.runInContext(fs.readFileSync(process.argv[2] || path.join(__dirname,'live_ui.js'),'utf8'),context);
assert.equal(sockets[0].url,'wss://device.test/api/group/live');
context.groupLiveDevice={};sockets[0].emit(1,false);sockets[0].emit(2,true);sockets[0].emit(3,false);
assert.deepEqual(changes,[false,true,false]);assert.equal(context.groupLiveHealthy(),true);
sockets[0].emit(2,true);assert.deepEqual(changes,[false,true,false]);
for (const date of [-2208988800000,4102444800000,0]) {
  calendar=date;watchdog();assert.equal(context.groupLiveHealthy(),true,'calendar jumps must not break a live connection');
  assert.equal(sockets.length,1);
}
now+=3100;watchdog();assert.equal(context.groupLiveHealthy(),false);
assert.equal(status.textContent,'HTTP · reconnecting');assert.equal(sockets.length,1);
now+=1000;watchdog();assert.equal(sockets.length,2);
sockets[1].emit(1,true);assert.equal(changes.at(-1),true,'new connection can start a new revision sequence');
context.document.hidden=true;visibility();assert.equal(context.groupLiveHealthy(),false);
context.document.hidden=false;visibility();assert.equal(polls,1,'returning to the page requests an HTTP baseline');
context._groupLatest.peer={_channel:'RS485'};assert.equal(context.groupLiveFallback(),true);
sockets[1].emit(2,false);assert.equal(polls,2,'a missing fast peer immediately requests current STM32/RS485 data');
console.log('Live UI: ordered short edges, replay guard, watchdog, reconnect and HTTP recovery passed');

import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const ctx = vm.createContext({Date,performance:{now:()=>Date.now()},setInterval:()=>0,_padNode2:n=>String(n).padStart(2,'0')});
vm.runInContext(fs.readFileSync(new URL('./group_ui.js',import.meta.url),'utf8'),ctx);
const node = {id:'BMI30-AAAAAAAAA',issued:Date.now()/1000,age_s:0,available:true,stm32_uid96:'111122223333444455556666',
  stm32_short_id:'123456789',sync:{role:'slave',assigned_role:'---',code:'S10',device_id:10},
  sensors:{optic_active:true,detadc1:true},optic:{},neighbors:[],hold_ds:30,via:['BMI30-AAAAAAAAA']};
ctx._groupFast={available:true,local_id:'BMI30-LOCAL',nodes:[node]};
ctx._groupNetwork={available:true,nodes:[]};
let rows=ctx._groupAugment([{node_id:10,rpi_id:'AAAAAAAAA',role:'slave',optic_active:false}]);
assert.equal(rows.length,1);
assert.equal(rows[0]._syncAssign,'slave');
assert.equal(rows[0]._canControl,true);
assert.equal(rows[0].detadc1,true);
// A different RS485 bus may reuse ID 10; never merge by numeric address.
ctx._groupFast.nodes=[node,{...node,id:'BMI30-BBBBBBBBB',stm32_uid96:'AAAABBBBCCCCDDDDEEEEFFFF',stm32_short_id:'987654321'}];
rows=ctx._groupAugment([{node_id:10,rpi_id:'AAAAAAAAA',role:'slave'}]);
assert.equal(rows.length,2);
assert.equal(new Set(rows.map(n=>n._networkId)).size,2);
// Firmware's short hardware identity still joins when LAN metadata vanishes.
ctx._groupLastFast={};ctx._groupFast.nodes=[node];
rows=ctx._groupAugment([{node_id:10,stm32_id:'123456789',role:'slave'}]);
assert.equal(rows.length,1);
assert.equal(rows[0]._networkId,'BMI30-AAAAAAAAA');
// Stale fast state cannot mask a newer RS485 value or keep controls active.
ctx._groupFast={available:false,nodes:[]};
rows=ctx._groupAugment([{node_id:10,rpi_id:'AAAAAAAAA',role:'slave',optic_active:false}]);
assert.equal(rows.length,1);
assert.equal(rows[0].optic_active,false);
assert.equal(rows[0]._channel,'RS485');
assert.equal(rows[0]._canControl,false);
console.log('Group UI: identity, role, separate buses and RS485 fallback PASS');
// Own Internet remains visible without a fast Core record or a reflected peer.
ctx._groupFast={available:false,nodes:[]};ctx._groupLastFast={};
const localNet={id:'BMI30-C9775FBAE',age_s:0,internet:{ok:true,rtt_ms:77},uplink:'wifi',freq:5180,signal:54};
ctx._groupNetwork={available:true,local_id:localNet.id,local:localNet,nodes:[]};
rows=ctx._groupAugment([{is_local:true,node_id:7,rpi_id:'C9775FBAE',role:'slave'}]);
assert.equal(ctx._groupNetworkText(rows[0],'internet'),'Online · 77 ms');
assert.match(ctx._groupNetworkText(rows[0],'link'),/Router.*5 GHz.*54%/);
localNet.internet={};assert.equal(ctx._groupNetworkText(rows[0],'internet'),'Unknown');
localNet.internet={ok:false};assert.equal(ctx._groupNetworkText(rows[0],'internet'),'No internet');
localNet.age_s=65;assert.equal(ctx._groupNetworkText(rows[0],'internet'),'Unknown');
console.log('Group UI: direct local Internet, missing probe and stale-state handling PASS');

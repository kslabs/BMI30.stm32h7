// Run with node; no third-party DOM or browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.value=''; this.classList={remove(){},add(){}}; }
  get options() { return this.children; }
  appendChild(child) { return this.insertBefore(child,null); }
  insertBefore(child,before) {
    const old=this.children.indexOf(child); if(old>=0) {this.children.splice(old,1);}
    const idx=before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(idx,0,child); return child;
  }
  removeChild(child) {this.children.splice(this.children.indexOf(child),1);}
  setAttribute(name,value) {this[name]=value;}
  addEventListener() {}
}
let clock=1000000;
let expiryTick;
const context=vm.createContext({Date:{now:()=>clock},performance:{now:()=>clock},setInterval:fn=>{expiryTick=fn;return 0;},
  document:{createElement:tag=>new Element(tag),activeElement:null},
  _padNode2:n=>String(n).padStart(2,'0'), _groupCols:[],
  _formatBool:v=>typeof v === 'boolean' ? (v ? 'ON' : 'off') : 'Unknown',
  _groupSetText(el,value){el.textContent=value;},_groupSetState(){},_groupSetBool(){},_groupSetIpCell(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'group_ui.js'),'utf8'),context);
const node={id:'BMI30-27448D72B',age_s:0,available:true,sync:{role:'slave',device_id:12,code:'S12'},
  optic:{reaction_enabled:false,neighbor_reaction_enabled:true,neighbor_device_id:1},
  neighbors:[{node_id:1,label:'M01',master:true}],hold_ds:30};
function sample(update={}) {
  context._groupFast={available:true,local_id:'BMI30-LOCAL',nodes:[Object.assign({},node,update)]};
  return context._groupAugment([])[0];
}
let d=sample(), sig=context._groupCompositionSig([d]);
assert.equal(d._channel,'Wi-Fi');
assert.equal(sample({transport:'bluetooth'})._channel,'Bluetooth');
assert.equal(sample({transport:'bluetooth',address:'bt:AA:BB:CC:DD:EE:FF',via:[node.id]}).ip,'');
const neighbor=context._groupMakeReactionCell(d,true), role=context._groupMakeSyncRoleCell(d);
const hold=context._groupMakeHoldCell(d);
context._groupCols=[{cells:{neighbor,role,hold}}];
const control=neighbor._ctl;
assert.equal(control.value,'1');
clock+=250;
d=sample({neighbors:[{node_id:1,label:'RS485 01',master:null},{node_id:10,label:'S10',master:false}]});
assert.equal(context._groupCompositionSig([d]),sig,'option changes must not rebuild columns');
context._groupUpdateColumns([d]);
assert.equal(neighbor._ctl,control,'retain the actual control');
assert.equal(control.value,'1');
assert.equal(control.options.find(o=>o.value==='1').textContent,'M01','unknown role is not Slave');
assert.equal(role._ctl.value,'slave'); assert.equal(hold._ctl.value,'3');
// Preserve an open selector, including its current edit, while another option disappears.
context.document.activeElement=control; control.value='10';
d=sample({neighbors:[]}); context._groupUpdateColumns([d]);
assert.equal(control.value,'10'); assert.equal(control.options.length,4);
context.document.activeElement=null; context._groupUpdateColumns([d]);
assert.equal(control.value,'1'); assert.match(control.options.find(o=>o.value==='1').textContent,/absent/);
// Missing settings may reuse a still-fresh complete tuple, but never become Off/Any.
clock+=250; d=sample({optic:{}}); context._groupUpdateColumns([d]);
assert.equal(control.value,'1'); assert.equal(control.disabled,false);
clock+=4100; d=sample({optic:{}}); context._groupUpdateColumns([d]);
assert.equal(control.value,'unknown'); assert.equal(control.disabled,true);
// Explicit null is a real Any choice; false is a real Off choice.
d=sample({optic:{reaction_enabled:false,neighbor_reaction_enabled:true,neighbor_device_id:null}});
context._groupUpdateColumns([d]); assert.equal(control.value,'any'); assert.equal(control.disabled,false);
d=sample({optic:{reaction_enabled:true,neighbor_reaction_enabled:false,neighbor_device_id:null}});
context._groupUpdateColumns([d]); assert.equal(control.value,'off');
// Availability changes disable existing controls without rebuilding the table.
d=sample({available:false}); assert.equal(context._groupCompositionSig([d]),sig);
context._groupUpdateColumns([d]); assert.equal(control.disabled,true); assert.equal(control.value,'unknown');
console.log('PASS: neighbor options, role, hold, focus, incomplete settings, Any/Off, expiry and availability');
// An unchanged zero pattern stays visible in idle; losing network freshness
// marks the retained number instead of replacing it with Unknown.
d=sample({optic:{led_pattern_selected:0,led_output_active:false,led_indication_enabled:true}});
assert.equal(context._groupNetworkText(d,'led'),'0');
assert.equal(context._groupNetworkText(d,'led-output'),'off · Enabled');
const led=new Element('td'); context._groupCols=[{cells:{led}}];
context._groupUpdateColumns([d]);
clock+=3000; expiryTick();
assert.equal(led.textContent,'0');
d=sample({available:false,optic:{led_pattern_selected:8}});
assert.equal(context._groupNetworkText(d,'led'),'8');
d=sample({optic:{led_pattern_selected:64}});
assert.equal(context._groupNetworkText(d,'led'),'—');
// Loss of the fast record restores values supplied by the STM32/RS485 builder.
const rs485={is_local:false,rpi_id:'27448D72B',uid_key:'rpi:27448D72B',node_id:12,role:'slave',detadc1:false,detadc2:true,online:true};
context._groupFast={available:true,local_id:'BMI30-LOCAL',nodes:[Object.assign({},node,{sensors:{detadc1:true,detadc2:false},optic:{}})]};
assert.equal(context._groupAugment([Object.assign({},rs485)])[0].detadc1,true);
context._groupFast={available:true,local_id:'BMI30-LOCAL',nodes:[]};
const fallback=context._groupAugment([Object.assign({},rs485)])[0];
assert.equal(fallback.detadc1,false);assert.equal(fallback.detadc2,true);
assert.equal(fallback._channel,'RS485');assert.equal(fallback._canControl,false);
console.log('PASS: fast ADC takes priority; loss restores confirmed RS485 flags and disables remote controls');
console.log('PASS: persistent idle pattern, output permission, stale/offline marking and invalid range');

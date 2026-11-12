// Test the actual embedded portal JavaScript, including late DOM updates.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(process.argv[2], 'utf8');
let clock = 1000000;
class Element {
  constructor() { this.children = []; this.textContent = ''; this.className = ''; }
  appendChild(child) { this.children.push(child); }
  querySelector(selector) { return this.children.find(child => '.' + child.className === selector); }
}
const context = vm.createContext({Date: {now: () => clock}, performance:{now:()=>clock}, setInterval() {},
  document: {activeElement: null, createElement: () => new Element()},
  _padNode2: n => String(n).padStart(2, '0'), _groupCols: [], _groupLocalApplyT: 0,
  _roleChar: role => role === 'master' ? 'M' : role === 'slave' ? 'S' : '?',
  _shortStm32DisplayId: (preferred, fallback) => String(preferred || fallback || '').replace(/[^0-9A-Fa-f]/g, '').toUpperCase().slice(-9),
  _groupLocalDesiredReaction: null, _groupLocalDesiredNeighborReaction: null,
  _groupLocalDesiredNeighborDeviceId: undefined, _groupLocalDesiredHold: null});
vm.runInContext(script, context);
const uid = '3336353430325113000C002C';
const rs485 = () => ({node_id: 12, code: 'S12', role: 'slave', stm32_uid96: uid,
  stm32_id: '556E5FA3B', stm32_short_id: '556E5FA3B', ip: '10.43.0.1'});
const fast = {id: 'BMI30-27448D72B', age_s: 0, available: true, stm32_uid96: uid,
  stm32_short_id: '556E5FA3B', sync: {device_id: 12, role: 'slave', code: 'S12'}, sensors: {}, optic: {}};
context._groupFast = {available: true, local_id: 'BMI30-05AB38E6A', nodes: [fast]};
let rows = context._groupAugment([rs485()]);
assert.equal(rows.length, 1);
assert.equal(rows[0].header_label, '27448D72B/1');
assert.equal(rows[0].stm32_id, '556E5FA3B');
assert.equal(rows[0].rpi_id, '27448D72B');
const head = context._groupMakeHead(rows[0]);
context._groupCols = [{head, cells: {}}];
// The fast channel and its 60-second cache can disappear without changing ID.
clock += 61000;
context._groupFast = {available: false, nodes: []};
rows = context._groupAugment([rs485()]);
assert.equal(rows[0].header_label, '27448D72B/1');
assert.equal(rows[0]._canControl, false);
context._groupUpdateColumns(rows);
assert.equal(head.querySelector('.group-dev-badge').textContent, '27448D72B/1');
// No unverified header/hostname/short-ID field can impersonate an RPI ID.
assert.equal(context._groupDeviceHeaderLabel({short_id: '556E5FA3B', host_id: '556E5FA3B',
  hostname: 'BMI30-05AB38E6A', group_label: '556E5FA3B/1'}, 12), 'RPI —');
assert.equal(context._groupDeviceHeaderLabel({rpi_id: '27448D72B', ip: '10.43.0.1'}, 12), '27448D72B/1');
// A different full UID must not inherit S12's number via its legacy short UID.
const other = {...rs485(), stm32_uid96: '111122223333444455556666'};
context._groupRememberRpiIdentity(other);
assert.equal(other.rpi_id, '');
// A real short-UID collision disables only the ambiguous legacy mapping.
context._groupRememberRpiIdentity({...other, rpi_id: '7AA5CED27'});
const legacy = {...rs485(), stm32_uid96: ''};
context._groupRememberRpiIdentity(legacy);
assert.equal(legacy.rpi_id, '');
// Late correct metadata refreshes an existing badge, without rebuilding it.
const row = {...rs485(), rpi_id: '27448D72B', ip: '192.168.1.112'};
context._groupUpdateColumns([row]);
assert.equal(head.querySelector('.group-dev-badge').textContent, '27448D72B/112');
// Network identity remains usable when no fast sensor record ever arrived.
context._groupRpiIdentities = Object.create(null);
context._groupNetwork = {available: true, nodes: [{id: 'BMI30-27448D72B', stm32_uid96: uid}]};
rows = context._groupAugment([rs485()]);
assert.equal(rows[0].rpi_id, '27448D72B');
assert.equal(rows[0]._canControl, false);

function device(lan, remoteUid = uid, remoteRpi = '') {
  return {sync: {role: 'master', device_id: 1, device_id_assigned: true},
    group_seen_mask: (1 << 1) | (1 << 12),
    host: {rpi_id: '05AB38E6A', ip: '192.168.1.107'},
    local: {node_id: 1}, remote: [{node_id: 12, online: true, ip: '10.43.0.1',
      stm32_uid96: remoteUid, short_id: '556E5FA3B', rpi_id: remoteRpi}],
    lan_devices: [lan]};
}
const lan = {api_ok: true, online: true, rpi_id: '7AA5CED27', ip: '10.43.0.1',
  node_id: 12, device_id_assigned: true, role: 'slave', stm32_uid96: 'AAAABBBBCCCCDDDDEEEEFFFF'};
rows = context._buildGroupDevices(device(lan));
assert.equal(rows.find(row => row.node_id === 12).rpi_id, '', 'shared IP/bus address cannot assign another board ID');
rows = context._buildGroupDevices(device({...lan, rpi_id: '27448D72B', stm32_uid96: uid}));
assert.equal(rows.find(row => row.node_id === 12).rpi_id, '27448D72B', 'full hardware UID joins the correct board');
rows = context._buildGroupDevices(device(lan, uid, '27448D72B'));
assert.equal(rows.find(row => row.node_id === 12).rpi_id, '27448D72B', 'known identity survives another LAN node with ID 12');
console.log('PASS: S12 identity, >60s disconnect, separate STM32 UID, late badge update, UID collision, LAN IP/bus-ID isolation');

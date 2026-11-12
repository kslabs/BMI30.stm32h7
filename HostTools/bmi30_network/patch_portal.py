"""Add per-device network state to the active websplit Group table."""
import ast
from pathlib import Path
import sys

HELPER = '''
def read_bmi30_network_summary() -> dict:
    try:
        path = pathlib.Path("/run/bmi30-network/status.json")
        if path.stat().st_size > 524288:
            return {"available": False}
        state = json.loads(path.read_text(encoding="utf-8"))
        now = time.time()
        age = max(0.0, now - float(state.get("checked_at", 0)))
        fields = ("id", "code", "role", "rs485_id", "stm32_uid96", "issued", "parent", "path",
                  "uplink", "iface", "ssid", "freq", "signal", "rssi_dbm", "internet")
        return {"available": age < 60, "age_s": round(age), "coordinator": state.get("coordinator"),
                "local_id": (state.get("local") or {}).get("id"),
                "nodes": [{k: n.get(k) for k in fields} for n in state.get("nodes", [])[:128]]}
    except (OSError, ValueError, TypeError):
        return {"available": False, "nodes": []}

'''

JS = '''
    var _groupNetwork = {};
    function _groupNetworkText(d, field) {
      var net = _groupNetwork || {};
      var nodes = net.nodes || [];
      var rid = String(d.rpi_id || '').toUpperCase();
      var uid = String(d.stm32_uid96 || '').toUpperCase();
      var n = nodes.find(function(item) {
        return (d.is_local && item.id === net.local_id) ||
          (rid && item.id === 'BMI30-' + rid) ||
          (uid && String(item.stm32_uid96 || '').toUpperCase() === uid);
      });
      if (!net.available) { return field === 'network' ? 'Service unavailable' : '—'; }
      if (!n) { return field === 'network' ? 'No BMI30 link' : '—'; }
      var age = Math.max(0, Math.round(Date.now()/1000 - Number(n.issued || 0)));
      if (age > 60) { return field === 'network' ? 'Last seen ' + age + ' s ago' : 'Unknown'; }
      if (field === 'network') {
        if (d.is_local && nodes.length < 2) { return 'Searching for peers'; }
        return 'Connected' + (n.id === net.coordinator ? ' · Coordinator' : '');
      }
      if (field === 'link') {
        var via;
        if (n.parent) {
          var p = nodes.find(function(item) { return item.id === n.parent; });
          via = 'Via ' + (p && p.code ? p.code : n.parent);
        } else { via = n.uplink === 'ethernet' ? 'Ethernet' : (n.ssid ? 'Router' : 'Not connected'); }
        if (n.uplink === 'wifi' && n.freq) {
          via += ' · ' + (n.freq >= 4900 ? '5 GHz' : '2.4 GHz') + ' · ' + n.signal + '%';
        }
        return via;
      }
      if (field === 'internet') {
        return n.internet && n.internet.ok ? 'Online · ' + Math.round(n.internet.rtt_ms) + ' ms' : 'No internet';
      }
      return '—';
    }
'''


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Unexpected portal structure: ' + old[:90])
    return source.replace(old, new)


def patch(source):
    source = once(source, 'def install_wifi_recovery() -> dict[str, Any]:', HELPER + 'def install_wifi_recovery() -> dict[str, Any]:')
    source = once(source, '            device_sensors["optic_settings"] = _read_core_optic_settings()',
                  '            device_sensors["bmi30_network"] = read_bmi30_network_summary()\n'
                  '            device_sensors["optic_settings"] = _read_core_optic_settings()')
    source = once(source, '                  <tr data-group-row="role"><th class="group-param">Current role</th></tr>',
                  '\n'.join('                  <tr data-group-row="' + key + '"><th class="group-param">' + label + '</th></tr>'
                            for key, label in [('network','BMI30 network'),('link','Uplink / signal'),('internet','Internet')]))
    source = once(source, '    function _groupRebuildColumns(devices) {{',
                  JS.replace('{','{{').replace('}','}}') + '    function _groupRebuildColumns(devices) {{')
    old = '        if (_groupRows.role) {{ var rc = _groupMakeTextCell(d); _groupSetText(rc, _capitalizeRole(d.role)); _groupRows.role.appendChild(rc); col.cells.role = rc; }}'
    new = '''        ['network','link','internet'].forEach(function(key) {{
          if (_groupRows[key]) {{ var cell = _groupMakeTextCell(d); _groupSetText(cell, _groupNetworkText(d,key));
            _groupRows[key].appendChild(cell); col.cells[key] = cell; }}
        }});'''
    source = once(source, old, new)
    source = once(source, '        if (col.cells.role) {{ _groupSetText(col.cells.role, _capitalizeRole(d.role)); }}',
                  "        ['network','link','internet'].forEach(function(key) {{ if(col.cells[key]) {{ _groupSetText(col.cells[key], _groupNetworkText(d,key)); }} }});")
    source = once(source, '      var devices = _buildGroupDevices(device || {{}});',
                  '      _groupNetwork = (device || {{}}).bmi30_network || {{}};\n      var devices = _buildGroupDevices(device || {{}});')
    source = once(source, 'Only devices present in the current STM32 RS485 mask are shown.',
                  'Only devices present in the current STM32 RS485 mask are shown. BMI30 network and Internet are reported separately. '
                  'Uplink / signal shows each device\'s own router or relay connection; signal is the NetworkManager link quality. '
                  'Internet latency is an HTTPS check, not an inter-device synchronization measurement.')
    ast.parse(source)
    return source


if __name__ == '__main__':
    src, dst = map(Path,sys.argv[1:])
    dst.write_text(patch(src.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')

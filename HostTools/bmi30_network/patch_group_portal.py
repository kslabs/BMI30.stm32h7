"""Extend the portal's existing Group table; preserve its RS485 fallback."""
import ast
from pathlib import Path
import sys

HELPER = '''
def read_bmi30_group_file(name: str, freshness: float) -> dict:
    try:
        path = pathlib.Path("/run/bmi30-network") / name
        if path.stat().st_size > 1048576:
            raise ValueError("Status file exceeds limit")
        state = json.loads(path.read_text(encoding="utf-8"))
        now = time.time()
        age = now - float(state.get("checked_at", 0))
        fields = ("id", "code", "role", "rs485_id", "stm32_uid96", "issued", "parent", "path",
                  "uplink", "iface", "ssid", "freq", "signal", "rssi_dbm", "internet")
        nodes = state.get("nodes", [])[:128]
        if name == "status.json":
            nodes = [{k: n.get(k) for k in fields} for n in nodes]
        for n in nodes:
            n["age_s"] = max(0, now - float(n.get("issued", 0)))
        return {"available": 0 <= age < freshness, "age_s": round(age, 2),
                "coordinator": state.get("coordinator"),
                "local_id": state.get("local_id") or (state.get("local") or {}).get("id"), "nodes": nodes}
    except (OSError, ValueError, TypeError, KeyError):
        return {"available": False, "nodes": []}


def apply_bmi30_group_command(body: dict) -> dict:
    import urllib.request
    action, params, target = body.get("action"), body.get("params"), body.get("target")
    if action not in ("sync_mode", "rs485_id", "optic_hold", "optic_reaction") or not isinstance(params, dict):
        return {"ok": False, "error": "Unsupported group command"}
    if target == "local":
        request = urllib.request.Request("http://127.0.0.1:8765/api/command",
                    json.dumps({"action": action, "params": params}).encode(), {"Content-Type": "application/json"})
        timeout = 3.5
    else:
        state = read_bmi30_group_file("group.json", 2)
        peer = next((n for n in state["nodes"] if n.get("id") == target and n["age_s"] < 4), None)
        if not state["available"] or not peer or not peer.get("available"):
            return {"ok": False, "error": "BMI30 channel unavailable; remote control is disabled"}
        request = urllib.request.Request("http://127.0.0.1:8791/local-command",
                    json.dumps({"target": target, "action": action, "params": params}).encode(),
                    {"Content-Type": "application/json", "X-BMI30-Local": "1"})
        timeout = 9.5
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read(16384))
        return {"ok": bool(result.get("ok")), "error": str(result.get("error") or "")[:240]}
    except Exception:
        return {"ok": False, "error": "Result not confirmed; refresh state before retrying"}

'''

HANDLER = '''        if path == "/api/group-command":
            if self._get_portal_session() is None:
                self.send_response(HTTPStatus.FORBIDDEN)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 4096:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                result = apply_bmi30_group_command(body)
            except (ValueError, TypeError, AttributeError):
                result = {"ok": False, "error": "Invalid group command"}
            payload = json.dumps(result).encode("utf-8")
            self.send_response(HTTPStatus.OK if result["ok"] else HTTPStatus.BAD_REQUEST)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

'''


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Unexpected portal structure: ' + old[:90])
    return source.replace(old, new)


def replace_function(source, name, new=''):
    begin = source.index('    function ' + name + '(')
    end = source.index('\n    function ', begin + 1)
    return source[:begin] + new + source[end:]


def patch(source):
    source = once(source, 'def install_wifi_recovery() -> dict[str, Any]:', HELPER + 'def install_wifi_recovery() -> dict[str, Any]:')
    source = once(source, '            device_sensors["optic_settings"] = _read_core_optic_settings()',
                  '            device_sensors["bmi30_network"] = read_bmi30_group_file("status.json", 60)\n'
                  '            device_sensors["bmi30_group"] = read_bmi30_group_file("group.json", 2)\n'
                  '            device_sensors["optic_settings"] = _read_core_optic_settings()')
    source = once(source, '        if path == "/api/group-sync-config":', HANDLER + '        if path == "/api/group-sync-config":')
    rows = [('network','Group channel'),('link','Uplink / signal'),('internet','Internet')]
    source = once(source, '                  <tr data-group-row="role"><th class="group-param">Current role</th></tr>',
                  '\n'.join('                  <tr data-group-row="' + key + '"><th class="group-param">' + label + '</th></tr>' for key,label in rows))
    more = [('relay','Relay'),('led','LED pattern'),('sound','Sound active'),('fault','Fault')]
    source = once(source, '                  <tr data-group-row="online"><th class="group-param">RS485 online</th></tr>',
                  '                  <tr data-group-row="online"><th class="group-param">Device online</th></tr>\n' +
                  '\n'.join('                  <tr data-group-row="' + key + '"><th class="group-param">' + label + '</th></tr>' for key,label in more))
    for name in ('_groupApplyOptic','_groupApplySyncRole','_groupApplyRs485Id',
                 '_groupMakeSyncRoleCell','_groupMakeRs485IdCell','_groupMakeReactionCell','_groupMakeHoldCell',
                 '_groupUpdateColumns','_groupCompositionSig'):
        source = replace_function(source, name)
    js = Path(__file__).with_name('group_ui.js').read_text(encoding='utf-8')
    source = once(source, '    function _groupRebuildColumns(devices) {{',
                  js.replace('{','{{').replace('}','}}') + '\n    function _groupRebuildColumns(devices) {{')
    old = '        if (_groupRows.role) {{ var rc = _groupMakeTextCell(d); _groupSetText(rc, _capitalizeRole(d.role)); _groupRows.role.appendChild(rc); col.cells.role = rc; }}'
    new = '''        ['network','link','internet','relay','led','sound','fault'].forEach(function(key) {{
          if (_groupRows[key]) {{ var cell = _groupMakeTextCell(d); _groupSetText(cell, _groupNetworkText(d,key));
            _groupRows[key].appendChild(cell); col.cells[key] = cell; }}
        }});'''
    source = once(source, old, new)
    source = replace_function(source, '_updateGroupMatrix', '''    function _updateGroupMatrix(device) {{
      if (!_groupMatrix) {{ return; }}
      _groupNetwork = (device || {{}}).bmi30_network || {{}};
      _groupFast = (device || {{}}).bmi30_group || {{}};
      _groupLatest = {{}};
      var devices = _groupAugment(_buildGroupDevices(device || {{}}));
      if (_groupEmpty) {{ _groupEmpty.hidden = devices.length > 0; }}
      _groupCommitDevices(devices, _groupCompositionSig(devices));
      _groupRefreshControls();
    }}
''')
    source = once(source, 'Only devices present in the current STM32 RS485 mask are shown.',
                  'BMI30 devices and current RS485 devices are shown. Group channel identifies the active source; '
                  'RS485 resumes automatically when BMI30 data expires. Remote controls require BMI30. '
                  'Neighbor optics always refer to the target device\'s RS485 bus. '
                  'Uplink quality describes each device\'s router or relay connection; Internet latency is an HTTPS check.')
    assert 'Assigned:' not in source[source.index('    function _buildGroupDevices'):source.index('    function _updateDeviceSensors')]
    ast.parse(source)
    return source


if __name__ == '__main__':
    src, dst = map(Path,sys.argv[1:])
    dst.write_text(patch(src.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')

"""Add local access controls to an explicitly supplied active portal/runtime."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent

def once(text, old, new):
    assert text.count(old) == 1, old[:100]
    return text.replace(old, new, 1)

PORTAL_HELPER = '''
def bmi30_access_call(path, data=None):
    import http.client
    token = json.loads(Path('/run/bmi30-access/token.json').read_text())['token']
    connection = http.client.HTTPConnection('127.0.0.1', 8793, timeout=12)
    try:
        connection.request('POST', path, json.dumps(data or {}),
            {'Content-Type': 'application/json', 'X-BMI30-Token': token})
        response = connection.getresponse()
        body = response.read(65536)
        if response.status != 200:
            raise RuntimeError('Access service rejected request')
        return json.loads(body)
    finally:
        connection.close()


def bmi30_access_qr(kind):
    import io
    import sys
    if '/opt/bmi30-network/vendor' not in sys.path:
        sys.path.insert(0, '/opt/bmi30-network/vendor')
    import qrcode
    import qrcode.image.svg
    if kind == 'portal':
        text = 'http://10.42.0.1/'
    elif kind == 'wifi':
        hotspot = detect_hotspot_connection()
        connection = hotspot.get('connection_id') or HOTSPOT_CONN
        ssid = run_command('nmcli', '-g', '802-11-wireless.ssid', 'connection', 'show', connection).strip()
        password = run_command('nmcli', '--show-secrets', '-g', '802-11-wireless-security.psk', 'connection', 'show', connection).strip()
        def escape(value):
            return ''.join(('\\\\' + c) if c in '\\\\;,:"' else c for c in value)
        text = 'WIFI:T:WPA;S:' + escape(ssid) + ';P:' + escape(password) + ';;'
    else:
        raise ValueError('Invalid QR kind')
    output = io.BytesIO()
    qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=4).save(output)
    return output.getvalue()


'''

GET_HANDLER = '''        if path == "/bmi30-connect.cmd":
            payload = Path('/opt/bmi30-network/Open-BMI30.cmd').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename="Open-BMI30.cmd"')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if send_body: self.wfile.write(payload)
            return

        if path in {"/api/access/status", "/api/access/qr"}:
            if self._get_portal_session() is None:
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403, send_body=send_body)
                return
            try:
                if path.endswith('/qr'):
                    kind = parse_qs(urlsplit(self.path).query).get('kind', ['portal'])[0]
                    payload = bmi30_access_qr(kind)
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/svg+xml')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    if send_body: self.wfile.write(payload)
                else:
                    self._send_json_response(bmi30_access_call('/status'), send_body=send_body)
            except Exception:
                self._send_json_response({"ok": False, "error": "Служба связи недоступна. Попробуйте ещё раз."}, 503, send_body=send_body)
            return

'''

POST_HANDLER = '''        if path == "/api/access/action":
            origin = self.headers.get('Origin', '')
            if (self._get_portal_session() is None or self.headers.get('X-BMI30-Access') != '1'
                    or (origin and urlsplit(origin).netloc != self.headers.get('Host', ''))):
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096: raise ValueError('Invalid size')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict): raise ValueError('Invalid request')
                response = bmi30_access_call('/action', data)
                self._send_json_response(response, 202)
            except Exception:
                self._send_json_response({"ok": False, "error": "Действие не выполнено. Проверьте выбор сети и повторите."}, 409)
            return

'''

def portal(text):
    assert 'def bmi30_access_call(' not in text
    text = once(text, 'def connect_wifi_internet(', PORTAL_HELPER + 'def connect_wifi_internet(')
    text = once(text, '    args = ["dev", "wifi", "connect", ssid, "ifname", WIFI_STA_IFACE]',
        '''    try:
        bmi30_access_call('/prepare-connect', {'ssid': ssid})
    except Exception:
        return False, "Служба связи недоступна. Повторите подключение позже."
    args = ["dev", "wifi", "connect", ssid, "ifname", WIFI_STA_IFACE]''')
    text = once(text, '        if path == "/api/status":', GET_HANDLER + '        if path == "/api/status":')
    text = once(text, '    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]',
        '    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]\n' + POST_HANDLER)
    # Insert only in authenticated portal HTML; the login page remains available.
    marker = '    wifi_meta_raw = config_payload.get("wifi_internet")'
    assert marker in text
    # The portal HTML already receives offline JS as Python constants.
    text = once(text, 'def bmi30_access_call(', 'BMI30_ACCESS_JS = ' + repr('<script>'+ (HERE/'access_ui.js').read_text(encoding='utf-8')+'</script>') + '\n\n\ndef bmi30_access_call(')
    # Insert into render_portal_page's returned HTML, before the first form's script.
    start = text.index('def render_portal_page(')
    end = text.find('\ndef ', start+5)
    if end < 0: end = len(text)
    section = text[start:end]
    assert '</body>' in section
    section = once(section, '</body>', '{BMI30_ACCESS_JS}\n</body>')
    text = text[:start]+section+text[end:]
    ast.parse(text)
    return text

def service(text):
    text = once(text, 'import network\n', 'import network\nimport access_policy\n')
    text = once(text, 'VERSION = "0.1.16"', 'VERSION = "0.1.17"')
    text = once(text, '    def remember_secret(self, key, value):\n',
        '    def remember_secret(self, key, value):\n        if isinstance(key, str) and key.startswith("wifi:") and access_policy.forgotten(key[5:]):\n            return\n')
    text = once(text, '        rescan = now - self.last_scan > 65',
        '        rescan = now - self.last_scan > 65 and not local["clients"] and not access_policy.maintenance()')
    text = once(text, '            profiles.update(self.profile_cache)',
        '''            profiles.update(self.profile_cache)
            profiles = {k: v for k, v in profiles.items() if not access_policy.forgotten(k)}
            blocked = [k for k in self.secrets if k.startswith('wifi:') and access_policy.forgotten(k[5:])]
            for k in blocked: self.secrets.pop(k, None)
            if blocked: network.atomic(self.secrets_path, json.dumps(self.secrets))''')
    text = once(text, '            if self.config.get("observe", False):',
        '''            if local.get("clients", 0) or access_policy.offline_mode() or access_policy.maintenance():
                can_switch = False
            if self.config.get("observe", False):''')
    # Prevent blocked saved profiles from being re-published during this tick.
    text = once(text, '            if local["path"] == [local["id"]] and local["ssid"] in self.profile_cache:',
        '            if local["path"] == [local["id"]] and local["ssid"] in self.profile_cache and not access_policy.forgotten(local["ssid"]):')
    ast.parse(text)
    return text

def communication(text):
    text = once(text, '    return result\n\n\ndef atomic(',
        '''    import access_policy
    if access_policy.offline_mode():
        result['wifi'] = False
    return result


def atomic(''')
    ast.parse(text)
    return text

def watchdog(text):
    marker = '        config = self.load_config()  # Malformed/unreadable config: fail closed.'
    replacement = '''        import sys
        if '/opt/bmi30-network' not in sys.path: sys.path.insert(0, '/opt/bmi30-network')
        import access_policy
        if access_policy.offline_mode() or access_policy.maintenance() or access_policy.ap_clients():
            return self.reset('local-access-priority')
''' + marker
    text = once(text, marker, replacement)
    ast.parse(text)
    return text

def hotspot(text):
    text = once(text, '        ipv4.method shared \\\n', '        ipv4.method shared \\\n        ipv4.addresses 10.42.0.1/24 \\\n        wifi-sec.wps-method disabled \\\n')
    text = once(text, 'SSID="${BMI30_HOTSPOT_SSID:-${saved_ssid:-${NETWORK_NAME_PREFIX}${suffix}}}"',
        '''# A cloned default SSID belongs to the new hardware; custom labels survive.
if [[ "$saved_ssid" =~ ^BMI30-[0-9A-Fa-f]{9}$ && "${saved_ssid^^}" != "BMI30-${suffix^^}" ]]; then
    saved_ssid=""
fi
SSID="${BMI30_HOTSPOT_SSID:-${saved_ssid:-${NETWORK_NAME_PREFIX}${suffix}}}"''')
    return text

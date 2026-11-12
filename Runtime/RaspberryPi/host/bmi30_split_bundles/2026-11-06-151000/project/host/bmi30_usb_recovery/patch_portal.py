"""Add a recovery page using the portal's existing session and origin checks."""
import ast, pathlib
HERE=pathlib.Path(__file__).resolve().parent

HELPER='''
_BMI30_USB_MODULE = None
def bmi30_usb_module():
    global _BMI30_USB_MODULE
    if _BMI30_USB_MODULE is None:
        import sys, importlib.util
        core = pathlib.Path(_resolve_project_path(detect_bmi30_split_system_version()['core_path']))
        directory = core.parent / 'bmi30_usb_recovery'
        sys.path.insert(0, str(directory))
        spec = importlib.util.spec_from_file_location('usb_recovery_backend', directory / 'usb_recovery_backend.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BMI30_USB_MODULE = module
    return _BMI30_USB_MODULE

'''

ORIGIN_HELPER='''
def bmi30_usb_origin_allowed(handler):
    origin = handler.headers.get('Origin', '')
    if not origin:
        return True
    host = handler.headers.get('Host', '')
    parsed = urlsplit(origin)
    if host and parsed.netloc == host:
        return True
    try:
        import ipaddress
        if not ipaddress.ip_address(handler.client_address[0]).is_loopback:
            return False
        cfg = json.loads(pathlib.Path('/etc/bmi30-agent/config.json').read_text())
        public = urlsplit(str(cfg.get('server_url') or ''))
        if public.scheme != 'https' or parsed.scheme != 'https' or parsed.username or parsed.password:
            return False
        hosts = {public.hostname}
        if public.hostname in ('teiots.net', 'www.teiots.net'):
            hosts.update(('teiots.net', 'www.teiots.net'))
        return parsed.hostname in hosts and (parsed.port or 443) == (public.port or 443)
    except (OSError, ValueError, TypeError):
        return False

'''

def upgrade_origin(source):
    if 'def bmi30_usb_origin_allowed(' in source:return source
    old="(origin and urlsplit(origin).netloc != self.headers.get('Host', ''))"
    if source.count(old)<1:raise ValueError('Unknown USB request origin check')
    # Restrict the change to the USB POST handler, preserving all other APIs.
    begin=source.index('        if path in ("/api/usb-recovery/plan"')
    end=source.index('            try:',begin)
    prefix=source[begin:end]
    if prefix.count(old)!=1:raise ValueError('Unknown USB POST guard')
    source=source[:begin]+prefix.replace(old,'(not bmi30_usb_origin_allowed(self))',1)+source[end:]
    anchor='class HotspotInfoHandler(BaseHTTPRequestHandler):'
    source=source.replace(anchor,ORIGIN_HELPER+anchor,1)
    return source

GET='''        if path == "/portal-recovery" or path == "/api/usb-recovery/status":
            if path == "/portal-recovery":
                if self._require_portal_session() is None:
                    return
            elif self._get_portal_session() is None:
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403, send_body=send_body)
                return
            try:
                module = bmi30_usb_module()
                if path.endswith('/status'):
                    self._send_json_response(module.dispatch('status'), send_body=send_body)
                else:
                    payload = (module.MODULE / 'recovery.html').read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    if send_body: self.wfile.write(payload)
            except Exception:
                self._send_json_response({"ok": False, "error": "Восстановление временно недоступно."}, 503, send_body=send_body)
            return

'''

POST='''        if path in ("/api/usb-recovery/plan", "/api/usb-recovery/start", "/api/usb-recovery/read-stm32", "/api/usb-recovery/enable-emmc"):
            origin = self.headers.get('Origin', '')
            if (self._get_portal_session() is None or self.headers.get('X-BMI30-USB') != '1'
                    or (not bmi30_usb_origin_allowed(self))):
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096: raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict): raise ValueError('Invalid request')
                response = bmi30_usb_module().dispatch(path.rsplit('/', 1)[-1], data)
                self._send_json_response(response, 202 if path.endswith('/start') else 200)
            except Exception as error:
                module = bmi30_usb_module()
                message = str(error) if isinstance(error, module.Refused) else 'Действие не выполнено. Обновите список USB.'
                self._send_json_response({"ok": False, "error": message}, 409)
            return

'''

def patch(source):
    if '_BMI30_USB_MODULE' in source:
        source=source.replace('USB · Восстановление системы','USB · Аварийная система')
        source=source.replace('("/api/usb-recovery/plan", "/api/usb-recovery/start"):',
                              '("/api/usb-recovery/plan", "/api/usb-recovery/start", "/api/usb-recovery/read-stm32", "/api/usb-recovery/enable-emmc"):')
        source=source.replace('("/api/usb-recovery/plan", "/api/usb-recovery/start", "/api/usb-recovery/read-stm32"):',
                              '("/api/usb-recovery/plan", "/api/usb-recovery/start", "/api/usb-recovery/read-stm32", "/api/usb-recovery/enable-emmc"):')
        source=upgrade_origin(source)
        ast.parse(source);return source
    anchor='class HotspotInfoHandler(BaseHTTPRequestHandler):'
    assert source.count(anchor)==1;source=source.replace(anchor,HELPER+ORIGIN_HELPER+anchor,1)
    anchor='    def _handle_request(self, send_body: bool) -> None:\n        path = self.path.split("?", 1)[0]\n'
    assert source.count(anchor)==1;source=source.replace(anchor,anchor+GET,1)
    anchor='    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]\n'
    assert source.count(anchor)==1;source=source.replace(anchor,anchor+POST,1)
    anchor='        <section class="portal-panel" id="panel-privacy">'
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+'\n          <div class="privacy-access-actions"><a class="link" href="portal-recovery">USB · Аварийная система</a></div>',1)
    ast.parse(source)
    return source

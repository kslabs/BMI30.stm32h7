"""Authenticated, read-only saved Wi-Fi page and explicit password disclosure."""
import ast

HELPER='''
_BMI30_WIFI_PROFILES_MODULE = None

def bmi30_wifi_profiles_module():
    global _BMI30_WIFI_PROFILES_MODULE
    if _BMI30_WIFI_PROFILES_MODULE is None:
        import importlib.util
        directory = bmi30_usb_module().MODULE
        spec = importlib.util.spec_from_file_location('bmi30_wifi_profiles', directory / 'wifi_profiles.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BMI30_WIFI_PROFILES_MODULE = module
    return _BMI30_WIFI_PROFILES_MODULE

'''

GET='''        if path in ("/portal-wifi-profiles", "/api/wifi-profiles"):
            if path == "/portal-wifi-profiles":
                if self._require_portal_session() is None: return
            elif self._get_portal_session() is None:
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403, send_body=send_body)
                return
            try:
                if path == "/api/wifi-profiles":
                    self._send_json_response(bmi30_wifi_profiles_module().dispatch('list'), send_body=send_body)
                else:
                    payload = (bmi30_usb_module().MODULE / 'wifi_profiles.html').read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    if send_body: self.wfile.write(payload)
            except Exception:
                self._send_json_response({"ok": False, "error": "Saved networks are temporarily unavailable."}, 503, send_body=send_body)
            return

'''

POST='''        if path == "/api/wifi-profiles/password":
            if (self._get_portal_session() is None or self.headers.get('X-BMI30-WIFI') != '1'
                    or not bmi30_usb_origin_allowed(self)):
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096: raise ValueError()
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict): raise ValueError()
                self._send_json_response(bmi30_wifi_profiles_module().dispatch('password', data))
            except Exception:
                self._send_json_response({"ok": False, "error": "The saved password could not be read."}, 409)
            return

'''


def group_links(source):
    wifi='<a class="link" href="portal-wifi-profiles">Saved Wi-Fi networks and passwords</a>'
    usb='<a class="link" href="portal-recovery">USB · Аварийная система</a>'
    separate='<div class="privacy-access-actions">'+wifi+'</div>\n          <div class="privacy-access-actions">'+usb+'</div>'
    together='<div class="privacy-access-actions">'+wifi+' '+usb+'</div>'
    if together in source:return source
    if source.count(separate)!=1:raise ValueError('Unknown privacy navigation layout')
    return source.replace(separate,together,1)


def patch(source):
    if '_BMI30_WIFI_PROFILES_MODULE' in source: return group_links(source)
    anchor='class HotspotInfoHandler(BaseHTTPRequestHandler):'
    assert source.count(anchor)==1
    source=source.replace(anchor,HELPER+anchor,1)
    for anchor,extra in (
        ('    def _handle_request(self, send_body: bool) -> None:\n        path = self.path.split("?", 1)[0]\n',GET),
        ('    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]\n',POST)):
        assert source.count(anchor)==1
        source=source.replace(anchor,anchor+extra,1)
    anchor='        <section class="portal-panel" id="panel-privacy">'
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+'\n          <div class="privacy-access-actions"><a class="link" href="portal-wifi-profiles">Saved Wi-Fi networks and passwords</a></div>',1)
    source=group_links(source)
    ast.parse(source)
    return source

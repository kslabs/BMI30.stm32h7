"""Prepare a checked portal patch. Does not install or restart BMI30 services."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Portal patch anchor changed: " + repr(old[:100]))
    return text.replace(old, new, 1)


def patch(text):
    helpers = '''
_radar_service = None
_radar_service_lock = threading.Lock()

def _get_radar_service():
    global _radar_service
    with _radar_service_lock:
        if _radar_service is None:
            import sys
            if "/opt/bmi30-network" not in sys.path:
                sys.path.insert(0, "/opt/bmi30-network")
            from radar_gate import RadarGateService
            config = pathlib.Path(os.getenv("BMI30_CONFIG_JSON", "/home/techaid/Documents/host/bmi30_config.json"))
            _radar_service = RadarGateService(config.with_name("bmi30_radar_port.json"))
        return _radar_service

'''
    text = once(text, 'def _initial_page_revision() -> str:', helpers + 'def _initial_page_revision() -> str:')
    anchor = '          </form>\n        </section>\n        <section class="portal-panel" id="panel-group">'
    text = once(text, anchor, '          </form>\n' + (HERE / 'portal_radar.html').read_text() +
                '        </section>\n        <section class="portal-panel" id="panel-group">')
    css = (HERE / 'portal_radar.css').read_text().replace('{', '{{').replace('}', '}}')
    text = once(text, '    #panel-group .group-head{{', css + '\n    #panel-group .group-head{{')
    js = (HERE / 'portal_radar.js').read_text().replace('{', '{{').replace('}', '}}')
    text = once(text, '    var groupLedForm = document.querySelector', js + '\n    var groupLedForm = document.querySelector')
    anchor = '    def _handle_request(self, send_body: bool) -> None:\n        path = self.path.split("?", 1)[0]\n'
    text = once(text, anchor, anchor + '''        if path == "/api/radar":
            if self._get_portal_session() is None:
                self._send_json_response({"ok": False, "error": "Not authorized."}, HTTPStatus.FORBIDDEN)
                return
            try:
                result = _get_radar_service().snapshot()
                self._send_json_response(result, HTTPStatus.OK)
            except Exception as exc:
                self._send_json_response({"ok": False, "error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
''')
    anchor = '    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]\n'
    text = once(text, anchor, anchor + '''        if path == "/api/radar":
            if self._get_portal_session() is None:
                self._send_json_response({"ok": False, "error": "Not authorized."}, HTTPStatus.FORBIDDEN)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    self.close_connection = True
                    raise ValueError("Invalid radar request length")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                result = _get_radar_service().command(body)
                self._send_json_response(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._send_json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
''')
    ast.parse(text)
    return text


def main():
    p = argparse.ArgumentParser()
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    old = args.source.read_bytes()
    text = patch(old.decode('utf-8'))
    target = args.output / 'hotspot_info_server.py'
    target.write_text(text, encoding='utf-8', newline='\n')
    shutil.copy2(HERE / 'radar_gate.py', args.output / 'radar_gate.py')
    (args.output / 'patch-manifest.json').write_text(json.dumps({
        'base_portal_sha256': hashlib.sha256(old).hexdigest(),
        'patched_portal_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'transport_sha256': hashlib.sha256((args.output / 'radar_gate.py').read_bytes()).hexdigest(),
        'installed': False, 'origin': str(args.source)
    }, indent=2), encoding='utf-8')
    print(str(target))


if __name__ == '__main__':
    main()

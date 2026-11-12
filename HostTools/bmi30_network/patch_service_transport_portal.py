"""Add a local group transport selector to an already clock-independent portal."""
import ast
from pathlib import Path

from patch_rpi_identity import replace_python_function

PATCH_MARKER = '"service_transport_version": "1",'


def patch(source):
    if PATCH_MARKER in source:
        return source
    marker = '"service_clock_mode": "monotonic-v2",'
    if source.count(marker) != 1:
        raise ValueError('Clock-independent portal required')
    source = source.replace(marker, marker + '\n        ' + PATCH_MARKER, 1)
    start = source.index('var _groupNetwork = {{}};')
    end = source.index('    function _groupRebuildColumns(', start)
    ui = Path(__file__).with_name('group_ui.js').read_text(encoding='utf-8')
    ui = ui.removeprefix('// The RS485 builder remains the fallback. Fast rows join by hardware identity.\n')
    source = source[:start] + ui.replace('\\', '\\\\').replace('{', '{{').replace('}', '}}') + '\n' + source[end:]
    marker = '            <p class="group-legend">'
    if source.count(marker) != 1:
        raise ValueError('Unexpected group panel')
    selector = '''            <div class="group-transport" style="margin:12px 0;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <label for="group-service-transport" data-group-transport-label>Service channel on this device</label>
              <select id="group-service-transport" data-group-transport disabled>
                <option value="wifi">Wi-Fi</option><option value="bluetooth">Bluetooth</option>
                <option value="both" selected>Wi-Fi + Bluetooth</option><option value="off">Off</option>
              </select>
              <span role="status" aria-live="polite" data-group-transport-result></span>
              <small data-group-transport-hint>Select on each device. Internet and page access stay connected.</small>
            </div>
'''
    source = source.replace(marker, selector + marker, 1)
    marker = '        if path == "/api/group-command":'
    if source.count(marker) != 1:
        raise ValueError('Unexpected group command handler')
    route = '''        if path == "/api/group-service-transports":
            if self._get_portal_session() is None:
                self.send_response(HTTPStatus.FORBIDDEN)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1024:
                    raise ValueError("Invalid request size")
                result = apply_bmi30_service_transports(json.loads(self.rfile.read(length)))
            except (ValueError, TypeError, AttributeError):
                result = {"ok": False, "error": "Invalid service channel selection"}
            payload = json.dumps(result).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

'''
    source = source.replace(marker, route + marker, 1)
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'read_bmi30_group_file')
    function = ast.get_source_segment(source, node)
    function = function.replace('"coordinator": state.get("coordinator"),',
        '"coordinator": state.get("coordinator"),\n                "service_transports": state.get("service_transports"),')
    if '"service_transports"' not in function:
        raise ValueError('Unexpected group status reader')
    source = replace_python_function(source, 'read_bmi30_group_file', function)
    helper = '''def apply_bmi30_service_transports(body: dict) -> dict:
    import urllib.request
    if (body.get("mode") not in ("wifi", "bluetooth", "both", "off")
            or not isinstance(body.get("revision"), str)):
        return {"ok": False, "error": "Invalid service channel selection"}
    request = urllib.request.Request("http://127.0.0.1:8791/local-service-transports",
        json.dumps({"mode": body["mode"], "revision": body["revision"]}).encode(),
        {"Content-Type": "application/json", "X-BMI30-Local": "1"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=4) as response:
            return json.loads(response.read(16384))
    except Exception:
        return {"ok": False, "error": "Selection not confirmed; refresh before retrying"}


'''
    source = source.replace('def apply_bmi30_group_command(', helper + 'def apply_bmi30_group_command(', 1)
    source = source.replace('BMI30 channel unavailable; remote control is disabled',
                            'Service channel unavailable; remote control is disabled')
    ast.parse(source, feature_version=(3, 11))
    return source

"""Authenticated manual server check, independent of the hourly poll cache."""
import ast

HELPER='''
_BMI30_MANUAL_FW_CHECK = True
def request_manual_firmware_check():
    if _firmware_operation_busy():
        return {"ok": False, "message": "A firmware operation is running."}, 409
    with _FIRMWARE_CONTROL_LOCK:
        if _FIRMWARE_CONTROL_STATE.get("checking"):
            return {"ok": True, "checking": True}, 202
        _FIRMWARE_CONTROL_STATE.update(checking=True, error="")
    _FIRMWARE_CONTROL_WAKE.set()
    return {"ok": True, "checking": True}, 202

'''
GET='''        if path == "/api/firmware-check.js":
            if self._get_portal_session() is None:
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403, send_body=send_body)
                return
            payload = (bmi30_usb_module().MODULE / "firmware_check.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if send_body: self.wfile.write(payload)
            return

'''
POST='''        if path == "/api/firmware-check":
            if self._get_portal_session() is None or self.headers.get("X-BMI30-Firmware-Check") != "1":
                self._send_json_response({"ok": False, "error": "Not authorized."}, 403)
                return
            self._read_raw_post_body()
            result, status = request_manual_firmware_check()
            self._send_json_response(result, status)
            return

'''

def patch(source):
    if '_BMI30_MANUAL_FW_CHECK' in source:return source
    anchor='class HotspotInfoHandler(BaseHTTPRequestHandler):'
    assert source.count(anchor)==1;source=source.replace(anchor,HELPER+anchor,1)
    anchor='    def _handle_request(self, send_body: bool) -> None:\n        path = self.path.split("?", 1)[0]\n'
    assert source.count(anchor)==1;source=source.replace(anchor,anchor+GET,1)
    anchor='    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]\n'
    assert source.count(anchor)==1;source=source.replace(anchor,anchor+POST,1)
    anchor='<div class="firmware-actions" id="firmware-actions" aria-label="Firmware version controls">'
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+'\n              <button class="firmware-action-btn" id="firmware-check-btn" type="button">↻ Check updates</button>',1)
    anchor='          <div class="session-notice-slot">{notice_html}</div>'
    assert source.count(anchor)==1
    source=source.replace(anchor,'          <p id="firmware-check-status" role="status" aria-live="polite" style="margin:4px 0;font-size:12px;opacity:.85"></p>\n'+anchor,1)
    anchor="            renderFirmwareState(data);"
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+"\n            window.dispatchEvent(new CustomEvent('bmi30-firmware-state', {{detail:data}}));",1)
    anchor="      rollbackButton.addEventListener('click', function () {{ startOperation('rollback'); }});"
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+"\n      window.addEventListener('bmi30-firmware-manual-state', function (event) {{ renderFirmwareState(event.detail); }});",1)
    # Only the management page has the marker/button. Defer loads after parsing.
    anchor='  {render_debug_panel_script()}\n  <script>'
    assert source.count(anchor)==1
    source=source.replace(anchor,'  {render_debug_panel_script()}\n  <script src="api/firmware-check.js" defer></script>\n  <script>',1)
    ast.parse(source);return source

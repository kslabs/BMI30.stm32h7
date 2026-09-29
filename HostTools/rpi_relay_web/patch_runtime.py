"""Apply narrowly checked relay integration to a staged websplit project."""
import argparse
import ast
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Expected exactly one patch anchor: '+repr(old[:110]))
    return text.replace(old,new,1)


def patch_core(text):
    text=once(text,'        self.scope = scope\n', '''        self.scope = scope
        from usb_vendor.relay_control import RelayService
        relay_config = Path(os.getenv("BMI30_CONFIG_JSON", str(HOST_DIR / "bmi30_config.json"))).parent / "bmi30_relay.json"
        self.relay_service = RelayService(scope, relay_config)
        scope._relay_service = self.relay_service
''')
    text=once(text,'                    "non_addressable_led": (','                    "relay": self.relay_service.snapshot(),\n                    "non_addressable_led": (')
    text=once(text,'        if parsed.path == "/api/tx-phase":', '''        if parsed.path == "/api/relay":
            _send_json(self, {"ok": True, "relay": api.relay_service.snapshot()})
            return
        if parsed.path == "/api/tx-phase":''')
    text=once(text,'            if action_l == "tx_phase":', '''            if action_l == "relay":
                _send_json(self, api.relay_service.command(params))
                return
            if action_l == "tx_phase":''')
    text=once(text,'    finally:\n        try:\n            server.shutdown()', '''    finally:
        API.relay_service.close()
        try:
            server.shutdown()''')
    text=once(text,'    server = start_http_server()','''    import signal
    previous_term = signal.getsignal(signal.SIGTERM)
    def stop_relay_on_term(signum, frame):
        API.relay_service.close()
        if callable(previous_term):
            previous_term(signum, frame)
        elif previous_term == signal.SIG_DFL:
            raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop_relay_on_term)
    server = start_http_server()''')
    return text


def patch_engine(text):
    text=once(text,'\tdef _rt_stop_detection_outputs(self):\n','''\tdef _rt_stop_detection_outputs(self):
\t\trelay = getattr(self, '_relay_service', None)
\t\tif relay is not None:
\t\t\trelay.observe_detection(False)
''')
    text=once(text,'\t\t\tgate_was_allowed = bool(getattr(self, "_optic_gate_last_allowed", True))', '''\t\t\trelay = getattr(self, '_relay_service', None)
\t\t\tif relay is not None:
\t\t\t\trelay.observe_detection(bool((getattr(self, '_det_hold0', False)
\t\t\t\t\tor getattr(self, '_det_hold1', False)) and gate_allowed
\t\t\t\t\tand not getattr(self, '_recovery_outputs_inhibited', False)
\t\t\t\t\tand self._current_mode_idx() >= 6))
\t\t\tgate_was_allowed = bool(getattr(self, "_optic_gate_last_allowed", True))''')
    text=once(text,'\t\t"""Mark accepted detection for web/API/sound output with a shared minimum hold."""\n', '''\t\t"""Mark accepted detection for web/API/sound output with a shared minimum hold."""
\t\t# Relay has its own minimum timer; do not inherit the sound/LED hold.
\t\t# Publish even one-shot detections that never enter sustained HOLD.
\t\ttry:
\t\t\trelay = getattr(self, '_relay_service', None)
\t\t\tif relay is not None and (fired0 or fired1):
\t\t\t\trelay.observe_detection(bool(self._optic_detection_gate_allowed()
\t\t\t\t\tand not getattr(self, '_recovery_outputs_inhibited', False)
\t\t\t\t\tand self._current_mode_idx() >= 6))
\t\texcept Exception:
\t\t\tpass
''')
    return text


def patch_portal(text, assets):
    text=once(text,'                "non_addressable_led",','                "non_addressable_led",\n                "relay",')
    row='''              <div class="group-nonaddr-led-row" data-relay-root>
                <div class="group-nonaddr-led-label">
                  <strong>Relay</strong>
                  <span>Tag detection · independent permission · Test holds until Stop</span>
                </div>
                <label class="group-nonaddr-led-enable">
                  <input type="checkbox" data-relay-enable aria-label="Relay permitted" disabled>Permitted
                </label>
                <label class="group-relay-duration">Minimum ON (ms)
                  <input type="number" data-relay-duration aria-label="Relay minimum ON time (ms)" min="1" max="2147483647" step="1" value="1000" disabled>
                </label>
                <span class="group-nonaddr-led-level" data-relay-level role="status">Relay: loading…</span>
                <button class="link link-secondary group-led-test-btn" type="button" data-relay-test aria-label="Test relay" aria-pressed="false" disabled>Test</button>
              </div>
'''
    text=once(text,'              <div class="group-led-actions">',row+'              <div class="group-led-actions">')
    text=once(text,'    #panel-group .group-led-actions{{', '''    #panel-group .group-relay-duration{{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:700}}
    #panel-group .group-relay-duration input{{width:110px;min-height:34px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);padding:5px 7px;font:inherit}}
    #panel-group .group-led-actions{{''')
    text=once(text,'    var groupLedForm = document.querySelector',assets.joinpath('portal_relay.js').read_text(encoding='utf-8').replace('{','{{').replace('}','}}')+'\n    var groupLedForm = document.querySelector')
    text=once(text,'      updateNonAddressableLed(settings, false);','      updateNonAddressableLed(settings, false);\n      updateRelay(settings.relay, false);')
    text=once(text,'        if path == "/api/non-addressable-led":', '''        if path == "/api/relay":
            if self._get_portal_session() is None:
                self.send_response(HTTPStatus.FORBIDDEN)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if not 0 < length <= 1024:
                    raise ValueError("Invalid relay request length")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("JSON object expected")
                req = Request(f"{CORE_SERVICE_URL}/api/command",
                    data=json.dumps({"action": "relay", "params": body}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-BMI30-Source": "portal_relay"}, method="POST")
                with urlopen(req, timeout=2.5) as response:
                    result = json.loads(response.read().decode("utf-8"))
                ok = bool(result.get("ok"))
                payload = json.dumps({"ok": ok, "message": result.get("error", ""),
                    "relay": result.get("relay", {}), "persisted": result.get("persisted", False)}).encode("utf-8")
                status = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
            except Exception as exc:
                payload = json.dumps({"ok": False, "message": str(exc)}).encode("utf-8")
                status = HTTPStatus.BAD_REQUEST
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/non-addressable-led":''')
    return text


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',type=Path,required=True)
    parser.add_argument('--engine',type=Path,required=True)
    parser.add_argument('--portal',type=Path,required=True)
    args=parser.parse_args()
    assets=Path(__file__).parent
    for path,fn in [(args.core,patch_core),(args.engine,patch_engine),(args.portal,lambda s:patch_portal(s,assets))]:
        result=fn(path.read_text(encoding='utf-8'))
        ast.parse(result,filename=str(path))
        path.write_text(result,encoding='utf-8',newline='\n')
    module=args.core.parent/'usb_vendor/relay_control.py'
    assert not module.exists(), 'Existing relay module must be reviewed before replacement'
    module.parent.mkdir(parents=True,exist_ok=True)
    module.write_bytes((assets/'relay_control.py').read_bytes())
    print('Patched core, engine, portal; installed independent relay module')


if __name__=='__main__':main()

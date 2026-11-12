"""Add the COM1 recovery contract to the existing websplit USB owner and portal."""
import ast
from pathlib import Path


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Unexpected source anchor: ' + before[:80])
    return source.replace(before, after, 1)


PARSER = '''def parse_communication_recovery(data):
    packet = bytes(data)
    if len(packet) != 24 or packet[:4] != b'COM1' or packet[4] != 1:
        return None
    if packet[5] not in (0, 1) or packet[6] not in (0, 1) or packet[7] != 0:
        return None
    generation, hold_ms = struct.unpack_from('<HH', packet, 8)
    if packet[5] and not generation:
        return None
    return {'version': 1, 'pending': bool(packet[5]), 'durable': bool(packet[6]),
            'generation': generation, 'hold_ms': hold_ms,
            'stm32_uid96': packet[12:24].hex().upper()}


'''

METHODS = '''    def get_communication_recovery(self, force=False):
        now = time.monotonic()
        if not force and now - getattr(self, '_comm_recovery_checked', -10.0) < 1.0:
            return getattr(self, '_comm_recovery_cache', None)
        self._comm_recovery_checked = now
        try:
            packet = self._ep0_ctrl_transfer(0xC0, 0x4F, 0, 0, 24, timeout=120)
            self._comm_recovery_cache = parse_communication_recovery(packet)
        except Exception:
            # Earlier firmware does not implement this optional request.
            self._comm_recovery_cache = None
        return self._comm_recovery_cache

    def ack_communication_recovery(self, generation):
        if type(generation) is not int or not 1 <= generation <= 65535:
            raise ValueError('Invalid recovery generation')
        state = self.get_communication_recovery(force=True)
        if not state or not state['pending'] or not state['durable'] or state['generation'] != generation:
            return False
        self._ep0_ctrl_transfer(0x40, 0x50, generation, 0, None, timeout=120)
        self._comm_recovery_checked = -10.0
        return True

'''


def patch_stream(source):
    source = replace_once(source, 'def _stat_packet_len_from_header(', PARSER + 'def _stat_packet_len_from_header(')
    source = replace_once(source, '    def _get_status_ep0(', METHODS + '    def _get_status_ep0(')
    ast.parse(source)
    return source


def patch_core(source):
    source = replace_once(source, '        if action in {"mode", "set_mode"}:', '''        if action == "communication_recovery_ack":
            generation = params.get("generation")
            if type(generation) is not int or not 1 <= generation <= 65535:
                raise ValueError("Invalid recovery generation")
            stream = getattr(scope, "stream", None)
            if stream is None or not hasattr(stream, "ack_communication_recovery"):
                raise RuntimeError("Recovery transport unavailable")
            acknowledged = stream.ack_communication_recovery(generation)
            return {"ok": True, "acknowledged": acknowledged, "generation": generation}
        if action in {"mode", "set_mode"}:''')
    source = replace_once(source, '    def status(self) -> dict[str, Any]:\n        scope = self.scope', '''    def status(self) -> dict[str, Any]:
        scope = self.scope''')
    source = replace_once(source, '        connected = bool(stream is not None and not bool(getattr(stream, "disconnected", False)))\n        try:\n            selected_mode', '''        connected = bool(stream is not None and not bool(getattr(stream, "disconnected", False)))
        communication_recovery = None
        if connected and hasattr(stream, "get_communication_recovery"):
            communication_recovery = stream.get_communication_recovery()
        try:
            selected_mode''')
    source = replace_once(source, '                "sync": _sync_status_snapshot(device_state_cache),',
        '                "communication_recovery": communication_recovery,\n                "sync": _sync_status_snapshot(device_state_cache),')
    ast.parse(source)
    return source


def patch_portal(source):
    # Apply every saved profile immediately, including profiles which were
    # suppressed at boot. A missing helper keeps older installations compatible.
    source = replace_once(source, '    save_channel_permission(channel, enabled)\n\n    def _result', '''    save_channel_permission(channel, enabled)
    policy = "/opt/bmi30-network/communication_recovery.py"
    if os.path.isfile(policy):
        try:
            subprocess.run(["/usr/bin/python3", str(policy), "--prepare"],
                           check=True, capture_output=True, timeout=10)
            _run_nmcli_result("connection", "reload", timeout=8.0)
        except Exception:
            return False, "Unable to apply saved communication permissions."

    def _result''')
    ast.parse(source)
    return source

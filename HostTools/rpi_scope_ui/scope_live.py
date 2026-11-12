"""Authenticated latest-frame oscilloscope stream; no hardware ownership."""
import json
import select
import socket
import struct
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener

from realtime import accept_key, consume, frame, same_origin

SCOPE_SLOTS = threading.BoundedSemaphore(4)

UI_STATUS_FIELDS = ('ok', 'busy', 'stale', 'service', 'connection', 'mode', 'fps',
                    'detector', 'normalization', 'smoothing', 'optimize', 'sync',
                    'tim2', 'sound', 'working_window', 'player_recorder')

def status_bytes(raw):
    full = json.loads(raw)
    return json.dumps({key: full[key] for key in UI_STATUS_FIELDS if key in full},
                      separators=(',', ':')).encode()


def frame_header(data):
    if len(data) < 8 or data[:4] != b'BMF1':
        raise ValueError('Invalid upstream frame')
    length = struct.unpack('<I', data[4:8])[0]
    if length > 65536 or 8 + length > len(data):
        raise ValueError('Invalid upstream header')
    return json.loads(data[8:8 + length])


def serve_scope(handler):
    # Reuse the portal's existing session and same-origin policy before upgrade.
    if handler._get_portal_session() is None:
        handler.send_error(403)
        return
    secure = handler._is_tls() or handler._request_forwarded_scheme() == 'https'
    host = handler._request_forwarded_host() or handler.headers.get('Host', '')
    if not same_origin(handler.headers.get('Origin', ''), host, secure):
        handler.send_error(403)
        return
    try:
        accept = accept_key(handler.headers)
        query = parse_qs(urlsplit(handler.path).query)
        channels = (query.get('channels') or ['both'])[0]
        if channels not in ('both', 'upper', 'lower'):
            raise ValueError('Invalid channel')
        points = max(16, min(600, int((query.get('max_points') or ['600'])[0])))
        debug = (query.get('scope_diag') or ['0'])[0] == '1'
    except (ValueError, TypeError):
        handler.send_error(400)
        return
    if not SCOPE_SLOTS.acquire(blocking=False):
        handler.send_error(503)
        return
    opened = time.monotonic()
    sent_frames = 0
    phase = 'upstream'
    try:
        opener = build_opener(ProxyHandler({}))
        # Validate the upstream before accepting the browser connection.
        def get(path):
            with opener.open('http://127.0.0.1:8765' + path, timeout=1) as response:
                data = response.read(262145)
                if len(data) > 262144:
                    raise ValueError('Upstream snapshot exceeds limit')
                return data
        status = status_bytes(get('/api/status'))
        json.loads(status)
        handler.protocol_version = 'HTTP/1.1'
        handler.send_response(101)
        handler.send_header('Upgrade', 'websocket')
        handler.send_header('Connection', 'Upgrade')
        handler.send_header('Sec-WebSocket-Accept', accept)
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        # A slow viewer must never accumulate a long history or stall acquisition.
        handler.connection.settimeout(2)
        handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        handler.connection.sendall(frame(status))
        phase = 'frames'
        incoming = bytearray()
        since = 0.0
        next_status = time.monotonic() + .5
        last_auth = time.monotonic()
        while True:
            started = time.monotonic()
            if started >= next_status:
                handler.connection.sendall(frame(status_bytes(get('/api/status'))))
                next_status = started + .5
            raw = get('/api/frame.bin?max_points=' + str(points) + '&channels=' + channels + '&since=' + str(since))
            header = frame_header(raw)
            if header.get('available') and not header.get('busy'):
                stamp = float(header.get('last_frame_t') or 0)
                if stamp != since:
                    handler.connection.sendall(frame(raw, 2))
                    sent_frames += 1
                    since = stamp
            if started - last_auth > 15:
                last_auth = started
                if handler._get_portal_session() is None:
                    break
            ready = bool(getattr(handler.connection, 'pending', lambda: 0)()) or bool(select.select([handler.connection], [], [], 0)[0])
            if ready:
                chunk = handler.connection.recv(4096)
                if not chunk:
                    break
                incoming.extend(chunk)
                if len(incoming) > 4096:
                    raise ValueError('Client control buffer exceeds limit')
                while True:
                    item = consume(incoming)
                    if item is None:
                        break
                    opcode, payload = item
                    if opcode == 8:
                        handler.connection.sendall(frame(payload, 8))
                        return
                    if opcode == 9:
                        handler.connection.sendall(frame(payload, 10))
            time.sleep(max(0, .04 - (time.monotonic() - started)))
    except (OSError, ValueError, TypeError) as error:
        if debug:
            print('Scope live ended: ' + type(error).__name__ + '; phase=' + phase +
                  '; frames=' + str(sent_frames) + '; seconds=' + str(round(time.monotonic() - opened, 2)), flush=True)
    finally:
        handler.close_connection = True
        SCOPE_SLOTS.release()

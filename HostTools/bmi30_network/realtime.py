"""Bounded live snapshots and a read-only RFC 6455 portal endpoint."""
from collections import deque
import base64
import hashlib
import json
import select
import socket
import struct
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

def semantic(value):
    if isinstance(value,dict):
        return {k:semantic(v) for k,v in value.items() if k not in ('checked_at','issued','seq','revision','age_s','cache_age_s',
                'led_pattern_actual_age_s','sensor_event_at','sensor_event_seq','sensor_event_boot','remaining_ms','observed_at','checked_mono') and not k.endswith('_age_s')}
    if isinstance(value,list):return [semantic(v) for v in value]
    return value

class Hub:
    def __init__(self):
        self.cv=threading.Condition(); self.seq=0; self.events=deque(maxlen=256); self.latest=None;self.signature=None;self.last=0
    def publish(self, group, packet=None):
        with self.cv:
            signature=json.dumps(semantic(group),sort_keys=True)
            if signature==self.signature and time.monotonic()-self.last<1:return
            self.signature=signature;self.last=time.monotonic()
            self.seq+=1; value={'type':'group','revision':self.seq,'group':group,'packet':packet}
            self.events.append(value); self.latest=value; self.cv.notify_all()
    def after(self, revision, timeout=1):
        with self.cv:
            if self.seq <= revision:self.cv.wait_for(lambda:self.seq>revision,timeout)
            if not self.latest:return []
            if revision==0 or (self.events and revision<self.events[0]['revision']-1):return [self.latest]
            return [e for e in self.events if e['revision']>revision]

def same_origin(origin, host, secure):
    try:
        parsed=urlsplit(origin)
        return (parsed.scheme==('https' if secure else 'http') and parsed.netloc.lower()==host.lower()
                and parsed.path in ('','/') and not parsed.query and not parsed.fragment and not parsed.username)
    except ValueError:return False

def accept_key(headers):
    if headers.get('Upgrade','').lower()!='websocket' or 'upgrade' not in {x.strip().lower() for x in headers.get('Connection','').split(',')}:
        raise ValueError('WebSocket upgrade required')
    if headers.get('Sec-WebSocket-Version')!='13':raise ValueError('WebSocket version 13 required')
    key=headers.get('Sec-WebSocket-Key','')
    if len(base64.b64decode(key,validate=True))!=16:raise ValueError('Invalid WebSocket key')
    return base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()

def frame(payload, opcode=1):
    size=len(payload)
    return bytes([0x80|opcode])+(bytes([size]) if size<126 else b'\x7e'+struct.pack('!H',size) if size<65536 else b'\x7f'+struct.pack('!Q',size))+payload

def consume(buffer):
    """Only masked client control frames are accepted by this read-only stream."""
    if len(buffer)<2:return None
    a,b=buffer[0],buffer[1];size=b&127;opcode=a&15
    if a&0x70 or not a&0x80 or not b&0x80 or opcode not in (8,9,10) or size>125:
        raise ValueError('Invalid client control frame')
    if len(buffer)<6+size:return None
    mask=buffer[2:6]; payload=bytes(buffer[6+i]^mask[i%4] for i in range(size))
    del buffer[:6+size]
    if opcode==8 and len(payload)==1:raise ValueError('Invalid close payload')
    if opcode==8 and payload:
        code=struct.unpack('!H',payload[:2])[0]
        if code<1000 or code>=5000 or code in (1004,1005,1006,1015) or 1016<=code<3000:
            raise ValueError('Invalid close code')
        payload[2:].decode('utf-8')
    return opcode,payload

WS_SLOTS=threading.BoundedSemaphore(8)

def notify_refresh():
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.sendto(json.dumps({'kind':'refresh','at':time.time()}).encode(),'/run/bmi30-network/owner-events.sock')
    except (AttributeError,OSError):pass

def serve_portal(handler):
    # Authentication belongs to the portal, and is checked BEFORE upgrade.
    if handler._get_portal_session() is None:
        handler.send_error(403);return
    secure=handler._is_tls() or handler._request_forwarded_scheme()=='https'
    host=handler._request_forwarded_host() or handler.headers.get('Host','')
    if not same_origin(handler.headers.get('Origin',''),host,secure):
        handler.send_error(403);return
    try:accept=accept_key(handler.headers)
    except (ValueError,TypeError):handler.send_error(400);return
    if not WS_SLOTS.acquire(blocking=False):handler.send_error(503);return
    upstream=None
    try:
        request=Request('http://127.0.0.1:8791/live',headers={'X-BMI30-Local':'1'})
        upstream=urlopen(request,timeout=3)
        handler.protocol_version='HTTP/1.1'
        handler.send_response(101); handler.send_header('Upgrade','websocket');handler.send_header('Connection','Upgrade')
        handler.send_header('Sec-WebSocket-Accept',accept); handler.end_headers();handler.wfile.flush()
        handler.close_connection=True; handler.connection.settimeout(2)
        handler.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        incoming=bytearray(); last_check=time.monotonic(); last_ping=0
        while True:
            # Upstream emits at least one freshness snapshot a second. A dead
            # upstream closes the WS and the client resumes bounded HTTP polling.
            line=upstream.readline(512*1024)
            if not line or not line.endswith(b'\n'):break
            handler.connection.sendall(frame(line.rstrip(b'\n')))
            now=time.monotonic()
            if now-last_check>15:
                last_check=now
                if handler._get_portal_session() is None:break
            if now-last_ping>10:
                handler.connection.sendall(frame(b'bmi30',9));last_ping=now
            ready=bool(getattr(handler.connection,'pending',lambda:0)()) or bool(select.select([handler.connection],[],[],0)[0])
            if ready:
                chunk=handler.connection.recv(4096)
                if not chunk:break
                incoming.extend(chunk)
                while True:
                    item=consume(incoming)
                    if item is None:break
                    opcode,payload=item
                    if opcode==8:
                        handler.connection.sendall(frame(payload,8));return
                    if opcode==9:handler.connection.sendall(frame(payload,10))
    except (OSError,ValueError):
        pass
    finally:
        if upstream:upstream.close()
        handler.close_connection=True;WS_SLOTS.release()

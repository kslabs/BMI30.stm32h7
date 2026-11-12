"""Small nonblocking event lane from the existing USB owner; never opens USB."""
from collections import deque
import json
import os
import socket
import threading
import time
import uuid

PATH = '/run/bmi30-network/owner-events.sock'
FIELDS = ('optic_active','optic_indication_allow','detadc1','detadc2','tx_enabled','online')

class Publisher:
    def __init__(self, path=PATH):
        self.path=path; self.boot=uuid.uuid4().hex; self.seq=0
        self.cv=threading.Condition(); self.pending=deque(maxlen=256)
        self.last=None; self.stopped=False; self.dropped=0; self.sent=0
        self.detection_stamp=None;self.detection_seen=-10.0
        self.worker=threading.Thread(target=self.run,name='bmi30-owner-events',daemon=True)
        self.worker.start()

    def update(self, cache, generation):
        local=(cache.get('sensors') or {}).get('local') or {}
        sensors={k:local[k] for k in FIELDS if type(local.get(k)) is bool}
        detection=cache.get('host_detection') or {}
        stamp=detection.get('at')
        if isinstance(stamp,(int,float)) and stamp!=self.detection_stamp:
            self.detection_stamp=stamp;self.detection_seen=time.monotonic()
        if isinstance(stamp,(int,float)) and time.monotonic()-self.detection_seen < 4:
            sensors.update({k:detection[k] for k in ('detadc1','detadc2') if type(detection.get(k)) is bool})
        node=local.get('node_id')
        if not sensors or type(node) is not int or not 0 <= node <= 31:return
        event=(cache.get('events') or {}).get('optic_state') or {}
        hold=event.get('optic_hold_ds')
        pattern=event.get('led_pattern')
        state={'sensors':sensors,'node_id':node,'hold_ds':hold if type(hold) is int and 0 <= hold <= 100 else None,
               'optic_pattern':pattern if type(pattern) is int and 0 <= pattern <= 15 else None}
        with self.cv:
            if self.stopped or state==self.last:return
            self.last=state; self.seq+=1
            packet={'boot':self.boot,'seq':self.seq,'generation':generation,'at':time.time(),'mono':time.monotonic(),**state}
            if len(self.pending)==self.pending.maxlen:self.dropped+=1
            self.pending.append(packet); self.cv.notify()

    def run(self):
        try:
            channel=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM); channel.setblocking(False)
        except (AttributeError,OSError):return
        try:
            while True:
                with self.cv:
                    while not self.pending and not self.stopped:self.cv.wait()
                    if self.stopped:return
                    packet=self.pending.popleft()
                try:
                    channel.sendto(json.dumps(packet,separators=(',',':')).encode(),self.path); self.sent+=1
                except OSError:
                    # No wait or retry in acquisition. A later complete snapshot
                    # reconciles state after a missing/busy event subscriber.
                    self.dropped+=1
        finally:channel.close()

    def close(self):
        with self.cv:self.stopped=True; self.cv.notify_all()
        if threading.current_thread() is not self.worker:self.worker.join(timeout=.2)

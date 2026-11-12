"""Bounded, independent group telemetry and routed control. Never opens USB.

Status is sampled from the existing owner cache/API. Original timestamps and
hardware identities survive relaying; RS485 IDs are never routing addresses.
"""
from __future__ import annotations

import concurrent.futures
import copy
import base64
import json
import logging
from pathlib import Path
import socket
import os
import struct
import threading
import time
import urllib.request
import uuid

import network
from protocol import ID, encode
from realtime import Hub, semantic
from freshness import Challenges, Receipts, age
from transport_policy import allows

PORT = 8793
FRESH = 4.0
MAX_HOPS = 4
LOG = logging.getLogger("bmi30-group")


def short_stm32_id(identity):
    """Same UID36 identity as STM32 rs485_compute_short_id36, never a bus ID."""
    try:
        raw = b"".join(int(identity["uid_w" + str(i)]).to_bytes(4, "little") for i in range(3))
        value = 1469598103934665603
        for byte in raw:
            value = ((value ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        return f'{((value ^ (value >> 36)) & 0xFFFFFFFFF) or 1:09X}'
    except (KeyError, ValueError, OverflowError, TypeError):
        return ""


def validate_action(action, params):
    if not isinstance(params, dict):
        raise ValueError("Invalid command parameters")
    if action == "sync_mode" and set(params) == {"role"} and params["role"] in ("master", "slave"):
        return dict(params)
    if action == "rs485_id" and set(params) == {"device_id"} and type(params["device_id"]) is int and 0 <= params["device_id"] <= 31:
        return dict(params)
    if action == "optic_hold" and set(params) == {"hold_ds"} and type(params["hold_ds"]) is int and 0 <= params["hold_ds"] <= 100:
        return dict(params)
    if action == "optic_reaction" and set(params) == {"enabled", "neighbor_enabled", "neighbor_device_id"}:
        if type(params["enabled"]) is bool and type(params["neighbor_enabled"]) is bool:
            node = params["neighbor_device_id"]
            if node is None or (type(node) is int and 0 <= node <= 31):
                return dict(params)
    raise ValueError("Unsupported command or invalid parameters")


def local_state(core, cache, now):
    sync = core.get("sync") or cache.get("sync") or {}
    optic = core.get("optic") or {}
    sensors = cache.get("sensors") or {}
    local = sensors.get("local") or {}
    event = (cache.get("events") or {}).get("optic_state") or {}
    connection = core.get("connection") or {}
    relative=cache.get('_relative_ages') or {}
    cache_age = relative.get('cache', now - float(cache.get("cache_written_at", cache.get("updated_at", 0))))
    # A live daemon with a dead acquisition owner must not advertise old sensors.
    available = bool(core.get("ok") and not core.get("stale") and connection.get("connected")
                     and 0 <= cache_age < 5)
    ident = ((cache.get("identity") or {}).get("stm32") or {})
    result = {"available": available, "sync": {k: sync.get(k) for k in
              ("role", "assigned_role", "device_id", "local_node_id", "device_id_assigned", "code", "sync_seen_mask")},
              "stm32_uid96": ident.get("uid96", ""), "stm32_short_id": short_stm32_id(ident), "sensors": {}, "optic": {},
              "hold_ds": event.get("optic_hold_ds"), "neighbors": [], "cache_age_s": round(cache_age, 3)}
    for key in ("optic_active", "optic_indication_allow", "detadc1", "detadc2", "tx_enabled", "online"):
        value = local.get(key, event.get(key))
        result["sensors"][key] = value if available and type(value) is bool else None
    detection=cache.get('host_detection') or {}
    detection_age=relative.get('detection',now-detection.get('at',0))
    if available and isinstance(detection.get('at'),(int,float)) and 0 <= detection_age < FRESH:
        for key in ('detadc1','detadc2'):
            if type(detection.get(key)) is bool:result['sensors'][key]=detection[key]
    for key in ("reaction_enabled", "neighbor_reaction_enabled", "neighbor_device_id", "reaction_local_active",
                "reaction_neighbor_active", "led_pattern_actual", "led_pattern_actual_age_s", "led_event"):
        result["optic"][key] = optic.get(key)
    # The deployed STAT layout can fail its own bus-map validation. Byte 98
    # from that layout must not be presented as a confirmed LED pattern.
    pattern = optic.get("led_pattern_actual")
    age = optic.get("led_pattern_actual_age_s")
    stat_pattern_valid = (optic.get("led_pattern_actual_source") != "stat"
                          or (cache.get("stat") or {}).get("sync_layout_valid") is True)
    if not (stat_pattern_valid and type(pattern) is int and 0 <= pattern <= 15
            and isinstance(age, (int, float)) and 0 <= age <= 5):
        update = (cache.get("event_updates") or {}).get("optic_state") or {}
        stamp = update.get("updated_at")
        pattern = event.get("led_pattern")
        pattern_age=relative.get('optic',now-stamp if isinstance(stamp,(int,float)) else None)
        if not (type(pattern) is int and 0 <= pattern <= 15
                and isinstance(pattern_age, (int, float)) and 0 <= pattern_age <= 5):
            pattern, pattern_age = None, None
        age=pattern_age
    result["optic"]["led_pattern_actual"] = pattern
    result["optic"]["led_pattern_actual_age_s"] = age
    # A pattern is a retained selection, including 0 (dark). The current
    # owner's successful command remains meaningful when no event changes;
    # its freshness is the owner's availability, not the event's age.
    commanded = optic.get("led_pattern_commanded")
    valid_command = type(commanded) is int and 0 <= commanded <= 15
    retained = event.get("led_pattern")
    if valid_command:
        selected, source = commanded, "commanded"
    elif pattern is not None:
        selected, source = pattern, "readback"
    elif type(retained) is int and 0 <= retained <= 15:
        selected, source = retained, "retained-event"
    else:
        selected, source = None, "none"
    result["optic"]["led_pattern_selected"] = selected
    result["optic"]["led_pattern_selected_source"] = source
    permission = (optic.get("non_addressable_led") or {}).get("enabled")
    result["optic"]["led_indication_enabled"] = permission if type(permission) is bool else None
    result["optic"]["led_output_active"] = (bool(permission and commanded > 0)
                                           if valid_command and type(permission) is bool else None)
    relay = optic.get("relay") or {}
    result["relay"] = {k: relay.get(k) for k in
                       ("enabled", "saved_enabled", "pc1", "active", "available",
                        "duration_ms", "test_enabled", "sequence", "age_s")}
    # Keep the relay read's age independent of ADC events and group heartbeats.
    relay_age = relay.get("age_s")
    result["relay"]["observed_at"] = (now - relay_age
        if isinstance(relay_age, (int, float)) and relay_age >= 0 else None)
    result["sound"] = {"active": optic.get("led_sound_output_active")}
    result["fault"] = (optic.get("led_state") or {}).get("fault")
    # SNS1 status rows can be briefly absent while SENSOR_MAP still confirms
    # membership. Presence and role are separate facts; an unknown role is not
    # Slave. Bus IDs here are options on this owner's bus, never route IDs.
    rows = {n["node_id"]: n for n in sensors.get("remote", [])[:32]
            if type(n.get("node_id")) is int and 0 <= n["node_id"] <= 31}
    sensor_map = (cache.get("events") or {}).get("sensor_map") or {}
    presence = sensor_map if sensor_map.get("valid") is True else sync
    mask = presence.get("sync_seen_mask")
    own = presence.get("local_node_id", presence.get("device_id"))
    if type(mask) is int and 0 <= mask <= 0xFFFFFFFF and type(own) is int and 0 <= own <= 31:
        neighbors = [i for i in range(32) if i != own and mask & (1 << i)]
    else:
        neighbors = sorted(i for i, n in rows.items() if n.get("online"))
    for node_id in neighbors:
        master = rows.get(node_id, {}).get("master")
        prefix = "M" if master is True else ("S" if master is False else "RS485 ")
        result["neighbors"].append({"node_id": node_id, "label": prefix + f'{node_id:02d}',
                                    "master": master if type(master) is bool else None})
    return result


class Group:
    def __init__(self, agent):
        self.agent, self.ident = agent, agent.config["id"]
        self.lock = threading.RLock()
        self.command_lock = threading.Lock()
        self.receipts = Receipts(FRESH)
        self.records, self.routes = self.receipts.records, {}
        self.route_options = {}
        self.challenges = Challenges()
        self.subscribers = {}
        self.last_probe = -10.0
        self.owner_received = -10.0
        self.cache_observation = None
        self.cache_received = -10.0
        self.local_marks = {}
        self.seq, self.rotation = 0, 0
        self.boot = uuid.uuid4().hex
        self.last_error = 0
        self.sample_lock = threading.RLock()
        self.wake = threading.Event()
        self.hub = Hub()
        self.owner_event = None
        self.owner_boot = None
        self.owner_seq = 0
        self.retired_owner_boots=set()
        self.last_sent = 0
        self.last_signature = None
        self.streams={}
        self.commands_path = Path(agent.config.get("state_dir", "/var/lib/bmi30-network")) / "commands.json"
        try:
            self.commands = json.loads(self.commands_path.read_text())
        except (OSError, ValueError):
            self.commands = {}
        self.last_command = 0
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def fresh(self, data, now=None):
        ident = data.get('id')
        stored = self.records.get(ident, {}).get('data', {})
        return (data.get('kind') == 'group-v1' and self.receipts.fresh(ident)
                and (data.get('boot'), data.get('seq')) == (stored.get('boot'), stored.get('seq')))

    def merge(self, packet, address):
        changed = []
        with self.lock:
            if not allows(self.agent,'bluetooth' if address.startswith('bt:') else 'wifi'):
                return changed
            sender = self.agent.protocol.verify(packet["self"])
            transit = self.challenges.elapsed(packet.get('request_id'), 'group', sender['id'], consume=False)
            if sender["id"] == self.ident:
                return
            entries = [{"record": packet["self"], "via": [], 'age_s':packet.get('self_age_s')}] + packet.get("records", [])[:8]
            for entry in entries:
                try:
                    data = self.agent.protocol.verify(entry["record"])
                    relative_age = age(entry.get('age_s')) + transit
                    via = entry.get("via", [])
                    path = [sender["id"]] + via
                    if (data.get('kind') != 'group-v1' or relative_age >= FRESH or data["id"] == self.ident or self.ident in path
                            or len(path) > MAX_HOPS or len(set(path)) != len(path)
                            or (data["id"] != sender["id"] and (not via or via[-1] != data["id"]))):
                        continue
                    old = self.records.get(data["id"], {}).get("data", {})
                    duplicate = old and (data['boot'], data['seq']) == (old['boot'], old['seq'])
                    if duplicate:
                        if not self.fresh(old):
                            continue
                        # A second medium/shorter route never refreshes TTL.
                    elif not self.receipts.put(entry['record'], relative_age):
                        continue
                    options=self.route_options.setdefault(data['id'],{})
                    previous=options.get(address)
                    if duplicate and previous and len(path)>len(previous['path']):
                        continue
                    options[address]={"address":address,"path":path,"received":time.monotonic(),
                                      "transport":'bluetooth' if address.startswith('bt:') else 'wifi'}
                    self.routes[data['id']]=self.control_routes(data['id'])[0]
                    changed.append(data["id"])
                except (ValueError, TypeError, KeyError):
                    continue
            if changed:
                self.publish()
        if sender['id'] in changed:
            bluetooth=getattr(self.agent,'bluetooth',None)
            if bluetooth:
                bluetooth.remember_peer(sender['id'],sender.get('bluetooth_address'))
        return changed

    def control_routes(self, ident):
        routes=list(self.route_options.get(ident,{}).values())
        if ident not in self.route_options and ident in self.routes:routes=[self.routes[ident]]
        routes=[r for r in routes if time.monotonic()-r['received']<FRESH
                and allows(self.agent,'bluetooth' if r['address'].startswith('bt:') else 'wifi')]
        return sorted(routes,key=lambda r:(len(r['path']), time.monotonic()-r['received']>=1.5,
                                           r.get('transport')=='bluetooth'))

    def drop_endpoint(self, address):
        with self.lock:
            for ident,options in self.route_options.items():
                options.pop(address,None)
                candidates=self.control_routes(ident)
                if candidates:self.routes[ident]=candidates[0]
                elif self.routes.get(ident,{}).get('address')==address:self.routes.pop(ident,None)

    def publish(self, now=None):
        public=[]
        for ident, value in self.records.items():
            if not self.fresh(value['data']):continue
            candidates=self.control_routes(ident)
            if ident!=self.ident and not candidates:continue
            route=candidates[0] if candidates else {}
            if route:self.routes[ident]=route
            relative_age=self.receipts.age(ident)
            item={**value['data'],'via':route.get('path',[]),'address':route.get('address'),'age_s':relative_age,
                  'transport':route.get('transport','wifi') if ident!=self.ident else 'local'}
            if isinstance(item.get('relay'),dict) and isinstance(item['relay'].get('age_s'),(int,float)):
                item['relay']={**item['relay'],'age_s':age(item['relay']['age_s'])+relative_age}
            public.append(item)
        snapshot={'checked_at':time.time(),'checked_mono':time.monotonic(),'clock_mode':'monotonic-v2',
                  'local_id':self.ident,'nodes':public,'available':True,
                  'service_transports':self.transport_status()}
        network.atomic(self.agent.config.get('group_state','/run/bmi30-network/group.json'),json.dumps(snapshot),0o644)
        self.hub.publish(snapshot, {'record':self.packet(None),'created_mono':time.monotonic()})

    def transport_status(self):
        with self.lock:
            selection=getattr(self.agent,'transports',None)
            status=selection.snapshot() if selection else {'mode':'both','allowed':['wifi','bluetooth'],'revision':'initial','error':''}
            active={r.get('transport','wifi') for ident in self.records if ident!=self.ident
                    and self.receipts.fresh(ident) for r in self.control_routes(ident)}
            return {**status,'active':[m for m in ('wifi','bluetooth') if m in active]}

    def set_transports(self, mode, revision):
        with self.lock:
            changed=self.agent.transports.set(mode,revision)
            if changed:
                # Keep replay history, discard observations from the old selection.
                for ident in list(self.records):
                    if ident!=self.ident:
                        self.records.pop(ident,None);self.receipts.deadlines.pop(ident,None)
                self.routes.clear();self.route_options.clear();self.subscribers.clear()
                with self.challenges.lock:self.challenges.pending.clear()
                self.last_probe=-10
                self.publish()
                self.wake.set()
        # Never wait for a Bluetooth lock while holding the group lock.
        if changed and not allows(self.agent,'bluetooth'):
            self.agent.bluetooth.disconnect_all()
        return self.transport_status()

    def packet(self, request_id, others=None, snapshot=None, revision=None):
        with self.lock:
            if snapshot is not None:
                if not snapshot.get('record'):return None
                data=copy.deepcopy(self.agent.protocol.verify(snapshot['record']))
                elapsed=age(time.monotonic()-snapshot['created_mono'])
                data['self_age_s']=age(data['self_age_s'])+elapsed
                if data['self_age_s']>=FRESH:return None
                data['records']=[{**entry,'age_s':age(entry['age_s'])+elapsed} for entry in data.get('records',[])
                                 if age(entry['age_s'])+elapsed<FRESH]
                data['request_id']=request_id
                data['revision']=revision
                return self.agent.protocol.sign(data)
            record=self.records.get(self.ident)
            if not record or not self.fresh(record['data']):return None
            if others is None:others=list(self.routes)
            entries=[{'record':self.records[k],'via':self.routes[k]['path'],'age_s':self.receipts.age(k)}
                     for k in others[:8] if k in self.routes and k in self.records and self.fresh(self.records[k]['data'])]
            return self.agent.protocol.sign({'id':self.ident,'boot':self.boot,'seq':self.seq,
                'request_id':request_id,'self':record,'self_age_s':self.receipts.age(self.ident),'records':entries})

    def receive_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", PORT))
        while True:
            try:
                raw, source = sock.recvfrom(60000)
                if not allows(self.agent,'wifi'):continue
                # The signed contents authenticate identities; observed source is
                # only a next hop and commands still require encrypted replies.
                packet = self.agent.protocol.verify(json.loads(raw))
                if packet.get('purpose') == 'group-probe-v2':
                    token=packet.get('request_id','')
                    if not isinstance(token,str) or len(token)!=32 or packet['id']==self.ident:continue
                    with self.lock:
                        if len(self.subscribers)<128 or packet['id'] in self.subscribers:
                            self.subscribers[packet['id']]=(source[0],token,time.monotonic())
                    self.broadcast()
                    continue
                if packet["id"] != packet["self"]["data"]["id"]:
                    raise ValueError("Wrong forwarding origin")
                changed=self.merge(packet, source[0])
                # Relay an original signed change immediately. Duplicate/older
                # arrivals do not produce another relay or renew its timestamp.
                if changed:self.broadcast(changed, exclude=source[0])
            except Exception:
                pass

    def sample(self):
        with self.agent.opener.open("http://127.0.0.1:8765/api/status", timeout=0.4) as response:
            core = json.loads(response.read(512 * 1024))
        cache_path = Path(self.agent.config.get("device_state", "/tmp/bmi30_device_state.json"))
        if cache_path.stat().st_size > 1024 * 1024:
            raise ValueError("Acquisition cache exceeds limit")
        cache = json.loads(cache_path.read_text())
        event=self.owner_event
        event_newer=event and time.monotonic()-self.owner_received < FRESH and (cache.get('owner_event_boot')!=event['boot'] or cache.get('cache_generation',0) < event['generation'])
        if event_newer:
            cache=copy.deepcopy(cache)
            cache.setdefault('sensors',{}).setdefault('local',{}).update(event['sensors'])
            cache['sensors']['local']['node_id']=event['node_id']
            if event.get('hold_ds') is not None:
                cache.setdefault('events',{}).setdefault('optic_state',{})['optic_hold_ds']=event['hold_ds']
        # The owner may publish a new cache while the HTTP status is fetched.
        # Timestamp after both reads so that cache cannot appear from the
        # future and briefly mark a healthy acquisition owner unavailable.
        now = time.time()
        marker=(cache_path.stat().st_mtime_ns, cache.get('cache_generation'), cache.get('owner_event_boot'))
        if marker!=self.cache_observation:
            self.cache_observation=marker; self.cache_received=time.monotonic()
        # The cache is produced on this host; observe write progress locally.
        cache=dict(cache)
        relative={'cache':time.monotonic()-self.cache_received}
        for key,stamp in (('detection',(cache.get('host_detection') or {}).get('at')),
                          ('optic',((cache.get('event_updates') or {}).get('optic_state') or {}).get('updated_at'))):
            if stamp is not None:
                old=self.local_marks.get(key)
                if not old or old[0]!=stamp:self.local_marks[key]=(stamp,time.monotonic())
                relative[key]=time.monotonic()-self.local_marks[key][1]
            else:relative[key]=float('inf')
        cache['_relative_ages']=relative
        data = local_state(core, cache, now)
        if event_newer and data['available']:data['sensors'].update(event['sensors'])
        if event:
            data['sensor_event_at']=event['at'];data['sensor_event_seq']=event['seq'];data['sensor_event_boot']=event['boot']
        self.seq += 1
        data.update(kind="group-v1", id=self.ident, issued=now, boot=self.boot, seq=self.seq)
        bluetooth=getattr(self.agent,'bluetooth',None)
        if bluetooth and isinstance(bluetooth.address,str) and bluetooth.address:
            data['bluetooth_address']=bluetooth.address
        return self.agent.protocol.sign(data)

    def tick(self):
        self.probe()
        with self.sample_lock:self._tick()

    def _tick(self):
        try:
            record = self.sample()
        except Exception as exc:
            # Stop refreshing old sensor values; peers expire them automatically.
            record = None
            if time.monotonic() - self.last_error > 30:
                LOG.warning("Acquisition cache unavailable: %s", type(exc).__name__)
                self.last_error = time.monotonic()
        now = time.time()
        with self.lock:
            if record:
                self.receipts.put(record)
            self.receipts.prune()
            self.route_options={k:{a:r for a,r in routes.items() if time.monotonic()-r['received']<FRESH}
                                for k,routes in self.route_options.items() if k in self.records}
            self.routes = {k: r for k, r in self.routes.items() if k in self.records and self.control_routes(k)}
            self.publish(now)
            if not record:
                return
            others = [k for k in self.records if k != self.ident and k in self.routes]
            # Eight original records per datagram, rotated for larger fleets.
            others = (others[self.rotation:] + others[:self.rotation])[:8]
            self.rotation = (self.rotation + 8) % max(1, len(self.routes))
            signature=encode(semantic(record['data']))
            if signature==self.last_signature and time.monotonic()-self.last_sent < 1:return
            self.last_signature=signature;self.last_sent=time.monotonic()
        self.broadcast(others)

    def broadcast(self, others=(), exclude=None):
        if not allows(self.agent,'wifi'):return
        with self.lock:
            subscribers=list(self.subscribers.values())
        for address,token,received in subscribers:
            if address==exclude or time.monotonic()-received>=FRESH:continue
            packet=self.packet(token,list(others))
            if packet:
                try:self.socket.sendto(encode(packet),(address,PORT))
                except OSError:pass

    def probe(self):
        if not allows(self.agent,'wifi'):return
        if time.monotonic()-self.last_probe<1:return
        self.last_probe=time.monotonic()
        token=self.challenges.issue('group',FRESH)
        packet=self.agent.protocol.sign({'id':self.ident,'boot':self.boot,'seq':self.seq,
                                        'purpose':'group-probe-v2','request_id':token})
        raw=encode(packet)
        with self.agent.lock:
            local = self.agent.local or {}
            targets = set(local.get("broadcasts", []))
            targets.update(a for (_, a), t in self.agent.peers.items() if time.monotonic() - t < 100)
            for peer in self.agent.config.get("peers", []):
                if peer["id"] != self.ident:
                    targets.update(peer.get("addresses", []))
            if local.get("gateway"):
                targets.add(local["gateway"])
        for address in sorted(targets)[:128]:
            try:
                self.socket.sendto(raw, (address, PORT))
            except OSError:
                pass

    def owner_update(self, event):
        if not isinstance(event,dict) or not isinstance(event.get('boot'),str) or len(event['boot'])!=32:return False
        if type(event.get('seq')) is not int or type(event.get('generation')) is not int:return False
        if type(event.get('node_id')) is not int or not 0 <= event['node_id'] <= 31:return False
        if 'mono' in event:
            try:
                if age(time.monotonic()-event['mono'])>=FRESH:return False
            except (ValueError,TypeError):return False
        sensors=event.get('sensors')
        if not isinstance(sensors,dict) or not sensors or any(k not in ('optic_active','optic_indication_allow','detadc1','detadc2','tx_enabled','online') or type(v) is not bool for k,v in sensors.items()):return False
        if event.get('hold_ds') is not None and (type(event['hold_ds']) is not int or not 0 <= event['hold_ds'] <= 100):return False
        with self.sample_lock:
            if event['boot'] in self.retired_owner_boots:return False
            if event['boot']==self.owner_boot and event['seq'] <= self.owner_seq:return False
            if self.owner_boot and event['boot']!=self.owner_boot:
                if len(self.retired_owner_boots)>=32:self.retired_owner_boots.clear()
                self.retired_owner_boots.add(self.owner_boot)
            self.owner_boot=event['boot'];self.owner_seq=event['seq'];self.owner_event=event;self.owner_received=time.monotonic()
            # A sensor edge uses the last complete configuration immediately.
            # Health/configuration reads reconcile separately in the periodic tick.
            with self.lock:
                prior=self.records.get(self.ident)
                if prior and self.fresh(prior['data']) and prior['data'].get('available'):
                    data=copy.deepcopy(prior['data']);data['sensors'].update(event['sensors'])
                    if isinstance((data.get('relay') or {}).get('age_s'),(int,float)):
                        data['relay']['age_s']+=self.receipts.age(self.ident)
                    if event.get('hold_ds') is not None:data['hold_ds']=event['hold_ds']
                    self.seq+=1;data.update(seq=self.seq,issued=time.time(),sensor_event_at=event['at'],
                        sensor_event_seq=event['seq'],sensor_event_boot=event['boot'])
                    self.receipts.put(self.agent.protocol.sign(data));self.publish()
                else:prior=None
            if prior:self.broadcast()
            else:self._tick()
        return True

    def owner_loop(self, path='/run/bmi30-network/owner-events.sock'):
        # Socket permissions and kernel credentials restrict this local lane
        # to the acquisition account. Network records remain fleet signed.
        import pwd
        uid=pwd.getpwnam('techaid').pw_uid
        path=Path(path)
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        try:
            if path.exists():path.unlink()
            sock.bind(str(path));os.chown(path,uid,-1);os.chmod(path,0o600)
            while True:
                raw,anc,flags,_=sock.recvmsg(8192,socket.CMSG_SPACE(12))
                if flags & socket.MSG_TRUNC:continue
                credential=next((struct.unpack('3i',v) for level,kind,v in anc if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS),None)
                if not credential or credential[1] not in (0,uid):continue
                try:
                    event=json.loads(raw)
                    if event.get('kind')=='refresh':self.wake.set()
                    else:self.owner_update(event)
                except Exception:pass
        finally:
            sock.close()
            if path.exists():path.unlink()

    def peer_stream(self, address, stop):
        revision=0;stream_boot=None
        while not stop.is_set() and allows(self.agent,'wifi'):
            renew=False
            try:
                token=self.challenges.issue('group',FRESH)
                request=self.agent.protocol.sign({'id':self.ident,'boot':self.boot,'seq':self.seq,
                    'request_id':token,'purpose':'group-stream','revision':revision,'stream_boot':stream_boot})
                auth=base64.b64encode(encode(request)).decode()
                with self.agent.opener.open(urllib.request.Request(f'http://{address}:8791/peer-live',
                        headers={'X-BMI30-Stream':auth}),timeout=3) as response:
                    while not stop.is_set() and allows(self.agent,'wifi'):
                        if self.challenges.elapsed(token,'group',consume=False)>2:
                            renew=True;break
                        line=response.readline(512*1024)
                        if not line or not line.endswith(b'\n'):break
                        packet=self.agent.protocol.verify(json.loads(line))
                        changed=self.merge(packet,address)
                        revision=packet.get('revision',0);stream_boot=packet['boot']
                        if changed:self.broadcast(changed,exclude=address)
            except Exception:pass
            if not renew:stop.wait(.1)

    def stream_manager(self):
        while True:
            with self.agent.lock:
                peers=[address for (ident,address),stamp in self.agent.peers.items()
                       if ident!=self.ident and time.monotonic()-stamp<60]
            with self.lock:
                peers += [r['address'] for r in self.routes.values() if len(r.get('path',[]))==1 and not r['address'].startswith('bt:')]
            targets=set(dict.fromkeys(peers[:]))
            targets=set(sorted(targets)[:8])
            if not allows(self.agent,'wifi'):targets=set()
            for address,stop in list(self.streams.items()):
                if address not in targets:stop.set();del self.streams[address]
            for address in targets:
                if address not in self.streams:
                    stop=threading.Event();self.streams[address]=stop
                    threading.Thread(target=self.peer_stream,args=(address,stop),daemon=True).start()
            time.sleep(1)

    def run(self):
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.owner_loop, daemon=True).start()
        threading.Thread(target=self.stream_manager, daemon=True).start()
        while True:
            started = time.monotonic()
            try:
                self.tick()
            except Exception as exc:
                if started - self.last_error > 30:
                    LOG.warning("Group status unavailable: %s", type(exc).__name__)
                    self.last_error = started
            self.wake.wait(max(0.05, 0.5 - (time.monotonic() - started)));self.wake.clear()

    def command(self, command, transit=0, ingress=None):
        if ingress is not None and not allows(self.agent,ingress):
            raise ValueError('Service channel disabled')
        now = time.monotonic()
        if not isinstance(command, dict) or len(encode(command)) > 4096:
            raise ValueError("Invalid command")
        cid = command.get("command_id", "")
        if len(cid) != 32 or any(c not in "0123456789abcdef" for c in cid):
            raise ValueError("Invalid command ID")
        relative_age=age(command.get('age_s'))+age(transit)
        if relative_age>=8:
            raise ValueError("Command expired; refresh the device state")
        target = command.get("target", "")
        if not ID.fullmatch(target):
            raise ValueError("Invalid target")
        params = validate_action(command.get("action"), command.get("params"))
        via = command.get("via", [])
        if not isinstance(via, list) or self.ident in via or len(via) >= MAX_HOPS:
            raise ValueError("Control route loop or hop limit")
        with self.lock:
            record = self.records.get(target, {}).get("data", {})
            routes = self.control_routes(target)
        if not self.fresh(record) or not record.get("available"):
            raise ValueError("BMI30 channel or acquisition unavailable; refresh before retrying")
        if target != self.ident:
            for route in routes:
                if any(n in via for n in route['path']):continue
                spent=time.monotonic()-now
                remaining=8-relative_age-spent
                if remaining<=0:break
                payload={**command,'via':via+[self.ident],'age_s':relative_age+spent}
                try:
                    if route['address'].startswith('bt:'):
                        reply,_=self.agent.bluetooth.rpc(route['address'],payload,timeout=remaining)
                    else:
                        reply,_=self.agent.rpc(route['address'],'command',payload,timeout=min(2,remaining))
                    if reply.get('command_id')!=cid or reply.get('target')!=target:
                        raise ValueError('Wrong command acknowledgement')
                    return reply
                except Exception:
                    # Preserve command_id across failover; the final owner
                    # persists it before dispatch and can never actuate twice.
                    self.drop_endpoint(route['address'])
            raise ValueError('No live control route; command result not confirmed')
        if not self.command_lock.acquire(blocking=False):
            raise ValueError("Device is processing another command")
        try:
            # Persistent tombstones survive clock changes and process restarts.
            if cid in self.commands:
                old = self.commands[cid]
                if old["action"] != command["action"] or old["params"] != params:
                    raise ValueError("Command ID reused with different parameters")
                return old["reply"]
            if now - self.last_command < 0.25:
                raise ValueError("Commands are arriving too quickly")
            self.last_command = now
            reply = {"command_id": cid, "target": target, "ok": False,
                     "error": "Result not confirmed; refresh state before retrying"}
            # Persist BEFORE dispatch: restarts and lost replies cannot execute
            # the same command a second time. No delayed/background retries.
            if len(self.commands)>=4096:
                raise ValueError('Command history capacity exceeded; no command executed')
            self.commands[cid] = {"action": command["action"], "params": params, "reply": reply}
            network.atomic(self.commands_path, json.dumps(self.commands))
            request = urllib.request.Request("http://127.0.0.1:8765/api/command",
                                             encode({"action": command["action"], "params": params}),
                                             {"Content-Type": "application/json"})
            try:
                with self.agent.opener.open(request, timeout=3) as response:
                    result = json.loads(response.read(16384))
                reply = {"command_id": cid, "target": target, "ok": bool(result.get("ok")),
                         "error": str(result.get("error") or "")[:240]}
            except Exception:
                pass
            self.commands[cid]["reply"] = reply
            network.atomic(self.commands_path, json.dumps(self.commands))
            return reply
        finally:
            self.command_lock.release()
            self.wake.set()

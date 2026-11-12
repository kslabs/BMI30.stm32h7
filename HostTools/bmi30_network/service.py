#!/usr/bin/env python3
"""BMI30 distributed network service; acquisition and RS485 are never opened."""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import random
import socket
import threading
import time
import urllib.request
import uuid

import network
import access_policy
import policy
import group
from protocol import Protocol, Records, encode, MAX_PACKET, ID
from freshness import Challenges, age
from bluetooth_transport import Manager as BluetoothManager
from transport_policy import Selection, allows

PORT = 8791
DISCOVERY = 8792
VERSION = "0.2.4"
LOG = logging.getLogger("bmi30-network")


def private_ip(value):
    address = ipaddress.IPv4Address(value)
    if not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
        raise ValueError("Not a LAN address")
    return str(address)


class Agent:
    def __init__(self, config):
        # A copied root may retain another board's ID. The hardware is authoritative.
        config = {**config, 'id': network.hardware_identity()}
        self.config = config
        self.network = network.Network(config)
        self.protocol = Protocol(config["fleet_key"])
        self.challenges = Challenges()
        self.records = Records(self.protocol)
        self.lock = threading.RLock()
        self.peers = {}  # (origin, address) -> monotonic last authenticated contact
        self.boot, self.seq = uuid.uuid4().hex, 0
        self.decision = policy.Decision()
        self.state = {"version": VERSION, "status": "starting",'clock_mode':'monotonic-v2'}
        self.local = None
        self.secrets_path = Path(config.get("state_dir", "/var/lib/bmi30-network")) / "credentials.json"
        self.secrets = json.loads(self.secrets_path.read_text()) if self.secrets_path.exists() else {}
        self.own_sources_path = self.secrets_path.with_name("credential-sources.json")
        self.own_sources = json.loads(self.own_sources_path.read_text()) if self.own_sources_path.exists() else {}
        self.profile_cache = {}
        self.last_scan = self.last_profiles = 0
        self.hold_until = 0
        self.history = []
        self.offline = 0
        self.switching = False
        self.last_visit = {}
        self.session_start = time.monotonic()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.transports = Selection(config.get('state_dir', '/var/lib/bmi30-network'))
        self.group = group.Group(self)
        self.bluetooth = BluetoothManager(self)

    def remember_secret(self, key, value):
        if isinstance(key, str) and key.startswith("wifi:") and access_policy.forgotten(key[5:]):
            return
        if not isinstance(key, str) or len(key) > 100 or not isinstance(value, dict):
            return
        if (not isinstance(value.get("psk"), str) or not 8 <= len(value["psk"]) <= 64
                or value.get("security") not in ("wpa-psk", "sae")
                or not ID.fullmatch(value.get("origin", ""))
                or not isinstance(value.get("updated"), (int, float))
                or type(value.get('revision')) is not int or value['revision'] < 1
                or not isinstance(value.get("ssid"), str) or not 1 <= len(value["ssid"].encode()) <= 32):
            return
        old = self.secrets.get(key)
        if not old or (value['revision'],value['origin']) > (old.get('revision',0),old.get('origin','')):
            if len(self.secrets) >= 256 and key not in self.secrets:
                return
            self.secrets[key] = value
            network.atomic(self.secrets_path, json.dumps(self.secrets))

    def own_secret(self, key, secret):
        value = {k: secret[k] for k in ("ssid", "psk", "security")}
        fingerprint = hashlib.sha256(encode(value)).hexdigest()
        if self.own_sources.get(key) == fingerprint and self.secrets.get(key,{}).get('revision',0)>=1:
            return
        # A peer's newly received password must not be overwritten on every
        # observation by our unchanged older NetworkManager profile.
        self.own_sources[key] = fingerprint
        network.atomic(self.own_sources_path, json.dumps(self.own_sources))
        revision=max((int(v.get('revision',0)) for v in self.secrets.values()),default=0)+1
        self.remember_secret(key, {**value, "origin": self.config["id"], "revision":revision, "updated": time.time()})

    def exchange(self, incoming, source, transit=0):
        with self.lock:
            sender = self.protocol.verify(incoming["self"])
            if sender['id'] != incoming.get('_source_id'):
                raise ValueError('Wrong exchange origin')
            if incoming.get("request_id") is None:
                raise ValueError("Missing request identifier")
            self.merge_peer_records([incoming["self"]] + incoming.get("records", [])[:127], incoming['record_ages'], transit)
            self.peers[(sender["id"], private_ip(source))] = time.monotonic()
            if len(self.peers) > 512:
                self.peers = dict(sorted(self.peers.items(), key=lambda kv: kv[1], reverse=True)[:256])
            for key, secret in list(incoming.get("credentials", {}).items())[:256]:
                self.remember_secret(key, secret)
            accepted = False
            if incoming.get("reserve"):
                # Parent cannot reparent during an attachment transaction. A
                # second health/path check is made through the new gateway.
                accepted = bool(self.local and not self.switching and policy.valid_path(self.local, sender["id"]))
                if accepted:
                    self.hold_until = max(self.hold_until, time.monotonic() + 120)
            return {"id": self.config["id"], "request_id": incoming["request_id"],
                    "self": self.records.records.get(self.config["id"]),
                    "records": list(self.records.records.values()), 'record_ages':self.records.ages(), "credentials": self.secrets,
                    "reserved": accepted}

    def contact(self, address, expected=None, reserve=False):
        address = private_ip(address)
        with self.lock:
            if self.local is None:
                return None
            request_id = uuid.uuid4().hex
            body = {"request_id": request_id, "self": self.records.records[self.config["id"]],
                    "records": list(self.records.records.values()), 'record_ages':self.records.ages(),
                    "credentials": dict(self.secrets), "reserve": reserve}
        reply,elapsed = self.rpc(address,'exchange',body,expected,timeout=8)
        with self.lock:
            if reply.get("request_id") != request_id or (expected and reply.get("id") != expected):
                raise ValueError("Wrong peer or reply")
            sender = self.protocol.verify(reply["self"])
            if sender["id"] != reply["id"]:
                raise ValueError("Wrong origin")
            self.merge_peer_records(reply.get("records", [])[:128],reply['record_ages'],elapsed)
            self.peers[(sender["id"], address)] = time.monotonic()
            for key, secret in list(reply.get("credentials", {}).items())[:256]:
                self.remember_secret(key, secret)
            return reply

    def merge_peer_records(self, records, ages=None, transit=0):
        # Only publish() is authoritative for this boot. A neighbour can echo
        # our previous eMMC/USB boot; accepting it retires the live boot forever.
        records = [r for r in records if isinstance(r, dict)
                   and isinstance(r.get('data'), dict)
                   and r['data'].get('id') != self.config['id']]
        self.records.merge(records, ages, transit)

    def rpc(self,address,purpose,payload,expected=None,timeout=8):
        """Fresh receiver challenge plus request-bound reply; no shared clock."""
        if purpose == 'command' and not allows(self, 'wifi'):
            raise ValueError('Wi-Fi service channel disabled')
        address=private_ip(address);started=time.monotonic();request_id=uuid.uuid4().hex
        hello=self.protocol.sign({'id':self.config['id'],'boot':self.boot,'seq':self.seq,
                                  'purpose':purpose,'request_id':request_id})
        request=urllib.request.Request(f'http://{address}:{PORT}/challenge',encode(hello),
                                       {'Content-Type':'application/json'})
        with self.opener.open(request,timeout=min(4,timeout)) as response:
            granted=self.protocol.verify(json.loads(response.read(16384)))
        if granted.get('request_id')!=request_id or granted.get('purpose')!=purpose or (expected and granted['id']!=expected):
            raise ValueError('Wrong challenge reply')
        elapsed=time.monotonic()-started
        if elapsed>=timeout:raise ValueError('Local request deadline exceeded')
        body={**payload,'_challenge':granted['challenge'],'_source_id':self.config['id'],
              '_request_id':request_id,'_prepared_elapsed_s':elapsed}
        raw=self.protocol.seal(body)
        request=urllib.request.Request(f'http://{address}:{PORT}/{purpose}',raw,{'Content-Type':'application/octet-stream'})
        with self.opener.open(request,timeout=max(.1,timeout-elapsed)) as response:
            reply=self.protocol.open(response.read(MAX_PACKET+1))
        elapsed=time.monotonic()-started
        if elapsed>=timeout or reply.get('_request_id')!=request_id or reply.get('_source_id')!=granted['id']:
            raise ValueError('Expired or wrong request reply')
        return reply,elapsed

    def accept_rpc(self,data,purpose):
        elapsed=self.challenges.elapsed(data.get('_challenge'),purpose,data.get('_source_id'))
        return elapsed+age(data.get('_prepared_elapsed_s'))

    def discovery_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY))
        while True:
            try:
                raw, source = sock.recvfrom(60000)
                record = json.loads(raw)
                with self.lock:
                    node = self.protocol.verify(record)
                    if node["id"] != self.config["id"]:
                        # Discovery is only a hint. A fresh challenge exchange
                        # is required before its state can influence routing.
                        self.peers[(node["id"], private_ip(source[0]))] = time.monotonic()
            except Exception:
                pass  # Unauthenticated LAN traffic is not a journal event.

    def publish(self, local):
        with self.lock:
            self.seq += 1
            local.update(boot=self.boot, seq=self.seq, issued=time.time())
            self.local = local
            record = self.protocol.sign(local)
            self.records.merge([record])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for address in local["broadcasts"]:
                try:
                    sock.sendto(encode(record), (address, DISCOVERY))
                except OSError:
                    pass

    def gossip(self):
        now = time.monotonic()
        with self.lock:
            self.peers = {k: t for k, t in self.peers.items() if now - t < 180}
            targets = dict(self.peers)
            for peer in self.config.get("peers", []):
                if peer["id"] != self.config["id"]:
                    for address in peer.get("addresses", []):
                        targets.setdefault((peer["id"], address), 0)
            if self.local.get("gateway"):
                targets.setdefault((None, self.local["gateway"]), 0)
        def poll(item):
            ident, address = item
            try:
                self.contact(address, ident)
            except Exception:
                pass
        # Only direct endpoints: learned gossip addresses are never scanned.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(poll, list(targets)[:128]))

    def route(self, local, nodes):
        local.update(parent=None, path=[], cost=10000)
        if not local["internet"]["ok"]:
            return
        parent = next((n for n in nodes if local["uplink"] == "wifi" and n.get("ap", {}).get("bssid") == local["bssid"]), None)
        if parent:
            if policy.valid_path(parent, local["id"]):
                local.update(parent=parent["id"], path=[local["id"]] + parent["path"],
                             cost=policy.radio_cost(local["signal"], local["freq"]) + 25 + parent["cost"])
        elif not local["ssid"].upper().startswith("BMI30") or local["uplink"] == "ethernet":
            local.update(path=[local["id"]], cost=policy.internet_cost(local["internet"]) + (
                policy.radio_cost(local["signal"], local["freq"]) if local["uplink"] == "wifi" else 5))

    def router_secret(self, ssid):
        """Prefer a peer's confirmed Internet credential to an untested saved copy."""
        saved = self.profile_cache.get(ssid)
        shared = self.secrets.get('wifi:' + ssid)
        local = self.local or {}
        # Keep the exact currently working station authoritative, including
        # a manually corrected password not yet distributed to neighbours.
        if (saved and local.get('ssid') == ssid and local.get('uplink') == 'wifi'
                and local.get('internet', {}).get('ok')
                and saved.get('uuid') == local.get('station_uuid')):
            return saved
        if shared:
            return shared
        if saved:
            return saved
        raise RuntimeError('Router credentials are unavailable')

    def execute(self, choice):
        with self.lock:
            self.switching = True
        started = time.time()
        success = False
        error = ""
        try:
            if choice["kind"] == "relay":
                parent = choice["parent"]
                secret = self.secrets["ap:" + parent]
                peer = next(n for n in self.records.nodes() if n["id"] == parent)
                addresses = [a for (p, a) in self.peers if p == parent]
                if addresses:
                    reply = self.contact(addresses[0], parent, reserve=True)
                    if not reply["reserved"]:
                        raise RuntimeError("Parent is changing its route")
                def verify(gateway):
                    reply = self.contact(gateway, parent, reserve=True)
                    if not reply["reserved"] or not policy.valid_path(reply["self"]["data"], self.config["id"]):
                        raise RuntimeError("Parent path is no longer valid")
                health = self.network.activate(choice, secret, verify)
            else:
                secret = self.router_secret(choice["ssid"])
                health = self.network.activate(choice, secret)
            success = True
            LOG.info("Route switched: %s %s %s MHz", choice["kind"], choice.get("parent"), choice["freq"])
        except Exception as exc:
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            LOG.warning("Route candidate failed; previous route restored: %s", error)
        finally:
            self.decision.completed(choice, time.monotonic(), success)
            with self.lock:
                self.switching = False
                self.history = (self.history + [{"at": started, "kind": choice["kind"], "parent": choice.get("parent"),
                                                "bssid": choice["bssid"], "ok": success, "error": error}])[-30:]
        return success

    def visit_peer(self, local):
        """Authenticate a visible node outside our LAN, then restore the uplink.

        Wi-Fi scans only reveal beacons, never another network's IP topology.
        A brief association is needed on this single radio. Active AP clients
        and native RDP sessions suppress these exploratory associations.
        """
        if local["clients"] or self.network.remote_desktop_active():
            return False
        known = {p["ap_ssid"]: p for p in self.config.get("peers", []) if p["id"] != local["id"]}
        for ident, value in self.secrets.items():
            if ident.startswith("ap:") and value["origin"] != local["id"]:
                known[value["ssid"]] = {"id": value["origin"], "ap_ssid": value["ssid"], "psk": value["psk"]}
        nodes = {n["id"] for n in self.records.nodes()}
        aps = [a for a in local["visible"] if a["ssid"] in known and known[a["ssid"]]["id"] not in nodes
               and time.monotonic() - self.last_visit.get(a["bssid"], 0) > 300]
        if not aps:
            return False
        aps.sort(key=lambda a: (known[a["ssid"]].get("role") != "master",
                               known[a["ssid"]].get("rs485_id", 255), -a["signal"]))
        choice = aps[0]
        peer = known[choice["ssid"]]
        self.last_visit[choice["bssid"]] = time.monotonic()
        def verify(gateway):
            self.contact(gateway, peer["id"])
        with self.lock:
            self.switching = True
        try:
            self.network.activate(choice, {"psk": peer["psk"]}, verify,
                                  commit=False, require_internet=False)
            LOG.info("Discovered adjacent network through %s; previous uplink restored", peer["id"])
        except Exception as exc:
            LOG.warning("Adjacent network visit failed: %s", type(exc).__name__)
        finally:
            with self.lock:
                self.switching = False
        return True

    def tick(self):
        now = time.monotonic()
        permissions = self.network.permissions()
        local = self.network.snapshot()
        local["internet"] = network.probe(local["iface"])
        if (now - self.last_profiles > 60 or not self.profile_cache
                or getattr(self, 'profile_station_uuid', None) != local.get('station_uuid')):
            self.profile_cache = self.network.profiles()
            self.last_profiles = now
            self.profile_station_uuid = local.get('station_uuid')
        rescan = now - self.last_scan > 65 and not local["clients"] and not access_policy.maintenance()
        local["visible"] = self.network.visible(rescan=rescan and permissions.get("wifi", True))
        active_ap = next((a for a in local["visible"] if a["bssid"] == local["bssid"]), None)
        if active_ap:
            local["signal"] = active_ap["signal"]
        if rescan:
            self.last_scan = now
        ap_psk = network.nm_value(local["ap_uuid"], "802-11-wireless-security.psk", secrets=True)
        desktop_active = self.network.remote_desktop_active()
        with self.lock:
            nodes = self.records.nodes()
            self.route(local, nodes)
            if 8 <= len(ap_psk) <= 64:
                self.own_secret("ap:" + local["id"], {"ssid": local["ap"]["ssid"], "psk": ap_psk, "security": "wpa-psk"})
            if (local['uplink'] == 'wifi' and local["path"] == [local["id"]]
                    and local["ssid"] in self.profile_cache and not access_policy.forgotten(local["ssid"])):
                self.own_secret("wifi:" + local["ssid"], self.profile_cache[local["ssid"]])
        self.publish(local)
        self.gossip()
        with self.lock:
            nodes = self.records.nodes()
            profiles = {s["ssid"]: s for k, s in self.secrets.items() if k.startswith("wifi:")}
            profiles.update(self.profile_cache)
            profiles = {ssid: self.router_secret(ssid) for ssid in profiles}
            profiles = {k: v for k, v in profiles.items() if not access_policy.forgotten(k)}
            blocked = [k for k in self.secrets if k.startswith('wifi:') and access_policy.forgotten(k[5:])]
            for k in blocked: self.secrets.pop(k, None)
            if blocked: network.atomic(self.secrets_path, json.dumps(self.secrets))
            choices = policy.candidates(local, nodes, local["visible"], profiles)
            self.offline = 0 if local["internet"]["ok"] else self.offline + 1
            choice = self.decision.choose(local, choices, now)
            status = "online" if local["internet"]["ok"] else "offline"
            can_switch = self.config.get("automatic", False) and permissions.get("wifi", True)
            can_switch = can_switch and now >= self.hold_until and now - self.session_start > 90
            if local["internet"]["ok"] and desktop_active:
                can_switch = False
            if local.get("clients", 0) or access_policy.offline_mode() or access_policy.maintenance():
                can_switch = False
            if self.config.get("observe", False):
                can_switch = False
            self.state = {"version": VERSION, "status": status, "checked_at": time.time(),
                          'checked_mono':time.monotonic(),'clock_mode':'monotonic-v2',
                          'bluetooth':self.bluetooth.snapshot(),
                          "automatic": can_switch, "coordinator": policy.coordinator(nodes),
                          "local": local, "nodes": nodes, "candidates": choices, "history": self.history,
                          "note": "Estimated route cost; 5 GHz preferred at usable signal; up to 4 nodes per route."}
            network.atomic("/run/bmi30-network/status.json", json.dumps(self.state), 0o644)
        if choice and can_switch:
            self.execute(choice)
        elif can_switch and self.offline >= 3:
            self.visit_peer(local)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def serve(agent, bind='0.0.0.0', port=PORT, ready=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(6)

        def do_GET(self):
            if self.path in ('/live','/peer-live'):
                local=self.path=='/live'
                if local:
                    if self.client_address[0]!='127.0.0.1' or self.headers.get('X-BMI30-Local')!='1':
                        self.send_error(403);return
                else:
                    try:
                        if not allows(agent, 'wifi'):raise ValueError('Wi-Fi service channel disabled')
                        token=self.headers.get('X-BMI30-Stream','')
                        if len(token)>4096:raise ValueError('Token exceeds limit')
                        request=agent.protocol.verify(json.loads(base64.b64decode(token,validate=True)))
                        if request.get('purpose')!='group-stream' or len(request.get('request_id',''))!=32:raise ValueError('Invalid stream request')
                    except Exception:self.send_error(403);return
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson')
                self.send_header('Cache-Control','no-store');self.end_headers()
                self.connection.settimeout(2);self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                revision=(request.get('revision',0) if not local and request.get('stream_boot')==agent.group.boot else 0)
                if type(revision) is not int or not 0<=revision<=agent.group.hub.seq:revision=0
                stream_until=time.monotonic()+2
                try:
                    while True:
                        if not local and (time.monotonic()>=stream_until or not allows(agent, 'wifi')):break
                        events=agent.group.hub.after(revision)
                        for event in events:
                            value=({k:v for k,v in event.items() if k!='packet'} if local else
                                   agent.group.packet(request['request_id'],snapshot=event.get('packet'),revision=event['revision']))
                            if value:self.wfile.write(encode(value)+b'\n');self.wfile.flush()
                            revision=event['revision']
                except (OSError,ValueError):pass
                return
            if self.path == "/status":
                with agent.lock:
                    raw = encode({**agent.state,'bluetooth':agent.bluetooth.snapshot(),
                                  'service_transports':agent.group.transport_status()})
                content = "application/json"
            elif self.path == "/":
                raw = Path(__file__).with_name("status.html").read_bytes()
                content = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path == '/local-service-transports':
                if self.client_address[0] != '127.0.0.1' or self.headers.get('X-BMI30-Local') != '1':
                    self.send_error(403);return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 1<=length<=1024:raise ValueError('Invalid request size')
                    body=json.loads(self.rfile.read(length))
                    result=agent.group.set_transports(body.get('mode'),body.get('revision'))
                    reply={'ok':True,'service_transports':result}
                except (ValueError,TypeError,AttributeError):
                    reply={'ok':False,'error':'Selection changed or invalid; refresh before retrying',
                           'service_transports':agent.group.transport_status()}
                except OSError:
                    reply={'ok':False,'error':'Cannot save service channel selection',
                           'service_transports':agent.group.transport_status()}
                raw=encode(reply)
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw);return
            if self.path=='/challenge':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 1<=length<=8192:raise ValueError('Invalid challenge size')
                    hello=agent.protocol.verify(json.loads(self.rfile.read(length)))
                    purpose=hello.get('purpose')
                    if purpose=='command' and not allows(agent,'wifi'):raise ValueError('Wi-Fi service channel disabled')
                    if purpose not in ('exchange','command') or len(hello.get('request_id',''))!=32:
                        raise ValueError('Invalid challenge request')
                    token=agent.challenges.issue(purpose,8,hello['id'])
                    raw=encode(agent.protocol.sign({'id':agent.config['id'],'boot':agent.boot,'seq':agent.seq,
                        'purpose':purpose,'request_id':hello['request_id'],'challenge':token}))
                    self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except Exception:self.send_error(403)
                return
            if self.path in ("/command", "/local-command"):
                local = self.path == "/local-command"
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 16384:
                        raise ValueError("Invalid length")
                    if local and (self.client_address[0] != "127.0.0.1" or self.headers.get("X-BMI30-Local") != "1"):
                        raise ValueError("Local portal access required")
                    raw = self.rfile.read(length)
                    if local:
                        data = json.loads(raw)
                        data = {"target": data["target"], "action": data["action"], "params": data["params"],
                                "command_id": uuid.uuid4().hex, 'age_s':0, "via": []}
                        transit=0
                    else:
                        with agent.lock:
                            data = agent.protocol.open(raw)
                        transit=agent.accept_rpc(data,'command')
                    try:
                        reply = agent.group.command(data,transit,ingress=None if local else 'wifi')
                    except Exception as exc:
                        reply = {"ok": False, "command_id": data.get("command_id"), "target": data.get("target"),
                                 "error": str(exc)[:240] if isinstance(exc, ValueError) else "Channel unavailable; refresh state before retrying"}
                    with agent.lock:
                        if not local:reply={**reply,'_request_id':data['_request_id'],'_source_id':agent.config['id']}
                        raw = encode(reply) if local else agent.protocol.seal(reply)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json" if local else "application/octet-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception:
                    self.send_error(403)
                return
            if self.path != "/exchange":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= MAX_PACKET:
                    raise ValueError("Invalid length")
                raw = self.rfile.read(length)
                with agent.lock:
                    data = agent.protocol.open(raw)
                transit=agent.accept_rpc(data,'exchange')
                reply = agent.exchange(data, self.client_address[0],transit)
                with agent.lock:
                    reply={**reply,'_request_id':data['_request_id'],'_source_id':agent.config['id']}
                    raw = agent.protocol.seal(reply)
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.send_error(403)
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(24)

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            super().process_request(request, client_address)

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()
    server=Server((bind, port),Handler)
    if ready:ready(server)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/bmi30-network/config.json")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    agent = Agent(json.loads(Path(args.config).read_text()))
    if args.once:
        agent.tick()
        print(json.dumps(agent.state))
        return
    threading.Thread(target=serve, args=(agent,), daemon=True).start()
    threading.Thread(target=agent.discovery_loop, daemon=True).start()
    threading.Thread(target=agent.group.run, daemon=True).start()
    threading.Thread(target=agent.bluetooth.run, daemon=True, name='bmi30-bluez').start()
    while True:
        try:
            agent.tick()
        except Exception as exc:
            LOG.error("Network observation failed: %s", type(exc).__name__)
        time.sleep(15 + random.uniform(0, 3))


if __name__ == "__main__":
    main()

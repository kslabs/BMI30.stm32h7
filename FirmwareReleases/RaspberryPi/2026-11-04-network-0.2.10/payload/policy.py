"""Pure routing policy. Costs are estimates, not a throughput benchmark."""
from __future__ import annotations

import hashlib
import ipaddress
import math

import cohesion

MAX_HOPS = 4
MAX_AGE = 100


def coordinator(nodes):
    """RS485 addresses are only bus-local; hardware identity breaks ties."""
    return min(nodes, key=lambda n: (n.get("role") != "master",
                                    n.get("rs485_id", 255), n["id"]))["id"] if nodes else None


def subnet(node_id, occupied=()):
    """Private /24, outside the legacy 10.42/10.43 portal networks."""
    start = int.from_bytes(hashlib.sha256(node_id.encode()).digest()[:2], "big") % 16384
    occupied = [ipaddress.ip_network(x, strict=False) for x in occupied]
    for i in range(16384):
        value = (start + i) % 16384
        net = ipaddress.ip_network(f"10.{64 + value // 256}.{value % 256}.0/24")
        if not any(net.overlaps(other) for other in occupied):
            return str(net.network_address + 1) + "/24"
    raise ValueError("No unused service subnet")


def radio_cost(signal, frequency):
    signal = max(0, min(100, float(signal)))
    # Prefer 5 GHz only when reception is usable. A weak 5 GHz link must lose
    # to a strong 2.4 GHz link; same-radio relays consume airtime twice.
    return 15 + (100 - signal) * 1.25 + (max(0, 45 - signal) * 2) - (
        12 if 4900 <= int(frequency) < 5900 and signal >= 45 else 0)


def internet_cost(probe):
    if not probe.get("ok"):
        return math.inf
    return min(80, max(0, probe.get("rtt_ms", 200)) * .10) + 80 * probe.get("loss", 0)


def valid_path(node, local_id):
    path = node.get("path", [])
    return (isinstance(path, list) and 0 < len(path) < MAX_HOPS
            and path[0] == node.get("id") and len(set(path)) == len(path)
            and local_id not in path and node.get("internet", {}).get("ok")
            and math.isfinite(node.get("cost", math.inf)))


def candidates(local, nodes, visible, profiles):
    """Only candidates with credentials, or authenticated BMI30 advertisements."""
    result = []
    for ap in visible:
        if ap.get("signal", 0) < 20:
            continue
        peer = next((n for n in nodes if n.get("ap", {}).get("bssid", "").lower()
                     == ap["bssid"].lower()), None)
        if peer:
            if not valid_path(peer, local["id"]):
                continue
            # Overlapping downstream addresses cannot be NAT-routed safely.
            try:
                if ipaddress.ip_interface(local["ap"]["address"]).network.overlaps(
                        ipaddress.ip_interface(peer["ap"]["address"]).network):
                    continue
            except (KeyError, ValueError):
                continue
            cost = radio_cost(ap["signal"], ap["freq"]) + 25 + peer["cost"]
            result.append({**ap, "kind": "relay", "parent": peer["id"], "cost": cost,
                           "path": [local["id"]] + peer["path"]})
        elif not ap["ssid"].upper().startswith("BMI30") and ap["ssid"] in profiles:
            # WAN quality is known only after association. Until then this is
            # an estimate, and activation must pass an actual internet probe.
            # An untested uplink must not look faster merely because its WAN
            # latency is unknown. Compare radios using the same WAN estimate;
            # activation still verifies the selected uplink before committing.
            penalty = internet_cost(local["internet"]) if local["internet"].get("ok") else 15
            if not math.isfinite(penalty):
                penalty = 15
            result.append({**ap, "kind": "direct", "parent": None,
                           "cost": radio_cost(ap["signal"], ap["freq"]) + penalty,
                           "path": [local["id"]]})
    return cohesion.annotate_candidates(local, nodes, sorted(result, key=lambda c: (c["cost"], c["bssid"])))


class Decision:
    def __init__(self, dwell=300, confirmations=3):
        self.dwell, self.confirmations = dwell, confirmations
        self.last_switch = -dwell
        self.pending = None
        self.count = 0
        self.failed = {}

    def choose(self, local, choices, now):
        choices = [c for c in choices if self.failed.get(c["bssid"], 0) <= now]
        if not choices:
            self.pending, self.count = None, 0
            return None
        healthy = local.get("internet", {}).get("ok", False)
        current = local.get("bssid", "").lower()
        band_upgrade = False
        cohesion_move = False
        route_class_changed = False
        if healthy:
            if local.get("uplink") == "ethernet" or local.get("clients", 0):
                self.pending, self.count = None, 0
                return None
            root = local.get("root_lan")
            root_key = root.get("key") if cohesion.valid_lan(root) else None
            majority = [c for c in choices if c.get("kind") == "direct"
                        and c.get("cohesion_majority") and c.get("signal", 0) >= 40]
            hold_lan = next((c.get("cohesion_hold_lan") for c in choices if c.get("cohesion_hold_lan")), None)
            if majority:
                choices = majority
                cohesion_move = root_key != choices[0]["cohesion_lan_key"]
                route_class_changed = cohesion_move or local.get("parent") is not None
            elif hold_lan:
                # A tie is not permission to move a healthy known LAN.
                choices = [c for c in choices if c.get("kind") == "direct"
                           and c.get("cohesion_lan_key") == hold_lan and c.get("signal", 0) >= 40]
                route_class_changed = local.get("parent") is not None
                if not choices:
                    self.pending, self.count = None, 0
                    return None
            elif root_key and local.get("parent") is not None:
                direct = [c for c in choices if c.get("kind") == "direct"
                          and c.get("cohesion_lan_key") == root_key and c.get("signal", 0) >= 40]
                if direct:
                    choices, route_class_changed = direct, True
            # Band preference must not move a usable direct router uplink
            # behind another BMI30's NAT merely because its AP has 5 GHz.
            # Weak reception or failed Internet still permits relay recovery.
            if (local.get("uplink") == "wifi" and local.get("parent") is None
                    and local.get("path") == [local.get("id")]
                    and not local.get("ssid", "").upper().startswith("BMI30")
                    and local.get("signal", 0) >= 40):
                choices = [c for c in choices if c.get("kind") != "relay"]
                if not choices:
                    self.pending, self.count = None, 0
                    return None
            # Separate entry/exit levels prevent band oscillation near one
            # signal threshold. A usable 5 GHz uplink survives WAN jitter;
            # weak reception and failed Internet still permit recovery.
            if route_class_changed:
                preferred = [c for c in choices if c.get("freq", 0) >= 4900 and c.get("signal", 0) >= 55]
                if preferred:
                    choices = preferred
            elif local.get("freq", 0) >= 4900 and local.get("signal", 0) >= 40:
                choices = [c for c in choices if c.get("freq", 0) >= 4900
                           and c.get("signal", 0) >= 40]
                if not choices:
                    self.pending, self.count = None, 0
                    return None
            elif local.get("freq", 0) < 4900:
                preferred = [c for c in choices if c.get("freq", 0) >= 4900
                             and c.get("signal", 0) >= 55]
                if preferred:
                    choices, band_upgrade = preferred, True
        best = choices[0]
        if best["bssid"].lower() == current:
            self.pending, self.count = None, 0
            return None
        if healthy:
            # Don't displace a healthy Ethernet uplink or strand AP clients
            # for a marginal improvement. Failure recovery is still allowed.
            cost = local.get("cost", math.inf)
            if now - self.last_switch < self.dwell or (not band_upgrade and not cohesion_move and not route_class_changed and
                    cost - best["cost"] < max(18, cost * .25)):
                self.pending, self.count = None, 0
                return None
        key = best["bssid"]
        self.count = self.count + 1 if key == self.pending else 1
        self.pending = key
        if self.count < self.confirmations:
            return None
        return best

    def completed(self, choice, now, success):
        self.pending, self.count = None, 0
        self.last_switch = now
        if not success:
            self.failed[choice["bssid"]] = now + 600

"""Evidence-only LAN cohesion. No network, device, credential or file access.

Callers supply authenticated Records.nodes() peers. RID membership proves a
shared physical group; SSIDs and numeric bus IDs alone never prove membership.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import ipaddress
import json
import math
import re
import time

MAX_AGE = 45.0
VERSION = 1


def short_uid96(uid):
    """STM32 rs485_compute_short_id36: UID display is words 2,1,0."""
    if not isinstance(uid, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", uid):
        return ""
    value = 1469598103934665603
    for byte in int(uid, 16).to_bytes(12, "little"):
        value = ((value ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f'{((value ^ (value >> 36)) & 0xFFFFFFFFF) or 1:09X}'


def _age(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else math.inf


def _mac(value):
    value = str(value or "").lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value):
        return None
    if value in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff") or int(value[:2], 16) & 1:
        return None
    return value


def _lan_key(cidr, gateway, mac):
    return hashlib.sha256(json.dumps([cidr, gateway, mac], separators=(",", ":")).encode()).hexdigest()


def valid_lan(value):
    if not isinstance(value, dict) or value.get("version") != VERSION or _age(value.get("age_s")) > MAX_AGE:
        return False
    try:
        net = ipaddress.IPv4Network(value["cidr"], strict=True)
        gateway = ipaddress.IPv4Address(value["gateway_ip"])
        mac = _mac(value["gateway_mac"])
        return (gateway in net and bool(mac) and value.get("key") == _lan_key(str(net), str(gateway), mac))
    except (ValueError, KeyError, TypeError):
        return False


def _rid_entry(entry, now):
    if not isinstance(entry, dict) or any(entry.get(k) is not True for k in
            ("complete", "recent", "short_valid", "node_id_matches_status")) or entry.get("node_conflict"):
        return None
    short = str(entry.get("short_id") or entry.get("host_id") or "").upper()
    updated = entry.get("host_updated_at")
    node = entry.get("node_id")
    if (not re.fullmatch(r"[0-9A-F]{9}", short) or type(node) is not int or not 0 <= node <= 31
            or type(updated) not in (float, int) or not math.isfinite(updated)
            or not -1 <= now - updated <= MAX_AGE):
        return None
    return short, max(0.0, now - updated)


def observe(snapshot, addr_rows, neighbor_rows, core, now=None):
    """Return fresh local membership/LAN evidence; missing evidence is unknown."""
    now = time.time() if now is None else now
    own = short_uid96(snapshot.get("stm32_uid96"))
    group = {"version": VERSION, "available": False, "own_short_id": own,
             "member_short_ids": [], "age_s": 0.0, "reason": "RID evidence unavailable"}
    result = {"cohesion": group, "root_lan": None}
    connection = core.get("connection") if isinstance(core, dict) else None
    if isinstance(core, dict) and core.get("ok") is True and not core.get("stale") and isinstance(connection, dict) and connection.get("connected") is True:
        rid = core.get("rs485_ident") if isinstance(core.get("rs485_ident"), dict) else {}
        entries = rid.get("nodes") or {}
        local = rid.get("local") if isinstance(rid.get("local"), dict) else {}
        local_entry = _rid_entry(local, now)
        sync = core.get("sync") if isinstance(core.get("sync"), dict) else {}
        node_id = sync.get("local_node_id", sync.get("device_id"))
        accepted = []
        membership_complete = isinstance(entries, dict) and 0 < len(entries) <= 32
        if isinstance(entries, dict):
            for key, entry in entries.items():
                item = _rid_entry(entry, now)
                if item and str(entry["node_id"]) == str(key):
                    accepted.append(item)
                else:
                    membership_complete = False
            if "node_count" in rid and (type(rid["node_count"]) is not int or rid["node_count"] != len(entries)):
                membership_complete = False
            if "node_list" in rid:
                rows = rid["node_list"]
                if (not isinstance(rows, list) or len(rows) != len(entries)
                        or any(not _rid_entry(row, now) for row in rows)
                        or sorted((row["node_id"], _rid_entry(row, now)[0]) for row in rows)
                        != sorted((entry["node_id"], _rid_entry(entry, now)[0]) for entry in entries.values()
                                  if _rid_entry(entry, now))):
                    membership_complete = False
        shorts = [item[0] for item in accepted]
        mapped_local = entries.get(str(node_id)) if isinstance(entries, dict) else None
        if (membership_complete and own and local_entry and local_entry[0] == own and local.get("node_id") == node_id
                and _rid_entry(mapped_local, now) and _rid_entry(mapped_local, now)[0] == own
                and own in shorts and len(shorts) == len(set(shorts))):
            group.update(available=True, member_short_ids=sorted(shorts),
                         age_s=max(item[1] for item in accepted), reason="fresh complete local RID")
        else:
            group["reason"] = "RID identity, freshness or membership unverified"
    # A BMI30 AP's subnet is downstream; only the verified route layer may
    # inherit that parent's upstream fingerprint.
    direct = snapshot.get("uplink") == "ethernet" or (
        snapshot.get("uplink") == "wifi" and bool(snapshot.get("ssid"))
        and not snapshot["ssid"].upper().startswith("BMI30"))
    if direct and (snapshot.get("internet") or {}).get("ok"):
        try:
            gateway = ipaddress.IPv4Address(snapshot.get("gateway"))
            iface = snapshot.get("iface")
            nets = {str(ipaddress.IPv4Network(f'{a["local"]}/{a["prefixlen"]}', strict=False))
                    for row in addr_rows if isinstance(row, dict) and row.get("ifname") == iface
                    for a in row.get("addr_info", []) if isinstance(a, dict) and a.get("family") == "inet"
                    and a.get("scope") == "global" and gateway in ipaddress.IPv4Network(f'{a["local"]}/{a["prefixlen"]}', strict=False)}
            macs = {_mac(n.get("lladdr")) for n in neighbor_rows
                    if isinstance(n, dict) and n.get("dev") == iface and n.get("dst") == str(gateway)
                    and not set(n.get("state", "").split() if isinstance(n.get("state"), str) else n.get("state") or []) & {"FAILED", "INCOMPLETE"}}
            macs.discard(None)
            if len(nets) == len(macs) == 1:
                cidr, mac = next(iter(nets)), next(iter(macs))
                result["root_lan"] = {"version": VERSION, "key": _lan_key(cidr, str(gateway), mac),
                    "cidr": cidr, "gateway_ip": str(gateway), "gateway_mac": mac,
                    "source": "direct", "source_id": snapshot.get("id"), "age_s": 0.0}
        except (ValueError, KeyError, TypeError):
            pass
    return result


def inherit_root(local, parent):
    """Copy only a fresh, authenticated valid parent's upstream LAN evidence."""
    root = parent.get("root_lan")
    path = parent.get("path")
    record_age = _age(parent.get("age_s"))
    if (not valid_lan(root) or record_age + _age(root.get("age_s")) > MAX_AGE
            or not (parent.get("internet") or {}).get("ok")
            or not isinstance(path, list) or not 0 < len(path) < 4 or not all(isinstance(p, str) for p in path) or path[0] != parent.get("id")
            or len(set(path)) != len(path) or local.get("id") in path
            or local.get("parent") != parent.get("id") or local.get("path") != [local.get("id")] + path):
        return None
    return {**root, "source": "relay", "via_parent": parent["id"],
            "age_s": record_age + _age(root["age_s"])}


def _membership(node, local=False):
    group = node.get("cohesion") or {}
    if not isinstance(group, dict):
        return None
    own = short_uid96(node.get("stm32_uid96"))
    age = _age(group.get("age_s")) + (0 if local else _age(node.get("age_s")))
    members = group.get("member_short_ids")
    if (group.get("version") != VERSION or group.get("available") is not True or not own
            or own != group.get("own_short_id") or age > MAX_AGE
            or not isinstance(members, list) or not 0 < len(members) <= 32
            or not all(isinstance(item, str) and re.fullmatch(r"[0-9A-F]{9}", item) for item in members)
            or own not in members
            or len(members) != len(set(members))):
        return None
    return own, set(members)


def annotate_candidates(local, nodes, choices):
    """Attach a strict mutual-group majority only to observed direct BSSIDs.

    Unknown BSSID-to-LAN mappings stay unknown; SSID aliases are never guessed.
    """
    own = _membership(local, local=True)
    peers = []
    if own:
        for node in nodes:
            if node.get("id") == local.get("id"):
                continue
            member = _membership(node)
            if member and member[0] != own[0] and member[0] in own[1] and own[0] in member[1]:
                peers.append(node)
    short_counts = Counter(short_uid96(n.get("stm32_uid96")) for n in peers)
    voters = [local] + [n for n in peers if short_counts[short_uid96(n.get("stm32_uid96"))] == 1] if own else []
    votes = Counter()
    direct_bssids = {}
    for node in voters:
        root = node.get("root_lan")
        record_age = 0 if node is local else _age(node.get("age_s"))
        if not valid_lan(root) or record_age + _age(root.get("age_s")) > MAX_AGE or not (node.get("internet") or {}).get("ok"):
            continue
        votes[root["key"]] += 1
        if (root.get("source") == "direct" and node.get("parent") is None
                and node.get("path") == [node.get("id")] and _mac(node.get("bssid"))):
            direct_bssids.setdefault(node["bssid"].lower(), set()).add(root["key"])
    denominator = len(own[1]) if own else 0
    winners = [key for key, count in votes.items() if count >= 2 and count * 2 > denominator]
    winner = winners[0] if len(winners) == 1 else None
    current_root = local.get("root_lan")
    current_key = current_root.get("key") if valid_lan(current_root) else None
    hold = current_key if (not winner and len(voters) >= 2 and current_key
                          and votes.get(current_key, 0) == max(votes.values(), default=0)) else None
    result = []
    for choice in choices:
        keys = direct_bssids.get(choice.get("bssid", "").lower(), set()) if choice.get("kind") == "direct" else set()
        key = next(iter(keys)) if len(keys) == 1 else None
        result.append({**choice, "cohesion_lan_key": key, "cohesion_majority": bool(winner and key == winner),
                       "cohesion_votes": votes.get(key, 0), "cohesion_group_size": denominator,
                       "cohesion_majority_key": winner, "cohesion_hold_lan": hold,
                       "cohesion_reason": "strict fresh mutual-group majority" if winner and key == winner else
                           ("candidate LAN unknown" if key is None else "no strict majority for candidate LAN")})
    return result

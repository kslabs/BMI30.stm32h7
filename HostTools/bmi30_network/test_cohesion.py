"""Source-backed RID identity and evidence-only majority route selection."""
import copy
import unittest

import cohesion
import policy

NOW = 1000.0
UIDS = ["3336353430325113000C002A", "333635343032511300090040", "333635343032511300090041"]


def core(index, count=3):
    rows = {str(i): {"node_id": i, "short_id": cohesion.short_uid96(uid),
            "complete": True, "recent": True, "short_valid": True,
            "node_id_matches_status": True, "node_conflict": False, "host_updated_at": NOW - 1}
            for i, uid in enumerate(UIDS[:count])}
    return {"ok": True, "stale": False, "connection": {"connected": True},
            "sync": {"local_node_id": index}, "rs485_ident": {"local": rows[str(index)], "nodes": rows}}


def observed(index, lan=0, count=3, freq=2417):
    subnet = f"192.168.{lan}.0/24"
    gateway = f"192.168.{lan}.1"
    local = {"id": f"BMI30-{index}", "stm32_uid96": UIDS[index], "parent": None,
             "path": [f"BMI30-{index}"], "uplink": "wifi", "iface": "wlan0", "clients": 0,
             "ssid": f"router-{lan}", "bssid": f"02:00:00:00:00:{index + 1:02x}",
             "freq": freq, "signal": 65, "gateway": gateway, "age_s": 0.0,
             "internet": {"ok": True, "rtt_ms": 40}, "cost": 10.0,
             "ap": {"bssid": f"02:00:00:01:00:{index + 1:02x}", "address": f"10.70.{index}.1/24"}}
    addr = [{"ifname": "wlan0", "addr_info": [{"family": "inet", "scope": "global",
             "local": f"192.168.{lan}.{100 + index}", "prefixlen": 24}]}]
    neigh = [{"dev": "wlan0", "dst": gateway, "lladdr": f"02:00:00:02:00:{lan + 1:02x}", "state": ["REACHABLE"]}]
    local.update(cohesion.observe(local, addr, neigh, core(index, count), now=NOW))
    assert local["root_lan"]["cidr"] == subnet
    return local


def candidates(local, peers, extra=None):
    nodes = [local] + peers
    visible = [{"ssid": n["ssid"], "bssid": n["bssid"], "signal": n["signal"], "freq": n["freq"]} for n in nodes]
    if extra:
        visible.append(extra)
    return policy.candidates(local, nodes, visible, {v["ssid"]: {} for v in visible})


class CohesionEvidenceTests(unittest.TestCase):
    def test_real_stm32_uid36_matches_s07_and_m02_rid(self):
        self.assertEqual(cohesion.short_uid96(UIDS[0]), "5F295F47B")
        self.assertEqual(cohesion.short_uid96(UIDS[1]), "8CC938B79")
        self.assertEqual(cohesion.short_uid96("123"), "")

    def test_observe_validates_fresh_complete_rid_and_gateway_fingerprint(self):
        node = observed(0)
        self.assertTrue(node["cohesion"]["available"])
        self.assertEqual(len(node["cohesion"]["member_short_ids"]), 3)
        self.assertTrue(cohesion.valid_lan(node["root_lan"]))

    def test_recent_boolean_alone_cannot_refresh_old_rid(self):
        c = core(0)
        for item in c["rs485_ident"]["nodes"].values():
            item["host_updated_at"] = NOW - 46
        self.assertFalse(cohesion.observe(observed(0), [], [], c, now=NOW)["cohesion"]["available"])

    def test_one_stale_member_cannot_shrink_the_majority_denominator(self):
        c = core(0)
        c["rs485_ident"]["nodes"]["2"]["host_updated_at"] = NOW - 46
        self.assertFalse(cohesion.observe(observed(0), [], [], c, now=NOW)["cohesion"]["available"])

    def test_node_count_and_node_list_must_agree_with_complete_map(self):
        for value in (99, None):
            c = core(0)
            c["rs485_ident"]["node_count"] = value
            self.assertFalse(cohesion.observe(observed(0), [], [], c, now=NOW)["cohesion"]["available"])
        c = core(0)
        c["rs485_ident"]["node_list"] = list(c["rs485_ident"]["nodes"].values())[:2]
        self.assertFalse(cohesion.observe(observed(0), [], [], c, now=NOW)["cohesion"]["available"])

    def test_wrong_own_uid_incomplete_rid_and_conflict_fail_closed(self):
        for field, value in (("complete", False), ("node_conflict", True), ("short_id", "000000001")):
            c = core(0)
            c["rs485_ident"]["local"][field] = value
            self.assertFalse(cohesion.observe(observed(0), [], [], c, now=NOW)["cohesion"]["available"])

    def test_root_inheritance_requires_same_valid_fresh_parent_path(self):
        parent = observed(1)
        local = observed(0)
        local.update(parent=parent["id"], path=[local["id"]] + parent["path"])
        inherited = cohesion.inherit_root(local, parent)
        self.assertEqual(inherited["key"], parent["root_lan"]["key"])
        self.assertEqual(inherited["source"], "relay")
        parent["age_s"] = 46
        self.assertIsNone(cohesion.inherit_root(local, parent))
        parent["age_s"] = 0
        local["path"] = [local["id"]]
        self.assertIsNone(cohesion.inherit_root(local, parent))

    def test_membership_freshness_adds_advertisement_age(self):
        local, b, c = observed(0), observed(1, 1), observed(2, 1)
        b["cohesion"]["age_s"] = 45
        b["age_s"] = 1
        self.assertFalse(any(v["cohesion_majority"] for v in candidates(local, [b, c])))

    def test_group_is_mutual_and_uid_bound_not_numeric_id_or_ssid(self):
        local, b, c = observed(0), observed(1, 1), observed(2, 1)
        b["cohesion"]["member_short_ids"].remove(cohesion.short_uid96(UIDS[0]))
        self.assertFalse(any(v["cohesion_majority"] for v in candidates(local, [b, c])))
        b = observed(1, 1)
        b["stm32_uid96"] = UIDS[0]
        self.assertFalse(any(v["cohesion_majority"] for v in candidates(local, [b, c])))

    def test_same_ssid_different_bssid_is_explicitly_unknown(self):
        local, b, c = observed(0), observed(1, 1), observed(2, 1)
        values = candidates(local, [b, c], {"ssid": b["ssid"], "bssid": "02:00:00:ff:00:ff", "signal": 99, "freq": 5180})
        unknown = next(v for v in values if v["bssid"] == "02:00:00:ff:00:ff")
        self.assertFalse(unknown["cohesion_majority"])
        self.assertIsNone(unknown["cohesion_lan_key"])
        self.assertEqual(unknown["cohesion_reason"], "candidate LAN unknown")

    def test_two_radios_with_different_ssids_can_share_observed_fingerprint(self):
        local, b, c = observed(0), observed(1, 1), observed(2, 1)
        c.update(ssid="router-other-band", freq=5180)
        self.assertEqual(sum(v["cohesion_majority"] for v in candidates(local, [b, c])), 2)

    def test_duplicate_stm_identity_does_not_create_extra_vote(self):
        local, b = observed(0), observed(1, 1)
        clone = copy.deepcopy(b)
        clone["id"] = "BMI30-CLONE"
        clone["bssid"] = "02:00:00:01:02:03"
        self.assertFalse(any(v["cohesion_majority"] for v in candidates(local, [b, clone])))


class CohesionDecisionTests(unittest.TestCase):
    def test_strict_majority_can_override_marginal_cost_but_not_dwell_or_confirmations(self):
        local, b, c = observed(0, freq=5180), observed(1, 1), observed(2, 1)
        values = candidates(local, [b, c])
        self.assertTrue(all(v["cost"] > local["cost"] for v in values if v["cohesion_majority"]))
        decision = policy.Decision()
        decision.last_switch = 900
        self.assertIsNone(decision.choose(local, values, 1100))
        self.assertIsNone(decision.choose(local, values, 1200))
        self.assertIsNone(decision.choose(local, values, 1216))
        selected = decision.choose(local, values, 1232)
        self.assertEqual(selected["cohesion_lan_key"], b["root_lan"]["key"])
        self.assertEqual(selected["freq"], 2417)

    def test_healthy_known_lan_tie_stays_even_if_other_lan_has_stronger_5ghz(self):
        local, peer = observed(0, count=2), observed(1, 1, count=2, freq=5180)
        peer["signal"] = 99
        self.assertIsNone(policy.Decision(confirmations=1).choose(local, candidates(local, [peer]), 1200))

    def test_relay_returns_to_verified_same_root_direct_24ghz_despite_5ghz_lock(self):
        parent, local = observed(1, count=2), observed(0, count=2, freq=5180)
        parent["signal"] = 40
        local.update(ssid=parent["id"], parent=parent["id"], path=[local["id"], parent["id"]])
        local["root_lan"] = cohesion.inherit_root(local, parent)
        values = candidates(local, [parent])
        chosen = policy.Decision(confirmations=1).choose(local, values, 1200)
        self.assertEqual(chosen["kind"], "direct")
        self.assertEqual(chosen["freq"], 2417)

    def test_majority_does_not_force_a_weak_direct_or_override_failed_internet(self):
        local, b, c = observed(0), observed(1, 1), observed(2, 1)
        b["signal"] = c["signal"] = 39
        self.assertIsNone(policy.Decision(confirmations=1).choose(local, candidates(local, [b, c]), 1200))
        local["internet"]["ok"] = False
        # Recovery remains the ordinary cost/probe policy, not a majority override.
        choices = candidates(local, [b, c])
        selected = policy.Decision(confirmations=1).choose(local, choices, 1200)
        self.assertIsNone(selected)  # Current direct BSSID remains the cheapest.

    def test_healthy_clients_and_ethernet_veto_cohesion_migration(self):
        for extra in ({"clients": 1}, {"uplink": "ethernet"}):
            local, b, c = observed(0), observed(1, 1), observed(2, 1)
            local.update(extra)
            self.assertIsNone(policy.Decision(confirmations=1).choose(local, candidates(local, [b, c]), 1200))


if __name__ == "__main__":
    unittest.main()

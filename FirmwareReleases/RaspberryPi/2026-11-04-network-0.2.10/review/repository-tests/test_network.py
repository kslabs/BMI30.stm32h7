import copy
import json
import math
import time
import unittest
from unittest.mock import patch

import network
import policy
from protocol import Protocol, Records


def node(name="BMI30-A", signal=60):
    return {"id": name, "role": "slave", "rs485_id": 1,
            "path": [name], "cost": 70, "internet": {"ok": True, "rtt_ms": 50, "loss": 0},
            "bssid": "00:00:00:00:00:01", "signal": signal, "freq": 5180, "ssid": "internet",
            "ap": {"bssid": "00:00:00:00:00:02", "address": "10.70.1.1/24"},
            "seq": 1, "issued": time.time(), "boot": "boot1"}


class PolicyTests(unittest.TestCase):
    def test_master_priority_and_duplicate_bus_ids(self):
        a, b, c = node("BMI30-A"), node("BMI30-B"), node("BMI30-C")
        a["rs485_id"] = 0
        b["role"] = c["role"] = "master"
        self.assertEqual(policy.coordinator([c, a, b]), "BMI30-B")
        c["rs485_id"] = 0
        self.assertEqual(policy.coordinator([a, b, c]), "BMI30-C")

    def test_5ghz_preferred_but_weak_signal_loses(self):
        self.assertLess(policy.radio_cost(70, 5180), policy.radio_cost(70, 2412))
        self.assertGreater(policy.radio_cost(25, 5180), policy.radio_cost(80, 2412))

    def test_relay_loop_and_hop_limit(self):
        parent = node("BMI30-B")
        self.assertTrue(policy.valid_path(parent, "BMI30-A"))
        for path in (["BMI30-B", "BMI30-A"], ["BMI30-B", "BMI30-B"],
                     ["BMI30-B", "BMI30-C", "BMI30-D", "BMI30-E"]):
            parent["path"] = path
            self.assertFalse(policy.valid_path(parent, "BMI30-A"))

    def test_peer_without_internet_never_gateway(self):
        peer = node("BMI30-B")
        peer["internet"]["ok"] = False
        self.assertFalse(policy.valid_path(peer, "BMI30-A"))

    def test_subnets_do_not_overlap(self):
        first = policy.subnet("BMI30-A")
        second = policy.subnet("BMI30-A", [first])
        self.assertNotEqual(first, second)

    def test_relay_subnet_collision_rejected(self):
        local, peer = node(), node("BMI30-B")
        ap = {"ssid": "BMI30-B", "bssid": peer["ap"]["bssid"], "freq": 5180, "signal": 90}
        self.assertEqual(policy.candidates(local, [peer], [ap], {}), [])
        peer["ap"]["address"] = "10.70.2.1/24"
        self.assertEqual(policy.candidates(local, [peer], [ap], {})[0]["parent"], "BMI30-B")

    def test_hysteresis_dwell_and_blacklist(self):
        local = node()
        choice = {"bssid": "00:00:00:00:00:03", "cost": 30, "freq": 5180, "signal": 90}
        decision = policy.Decision()
        self.assertIsNone(decision.choose(local, [choice], 1))
        self.assertIsNone(decision.choose(local, [choice], 2))
        self.assertEqual(decision.choose(local, [choice], 3), choice)
        decision.completed(choice, 3, False)
        self.assertIsNone(decision.choose(local, [choice], 400))

    def test_healthy_ethernet_and_children_keep_connection(self):
        choice = {"bssid": "00:00:00:00:00:03", "cost": 1}
        for extra in ({"uplink": "ethernet"}, {"clients": 1}):
            decision = policy.Decision(confirmations=1)
            self.assertIsNone(decision.choose({**node(), **extra}, [choice], 500))

    def test_failed_internet_can_failover_with_children(self):
        local = node()
        local.update(clients=2, internet={"ok": False})
        choice = {"bssid": "00:00:00:00:00:03", "cost": 200}
        self.assertEqual(policy.Decision(confirmations=1).choose(local, [choice], 1), choice)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = Protocol("12" * 32)

    def test_wrong_key_tampering_and_replay_rejected(self):
        try:
            import cryptography
        except ImportError:
            self.skipTest("Linux target supplies python3-cryptography")
        raw = self.protocol.seal({"psk": "private-password"})
        self.assertNotIn(b"private-password", raw)
        with self.assertRaises(Exception):
            Protocol("34" * 32).open(raw)
        self.assertEqual(self.protocol.open(raw)["psk"], "private-password")
        with self.assertRaises(ValueError):
            self.protocol.open(raw)

    def test_expired_gossip_cannot_be_refreshed_by_relay(self):
        data = node()
        records = Records(self.protocol)
        signed = self.protocol.sign(data)
        records.merge([signed])
        with patch("time.monotonic", return_value=time.monotonic() + 101):
            records.merge([signed])
            self.assertEqual(records.nodes(), [])

    def test_sequence_rollback_and_signature_tampering(self):
        data = node()
        records = Records(self.protocol)
        data["seq"] = 2
        records.merge([self.protocol.sign(data)])
        older = {**data, "seq": 1}
        records.merge([self.protocol.sign(older)])
        self.assertEqual(records.nodes()[0]["seq"], 2)
        signed = self.protocol.sign(data)
        signed["data"] = {**data, "id": "BMI30-EVIL"}
        with self.assertRaises(ValueError):
            self.protocol.verify(signed)


class AdapterTests(unittest.TestCase):
    def test_nmcli_escaped_ssid(self):
        self.assertEqual(network.split_terse(r"Wi\:Fi:ab\:cd\:ef:5180 MHz"),
                         ["Wi:Fi", "ab:cd:ef", "5180 MHz"])

    def test_failed_switch_rolls_back(self):
        adapter = network.Network({"id": "BMI30-A"})
        old = {"ap_uuid": "97d67fea-4a00-5c55-8e3f-bac0a10533a6"}
        choice = {"bssid": "00:00:00:00:00:03", "freq": 5180, "channel": 36}
        with patch.object(adapter, "snapshot", return_value=old), patch.object(adapter, "profile", return_value="new"), \
             patch.object(adapter, "wait_restored"), \
             patch("network.nm_fields", return_value={"802-11-wireless.mode":"ap", "connection.interface-name":"wlan0ap"}), \
             patch("network.Checkpoint") as checkpoint, patch("network.run", side_effect=RuntimeError("activation failed")):
            with self.assertRaises(RuntimeError):
                adapter.activate(choice, {"psk": "test-secret"})
            checkpoint.return_value.rollback.assert_called_once()
            checkpoint.return_value.commit.assert_not_called()

    def test_captive_portal_not_internet(self):
        with patch("network.run", return_value="200"):
            self.assertFalse(network.probe("wlan0")["ok"])
        with patch("network.run", return_value="204") as runner:
            self.assertTrue(network.probe("wlan0")["ok"])
            for call in runner.call_args_list:
                self.assertIn("--interface", call.args)
                self.assertIn("wlan0", call.args)


if __name__ == "__main__":
    unittest.main()

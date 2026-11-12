"""Keep a usable router uplink when only a BMI30 relay passes band entry."""
import unittest

import policy


def m02_local(signal=65, healthy=True, freq=2417):
    return {
        "id": "BMI30-E29805C7C", "uplink": "wifi", "clients": 0,
        "ssid": "VINETA-EL 2,4GHz", "bssid": "70:4f:57:da:0c:53",
        "parent": None, "path": ["BMI30-E29805C7C"],
        "freq": freq, "signal": signal, "cost": 69.06,
        "internet": {"ok": healthy},
    }


def observed_choices(direct5_signal=54):
    # Costs/signals from the pre-switch M02 observation on 2026-11-04.
    return [
        {"kind": "direct", "ssid": "VINETA-EL 2,4GHz",
         "bssid": "70:4f:57:da:0c:53", "freq": 2417,
         "signal": 65, "cost": 69.06},
        {"kind": "direct", "ssid": "VINETA-EL5GHz",
         "bssid": "70:4f:57:da:0c:54", "freq": 5180,
         "signal": direct5_signal, "cost": 70.81},
        {"kind": "relay", "ssid": "BMI30-C9775FBAE",
         "bssid": "2e:cf:67:50:12:42", "freq": 5180,
         "signal": 74, "cost": 125.95, "parent": "BMI30-C9775FBAE"},
    ]


class DirectUplinkPolicyTests(unittest.TestCase):
    def test_observed_m02_does_not_leave_healthy_direct_for_5ghz_relay(self):
        decision = policy.Decision()
        for now in (1000, 1016, 1032, 1048, 1064):
            self.assertIsNone(decision.choose(m02_local(), observed_choices(), now))
        self.assertIsNone(decision.pending)

    def test_5ghz_upgrade_still_occurs_between_direct_uplinks(self):
        decision = policy.Decision()
        choices = observed_choices(direct5_signal=55)
        self.assertIsNone(decision.choose(m02_local(), choices, 1000))
        self.assertIsNone(decision.choose(m02_local(), choices, 1016))
        selected = decision.choose(m02_local(), choices, 1032)
        self.assertEqual(selected["kind"], "direct")
        self.assertEqual(selected["freq"], 5180)

    def test_usable_5ghz_direct_is_not_replaced_by_even_stronger_peer(self):
        local = m02_local(signal=40, freq=5180)
        local["ssid"] = "VINETA-EL5GHz"
        choices = observed_choices()[2:]
        self.assertIsNone(policy.Decision(confirmations=1).choose(local, choices, 1000))

    def test_current_direct_is_retained_even_when_scan_only_sees_relay(self):
        self.assertIsNone(policy.Decision(confirmations=1).choose(
            m02_local(), observed_choices()[2:], 1000))

    def test_weak_direct_can_fall_back_to_relay(self):
        decision = policy.Decision()
        for now in (1000, 1016):
            self.assertIsNone(decision.choose(m02_local(signal=39), observed_choices(), now))
        self.assertEqual(decision.choose(m02_local(signal=39), observed_choices(), 1032)["kind"], "relay")

    def test_failed_direct_can_fall_back_despite_high_reception(self):
        choice = policy.Decision(confirmations=1).choose(
            m02_local(healthy=False), observed_choices()[2:], 1000)
        self.assertEqual(choice["kind"], "relay")

    def test_existing_relay_is_not_misclassified_as_direct(self):
        local = m02_local()
        local.update(ssid="BMI30-OLD", bssid="02:00:00:00:00:01",
                     parent="BMI30-OLD", path=[local["id"], "BMI30-OLD"])
        choice = policy.Decision(confirmations=1).choose(local, observed_choices(), 1000)
        self.assertEqual(choice["kind"], "relay")

    def test_unknown_topology_is_not_assumed_to_be_a_proven_direct_uplink(self):
        local = m02_local()
        local["path"] = []
        choice = policy.Decision(confirmations=1).choose(local, observed_choices(), 1000)
        self.assertEqual(choice["kind"], "relay")

    def test_dwell_and_client_guards_still_apply_to_direct_band_upgrade(self):
        choices = observed_choices(direct5_signal=70)
        decision = policy.Decision(confirmations=1)
        decision.last_switch = 900
        self.assertIsNone(decision.choose(m02_local(), choices, 1000))
        self.assertIsNone(decision.choose({**m02_local(), "clients": 1}, choices, 1300))
        self.assertEqual(decision.choose(m02_local(), choices, 1300)["kind"], "direct")


if __name__ == "__main__":
    unittest.main()

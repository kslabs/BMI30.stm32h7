import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from group import Group, local_state, validate_action, short_stm32_id
from protocol import Protocol


class GroupTest(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).parent / ("test-state-" + uuid.uuid4().hex)
        directory.mkdir()
        self.temp = SimpleNamespace(name=str(directory))
        self.agent = Mock(config={"id": "BMI30-A", "state_dir": self.temp.name, "group_state":str(directory/'group.json')},
                          protocol=Protocol("17" * 32), lock=threading.RLock(), transports=None)
        self.group = Group(self.agent)
        self.addCleanup(self.group.socket.close)
        def cleanup():
            for path in directory.iterdir():
                path.unlink()
            directory.rmdir()
        self.addCleanup(cleanup)

    def record(self, ident, at=None, seq=1):
        return self.agent.protocol.sign({"id": ident, "kind": "group-v1", "issued": at or time.time(),
                                         "seq": seq, "boot": "abc", "available": True})

    def merge(self, packet, address):
        packet={'request_id':self.group.challenges.issue('group',4),'self_age_s':0,**packet}
        packet['records']=[{'age_s':0,**entry} for entry in packet.get('records',[])]
        return self.group.merge(packet,address)

    def test_routes_use_hardware_id_and_shortest_duplicate(self):
        record = self.record("BMI30-C")
        self.merge({"self": self.record("BMI30-B"), "records": [{"record": record, "via": ["BMI30-C"]}]}, "192.168.1.2")
        self.assertEqual(self.group.routes["BMI30-C"]["path"], ["BMI30-B", "BMI30-C"])
        self.merge({"self": record}, "192.168.1.3")
        self.assertEqual(self.group.routes["BMI30-C"]["path"], ["BMI30-C"])

    def test_relay_cannot_refresh_stale_values_or_form_loop(self):
        self.merge({"self": self.record("BMI30-B"), "records": [
            {"record": self.record("BMI30-C", time.time() - 5), "via": ["BMI30-C"], 'age_s':5},
            {"record": self.record("BMI30-D"), "via": ["BMI30-A", "BMI30-D"]}]}, "192.168.1.2")
        self.assertNotIn("BMI30-C", self.group.records)
        self.assertNotIn("BMI30-D", self.group.records)

    def test_whitelist_and_bounds(self):
        for a, p in [("reboot", {}), ("rs485_id", {"device_id": True}), ("rs485_id", {"device_id": 32}),
                     ("sync_mode", {"role": "auto"}), ("optic_hold", {"hold_ds": 101})]:
            with self.assertRaises(ValueError):
                validate_action(a, p)

    def test_dead_owner_does_not_publish_live_sensors(self):
        now = time.time()
        core = {"ok": True, "connection": {"connected": True}}
        cache = {"cache_written_at": now - 10, "sensors": {"local": {"detadc1": True}}}
        state = local_state(core, cache, now)
        self.assertFalse(state["available"])
        self.assertIsNone(state["sensors"]["detadc1"])

    def test_cache_published_during_status_request_does_not_disconnect_owner(self):
        cache = Path(self.temp.name) / 'device-state.json'
        cache.write_text(json.dumps({'cache_written_at':1000.001,
                                    'sensors':{'local':{'detadc1':True}}}))
        self.agent.config['device_state'] = str(cache)
        with patch('group.time.time',return_value=1000) as clock:
            def status(*args,**kwargs):
                clock.return_value = 1000.002
                return contextlib.closing(io.BytesIO(b'{"ok":true,"connection":{"connected":true}}'))
            self.agent.opener.open.side_effect = status
            sample = self.group.sample()['data']
        self.assertTrue(sample['available'])
        self.assertTrue(sample['sensors']['detadc1'])
        self.assertGreaterEqual(sample['cache_age_s'],0)

    def test_uid_matches_live_stm32_identity(self):
        self.assertEqual(short_stm32_id({"uid_w0":655425,"uid_w1":808603923,"uid_w2":859190580}), "713B64477")

    def test_neighbor_membership_uses_confirmed_map_not_transient_sensor_rows(self):
        core = {"ok": True, "connection": {"connected": True},
                "sync": {"local_node_id": 12, "sync_seen_mask": (1 << 1) | (1 << 12)}}
        cache = {"cache_written_at": 100, "sensors": {"remote": []}}
        state = local_state(core, cache, 100)
        self.assertEqual(state["neighbors"], [{"node_id": 1, "label": "RS485 01", "master": None}])
        # A real presence change removes the option immediately.
        cache["events"] = {"sensor_map": {"valid": True, "local_node_id": 12, "sync_seen_mask": 1 << 12}}
        self.assertEqual(local_state(core, cache, 100)["neighbors"], [])

    def test_missing_master_flag_does_not_claim_slave_role(self):
        core = {"ok": True, "connection": {"connected": True}}
        cache = {"cache_written_at": 100, "sensors": {"remote": [
            {"node_id": 1, "online": True}, {"node_id": 10, "online": True, "master": False},
            {"node_id": 11, "online": True, "master": True}]}}
        neighbors = local_state(core, cache, 100)["neighbors"]
        self.assertEqual([n["label"] for n in neighbors], ["RS485 01", "S10", "M11"])

    def test_led_pattern_requires_a_valid_layout_or_fresh_optic_event(self):
        core = {"ok": True, "connection": {"connected": True}, "optic": {
            "led_pattern_actual": 36, "led_pattern_actual_source": "stat", "led_pattern_actual_age_s": .1}}
        cache = {"cache_written_at": 100, "stat": {"sync_layout_valid": False}}
        self.assertIsNone(local_state(core, cache, 100)["optic"]["led_pattern_actual"])
        core["optic"]["led_pattern_actual"] = 4
        self.assertIsNone(local_state(core, cache, 100)["optic"]["led_pattern_actual"])
        cache["events"] = {"optic_state": {"led_pattern": 0}}
        cache["event_updates"] = {"optic_state": {"updated_at": 99}}
        self.assertEqual(local_state(core, cache, 100)["optic"]["led_pattern_actual"], 0)
        self.assertIsNone(local_state(core, cache, 105)["optic"]["led_pattern_actual"])
        cache["stat"]["sync_layout_valid"] = True
        self.assertEqual(local_state(core, cache, 105)["optic"]["led_pattern_actual"], 4)

    def test_duplicate_command_executes_once_even_after_restart(self):
        self.group.receipts.put(self.record("BMI30-A"))
        command = {"command_id": "a" * 32, "target": "BMI30-A", 'age_s':0, "via": [],
                   "action": "rs485_id", "params": {"device_id": 10}}
        self.agent.opener.open.side_effect = lambda *a, **k: contextlib.closing(io.BytesIO(b'{"ok":true}'))
        self.assertTrue(self.group.command(command)["ok"])
        self.assertTrue(self.group.command(command)["ok"])
        other = Group(self.agent)
        self.addCleanup(other.socket.close)
        other.receipts.put(self.record('BMI30-A'))
        self.assertTrue(other.command(command)["ok"])
        self.assertEqual(self.agent.opener.open.call_count, 1)
        command["params"] = {"device_id": 11}
        with self.assertRaises(ValueError):
            self.group.command(command)

    def test_led_selection_remains_in_idle_and_ignores_invalid_stat(self):
        core = {"ok": True, "connection": {"connected": True}, "optic": {
            "led_pattern_actual": 64, "led_pattern_actual_source": "stat", "led_pattern_actual_age_s": .1,
            "led_pattern_commanded": 0, "non_addressable_led": {"enabled": True}}}
        cache = {"cache_written_at": 1000, "stat": {"sync_layout_valid": False},
                 "events": {"optic_state": {"led_pattern": 7}},
                 "event_updates": {"optic_state": {"updated_at": 100}}}
        optic = local_state(core, cache, 1000)["optic"]
        self.assertIsNone(optic["led_pattern_actual"])
        self.assertEqual(optic["led_pattern_selected"], 0)
        self.assertEqual(optic["led_pattern_selected_source"], "commanded")
        self.assertFalse(optic["led_output_active"])
        core["optic"]["led_pattern_commanded"] = 8
        self.assertTrue(local_state(core, cache, 1000)["optic"]["led_output_active"])
        core["optic"]["non_addressable_led"]["enabled"] = False
        optic = local_state(core, cache, 1000)["optic"]
        self.assertEqual(optic["led_pattern_selected"], 8)
        self.assertFalse(optic["led_output_active"])
        core["optic"]["led_pattern_commanded"] = 64
        self.assertEqual(local_state(core, cache, 1000)["optic"]["led_pattern_selected"], 7)
        cache["events"]["optic_state"]["led_pattern"] = 64
        self.assertIsNone(local_state(core, cache, 1000)["optic"]["led_pattern_selected"])

    def test_expired_command_is_never_queued(self):
        with self.assertRaises(ValueError):
            self.group.command({"command_id": "b" * 32, "target": "BMI30-A", 'age_s':9,
                                "action": "rs485_id", "params": {"device_id": 10}})
        self.agent.opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()

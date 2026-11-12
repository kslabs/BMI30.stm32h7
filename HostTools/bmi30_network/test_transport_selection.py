"""Selection persistence, actual routes, disabled ingress and real BT streams."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from transport_policy import Selection
from test_clockless import directory, record
from test_bluetooth_transport import BluetoothTests, wait_for


class SelectionTests(unittest.TestCase):
    agent = BluetoothTests.agent
    linked = BluetoothTests.linked

    def test_persistence_validation_and_stale_revision(self):
        with directory() as root:
            s=Selection(root)
            for mode,allowed in [('wifi',['wifi']),('bluetooth',['bluetooth']),('off',[]),('both',['wifi','bluetooth'])]:
                before=s.snapshot()
                self.assertTrue(s.set(mode,before['revision']))
                self.assertEqual(Selection(root).snapshot(),s.snapshot())
                self.assertEqual(s.snapshot()['allowed'],allowed)
                with self.assertRaises(ValueError):s.set('off',before['revision'])
            for value in ('bad',[],True,None):
                with self.assertRaises(ValueError):s.set(value,s.revision)

    def test_failed_save_retains_live_selection_and_corrupt_file_is_off(self):
        with directory() as root:
            s=Selection(root);before=s.snapshot()
            with patch('transport_policy.network.atomic',side_effect=OSError):
                with self.assertRaises(OSError):s.set('off',s.revision)
            self.assertEqual(before,s.snapshot())
            s.path.write_text('broken')
            self.assertEqual(Selection(root).snapshot()['mode'],'off')

    def test_disabled_ingress_cannot_dispatch_and_local_owner_remains_available(self):
        with directory() as root:
            a=self.agent(root,'BMI30-A')
            a.group.set_transports('off',a.transports.revision)
            for ingress in ('wifi','bluetooth'):
                with self.assertRaisesRegex(ValueError,'disabled'):a.group.command({},ingress=ingress)
            self.assertIn('BMI30-A',a.group.records)
            self.assertEqual(a.group.transport_status()['active'],[])
            self.assertEqual(a.group.merge({},'192.168.1.2'),[])
            self.assertEqual(a.group.merge({},'bt:AA:BB:CC:DD:EE:01'),[])

    def test_switch_clears_observations_preserves_replay_history_and_reports_actual_medium(self):
        with directory() as root:
            a=self.agent(root,'BMI30-A')
            b=record(a.protocol,'BMI30-B')
            def merge(address):
                token=a.group.challenges.issue('group',4)
                return a.group.merge({'self':b,'self_age_s':0,'request_id':token},address)
            merge('192.168.1.2')
            merge('bt:AA:BB:CC:DD:EE:02')
            self.assertEqual(a.group.transport_status()['active'],['wifi','bluetooth'])
            a.group.set_transports('bluetooth',a.transports.revision)
            self.assertNotIn('BMI30-B',a.group.records)
            self.assertIn('BMI30-B',a.group.receipts.history)
            self.assertEqual(merge('192.168.1.2'),[])
            self.assertEqual(merge('bt:AA:BB:CC:DD:EE:02'),[],'old sequence must stay rejected')
            b=record(a.protocol,'BMI30-B',seq=2)
            merge('bt:AA:BB:CC:DD:EE:02')
            self.assertEqual(a.group.transport_status()['active'],['bluetooth'])
            snapshot=json.loads(Path(a.group.agent.config['group_state']).read_text())
            self.assertEqual(snapshot['service_transports']['mode'],'bluetooth')

    def test_switch_off_closes_live_bluetooth_stream_and_blocks_reconnection(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            with self.linked(a,b) as (one,two):
                wait_for(lambda:'BMI30-B' in a.group.records)
                a.group.set_transports('off',a.transports.revision)
                wait_for(lambda:one.stopped.is_set() and two.stopped.is_set())
                self.assertFalse(a.bluetooth.enabled())
                self.assertFalse(a.bluetooth.activate(one))
                self.assertNotIn('BMI30-B',a.group.records)
                a.group.set_transports('both',a.transports.revision)
                self.assertTrue(a.bluetooth.enabled())

    def test_fresh_authenticated_wifi_hint_survives_restart_and_bad_frame_cannot_replace_it(self):
        with directory() as root:
            a=self.agent(root,'BMI30-A')
            r=record(a.protocol,'BMI30-B')['data'];r['bluetooth_address']='AA:BB:CC:DD:EE:02'
            token=a.group.challenges.issue('group',4)
            a.group.merge({'self':a.protocol.sign(r),'self_age_s':0,'request_id':token},'192.168.1.2')
            self.assertEqual(a.bluetooth.known_candidates(),{'AA:BB:CC:DD:EE:02':'BMI30-B'})
            import bluetooth_transport
            restored=bluetooth_transport.Manager(a);self.addCleanup(restored.executor.shutdown,wait=True)
            self.assertEqual(restored.known_candidates(),a.bluetooth.known_candidates())
            r['seq']+=1;r['bluetooth_address']='AA:BB:CC:DD:EE:03'
            with self.assertRaises(ValueError):
                a.group.merge({'self':a.protocol.sign(r),'self_age_s':0,'request_id':'bad'},'192.168.1.2')
            self.assertNotIn('AA:BB:CC:DD:EE:03',a.bluetooth.known_candidates())


if __name__=='__main__':unittest.main()

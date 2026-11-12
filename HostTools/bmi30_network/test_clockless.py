"""Offline peers with unrelated calendars, clock jumps, relays and replay."""
import contextlib
import base64
import copy
import io
import json
from pathlib import Path
import queue
import shutil
import threading
import time
import unittest
import uuid
import urllib.request
import urllib.error
from unittest.mock import Mock, patch

from freshness import Challenges, Receipts
from protocol import Protocol, Records, encode
from group import Group
import ast
import types
import patch_service_clock_portal
import service


@contextlib.contextmanager
def directory():
    path=Path(__file__).resolve().parent/('test-clockless-'+uuid.uuid4().hex)
    path.mkdir()
    try:yield path
    finally:
        for item in path.iterdir():
            assert item.resolve().is_relative_to(path.resolve())
            if item.is_dir():shutil.rmtree(item)
            else:item.unlink()
        path.rmdir()


def record(protocol,ident='BMI30-B',seq=1,boot='boot-a',issued=-2208988800):
    return protocol.sign({'id':ident,'boot':boot,'seq':seq,'issued':issued,'kind':'group-v1','available':True})


class ClocklessTests(unittest.TestCase):
    def setUp(self):
        self.protocol=Protocol('17'*32)

    def test_unrelated_calendars_do_not_affect_authentication_or_freshness(self):
        receipts=Records(self.protocol)
        for seq,calendar in enumerate((-2208988800,0,4102444800),1):
            with patch('time.time',return_value=calendar):
                signed=record(self.protocol,seq=seq,issued=4102444800-calendar)
                self.assertEqual(self.protocol.verify(signed)['seq'],seq)
                receipts.merge([signed])
                self.assertEqual(receipts.nodes()[0]['seq'],seq)

    def test_expiration_and_duplicate_history_survive_forward_and_backward_date_jumps(self):
        with patch('time.monotonic',return_value=1000) as timer:
            receipts=Receipts(4);signed=record(self.protocol)
            self.assertTrue(receipts.put(signed))
            for calendar in (-2208988800,4102444800,0):
                with patch('time.time',return_value=calendar):self.assertTrue(receipts.fresh('BMI30-B'))
            timer.return_value=1005;receipts.prune()
            self.assertFalse(receipts.put(signed),'expiration must not erase the replay watermark')
            self.assertFalse(receipts.fresh('BMI30-B'))
            self.assertTrue(receipts.put(record(self.protocol,seq=2)))

    def test_relay_age_accumulates_and_duplicate_cannot_renew_deadline(self):
        with patch('time.monotonic',return_value=1000) as timer:
            first,second=Receipts(4),Receipts(4);signed=record(self.protocol)
            first.put(signed,.2);timer.return_value=1001
            second.put(signed,first.age('BMI30-B')+.3)
            timer.return_value=1002
            self.assertFalse(second.put(signed,0))
            timer.return_value=1004
            self.assertFalse(first.fresh('BMI30-B'));self.assertFalse(second.fresh('BMI30-B'))

    def test_retired_boot_and_sequence_rollback_do_not_return(self):
        receipts=Receipts(4)
        receipts.put(record(self.protocol,seq=5))
        self.assertFalse(receipts.put(record(self.protocol,seq=4,issued=9999999999)))
        self.assertTrue(receipts.put(record(self.protocol,seq=1,boot='boot-b',issued=0)))
        self.assertFalse(receipts.put(record(self.protocol,seq=100,boot='boot-a')))

    def test_challenge_expires_without_calendar_and_cannot_be_replayed_after_restart(self):
        with patch('time.monotonic',return_value=1000) as timer:
            tickets=Challenges();token=tickets.issue('command',8,'BMI30-B')
            with patch('time.time',return_value=-2208988800):self.assertEqual(tickets.elapsed(token,'command','BMI30-B'),0)
            with self.assertRaises(ValueError):tickets.elapsed(token,'command','BMI30-B')
            token=tickets.issue('group',4);timer.return_value=1005
            with self.assertRaises(ValueError):tickets.elapsed(token,'group',consume=False)
            with self.assertRaises(ValueError):Challenges().elapsed(token,'group',consume=False)

    def test_first_arrival_requires_receiver_nonce_and_includes_round_trip_age(self):
        with directory() as root, patch('time.monotonic',return_value=1000) as timer:
            agent=Mock(config={'id':'BMI30-A','state_dir':str(root),'group_state':str(root/'group.json')},
                          protocol=self.protocol,lock=threading.RLock(),transports=None)
            group=Group(agent);self.addCleanup(group.socket.close)
            packet={'self':record(self.protocol),'self_age_s':0}
            with self.assertRaises(ValueError):group.merge(packet,'192.168.1.2')
            token=group.challenges.issue('group',4);timer.return_value=1003
            group.merge({**packet,'request_id':token},'192.168.1.2')
            self.assertGreaterEqual(group.receipts.age('BMI30-B'),3)
            timer.return_value=1004.1
            self.assertFalse(group.fresh(packet['self']['data']))
            token=group.challenges.issue('group',4)
            group.merge({**packet,'request_id':token},'192.168.1.2')
            self.assertFalse(group.fresh(packet['self']['data']),'a new request cannot renew the same old sequence')

    def test_group_snapshot_contains_relative_ages_with_unrelated_origin_date(self):
        with directory() as root:
            agent=Mock(config={'id':'BMI30-A','state_dir':str(root),'group_state':str(root/'group.json')},
                       protocol=self.protocol,lock=threading.RLock(),transports=None)
            group=Group(agent);self.addCleanup(group.socket.close)
            group.receipts.put(record(self.protocol,'BMI30-A',issued=4102444800))
            with patch('time.time',return_value=-2208988800):group.publish()
            snapshot=json.loads((root/'group.json').read_text())
            self.assertEqual(snapshot['clock_mode'],'monotonic-v2')
            self.assertLess(snapshot['nodes'][0]['age_s'],1)

    def test_network_credential_order_uses_revision_instead_of_dates(self):
        with directory() as root, patch('network.hardware_identity',return_value='BMI30-A'),patch('service.network.Network'):
            agent=service.Agent({'id':'BMI30-A','fleet_key':'17'*32,'state_dir':str(root)})
            self.addCleanup(agent.group.socket.close)
            base={'ssid':'test','psk':'test-password','security':'wpa-psk','origin':'BMI30-B'}
            agent.remember_secret('wifi:test',{**base,'revision':1,'updated':4102444800})
            agent.remember_secret('wifi:test',{**base,'revision':2,'updated':0})
            self.assertEqual(agent.secrets['wifi:test']['revision'],2)

    def test_stream_history_retains_both_edges_when_binding_a_new_receiver_nonce(self):
        with directory() as root,patch('time.monotonic',return_value=1000) as timer:
            agent=Mock(config={'id':'BMI30-A','state_dir':str(root),'group_state':str(root/'group.json')},
                       protocol=self.protocol,lock=threading.RLock(),transports=None)
            group=Group(agent);self.addCleanup(group.socket.close)
            for seq,value in ((1,False),(2,True),(3,False)):
                data=record(self.protocol,'BMI30-A',seq=seq)['data'];data['sensors']={'detadc1':value}
                group.receipts.put(self.protocol.sign(data));group.publish()
            timer.return_value=1001
            frames=[self.protocol.verify(group.packet('a'*32,snapshot=event['packet'],revision=event['revision']))
                    for event in group.hub.after(0,0)]
            # after(0) reconciles latest; a resumed live revision retains edges.
            frames=[self.protocol.verify(group.packet('b'*32,snapshot=event['packet'],revision=event['revision']))
                    for event in group.hub.after(1,0)]
            self.assertEqual([frame['self']['data']['sensors']['detadc1'] for frame in frames],[True,False])
            self.assertTrue(all(frame['self_age_s']>=1 for frame in frames))
            timer.return_value=1005
            self.assertIsNone(group.packet('c'*32,snapshot=group.hub.latest['packet']))

    def test_sensor_edges_do_not_make_an_old_relay_read_young_again(self):
        with directory() as root,patch('time.monotonic',return_value=1000) as timer:
            agent=Mock(config={'id':'BMI30-A','state_dir':str(root)},protocol=self.protocol,lock=threading.RLock())
            group=Group(agent);self.addCleanup(group.socket.close)
            group.seq=1
            data=record(self.protocol,'BMI30-A')['data']
            data.update(sensors={},relay={'sequence':7,'age_s':.5})
            group.receipts.put(self.protocol.sign(data));group.publish=Mock();group.broadcast=Mock()
            for seq,elapsed in ((1,1),(2,3)):
                timer.return_value=1000+elapsed
                self.assertTrue(group.owner_update({'boot':'a'*32,'seq':seq,'generation':seq,'at':0,
                    'node_id':12,'sensors':{'detadc1':bool(seq%2)}}))
            relay=group.records[group.ident]['data']['relay']
            self.assertEqual(relay['sequence'],7);self.assertEqual(relay['age_s'],3.5)


class PortalClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=Path(__file__).resolve().parents[2]/'FirmwareReleases/RaspberryPi/2026-10-23-rpi-identity/review/portal.py'
        cls.candidate=patch_service_clock_portal.patch(base.read_text(encoding='utf-8'))
        node=next(n for n in ast.parse(cls.candidate).body if isinstance(n,ast.FunctionDef) and n.name=='read_bmi30_group_file')
        cls.function=compile(ast.Module(body=[node],type_ignores=[]),'portal-read-group','exec')

    def read(self,state,name='group.json',elapsed=.5):
        path=Mock();path.stat.return_value.st_size=1024;path.read_text.return_value=json.dumps(state)
        root=Mock();root.__truediv__=Mock(return_value=path)
        scope={'pathlib':types.SimpleNamespace(Path=Mock(return_value=root)),
               'time':types.SimpleNamespace(monotonic=lambda:1000+elapsed,time=lambda:-2208988800),'json':json}
        exec(self.function,scope)
        return scope['read_bmi30_group_file'](name,2)

    def test_portal_uses_elapsed_age_and_never_compares_remote_date(self):
        state={'clock_mode':'monotonic-v2','checked_mono':1000,'checked_at':4102444800,
               'nodes':[{'id':'BMI30-A','issued':4102444800,'age_s':.25,'relay':{'age_s':1}}]}
        result=self.read(state)
        self.assertTrue(result['available']);self.assertEqual(result['nodes'][0]['age_s'],.75)
        self.assertEqual(result['nodes'][0]['relay']['age_s'],1.5)
        self.assertEqual(self.read(state,'status.json')['nodes'][0]['age_s'],.75)
        self.assertFalse(self.read(state,elapsed=3)['available'])

    def test_portal_rejects_legacy_calendar_only_runtime(self):
        self.assertFalse(self.read({'checked_at':-2208988800,'nodes':[]})['available'])


try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO=True
except ImportError:
    HAS_CRYPTO=False


@unittest.skipUnless(HAS_CRYPTO,'Target cryptography dependency required for encrypted HTTP integration')
class EncryptedOfflineTests(unittest.TestCase):
    def test_real_http_handshake_exchange_command_and_stream_with_different_dates(self):
        with directory() as root:
            agents=[]
            for ident,calendar in (('BMI30-A',-2208988800),('BMI30-B',4102444800)):
                state=root/ident;state.mkdir()
                with patch('network.hardware_identity',return_value=ident),patch('service.network.Network'):
                    agent=service.Agent({'id':ident,'fleet_key':'17'*32,'state_dir':str(state),
                                         'group_state':str(state/'group.json')})
                self.addCleanup(agent.group.socket.close)
                with patch('time.time',return_value=calendar):
                    agent.publish({'id':ident,'broadcasts':[],'internet':{'ok':False},'gateway':None,'path':[]})
                    agent.group.receipts.put(record(agent.protocol,ident,issued=calendar))
                agents.append(agent)
            client,server=agents
            # Only the acquisition API is simulated; encrypted peer HTTP is real.
            actions=[]
            def core(request,*args,**kwargs):
                actions.append(json.loads(request.data));return contextlib.closing(io.BytesIO(b'{"ok":true}'))
            server.opener.open=core
            ready=queue.Queue()
            thread=threading.Thread(target=service.serve,args=(server,'127.0.0.1',0,ready.put),daemon=True)
            thread.start();http=ready.get(timeout=3)
            try:
                with patch('service.PORT',http.server_port),patch('service.private_ip',side_effect=lambda address:address):
                    self.assertEqual(client.contact('127.0.0.1','BMI30-B')['id'],'BMI30-B')
                    self.assertEqual({n['id'] for n in client.records.nodes()},{'BMI30-A','BMI30-B'})
                    token=client.group.challenges.issue('group',4)
                    client.group.merge(client.protocol.verify(server.group.packet(token)),'127.0.0.1')
                    command={'command_id':'a'*32,'target':'BMI30-B','action':'rs485_id','params':{'device_id':12},'via':[],'age_s':0}
                    self.assertTrue(client.group.command(command)['ok'])
                    with patch('time.time',return_value=0):self.assertTrue(client.group.command(command)['ok'])
                    self.assertEqual(len(actions),1)
                    server.group.publish()
                    token=client.group.challenges.issue('group',4)
                    auth=client.protocol.sign({'id':client.config['id'],'boot':client.boot,'seq':client.seq,
                                              'purpose':'group-stream','request_id':token})
                    request=urllib.request.Request(f'http://127.0.0.1:{http.server_port}/peer-live',
                        headers={'X-BMI30-Stream':base64.b64encode(encode(auth)).decode()})
                    with client.opener.open(request,timeout=3) as response:
                        frame=client.protocol.verify(json.loads(response.readline()))
                    self.assertEqual(frame['request_id'],token)
                    self.assertEqual(frame['self']['data']['issued'],4102444800)
                    client.group.merge(frame,'127.0.0.1')
            finally:
                http.shutdown();http.server_close();thread.join(timeout=3)

    def test_encrypted_request_proof_cannot_be_reused_after_replay_cache_reset(self):
        with directory() as root,patch('network.hardware_identity',return_value='BMI30-A'),patch('service.network.Network'):
            agent=service.Agent({'id':'BMI30-A','fleet_key':'17'*32,'state_dir':str(root)})
            self.addCleanup(agent.group.socket.close)
            token=agent.challenges.issue('command',8,'BMI30-B')
            body={'_challenge':token,'_source_id':'BMI30-B','_prepared_elapsed_s':0}
            encrypted=agent.protocol.seal(body)
            with patch('time.time',return_value=4102444800):
                opened=agent.protocol.open(encrypted);self.assertLess(agent.accept_rpc(opened,'command'),1)
            agent.protocol.replays.clear()
            with patch('time.time',return_value=-2208988800):
                opened=agent.protocol.open(encrypted)
                with self.assertRaises(ValueError):agent.accept_rpc(opened,'command')


if __name__=='__main__':unittest.main()

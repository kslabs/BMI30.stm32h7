import base64
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import uuid
import threading
import time
import unittest
from unittest.mock import Mock, patch
from group import Group,local_state
from owner_events import Publisher
from protocol import Protocol
from realtime import Hub, accept_key, consume, frame, same_origin, semantic, serve_portal

@contextlib.contextmanager
def test_directory():
    directory=Path(__file__).resolve().parent/('test-work-'+uuid.uuid4().hex)
    directory.mkdir()
    try:yield str(directory)
    finally:
        for file in directory.iterdir():file.unlink()
        directory.rmdir()

class RealtimeTests(unittest.TestCase):
    def test_relay_read_survives_sensor_edges_without_renewing_its_age(self):
        now=time.time()
        relay={'available':True,'enabled':True,'saved_enabled':True,'pc1':True,'sequence':7,'age_s':.8}
        data=local_state({'ok':True,'connection':{'connected':True},'optic':{'relay':relay}},
                         {'cache_written_at':now},now)
        self.assertEqual(data['relay']['observed_at'],now-.8)
        self.assertEqual(data['relay']['sequence'],7)
        self.assertTrue(data['relay']['pc1']);self.assertTrue(data['relay']['saved_enabled'])
        with test_directory() as directory:
            agent=Mock(config={'id':'BMI30-A','state_dir':directory},protocol=Protocol('17'*32),lock=threading.RLock())
            group=Group(agent);self.addCleanup(group.socket.close)
            data.update(kind='group-v1',id=group.ident,issued=now,boot=group.boot,seq=1)
            group.receipts.put(agent.protocol.sign(data))
            group.publish=Mock();group.broadcast=Mock()
            event={'boot':'a'*32,'seq':1,'generation':10,'at':now,'node_id':12,'sensors':{'detadc1':True}}
            self.assertTrue(group.owner_update(event))
            self.assertEqual(group.records[group.ident]['data']['relay'],data['relay'])

    def test_fast_adc_precedes_optic_gate_led_and_relay_io(self):
        candidate=Path(os.getenv('BMI30_TEST_ENGINE',str(Path(__file__).resolve().parents[2]/'.tmp/group-realtime-20261005/engine-candidate.py')))
        tree=ast.parse(candidate.read_text(encoding='utf-8'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_rt_apply_detection_outputs')
        scope={'time':time};exec(compile(ast.Module(body=[method],type_ignores=[]),'candidate','exec'),scope)
        order=[];stream=Mock();stream.publish_det_adc.side_effect=lambda a,b:order.append(('fast',a,b))
        engine=Mock(stream=stream,_relay_service=None,_recovery_outputs_inhibited=False,_optic_gate_last_allowed=True,_sound_test_enabled=False)
        engine._det_output_active.side_effect=[True,False]
        engine._optic_detection_gate_allowed.side_effect=lambda:order.append(('optic-read',)) or False
        engine._group_led_apply_detection.side_effect=lambda *a,**kw:order.append(('led-io',))
        engine._send_det_adc_status.side_effect=lambda *a:order.append(('adc-io',))
        scope['_rt_apply_detection_outputs'](engine)
        self.assertEqual(order,[('fast',True,False),('optic-read',),('led-io',),('adc-io',)])

    def test_fast_falling_edge_precedes_output_shutdown(self):
        candidate=Path(os.getenv('BMI30_TEST_ENGINE',str(Path(__file__).resolve().parents[2]/'.tmp/group-realtime-20261005/engine-candidate.py')))
        tree=ast.parse(candidate.read_text(encoding='utf-8'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_rt_stop_detection_outputs')
        scope={};exec(compile(ast.Module(body=[method],type_ignores=[]),'candidate','exec'),scope)
        order=[];engine=Mock(_sound_test_enabled=False)
        engine.stream.publish_det_adc.side_effect=lambda a,b:order.append(('fast',a,b))
        engine._relay_service.observe_detection.side_effect=lambda a:order.append(('relay-io',))
        scope['_rt_stop_detection_outputs'](engine)
        self.assertEqual(order[:2],[('fast',False,False),('relay-io',)])

    def test_detector_publishes_before_a_failing_stm32_write(self):
        candidate=Path(os.getenv('BMI30_TEST_ENGINE',str(Path(__file__).resolve().parents[2]/'.tmp/group-realtime-20261005/engine-candidate.py')))
        tree=ast.parse(candidate.read_text(encoding='utf-8'))
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_send_det_adc_status')
        scope={};exec(compile(ast.Module(body=[method],type_ignores=[]),'candidate','exec'),scope)
        order=[];stream=Mock()
        stream.publish_det_adc.side_effect=lambda a,b:order.append(('fast',a,b))
        def blocked(**kw):order.append(('stm32',kw['bits']));raise OSError('delayed STM32')
        stream.set_det_adc.side_effect=blocked
        engine=Mock(stream=stream,_recovery_outputs_inhibited=False,_det_adc_status_bits=-1,_det_adc_status_stream_token=0)
        self.assertFalse(scope['_send_det_adc_status'](engine,True,False))
        self.assertEqual(order,[('fast',True,False),('stm32',1)])

    def test_fast_detector_does_not_wait_for_rs485_readback(self):
        now=time.time();core={'ok':True,'connection':{'connected':True}}
        cache={'cache_written_at':now,'sensors':{'local':{'detadc1':False,'detadc2':False}},
               'host_detection':{'at':now,'detadc1':True,'detadc2':False}}
        self.assertTrue(local_state(core,cache,now)['sensors']['detadc1'])
        cache['host_detection']['at']=now-5
        self.assertFalse(local_state(core,cache,now)['sensors']['detadc1'],'expired fast data returns to the confirmed STM32 value')

    def test_websocket_upgrade_origin_and_key(self):
        headers={'Upgrade':'websocket','Connection':'keep-alive,Upgrade','Sec-WebSocket-Version':'13',
                 'Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ=='}
        self.assertEqual(accept_key(headers),'s3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
        self.assertTrue(same_origin('https://192.168.0.150','192.168.0.150',True))
        for origin in ('http://192.168.0.150','https://evil.test','https://192.168.0.150.evil.test','null'):
            self.assertFalse(same_origin(origin,'192.168.0.150',True))
        for key in ('!invalid','c2hvcnQ='):
            with self.assertRaises(ValueError):accept_key({**headers,'Sec-WebSocket-Key':key})

    def test_masked_control_frames_can_arrive_in_parts(self):
        payload=b'ping';mask=b'\x01\x02\x03\x04'
        packet=b'\x89\x84'+mask+bytes(b^mask[i%4] for i,b in enumerate(payload))
        buffer=bytearray(packet[:4]);self.assertIsNone(consume(buffer))
        buffer.extend(packet[4:]);self.assertEqual(consume(buffer),(9,payload));self.assertFalse(buffer)
        for invalid in (b'\x81\x80'+mask,b'\x09\x80'+mask,b'\x89\x00',b'\x89\xfe'):
            with self.assertRaises(ValueError):consume(bytearray(invalid))
        self.assertEqual(frame(b'ping',10),b'\x8a\x04ping')
        self.assertEqual(frame(b'x'*126)[:4],b'\x81\x7e\x00\x7e')

    def test_websocket_authentication_precedes_upgrade(self):
        handler=Mock();handler._get_portal_session.return_value=None
        serve_portal(handler);handler.send_error.assert_called_once_with(403)
        handler.send_response.assert_not_called()

    def test_hub_keeps_short_rising_and_falling_changes(self):
        hub=Hub()
        def state(active):return {'checked_at':time.time(),'nodes':[{'id':'BMI30-A','sensors':{'detadc1':active}}]}
        hub.publish(state(False)); revision=hub.seq
        hub.publish(state(True));hub.publish(state(False))
        events=hub.after(revision,0)
        self.assertEqual([e['group']['nodes'][0]['sensors']['detadc1'] for e in events],[True,False])
        hub.publish(state(False));self.assertEqual(hub.seq,revision+2,'unchanged snapshots do not create a burst')
        for i in range(300):hub.publish(state(bool(i%2)))
        self.assertEqual(len(hub.events),256)
        self.assertEqual(len(hub.after(revision,0)),1,'overflow reconciles with the latest complete state')

    def test_owner_replay_and_retired_boot_are_rejected(self):
        with test_directory() as directory:
            agent=Mock(config={'id':'BMI30-A','state_dir':directory},protocol=Protocol('17'*32),lock=threading.RLock())
            group=Group(agent);self.addCleanup(group.socket.close);group._tick=Mock()
            event={'boot':'a'*32,'seq':1,'generation':10,'at':time.time(),'node_id':12,'sensors':{'detadc1':True},'hold_ds':30}
            self.assertTrue(group.owner_update(event));self.assertFalse(group.owner_update(event))
            self.assertFalse(group.owner_update({**event,'seq':2,'mono':time.monotonic()-5}))
            self.assertFalse(group.owner_update({**event,'seq':2,'sensors':{'detadc1':1}}))
            self.assertTrue(group.owner_update({**event,'boot':'b'*32,'seq':1}))
            self.assertFalse(group.owner_update({**event,'seq':3}))

    def test_file_snapshot_cannot_revert_a_new_owner_edge(self):
        with test_directory() as directory:
            cache=Path(directory)/'state.json';now=time.time()
            stale={'cache_written_at':now,'cache_generation':9,'owner_event_boot':'a'*32,
                   'sensors':{'local':{'detadc1':False}}}
            cache.write_text(json.dumps(stale))
            agent=Mock(config={'id':'BMI30-A','state_dir':directory,'device_state':str(cache)},
                       protocol=Protocol('17'*32),lock=threading.RLock())
            group=Group(agent);self.addCleanup(group.socket.close)
            group.owner_event={'boot':'a'*32,'seq':1,'generation':10,'at':now,'node_id':12,'sensors':{'detadc1':True},'hold_ds':30}
            group.owner_received=time.monotonic()
            agent.opener.open.side_effect=lambda *a,**k:contextlib.closing(io.BytesIO(b'{"ok":true,"connection":{"connected":true}}'))
            self.assertTrue(group.sample()['data']['sensors']['detadc1'])
            cache.write_text(json.dumps({**stale,'cache_generation':11}))
            self.assertFalse(group.sample()['data']['sensors']['detadc1'],'a genuinely newer snapshot is authoritative')
            group.owner_event['boot']='b'*32;group.owner_event['generation']=1
            self.assertTrue(group.sample()['data']['sensors']['detadc1'],'old generation from a retired owner must not suppress a new owner')

    def test_owner_publisher_never_waits_for_a_missing_subscriber(self):
        publisher=Publisher(path='nonexistent-bmi30-test-socket');self.addCleanup(publisher.close)
        cache={'sensors':{'local':{'node_id':12,'detadc1':False}}}
        began=time.monotonic()
        for i in range(1000):
            cache['sensors']['local']['detadc1']=bool(i%2);publisher.update(cache,i)
        self.assertLess(time.monotonic()-began,.5)
        self.assertLessEqual(len(publisher.pending),256)
        self.assertEqual(publisher.last['sensors']['detadc1'],True)

if __name__=='__main__':unittest.main()

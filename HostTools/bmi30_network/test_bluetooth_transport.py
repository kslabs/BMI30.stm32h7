"""Real encrypted streams over socketpair; RF/BlueZ hardware is not simulated as proof."""
import contextlib
import io
import json
import socket
import struct
import threading
import time
import unittest
import queue
from unittest.mock import Mock, patch

import bluetooth_transport as bt
from protocol import encode
from test_clockless import directory, record, HAS_CRYPTO
import service


def wait_for(condition, timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if condition():return
        time.sleep(.01)
    raise AssertionError('Condition not reached before local monotonic deadline')


@unittest.skipUnless(HAS_CRYPTO,'AES-GCM dependency required')
class BluetoothTests(unittest.TestCase):
    def agent(self,root,ident,key='17'*32):
        state=root/ident;state.mkdir()
        with patch('network.hardware_identity',return_value=ident),patch('service.network.Network'):
            agent=service.Agent({'id':ident,'fleet_key':key,'state_dir':str(state),'group_state':str(state/'group.json')})
        self.addCleanup(agent.bluetooth.executor.shutdown,wait=True)
        self.addCleanup(agent.group.socket.close)
        agent.group.seq=1
        data=record(agent.protocol,ident,issued=4102444800 if ident=='BMI30-A' else -2208988800)['data']
        data['sensors']={'detadc1':False}
        agent.group.receipts.put(agent.protocol.sign(data));agent.group.publish()
        return agent

    @contextlib.contextmanager
    def linked(self,a,b):
        left,right=socket.socketpair()
        one=bt.Session(a.bluetooth,left,'AA:BB:CC:DD:EE:02',outgoing=True)
        two=bt.Session(b.bluetooth,right,'AA:BB:CC:DD:EE:01')
        workers=[threading.Thread(target=s.run,daemon=True) for s in (one,two)]
        for worker in workers:worker.start()
        try:
            wait_for(lambda:bool(a.bluetooth.links and b.bluetooth.links))
            yield one,two
        finally:
            one.close();two.close()
            for worker in workers:worker.join(timeout=3)

    def test_bluetooth_only_offline_telemetry_commands_and_shared_replay_history(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            actions=[]
            def core(request,*args,**kwargs):
                actions.append(json.loads(request.data));return contextlib.closing(io.BytesIO(b'{"ok":true}'))
            b.opener.open=core
            with self.linked(a,b) as (one,two):
                wait_for(lambda:'BMI30-B' in a.group.records and 'BMI30-A' in b.group.records)
                self.assertEqual(a.group.routes['BMI30-B']['transport'],'bluetooth')
                command={'command_id':'b'*32,'target':'BMI30-B','action':'rs485_id','params':{'device_id':12},'via':[],'age_s':0}
                self.assertTrue(a.group.command(command)['ok'])
                # Same command arrives again via the Wi-Fi handler's common
                # dispatch function. It must not touch the physical API twice.
                with patch('time.time',return_value=0):self.assertTrue(b.group.command(command)['ok'])
                self.assertEqual(len(actions),1)
                state=json.loads((root/'BMI30-A/group.json').read_text())
                peer=next(n for n in state['nodes'] if n['id']=='BMI30-B')
                self.assertEqual(peer['transport'],'bluetooth');self.assertLess(peer['age_s'],4)
                self.assertEqual(a.bluetooth.snapshot()['nominal_channels'],79)

    def test_wifi_timeout_falls_back_to_bluetooth_with_same_command_id(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            b.opener.open=lambda *args,**kwargs:contextlib.closing(io.BytesIO(b'{"ok":true}'))
            with self.linked(a,b):
                wait_for(lambda:'BMI30-B' in a.group.records)
                token=a.group.challenges.issue('group',4)
                a.group.merge(a.protocol.verify(b.group.packet(token)),'192.168.1.2')
                self.assertEqual(a.group.control_routes('BMI30-B')[0]['transport'],'wifi')
                a.rpc=Mock(side_effect=TimeoutError('Wi-Fi unavailable'))
                command={'command_id':'c'*32,'target':'BMI30-B','action':'rs485_id','params':{'device_id':12},'via':[],'age_s':0}
                self.assertTrue(a.group.command(command)['ok'])
                self.assertEqual(a.rpc.call_args.args[2]['command_id'],command['command_id'])
                self.assertEqual(a.group.control_routes('BMI30-B')[0]['transport'],'bluetooth')

    def test_lost_wifi_ack_after_actuation_does_not_repeat_action_over_bluetooth(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            b.opener.open=Mock(side_effect=lambda *args,**kwargs:contextlib.closing(io.BytesIO(b'{"ok":true}')))
            with self.linked(a,b):
                wait_for(lambda:'BMI30-B' in a.group.records)
                token=a.group.challenges.issue('group',4)
                a.group.merge(a.protocol.verify(b.group.packet(token)),'192.168.1.2')
                def lost_ack(address,purpose,payload,**kwargs):
                    self.assertTrue(b.group.command(payload)['ok']);raise TimeoutError('Acknowledgement lost')
                a.rpc=lost_ack
                command={'command_id':'d'*32,'target':'BMI30-B','action':'rs485_id','params':{'device_id':12},'via':[],'age_s':0}
                self.assertTrue(a.group.command(command)['ok']);self.assertEqual(b.opener.open.call_count,1)

    def test_short_sensor_edges_cross_the_bluetooth_stream_in_order(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            with self.linked(a,b):
                wait_for(lambda:'BMI30-B' in a.group.records)
                with a.group.lock:revision=a.group.hub.seq
                for seq,value in ((2,True),(3,False)):
                    data=record(b.protocol,'BMI30-B',seq=seq)['data'];data['sensors']={'detadc1':value}
                    with b.group.lock:
                        b.group.seq=seq;b.group.receipts.put(b.protocol.sign(data));b.group.publish()
                wait_for(lambda:a.group.records['BMI30-B']['data']['seq']==3)
                values=[]
                # Records are stored before their public snapshot is written;
                # wait for the receiver's complete merge/publication lock.
                with a.group.lock:events=a.group.hub.after(revision,0)
                for event in events:
                    remote=next(n for n in event['group']['nodes'] if n['id']=='BMI30-B')
                    if remote['seq']>1:values.append(remote['sensors']['detadc1'])
                self.assertEqual(values,[True,False])

    def test_session_binding_rejects_reordered_frames_and_wrong_fleet_key(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            left,right=socket.socketpair();self.addCleanup(left.close);self.addCleanup(right.close)
            session=bt.Session(a.bluetooth,left,'AA:BB:CC:DD:EE:02')
            session.peer='BMI30-B';session.session_id='session'
            forged={'session':'session','from':'BMI30-B','to':'BMI30-A','seq':2,'body':{'kind':'probe'}}
            bt.write_frame(right,b.protocol.seal(forged))
            with self.assertRaisesRegex(ValueError,'replayed'):session.receive()
            forged['seq']=1
            bt.write_frame(right,b.protocol.seal(forged));self.assertEqual(session.receive()['kind'],'probe')
            bt.write_frame(right,b.protocol.seal(forged))
            with self.assertRaisesRegex(ValueError,'replayed'):session.receive()
            b.protocol.key=bytes.fromhex('18'*32)
            with self.assertRaises(Exception):a.protocol.open(b.protocol.seal(forged))

    def test_live_frames_do_not_exhaust_the_separate_http_replay_window(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            left,right=socket.socketpair();self.addCleanup(left.close);self.addCleanup(right.close)
            session=bt.Session(a.bluetooth,left,'AA:BB:CC:DD:EE:02')
            session.peer='BMI30-B';session.session_id='current-session'
            for seq in range(1,5001):
                body={'session':session.session_id,'from':'BMI30-B','to':'BMI30-A','seq':seq,'body':{'kind':'probe'}}
                bt.write_frame(right,b.protocol.seal(body));session.receive()
            self.assertEqual(session.received,5000);self.assertEqual(a.protocol.replays,{})

    def test_four_device_route_combines_bluetooth_hops_and_real_encrypted_wifi_http(self):
        with directory() as root:
            a,b,c,d=[self.agent(root,'BMI30-'+letter) for letter in 'ABCD']
            d.opener.open=Mock(side_effect=lambda *args,**kwargs:contextlib.closing(io.BytesIO(b'{"ok":true}')))
            ready=queue.Queue()
            thread=threading.Thread(target=service.serve,args=(d,'127.0.0.1',0,ready.put),daemon=True)
            thread.start();http=ready.get(timeout=3)
            try:
                with patch('service.PORT',http.server_port),patch('service.private_ip',side_effect=lambda address:address):
                    token=c.group.challenges.issue('group',4)
                    c.group.merge(c.protocol.verify(d.group.packet(token)),'127.0.0.1')
                    with self.linked(b,c),self.linked(a,b):
                        wait_for(lambda:'BMI30-D' in a.group.records)
                        self.assertEqual(a.group.routes['BMI30-D']['path'],['BMI30-B','BMI30-C','BMI30-D'])
                        command={'command_id':'e'*32,'target':'BMI30-D','action':'rs485_id',
                                 'params':{'device_id':13},'via':[],'age_s':0}
                        self.assertTrue(a.group.command(command)['ok'])
                        self.assertEqual(d.opener.open.call_count,1)
            finally:
                http.shutdown();http.server_close();thread.join(timeout=3)

    def test_expired_telemetry_lease_does_not_close_a_live_bluetooth_session(self):
        with directory() as root:
            a,b=self.agent(root,'BMI30-A'),self.agent(root,'BMI30-B')
            with self.linked(a,b) as (one,two):
                wait_for(lambda:'BMI30-B' in a.group.records)
                old=a.group.records['BMI30-B']['data']['seq']
                # A frame delayed beyond an old probe must be discarded. It
                # must not tear down the live authenticated session.
                packet=b.group.packet('0'*32)
                two.enqueue({'kind':'group','packet':packet})
                time.sleep(.05)
                self.assertFalse(one.stopped.is_set());self.assertFalse(two.stopped.is_set())
                self.assertEqual(a.group.records['BMI30-B']['data']['seq'],old)
                data=record(b.protocol,'BMI30-B',seq=2)['data'];data['sensors']={'detadc1':True}
                with b.group.lock:
                    b.group.seq=2;b.group.receipts.put(b.protocol.sign(data));b.group.publish()
                wait_for(lambda:a.group.records['BMI30-B']['data']['seq']==2)
                self.assertFalse(one.stopped.is_set())

    def test_length_limit_and_closed_peer_are_rejected(self):
        left,right=socket.socketpair();self.addCleanup(left.close);self.addCleanup(right.close)
        right.sendall(struct.pack('!I',bt.MAX_PACKET+1))
        with self.assertRaises(ValueError):bt.read_frame(left)
        right.close()
        with self.assertRaises(ConnectionError):bt.read_frame(left)


if __name__=='__main__':unittest.main()

import base64
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bmi30_network'))
import scope_live

def binary(stamp=1):
    header=json.dumps({'available':True,'last_frame_t':stamp}).encode()
    return b'BMF1'+struct.pack('<I',len(header))+header

class StreamTests(unittest.TestCase):
    def test_status_keeps_display_state_and_omits_unrelated_telemetry(self):
        source={'ok':True,'mode':{'avg_n':24},'detector':{'fire_seq0':7,'channels':{'upper':{'trigger':3}}},
                'normalization':{'enabled':True},'rs485_ident':{'large':'unrelated'}}
        result=json.loads(scope_live.status_bytes(json.dumps(source).encode()))
        self.assertEqual(result['mode'],source['mode']);self.assertEqual(result['detector'],source['detector'])
        self.assertEqual(result['normalization'],source['normalization']);self.assertNotIn('rs485_ident',result)
    def handler(self):
        h=Mock();h.path='/api/scope/live?channels=both&max_points=600'
        h._get_portal_session.return_value={'user':'test'};h._is_tls.return_value=False
        h._request_forwarded_scheme.return_value='http';h._request_forwarded_host.return_value=''
        h.headers={'Origin':'http://device','Host':'device','Upgrade':'websocket','Connection':'Upgrade',
                   'Sec-WebSocket-Version':'13','Sec-WebSocket-Key':base64.b64encode(b'0123456789abcdef').decode()}
        h.connection.pending.return_value=0
        return h
    def test_rejects_missing_session_before_upgrade(self):
        h=self.handler();h._get_portal_session.return_value=None
        scope_live.serve_scope(h);h.send_error.assert_called_once_with(403);h.send_response.assert_not_called()
    def test_rejects_cross_origin_and_missing_origin(self):
        for origin in ('http://other',''):
            h=self.handler();h.headers['Origin']=origin;scope_live.serve_scope(h)
            h.send_error.assert_called_once_with(403);h.send_response.assert_not_called()
    def test_rejects_bad_upgrade_or_channel(self):
        for field,value in [('Sec-WebSocket-Key','bad'),('Sec-WebSocket-Version','12'),('Upgrade','none')]:
            h=self.handler();h.headers[field]=value;scope_live.serve_scope(h);h.send_error.assert_called_once_with(400)
        h=self.handler();h.path='/api/scope/live?channels=invalid';scope_live.serve_scope(h);h.send_error.assert_called_once_with(400)
    def test_bounded_client_slots(self):
        for i in range(4):self.assertTrue(scope_live.SCOPE_SLOTS.acquire(False))
        try:
            h=self.handler();scope_live.serve_scope(h);h.send_error.assert_called_once_with(503)
        finally:
            for i in range(4):scope_live.SCOPE_SLOTS.release()
    def test_invalid_upstream_headers(self):
        for raw in (b'',b'badframe',b'BMF1'+struct.pack('<I',1000)+b'{}'):
            with self.assertRaises(ValueError):scope_live.frame_header(raw)
        self.assertEqual(scope_live.frame_header(binary())['last_frame_t'],1)
    def test_binary_and_status_stream_and_clean_close(self):
        h=self.handler();sent=[];h.connection.sendall.side_effect=sent.append
        # A valid masked empty close control frame.
        h.connection.recv.return_value=b'\x88\x80\x01\x02\x03\x04'
        def ready(*args):return ([h.connection],[],[])
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.side_effect=[b'{"ok":true}',binary()]
        opener=Mock();opener.open.return_value=response
        with patch.object(scope_live,'build_opener',return_value=opener),patch.object(scope_live.select,'select',side_effect=ready):
            scope_live.serve_scope(h)
        h.send_response.assert_called_once_with(101)
        self.assertEqual(sent[0][0],0x81);self.assertEqual(sent[1][0],0x82);self.assertEqual(sent[-1][0],0x88)
        self.assertIn(binary(),sent[1]);self.assertTrue(h.close_connection)
    def test_slow_browser_releases_slot(self):
        h=self.handler();h.connection.sendall.side_effect=TimeoutError('slow viewer')
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"ok":true}'
        opener=Mock();opener.open.return_value=response
        with patch.object(scope_live,'build_opener',return_value=opener):scope_live.serve_scope(h)
        for i in range(4):self.assertTrue(scope_live.SCOPE_SLOTS.acquire(False))
        for i in range(4):scope_live.SCOPE_SLOTS.release()

if __name__=='__main__':unittest.main()

import asyncio
import io
import json
import time
import unittest
import zlib
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.rtcsctptransport import DataChunk, WEBRTC_BINARY

import scope_rtc
import scope_rtc_proxy
from test_scope_live import binary


class ProxyTests(unittest.TestCase):
    def handler(self, body=None):
        h = Mock()
        data = json.dumps(body or {'lease':'test'}).encode()
        h.headers = {'Origin':'http://device', 'Host':'device', 'Content-Length':str(len(data))}
        h.rfile = io.BytesIO(data)
        h._get_portal_session.return_value = {'u':'verified', 'r':'user'}
        h._is_tls.return_value = False
        h._request_forwarded_scheme.return_value = 'http'
        h._request_forwarded_host.return_value = ''
        return h

    def test_unauthorized_or_cross_origin_never_reaches_sidecar(self):
        for missing in (True, False):
            h = self.handler()
            if missing: h._get_portal_session.return_value = None
            else: h.headers['Origin'] = 'http://other'
            with patch.object(scope_rtc_proxy, 'build_opener') as opener:
                scope_rtc_proxy.signaling(h, 'offer')
                opener.assert_not_called()
            self.assertEqual(h._send_json_response.call_args.args[1], 403)

    def test_size_limit(self):
        h = self.handler(); h.headers['Content-Length'] = '65537'
        scope_rtc_proxy.signaling(h, 'offer')
        self.assertEqual(h._send_json_response.call_args.args[1], 413)

    def test_owner_is_verified_and_target_is_fixed_loopback(self):
        h = self.handler({'owner':'forged','lease':'test'})
        response = Mock(); response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"ok":true}'
        opener = Mock();opener.open.return_value=response
        with patch.object(scope_rtc_proxy,'build_opener',return_value=opener):
            scope_rtc_proxy.signaling(h,'lease')
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url,'http://127.0.0.1:8766/lease')
        self.assertEqual(json.loads(req.data)['owner'],'verified:user')


class RtcTests(unittest.IsolatedAsyncioTestCase):
    async def test_address_discovery_works_inside_existing_socket_sandbox(self):
        addresses=scope_rtc.local_addresses(True,False)
        self.assertTrue(addresses)
        self.assertTrue(all(not address.startswith('127.') for address in addresses))

    async def asyncSetUp(self):
        self.counter = 0
        async def snapshot(request):
            self.counter += 1
            return web.Response(body=binary(time.time()),content_type='application/octet-stream')
        async def status(request):
            return web.json_response({'ok':True,'mode':{'selected':6},'rs485_ident':{'unused':True}})
        api = web.Application();api.router.add_get('/api/frame.bin',snapshot);api.router.add_get('/api/status',status)
        self.api = TestServer(api);await self.api.start_server()
        self.app = scope_rtc.create_app(str(self.api.make_url('/')).rstrip('/'))
        self.client = TestClient(TestServer(self.app));await self.client.start_server()
        self.pc = None

    async def asyncTearDown(self):
        if self.pc: await self.pc.close()
        await self.client.close();await self.api.close()

    async def connect(self, compressed=False):
        self.pc=RTCPeerConnection(RTCConfiguration(iceServers=[]))
        frames=self.pc.createDataChannel('scope-frames',ordered=False,maxRetransmits=0)
        status=self.pc.createDataChannel('scope-status',ordered=False,maxRetransmits=0)
        seen=asyncio.Event();received=[]
        @frames.on('message')
        def got(data):
            received.append(data);seen.set()
        await self.pc.setLocalDescription(await self.pc.createOffer())
        response=await self.client.post('/offer',json={'type':'offer','sdp':self.pc.localDescription.sdp,
            'channels':'both','max_points':600,'owner':'verified:user','compression':compressed})
        self.assertEqual(response.status,200)
        answer=await response.json()
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type=answer['type']))
        await asyncio.wait_for(seen.wait(),8)
        return answer,frames,status,received

    async def test_real_udp_negotiation_delivers_binary_with_no_retries(self):
        answer,frames,status,received=await self.connect()
        self.assertFalse(frames.ordered);self.assertEqual(frames.maxRetransmits,0)
        self.assertEqual(received[0][:4],b'BMF1')
        self.assertEqual(self.app['peers'][0]['frame'].maxRetransmits,0)
        self.assertEqual(self.app['peers'][0]['frame'].ordered,False)
        self.assertIn(' UDP ',answer['sdp'].upper())

    async def test_no_viewer_means_no_backend_polling(self):
        await asyncio.sleep(.12);self.assertEqual(self.counter,0)

    async def test_lossless_compressed_binary(self):
        answer,frames,status,received=await self.connect(compressed=True)
        data=received[0]
        if data[:4]==b'BMC1':data=zlib.decompress(data[4:])
        self.assertEqual(data[:4],b'BMF1')
        self.assertTrue(scope_rtc.frame_header(data)['available'])

    async def test_bad_offer_and_unknown_lease(self):
        r=await self.client.post('/offer',json={'type':'offer','sdp':'bad','owner':'test'})
        self.assertEqual(r.status,400);self.assertEqual(self.app['peers'],[])
        r=await self.client.post('/lease',json={'lease':'bad','owner':'test'})
        self.assertEqual(r.status,404)

    async def test_session_lease_owner_and_expiry_release_resources(self):
        answer,*_=await self.connect()
        r=await self.client.post('/lease',json={'lease':answer['lease'],'owner':'other'})
        self.assertEqual(r.status,404)
        r=await self.client.post('/lease',json={'lease':answer['lease'],'owner':'verified:user'})
        self.assertEqual(r.status,200)
        self.app['peers'][0]['until']=time.monotonic()-1
        for _ in range(50):
            if not self.app['peers']:break
            await asyncio.sleep(.02)
        self.assertEqual(self.app['peers'],[])

    async def test_saturated_sender_drops_instead_of_building_backlog(self):
        answer,*_=await self.connect()
        peer=self.app['peers'][0]
        fake=Mock();fake.readyState='open';fake.bufferedAmount=1000000
        peer['frame']=fake
        await asyncio.sleep(.14)
        fake.send.assert_not_called();self.assertGreater(peer['dropped'],0)

    async def test_viewer_limit(self):
        self.app['peers'][:]=[{'pc':Mock(close=Mock()),'until':time.monotonic()+10}] * 4
        r=await self.client.post('/offer',json={});self.assertEqual(r.status,503)
        self.app['peers'].clear()

    async def test_packet_loss_does_not_leave_display_stuck_for_seconds(self):
        answer,frames,status,received=await self.connect(compressed=True)
        peer=self.app['peers'][0]
        server=peer['pc'].sctp
        sender=peer['frame'];peer['frame']=None
        await asyncio.sleep(.05)
        # Reproduce the one-packet congestion window after a loss. Larger
        # messages and an idle second channel ensure fast-SACK traffic cannot
        # hide a stuck tail packet.
        server._cwnd=1200
        send=server._send_chunk;count=0;seen=[]
        async def lossy(chunk):
            nonlocal count
            if isinstance(chunk,DataChunk) and chunk.protocol==WEBRTC_BINARY:
                count+=1
                if count%17 in (7,8):return
            await send(chunk)
        server._send_chunk=lossy
        frames.on('message',lambda _:seen.append(time.monotonic()))
        end=time.monotonic()+3
        while time.monotonic()<end:
            if sender.bufferedAmount<=2400:sender.send(b'x'*1194)
            await asyncio.sleep(.04)
        gaps=[b-a for a,b in zip(seen,seen[1:])]
        self.loss_stats={'sent':count,'received':len(seen),'max_gap_ms':max(gaps,default=0)*1000,'rto_ms':server._rto*1000}
        self.assertGreater(count,14);self.assertGreater(len(seen),12)
        self.assertLess(max(gaps),.8,'a lost volatile packet must not stall later frames for seconds')


if __name__=='__main__':unittest.main()

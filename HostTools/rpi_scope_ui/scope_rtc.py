"""Loopback signaling and bounded UDP display transport; reads snapshots only."""
import asyncio
import fcntl
import json
from pathlib import Path
import secrets
import socket
import struct
import time
import zlib

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc import rtcsctptransport
from aioice import ice

from scope_live import frame_header, status_bytes
from scope_codec import packets

CORE_ORIGIN = 'http://127.0.0.1:8765'
LEASE_SECONDS = 30
MAX_PEERS = 4

# Volatile display messages have no retransmissions. The library's 1-3 second
# wait leaves the sender stuck on a lost packet; retire it sooner. These tuning
# parameters affect only this dedicated display process, never other services.
rtcsctptransport.SCTP_RTO_MIN = .12
rtcsctptransport.SCTP_RTO_MAX = .35


def local_addresses(use_ipv4, use_ipv6):
    # Read addresses within the service's existing socket-family sandbox.
    # SIOCGIFADDR is a read-only ioctl; no Netlink socket or network changes.
    addresses = []
    if use_ipv4:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reader:
            for interface in Path('/sys/class/net').iterdir():
                try:
                    raw = fcntl.ioctl(reader.fileno(), 0x8915,
                                      struct.pack('256s', interface.name.encode()[:15]))
                    address = socket.inet_ntoa(raw[20:24])
                    if not address.startswith('127.') and address != '0.0.0.0':
                        addresses.append(address)
                except OSError:
                    pass
    if use_ipv6:
        try:
            for line in Path('/proc/net/if_inet6').read_text().splitlines():
                raw, _, _, scope, _, _ = line.split()
                if scope == '00':
                    address = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(raw))
                    if address != '::1':
                        addresses.append(address)
        except OSError:
            pass
    return list(dict.fromkeys(addresses))


# aioice is pinned in the bundle; replace only its local-address discovery.
ice.get_host_addresses = local_addresses


async def retire(app, peer):
    if peer not in app['peers']:
        return
    app['peers'].remove(peer)
    await peer['pc'].close()


async def offer(request):
    app = request.app
    if len(app['peers']) >= MAX_PEERS:
        raise web.HTTPServiceUnavailable(text='Display viewer limit reached')
    try:
        body = await request.json()
        channels = body.get('channels', 'both')
        points = max(16, min(600, int(body.get('max_points', 600))))
        if body.get('type') != 'offer' or channels not in ('both', 'upper', 'lower'):
            raise ValueError('Invalid display offer')
        sdp = body['sdp']
        if not isinstance(sdp, str) or len(sdp) > 60000 or 'm=application ' not in sdp or 'm=audio ' in sdp or 'm=video ' in sdp:
            raise ValueError('Only display data channels are supported')
        owner = str(body['owner'])
        if not owner or len(owner) > 256:
            raise ValueError('Invalid viewer identity')
    except (ValueError, KeyError, TypeError):
        raise web.HTTPBadRequest(text='Invalid display offer')
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer = {'pc': pc, 'channels': channels, 'points': points, 'frame': None,
            'status': None, 'since': 0.0, 'lease': secrets.token_urlsafe(24),
            'until': time.monotonic() + LEASE_SECONDS, 'owner': owner,
            'sent': 0, 'dropped': 0, 'compression': body.get('compression') is True}
    app['peers'].append(peer)

    @pc.on('datachannel')
    def channel_opened(channel):
        if channel.label == 'scope-frames' and not channel.ordered and channel.maxRetransmits == 0:
            peer['frame'] = channel
        elif channel.label == 'scope-status':
            peer['status'] = channel
        else:
            channel.close()

    @pc.on('connectionstatechange')
    async def connection_changed():
        if pc.connectionState in ('failed', 'closed'):
            await retire(app, peer)

    try:
        await asyncio.wait_for(pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='offer')), 5)
        await asyncio.wait_for(pc.setLocalDescription(await pc.createAnswer()), 5)
        return web.json_response({'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type,
                                  'lease': peer['lease']})
    except (ValueError, TypeError, asyncio.TimeoutError):
        await retire(app, peer)
        raise web.HTTPBadRequest(text='Invalid display negotiation')
    except Exception:
        await retire(app, peer)
        raise


async def lease(request):
    try:
        body = await request.json()
        token, owner = str(body['lease']), str(body['owner'])
    except (ValueError, TypeError, KeyError):
        raise web.HTTPBadRequest()
    for peer in request.app['peers']:
        if secrets.compare_digest(token, peer['lease']) and owner == peer['owner']:
            peer['until'] = time.monotonic() + LEASE_SECONDS
            return web.json_response({'ok': True})
    raise web.HTTPNotFound()


async def health(request):
    peers = request.app['peers']
    return web.json_response({'ok': True, 'viewers': len(peers),
        'sent': sum(p['sent'] for p in peers), 'dropped': sum(p['dropped'] for p in peers)})


async def snapshots(app):
    # One polling loop shared by all viewers, with no frame backlog per viewer.
    async with ClientSession(timeout=ClientTimeout(total=.8), trust_env=False) as client:
        async def get(path):
            async with client.get(app['upstream'] + path) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    data.extend(chunk)
                    if len(data) > 262144:
                        raise ValueError('Display snapshot exceeds limit')
                return bytes(data)

        next_status = 0.0
        while True:
            started = time.monotonic()
            for peer in app['peers'][:]:
                if started >= peer['until']:
                    await retire(app, peer)
            viewers = [p for p in app['peers'] if p['frame'] and p['frame'].readyState == 'open']
            if viewers:
                groups = {(p['channels'], p['points']) for p in viewers}
                for channels, points in groups:
                    group = [p for p in viewers if (p['channels'], p['points']) == (channels, points)]
                    since = min(p['since'] for p in group)
                    try:
                        raw = await get('/api/frame.bin?channels=' + channels + '&max_points=' + str(points) + '&since=' + str(since))
                        header = frame_header(raw)
                        if not header.get('available') or header.get('busy'):
                            continue
                        stamp = float(header.get('last_frame_t') or 0)
                        compact = packets(raw) if any(p['compression'] for p in group) else []
                        for peer in group:
                            if stamp <= peer['since']:
                                continue
                            peer['since'] = stamp
                            channel = peer['frame']
                            payloads = compact if peer['compression'] else [raw]
                            if channel.readyState == 'open' and channel.bufferedAmount <= sum(map(len, payloads)) * 2:
                                for payload in payloads:
                                    channel.send(payload)
                                peer['sent'] += 1
                            else:
                                peer['dropped'] += 1
                    except (ClientError, OSError, ValueError, asyncio.TimeoutError):
                        pass
            await asyncio.sleep(max(.005, .04 - (time.monotonic() - started)))


async def lifecycle(app):
    task = asyncio.create_task(snapshots(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.gather(*(retire(app, p) for p in app['peers'][:]))


def create_app(upstream=CORE_ORIGIN):
    app = web.Application(client_max_size=65536)
    app['peers'], app['upstream'] = [], upstream
    app.router.add_post('/offer', offer)
    app.router.add_post('/lease', lease)
    app.router.add_get('/health', health)
    app.cleanup_ctx.append(lifecycle)
    return app


if __name__ == '__main__':
    web.run_app(create_app(), host='127.0.0.1', port=8766, access_log=None)

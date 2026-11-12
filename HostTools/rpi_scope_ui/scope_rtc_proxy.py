"""Portal authentication boundary for loopback-only WebRTC signaling."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from realtime import same_origin


def signaling(handler, action):
    session = handler._get_portal_session()
    secure = handler._is_tls() or handler._request_forwarded_scheme() == 'https'
    host = handler._request_forwarded_host() or handler.headers.get('Host', '')
    if session is None or not same_origin(handler.headers.get('Origin', ''), host, secure):
        handler._send_json_response({'ok': False, 'error': 'Not authorized'}, 403)
        return
    try:
        size = int(handler.headers.get('Content-Length', '0'))
        if size <= 0 or size > 65536:
            handler.close_connection = True
            handler._send_json_response({'ok': False, 'error': 'Invalid display request size'}, 413)
            return
        body = json.loads(handler.rfile.read(size))
        if not isinstance(body, dict) or action not in ('offer', 'lease'):
            raise ValueError('Invalid display request')
        # Identity comes from the verified portal session, never from client JSON.
        body['owner'] = str(session['u']) + ':' + str(session.get('r', 'user'))
        data = json.dumps(body, separators=(',', ':')).encode()
    except (ValueError, TypeError, KeyError):
        handler._send_json_response({'ok': False, 'error': 'Invalid display request'}, 400)
        return
    try:
        request = Request('http://127.0.0.1:8766/' + action, data=data,
                          headers={'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=12) as response:
            result = json.loads(response.read(65537))
        handler._send_json_response(result, 200)
    except HTTPError as error:
        handler._send_json_response({'ok': False, 'error': 'Display negotiation rejected'}, error.code)
    except (OSError, URLError, ValueError):
        handler._send_json_response({'ok': False, 'error': 'Display transport unavailable'}, 503)

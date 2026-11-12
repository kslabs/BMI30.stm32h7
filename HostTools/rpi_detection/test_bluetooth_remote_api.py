"""Validate authorization and wire contracts without a Bluetooth device."""
import ast
import io
import json
from http import HTTPStatus
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit

from patch_bluetooth_remote_api import HELPERS


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        controller = SimpleNamespace(
            start=lambda mac: self.result('start', mac),
            stop=lambda test: self.result('stop', test),
            snapshot=lambda test, cursor, refresh: self.result('events', test, cursor, refresh))
        ns = dict(json=json, parse_qs=parse_qs, urlsplit=urlsplit, HTTPStatus=HTTPStatus,
                  bmi30_bluetooth_remote_test=lambda: controller,
                  bmi30_usb_origin_allowed=lambda handler: handler.same_origin)
        tree = ast.parse(HELPERS)
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'bmi30_bluetooth_remote_request']
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<api>', 'exec'), ns)
        self.dispatch = ns['bmi30_bluetooth_remote_request']

    def result(self, *args):
        self.calls.append(args)
        return {'ok': True, 'test_id': 'real-test', 'status': 'ready', 'events': []}

    def handler(self, body=None, *, authenticated=True, header=True, origin=True, path='/api/bluetooth/remote-test/events?test_id=abc&cursor=12'):
        raw = json.dumps(body).encode() if body is not None else b''
        h = SimpleNamespace(headers={'Content-Length': str(len(raw))}, rfile=io.BytesIO(raw),
            path=path, same_origin=origin, close_connection=False,
            _get_portal_session=lambda: {} if authenticated else None)
        if header:
            h.headers['X-BMI30-Bluetooth-Test'] = '1'
        h._send_json_response = lambda data, status, **kw: setattr(h, 'response', (data, int(status), kw))
        return h

    def test_start_uses_exact_selected_mac_without_creating_events(self):
        h = self.handler({'mac': 'FF:24:01:26:A0:4A'})
        self.dispatch(h, 'start')
        self.assertEqual(self.calls, [('start', 'FF:24:01:26:A0:4A')])
        self.assertEqual(h.response[0]['events'], [])

    def test_authentication_header_and_origin_are_each_required(self):
        for flags in ({'authenticated': False}, {'header': False}, {'origin': False}):
            h = self.handler({'mac': 'FF:24:01:26:A0:4A'}, **flags)
            self.dispatch(h, 'start')
            self.assertEqual(h.response[1], 403)
        self.assertEqual(self.calls, [])

    def test_events_preserve_cursor_and_head_does_not_renew_lease(self):
        for send in (True, False):
            h = self.handler()
            self.dispatch(h, 'events', send_body=send)
            self.assertEqual(self.calls[-1], ('events', 'abc', 12, send))

    def test_bad_cursor_or_missing_test_never_reaches_reader(self):
        for query in ('cursor=0', 'test_id=x&cursor=-1', 'test_id=x&cursor=nan'):
            h = self.handler(path='/api/bluetooth/remote-test/events?'+query)
            self.dispatch(h, 'events')
            self.assertEqual(h.response[1], 400)
        self.assertEqual(self.calls, [])

    def test_malformed_oversized_or_truncated_json_does_not_start(self):
        for raw, size in ((b'[]',2),(b'{',1),(b'{}',4097),(b'{}',4)):
            h = self.handler()
            h.rfile = io.BytesIO(raw)
            h.headers['Content-Length'] = str(size)
            self.dispatch(h, 'start')
            self.assertEqual(h.response[1], 400)
        self.assertEqual(self.calls, [])

    def test_stop_requires_its_test_and_unknown_action_is_rejected(self):
        h = self.handler({'test_id': 'one'})
        self.dispatch(h, 'stop')
        self.assertEqual(self.calls, [('stop', 'one')])
        h = self.handler({})
        self.dispatch(h, 'stop')
        self.assertEqual(h.response[1],400)
        self.dispatch(h, 'invent')
        self.assertEqual(h.response[1],400)


if __name__ == '__main__':
    unittest.main()

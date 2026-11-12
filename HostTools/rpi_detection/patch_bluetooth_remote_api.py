"""Add authenticated Bluetooth remote-test routes to the resolved portal source."""
import ast


HELPERS = '''
_BMI30_BLUETOOTH_REMOTE_TEST = None
_BMI30_BLUETOOTH_REMOTE_LOCK = threading.Lock()


def bmi30_bluetooth_remote_test():
    global _BMI30_BLUETOOTH_REMOTE_TEST
    with _BMI30_BLUETOOTH_REMOTE_LOCK:
        if _BMI30_BLUETOOTH_REMOTE_TEST is None:
            import sys
            core = pathlib.Path(_resolve_project_path(detect_bmi30_split_system_version()['core_path']))
            directory = core.parent
            module_path = directory / 'bmi30_bluetooth_remote_test.py'
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))
            spec = importlib.util.spec_from_file_location('bmi30_bluetooth_remote_test', module_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _BMI30_BLUETOOTH_REMOTE_TEST = module.BluetoothRemoteTest()
        return _BMI30_BLUETOOTH_REMOTE_TEST


def bmi30_bluetooth_remote_request(handler, action, *, send_body=True):
    if (handler._get_portal_session() is None or
            handler.headers.get('X-BMI30-Bluetooth-Test') != '1' or
            not bmi30_usb_origin_allowed(handler)):
        handler._send_json_response({'ok': False, 'error': 'Not authorized.'}, HTTPStatus.FORBIDDEN,
                                    send_body=send_body)
        return
    try:
        if action == 'events':
            query = parse_qs(urlsplit(handler.path).query)
            test_id = str(query.get('test_id', [''])[0])
            cursor = int(query.get('cursor', ['0'])[0])
            if not test_id or cursor < 0:
                raise ValueError('Select a running remote test and valid event cursor')
            state = bmi30_bluetooth_remote_test().snapshot(test_id, cursor, refresh=send_body)
        else:
            length = int(handler.headers.get('Content-Length', '0') or '0')
            if not 0 < length <= 4096:
                handler.close_connection = True
                raise ValueError('Invalid remote-test request size')
            raw = handler.rfile.read(length)
            if len(raw) != length:
                raise ValueError('Incomplete remote-test request')
            body = json.loads(raw.decode('utf-8'))
            if not isinstance(body, dict):
                raise ValueError('Expected a remote-test request object')
            if action == 'start':
                state = bmi30_bluetooth_remote_test().start(body.get('mac'))
            elif action == 'stop':
                test_id = body.get('test_id')
                if not isinstance(test_id, str) or not test_id:
                    raise ValueError('Select a running remote test')
                state = bmi30_bluetooth_remote_test().stop(test_id)
            else:
                raise ValueError('Unsupported remote-test action')
        code = HTTPStatus.OK if state.get('ok') else HTTPStatus.CONFLICT
        handler._send_json_response(state, code, send_body=send_body)
    except (ValueError, TypeError, UnicodeError) as exc:
        handler._send_json_response({'ok': False, 'error': str(exc)}, HTTPStatus.BAD_REQUEST,
                                    send_body=send_body)
    except Exception:
        handler._send_json_response({'ok': False, 'error': 'Bluetooth remote test is unavailable.'},
                                    HTTPStatus.SERVICE_UNAVAILABLE, send_body=send_body)


'''


def patch(source):
    if 'def bmi30_bluetooth_remote_request(' in source:
        raise ValueError('Bluetooth remote-test API already exists')
    anchor = 'class HotspotInfoHandler(BaseHTTPRequestHandler):'
    assert source.count(anchor) == 1
    source = source.replace(anchor, HELPERS + anchor)
    anchor = '        # JSON API\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, '''        if path == "/api/bluetooth/remote-test/events":
            bmi30_bluetooth_remote_request(self, "events", send_body=send_body)
            return

''' + anchor)
    anchor = '    def do_POST(self) -> None:\n        path = self.path.split("?", 1)[0]\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '''        if path in ("/api/bluetooth/remote-test/start", "/api/bluetooth/remote-test/stop"):
            bmi30_bluetooth_remote_request(self, path.rsplit("/", 1)[-1])
            return

''')
    ast.parse(source)
    return source

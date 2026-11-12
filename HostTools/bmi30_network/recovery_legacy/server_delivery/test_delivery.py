import ast
import contextlib
import importlib.util
import json
from pathlib import Path
import queue
import shutil
import sys
import threading
import types
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))
import network
import service
from test_recovery import module as old_module, agent as old_agent, Clock
import make_server_sources as sources
import build_server


@contextlib.contextmanager
def directory():
    root = HERE / ('qa-' + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        assert root.resolve().parent == HERE.resolve()
        shutil.rmtree(root)


class DeliveryTests(unittest.TestCase):
    def test_detector_and_portal_functions_preserved(self):
        path = build_server.ROOT / 'FirmwareReleases/RaspberryPi/2026-10-23-1514/BMI30-RPi-2026-10-23-1514.tar.gz'
        files = build_server.read_bundle(path)
        original = files['project/hotspot_info_server.py'].decode()
        candidate = sources.portal(original)
        def functions(text):
            return {n.name: ast.dump(n) for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        before, after = functions(original), functions(candidate)
        permitted = {'main', 'HotspotInfoHandler'}
        self.assertEqual({k:v for k,v in before.items() if k not in permitted},
                         {k:after[k] for k in before if k not in permitted})
        def methods(text):
            cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='HotspotInfoHandler')
            return {n.name:ast.dump(n) for n in cls.body if isinstance(n,ast.FunctionDef)}
        left,right=methods(original),methods(candidate)
        self.assertEqual({k:v for k,v in left.items() if k!='_handle_request'},
                         {k:right[k] for k in left if k!='_handle_request'})

    def test_v2_agent_methods_preserved(self):
        original = (build_server.ROOT / '.tmp/legacy-menu-20261027/M10-network-original.py').read_text()
        candidate = sources.service(original, '0.2.4-recovery2')
        import patch_recovery
        unchanged = patch_recovery.unchanged_methods(original, candidate)
        self.assertNotIn('tick', unchanged)
        self.assertIn('self.legacy_recovery.active.maybe_visit(local)', candidate)
        self.assertIn('rpc', unchanged)
        self.assertIn('exchange', unchanged)
        self.assertIn('visit_peer', unchanged)

    def test_new_runtime_accepts_the_unmodified_old_receiver(self):
        donor, caller = 'BMI30-D5EB8F26C', 'BMI30-6922A5A0D'
        with directory() as root:
            helper = types.ModuleType('new_legacy_recovery')
            exec(sources.helper((donor, caller), 'test-bundle', '00' * 32), helper.__dict__)
            scope = {'__name__': 'new_menu_service', '__file__': 'menu-service.py'}
            original = (build_server.ROOT / '.tmp/legacy-menu-20261027/M10-network-original.py').read_text()
            with patch.dict(sys.modules, {'legacy_recovery': helper}):
                exec(sources.service(original, '0.2.4-recovery2'), scope)
            state = root / 'state'
            state.mkdir()
            with patch('network.hardware_identity', return_value=donor), patch('network.Network'):
                server = scope['Agent']({'id': donor, 'fleet_key': '17' * 32, 'state_dir': str(state)})
            self.addCleanup(server.group.socket.close)
            server.secrets = {'wifi:test-router': {'ssid':'test-router','psk':'test-password',
                             'security':'wpa-psk','origin':donor,'updated':2000000000}}
            server.publish({'id':donor,'broadcasts':[],'path':[],'internet':{'ok':False}})
            clock = Clock(0)
            old = old_module(clock)
            client = old_agent(old, 'BMI30-27448D72B', root, clock)
            ready = queue.Queue()
            worker = threading.Thread(target=scope['serve'],args=(server,'127.0.0.1',0,ready.put),daemon=True)
            worker.start()
            http = ready.get(timeout=3)
            try:
                old['PORT'] = http.server_port
                client.contact('127.0.0.1', donor)
                self.assertEqual(json.loads(client.secrets_path.read_text())['wifi:test-router']['psk'],'test-password')
                # The unchanged v2 challenge/exchange also works beside v1 recovery.
                other_state = root / 'caller'
                other_state.mkdir()
                with patch('network.hardware_identity',return_value=caller),patch('network.Network'):
                    other=scope['Agent']({'id':caller,'fleet_key':'17'*32,'state_dir':str(other_state)})
                self.addCleanup(other.group.socket.close)
                other.publish({'id':caller,'broadcasts':[],'path':[],'internet':{'ok':False}})
                scope['PORT']=http.server_port
                with patch('service.private_ip',side_effect=lambda value:value):
                    scope['private_ip']=lambda value:value
                    self.assertEqual(other.contact('127.0.0.1',donor)['id'],donor)
            finally:
                http.shutdown()
                http.server_close()
                worker.join(timeout=3)

    def test_saved_server_installer_defines_guard_before_cli_execution(self):
        source = sources.installer(build_server.TEST_IDS, True)
        self.assertLess(source.index('_component_rollback = rollback'), source.rindex("if __name__ == '__main__':"))
        self.assertIn('deferred-by-firmware-operation', source)
        compile(source, 'server-installer', 'exec')


if __name__ == '__main__':
    unittest.main()

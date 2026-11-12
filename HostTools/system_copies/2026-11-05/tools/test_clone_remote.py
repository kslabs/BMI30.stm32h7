"""Independent CPU-only tests of the actual clone worker AST; no device access."""
from __future__ import annotations
import ast
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'clone_remote.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
KEEP = (ast.FunctionDef, ast.ClassDef, ast.Assign)
NODES = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
NODES += [n for n in TREE.body if isinstance(n, KEEP)]
NS = dict(globals(), fcntl=types.SimpleNamespace())
exec(compile(ast.fix_missing_locations(ast.Module(body=NODES, type_ignores=[])), str(SOURCE), 'exec'), NS)


class TextPath:
    texts = {}
    def __init__(self, *parts):
        self.value = '/'.join(str(x).rstrip('/') for x in parts)
        self.name = PurePosixPath(self.value).name
    def read_text(self): return self.texts[self.value]


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((HERE / 's07-plan.json').read_text())
        self.pin = copy.deepcopy(self.plan['target'])
        self.node = {'path': '/dev/sda', 'type': 'disk', 'tran': 'usb', 'ro': False,
                     'mountpoints': [None], 'children': [{'path': '/dev/sda1', 'type': 'part', 'ro': False, 'mountpoints': [None]}]}
        self.nodes = [self.node]
        self.mounts = {'/': '/dev/mmcblk0p2', '/boot/firmware': '/dev/mmcblk0p1'}
        self.parts = {'/': self.plan['root_partuuid'], '/boot/firmware': self.plan['boot_partuuid']}
        self.calls = []
        TextPath.texts = {'/proc/device-tree/serial-number': self.plan['cm5'] + '\0',
                          '/proc/sys/kernel/random/boot_id': self.plan['boot_id'] + '\n'}
        def command(*args, **kwargs):
            self.calls.append(args)
            if args[:2] == ('findmnt', '-n'):
                return (self.mounts if args[3] == 'SOURCE' else self.parts)[args[4]]
            if args[:1] == ('swapon',): return ''
            raise AssertionError('Unexpected command, including any write: ' + repr(args))
        self.patches = patch.dict(NS, Path=TextPath, command=command,
            os=types.SimpleNamespace(geteuid=lambda: 0),
            socket=types.SimpleNamespace(gethostname=lambda: self.plan['host']),
            disk_inventory=lambda: self.nodes, disk_pin=lambda disk: self.pin)
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def refused_before_execute_write(self):
        worker = NS['Copy'].__new__(NS['Copy'])
        worker.plan = self.plan
        worker.run = lambda *a, **k: self.fail('Destructive boundary reached')
        worker.stage = lambda *a, **k: self.fail('Initial guard did not reject')
        with self.assertRaises(NS['Refused']): worker.execute()
        self.assertTrue(all(c[0] in ('findmnt', 'swapon') for c in self.calls))

    def test_valid_fresh_plan_guard(self): NS['guard'](self.plan)
    def test_usb_root_rejected_before_write(self):
        self.mounts['/'] = '/dev/sda2'; self.refused_before_execute_write()
    def test_wrong_source_boot_rejected_before_write(self):
        self.mounts['/boot/firmware'] = '/dev/sda1'; self.refused_before_execute_write()
    def test_wrong_source_partuuid_rejected_before_write(self):
        self.parts['/'] = 'different-02'; self.refused_before_execute_write()
    def test_wrong_plan_source_rejected_before_write(self):
        self.plan['root'] = '/dev/sda2'; self.refused_before_execute_write()
    def test_wrong_cm5_rejected_before_write(self):
        TextPath.texts['/proc/device-tree/serial-number'] = 'other'; self.refused_before_execute_write()
    def test_source_reboot_rejected_before_write(self):
        TextPath.texts['/proc/sys/kernel/random/boot_id'] = 'other'; self.refused_before_execute_write()
    def test_usb_attachment_pins_rejected_before_write(self):
        for field, value in [('serial', 'other'), ('model', 'other'), ('size', 1),
                             ('maj_min', '8:16'), ('diskseq', int(self.pin['diskseq']) + 1), ('tran', 'sata')]:
            with self.subTest(field=field):
                original = self.pin[field]; self.pin[field] = value
                self.refused_before_execute_write(); self.pin[field] = original
    def test_mounted_child_rejected_before_write(self):
        self.node['children'][0]['mountpoints'] = ['/media/other']; self.refused_before_execute_write()
    def test_readonly_child_rejected_before_write(self):
        self.node['children'][0]['ro'] = True; self.refused_before_execute_write()
    def test_second_usb_rejected_before_write(self):
        self.nodes.append({'path': '/dev/sdb', 'type': 'disk', 'tran': 'usb'}); self.refused_before_execute_write()
    def test_missing_erase_authorization_rejected_before_write(self):
        self.plan['usb_erase_authorized'] = False; self.refused_before_execute_write()
    def test_owned_mount_only_is_allowed(self):
        self.node['children'][0]['mountpoints'] = ['/owned/mount']
        NS['guard'](self.plan, allowed_mounts=('/owned/mount',))
        with self.assertRaises(NS['Refused']): NS['guard'](self.plan, allowed_mounts=('/different',))

    def test_open_descriptor_must_still_be_pinned_block_device(self):
        for mode, descriptor_device, node_device, passes in ((stat.S_IFBLK, 2048, 2048, True),
            (stat.S_IFREG, 2048, 2048, False), (stat.S_IFBLK, 2048, 2064, False)):
            with self.subTest(mode=mode, descriptor=descriptor_device, node=node_device):
                fake_os = types.SimpleNamespace(geteuid=lambda: 0,
                    fstat=lambda fd: types.SimpleNamespace(st_mode=mode, st_rdev=descriptor_device),
                    stat=lambda path: types.SimpleNamespace(st_rdev=node_device))
                with patch.dict(NS, os=fake_os):
                    if passes: NS['guard'](self.plan, descriptor=123)
                    else:
                        with self.assertRaises(NS['Refused']): NS['guard'](self.plan, descriptor=123)


class LocalSemanticTests(unittest.TestCase):
    def owned_directory(self):
        root = HERE / ('review-temp-' + uuid.uuid4().hex)
        root.mkdir()
        def cleanup():
            resolved = root.resolve()
            if resolved.parent != HERE or not resolved.name.startswith('review-temp-'):
                raise AssertionError('Unsafe test cleanup path')
            shutil.rmtree(resolved)
        self.addCleanup(cleanup)
        return root

    def test_diskseq_serialization_matches_plan_integer(self):
        plan = json.loads((HERE / 's07-plan.json').read_text())
        disk = dict(plan['target'], **{'maj:min': plan['target']['maj_min']})
        TextPath.texts = {'/sys/class/block/sda/diskseq': str(plan['target']['diskseq'])}
        with patch.dict(NS, Path=TextPath): pin = NS['disk_pin'](disk)
        self.assertEqual(pin, plan['target'])

    def test_both_real_jobids_accepted_and_traversal_refused(self):
        main = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'require'
                 and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and n.args[1].value == 'Invalid job ID']
        self.assertEqual(len(calls), 1)
        code = compile(ast.Expression(calls[0].args[0]), '<actual-jobid-guard>', 'eval')
        for name in ('s07', 'm02'):
            plan = json.loads((HERE / (name + '-plan.json')).read_text())
            self.assertTrue(eval(code, dict(NS, plan=plan)))
        for value in ('../x', 'x/y', '', 'A', 'x' * 65, 123):
            self.assertFalse(eval(code, dict(NS, plan={'job_id': value})))

    def test_bulk_excludes_keep_own_docs_tmp_and_backups(self):
        self.assertNotIn('/home/techaid/Documents/.tmp/***', NS['BASE_EXCLUDES'])
        self.assertFalse(any('Documents' in x or 'bmi30-legacy-menu' in x for x in NS['BASE_EXCLUDES']))

    def test_actual_execute_root_verify_precedes_boot_activation(self):
        klass = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'Copy')
        execute = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        boot = [n for n in ast.walk(execute) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'activate_boot']
        self.assertEqual(len(boot), 1)
        verify = [n for n in ast.walk(execute) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'verify_tree']
        self.assertTrue(verify and all(n.lineno < boot[0].lineno for n in verify))
        matches = [n for n in ast.walk(execute) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'require'
                   and any(isinstance(x, ast.Constant) and x.value == 'Own settings/identity/active code differs on USB' for x in n.args)]
        self.assertEqual(len(matches), 1)
        self.assertLess(matches[0].lineno, boot[0].lineno)
        root_sync = [n for n in ast.walk(execute) if isinstance(n, ast.Call)
                     and any(isinstance(x, ast.Constant) and x.value == 'sync-root-before-boot' for x in n.args)]
        self.assertEqual(len(root_sync), 1)
        self.assertLess(root_sync[0].lineno, boot[0].lineno)

    def boot_worker(self, fail_label=None):
        worker = NS['Copy'].__new__(NS['Copy'])
        root = self.owned_directory()
        worker.boot = root / 'boot'; worker.boot.mkdir()
        worker.plan = {'job_id': 'test-job'}
        worker.check_mounts = lambda: None
        commands = []
        def run(label, args, **kwargs):
            commands.append(label)
            if label == 'boot-stage':
                stage = Path(args[-1])
                (stage / 'overlays').mkdir()
                (stage / 'overlays/example.dtbo').write_bytes(b'overlay')
                (stage / 'cmdline.txt').write_text('console=tty1 root=PARTUUID=original-02 rootwait\n')
                (stage / 'config.txt').write_text('[all]\nkernel=kernel_2712.img\n')
                (stage / 'kernel_2712.img').write_bytes(b'kernel')
                (stage / 'start4.elf').write_bytes(b'firmware')
            if label == 'sync-boot-before-kernels':
                self.assertFalse((worker.boot / 'kernel_2712.img').exists())
                self.assertFalse((worker.boot / 'start4.elf').exists())
                self.assertIn('root=PARTUUID=11223344-02', (worker.boot / 'cmdline.txt').read_text())
            if label == fail_label: raise NS['Refused']('Injected ' + label)
            log = root / (label + '.log'); log.write_text('')
            return log
        worker.run = run
        return worker, commands

    def test_boot_stage_rewrites_root_and_publishes_executables_last(self):
        worker, commands = self.boot_worker()
        moves = []
        original_replace = os.replace
        def replace(source, target):
            moves.append(Path(target).name)
            return original_replace(source, target)
        with patch.object(os, 'replace', replace): worker.activate_boot('11223344')
        self.assertLess(moves.index('cmdline.txt'), moves.index('start4.elf'))
        self.assertLess(moves.index('start4.elf'), moves.index('kernel_2712.img'))
        self.assertEqual((worker.boot / 'kernel_2712.img').read_bytes(), b'kernel')
        self.assertFalse(any(p.name.startswith('.bmi30-copy-stage-') for p in worker.boot.iterdir()))
        self.assertIn('boot-active-checksum', commands)

    def test_boot_stage_failure_cannot_activate_kernel(self):
        for fail in ('boot-stage', 'boot-stage-checksum', 'sync-boot-before-kernels'):
            with self.subTest(fail=fail):
                worker, _ = self.boot_worker(fail)
                with self.assertRaises(NS['Refused']): worker.activate_boot('11223344')
                self.assertFalse((worker.boot / 'kernel_2712.img').exists())
                self.assertFalse((worker.boot / 'start4.elf').exists())

    def test_fstab_rewrite_changes_copied_root_only(self):
        root = self.owned_directory()
        source = root / 'source-fstab'
        original = 'PARTUUID=old-02 / ext4 defaults 0 1\nPARTUUID=old-01 /boot/firmware vfat defaults 0 2\n'
        source.write_text(original)
        worker = NS['Copy'].__new__(NS['Copy']); worker.mount = root / 'target'
        (worker.mount / 'etc').mkdir(parents=True)
        (worker.mount / 'etc/fstab').write_text(original)
        worker.check_mounts = lambda: None
        worker.rewrite_fstab('11223344')
        text = (worker.mount / 'etc/fstab').read_text()
        self.assertIn('PARTUUID=11223344-02', text); self.assertIn('PARTUUID=11223344-01', text)
        self.assertEqual(source.read_text(), original)

    def test_absolute_symlinks_resolve_inside_clone_not_live_source(self):
        links = {'/usb/home/host/active': '/home/host/.tmp/runtime',
                 '/usb/loop': '/loop'}
        class VirtualPath:
            def __init__(self, *parts):
                self.path = PurePosixPath(*(str(p) for p in parts))
            def __truediv__(self, part): return VirtualPath(self.path / part)
            def __str__(self): return str(self.path)
            def __eq__(self, other): return isinstance(other, VirtualPath) and self.path == other.path
            @property
            def parent(self): return VirtualPath(self.path.parent)
            def is_symlink(self): return str(self.path) in links
        with patch.dict(NS, Path=VirtualPath, os=types.SimpleNamespace(readlink=lambda p: links[str(p)])):
            root = VirtualPath('/usb')
            self.assertEqual(str(NS['rooted'](root, 'home/host/active/code.py')), '/usb/home/host/.tmp/runtime/code.py')
            self.assertEqual(str(NS['rooted'](root, 'home/host/active', follow_final=False)), '/usb/home/host/active')
            with self.assertRaises(NS['Refused']): NS['rooted'](root, '../../etc/passwd')
            with self.assertRaises(NS['Refused']): NS['rooted'](root, 'loop/code.py')

    def cleanup_fixture(self, foreign=False, missing=False):
        root = self.owned_directory(); busy = root / 'busy'
        if not missing: busy.write_text(json.dumps({'token': 'foreign' if foreign else 'ours'}))
        inode = busy.stat() if busy.exists() else types.SimpleNamespace(st_dev=123, st_ino=456)
        owner = ('ours', inode.st_dev, inode.st_ino)
        plan = {'job_id': 'test-cleanup'}
        saved = {'job_id': plan['job_id'], 'units': ['bmi30-core.service'], 'owner': owner}
        (root / 'pause-state.json').write_text(json.dumps(saved))
        calls = []
        def claim(path):
            try:
                with path.open('x') as f: json.dump({'token': 'reclaimed'}, f)
            except FileExistsError: return None
            st = path.stat(); calls.append(('claim',))
            return ('reclaimed', st.st_dev, st.st_ino)
        def release(path, acquired):
            if path.exists():
                st = path.stat()
                if json.loads(path.read_text()).get('token') == acquired[0] and (st.st_dev, st.st_ino) == tuple(acquired[1:]):
                    calls.append(('release',)); path.unlink()
        def path_map(*parts):
            if parts == ('/run/bmi30-access/busy',): return busy
            return Path(*parts)
        context = patch.dict(NS, Path=path_map, identity=lambda p: None,
            access_helpers=lambda: (claim, release), command=lambda *a, **k: calls.append(a) or '',
            atomic_json=lambda path, value: path.write_text(json.dumps(value)))
        return root, busy, plan, calls, context

    def test_cleanup_foreign_busy_does_not_restart_or_release(self):
        root, busy, plan, calls, context = self.cleanup_fixture(foreign=True)
        with context, self.assertRaises(NS['Refused']): NS['cleanup'](plan, root)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(busy.read_text())['token'], 'foreign')

    def test_cleanup_owned_busy_restarts_then_releases(self):
        root, busy, plan, calls, context = self.cleanup_fixture()
        with context: NS['cleanup'](plan, root)
        self.assertEqual(calls, [('systemctl', 'start', 'bmi30-core.service'), ('release',)])
        self.assertFalse(busy.exists())
        self.assertEqual(json.loads((root / 'pause-state.json').read_text())['units'], [])

    def test_cleanup_absent_busy_reclaims_before_restart(self):
        root, busy, plan, calls, context = self.cleanup_fixture(missing=True)
        with context: NS['cleanup'](plan, root)
        self.assertEqual(calls, [('claim',), ('systemctl', 'start', 'bmi30-core.service'), ('release',)])
        self.assertFalse(busy.exists())

    def test_cleanup_missing_owner_refuses_restart(self):
        root, busy, plan, calls, context = self.cleanup_fixture()
        data = json.loads((root / 'pause-state.json').read_text()); data['owner'] = None
        (root / 'pause-state.json').write_text(json.dumps(data))
        with context, self.assertRaises(NS['Refused']): NS['cleanup'](plan, root)
        self.assertEqual(calls, [])

    def test_cleanup_does_not_touch_live_worker(self):
        root, busy, plan, calls, context = self.cleanup_fixture()
        data = json.loads((root / 'pause-state.json').read_text()); data['worker'] = {'pid': 123, 'start_ticks': '456'}
        (root / 'pause-state.json').write_text(json.dumps(data))
        with context, patch.dict(NS, process_start=lambda pid: data['worker']), self.assertRaises(NS['Refused']):
            NS['cleanup'](plan, root)
        self.assertEqual(calls, [])

    def resume_worker(self):
        worker = NS['Copy'].__new__(NS['Copy'])
        worker.plan = {'job_id': 'test-resume'}; worker.job = self.owned_directory()
        worker.paused = ['bmi30-hotspot-info.service', 'bmi30-core.service']
        worker.last_owner = ('ours', 123, 456)
        worker.lease = types.SimpleNamespace(owner=worker.last_owner, refresh=lambda: None)
        return worker

    def state_context(self, command):
        return patch.dict(NS, command=command,
            process_start=lambda pid: {'pid': pid, 'start_ticks': 'fake-native-test'},
            atomic_json=lambda path, value: path.write_text(json.dumps(value)))

    def test_resume_without_lease_keeps_owner_proof_and_does_not_start(self):
        worker = self.resume_worker(); worker.lease = None; calls = []
        with self.state_context(lambda *a, **k: calls.append(a)):
            errors = worker.resume()
        self.assertEqual(calls, []); self.assertEqual(errors, worker.paused)
        saved = json.loads((worker.job / 'pause-state.json').read_text())
        self.assertEqual(saved['owner'], list(worker.last_owner))
        self.assertEqual(saved['units'], worker.paused)

    def test_resume_foreign_owner_does_not_start_services(self):
        worker = self.resume_worker(); calls = []
        def lost(): raise NS['Refused']('Foreign owner')
        worker.lease.refresh = lost
        with self.state_context(lambda *a, **k: calls.append(a)): errors = worker.resume()
        self.assertEqual(calls, []); self.assertEqual(set(errors), set(worker.paused))

    def test_partial_resume_retains_only_failed_unit_and_owner_after_close(self):
        worker = self.resume_worker(); calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[-1] == 'bmi30-core.service': raise NS['Refused']('Injected restart failure')
        with self.state_context(command):
            errors = worker.resume()
            worker.lease = None
            worker.save_pause_state()
        self.assertEqual(errors, ['bmi30-core.service'])
        self.assertEqual(worker.paused, ['bmi30-core.service'])
        saved = json.loads((worker.job / 'pause-state.json').read_text())
        self.assertEqual(saved['owner'], list(worker.last_owner))
        self.assertFalse(saved['resumed'])
        self.assertEqual(len(calls), 2)

    def test_root_sync_direction_and_final_pass_disallows_vanished_files(self):
        worker = NS['Copy'].__new__(NS['Copy']); worker.mount = Path('local-target')
        worker.check_mounts = lambda: None
        worker.excludes = list(NS['BASE_EXCLUDES']); calls = []
        worker.run = lambda *args, **kwargs: calls.append((args, kwargs))
        worker.root_sync('bulk', allow_vanished=True)
        worker.root_sync('final')
        for args, _ in calls:
            self.assertEqual(args[1][-2:], ['/', 'local-target/'])
        self.assertEqual(calls[0][1]['allowed_exit'], (0, 24))
        self.assertEqual(calls[1][1]['allowed_exit'], (0,))


if __name__ == '__main__': unittest.main(verbosity=2)

"""Exact active access ownership methods, real O_EXCL, no Linux/server imports."""
import ast
import json
import os
from pathlib import Path
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE=Path(__file__).with_name('access.py')


class EndOfJob(Exception):pass


class AccessOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.folder=SOURCE.parent/('access-ownership-test-'+uuid.uuid4().hex)
        self.folder.mkdir()  # inherit workspace ACL; no mode700 Windows tempdir
        tree=ast.parse(SOURCE.read_text(encoding='utf8'))
        supervisor=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Supervisor')
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('claim_busy','release_busy')]
        nodes += [n for n in supervisor.body if isinstance(n,ast.FunctionDef) and n.name=='tick']
        def stop():raise EndOfJob()
        self.ns={'os':os,'Path':Path,'json':json,'uuid':uuid,'time':SimpleNamespace(time=lambda:1000.,monotonic=lambda:100.),
                 'RUNTIME':self.folder,'CONFIG':SimpleNamespace(read_text=stop),'LOG':Mock()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(SOURCE),'exec'),self.ns)
        self.busy=self.folder/'busy'

    def tearDown(self):
        for path in self.folder.iterdir():path.unlink()
        self.folder.rmdir()

    def supervisor(self,action=None):
        value=SimpleNamespace(lock=threading.RLock(),pending=(50.,'recover',None),action=action or Mock(),last_action=None,next_repair=0.)
        value.tick=MethodType(self.ns['tick'],value)
        return value

    def tick(self,supervisor):
        with self.assertRaises(EndOfJob):supervisor.tick()

    def test_due_job_cannot_replace_or_remove_an_external_busy_and_stays_pending(self):
        original=b'{"token":"manual-maintenance","started":900}'
        self.busy.write_bytes(original);supervisor=self.supervisor();pending=supervisor.pending
        self.tick(supervisor)
        self.assertEqual(self.busy.read_bytes(),original);self.assertEqual(supervisor.pending,pending)
        supervisor.action.assert_not_called();self.assertIsNone(supervisor.last_action)

    def test_owned_job_claims_unique_token_before_consuming_and_releases_only_own_file(self):
        seen=[]
        def action(*args):
            seen.append(json.loads(self.busy.read_text()));self.assertIsNone(supervisor.pending)
        supervisor=self.supervisor(action);self.tick(supervisor)
        self.assertEqual(len(seen),1);self.assertEqual(seen[0]['owner'],'bmi30-access')
        self.assertEqual(len(seen[0]['token']),32);self.assertFalse(self.busy.exists())
        self.assertTrue(supervisor.last_action['ok']);self.assertEqual(supervisor.next_repair,160.)

    def test_external_owner_replacement_during_action_survives_finally(self):
        def action(*args):self.busy.write_text('{"token":"new-manual-owner"}')
        supervisor=self.supervisor(action);self.tick(supervisor)
        self.assertEqual(json.loads(self.busy.read_text())['token'],'new-manual-owner')

    def test_exclusive_claim_race_preserves_new_external_busy_and_pending_job(self):
        supervisor=self.supervisor();pending=supervisor.pending
        def racing_open(path,flags,mode):
            self.assertTrue(flags & os.O_EXCL);self.assertTrue(flags & os.O_CREAT)
            self.busy.write_text('{"token":"race-winner"}')
            raise FileExistsError('other maintenance owner won')
        with patch.object(os,'open',side_effect=racing_open):self.tick(supervisor)
        self.assertEqual(supervisor.pending,pending);supervisor.action.assert_not_called()
        self.assertEqual(json.loads(self.busy.read_text())['token'],'race-winner')

    def test_action_failure_releases_owned_claim_without_leaking_error_text(self):
        supervisor=self.supervisor(Mock(side_effect=RuntimeError('private error')));self.tick(supervisor)
        self.assertFalse(self.busy.exists());self.assertFalse(supervisor.last_action['ok'])
        self.assertEqual(supervisor.last_action['error'],'RuntimeError')

    def test_initial_maintenance_guard_and_all_other_supervisor_methods_are_unchanged(self):
        before=ast.parse(SOURCE.parents[1].joinpath('active_m02_access.py').read_text(encoding='utf8'))
        after=ast.parse(SOURCE.read_text(encoding='utf8'))
        main=lambda tree:next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        self.assertEqual(ast.dump(main(before),include_attributes=False),ast.dump(main(after),include_attributes=False))
        methods=lambda tree:{n.name:ast.dump(n,include_attributes=False) for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='Supervisor' for n in c.body if isinstance(n,ast.FunctionDef) and n.name!='tick'}
        self.assertEqual(methods(before),methods(after))


if __name__=='__main__':unittest.main()

"""Actual handlers and AESGCM; only approved-auth is faked. No network/server IO."""
import errno
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
sys.dont_write_bytecode=True
import time
import unittest
from unittest.mock import patch
import uuid
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient

W=Path(__file__).resolve().parent
PROJECT=next((p for p in W.parents if(p/'HostTools').is_dir()and(p/'Core').is_dir()),W)
FIXTURES=PROJECT/'.tmp/hub-central-fixtures-20261105'
SOURCE=W/'candidate/bmi30hub/central_store.py'
spec=importlib.util.spec_from_file_location('central_store',SOURCE)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
A='BMI30-0000000000000001';A2='BMI30-0000000000000002';B='BMI30-0000000000000003';NULL='BMI30-0000000000000004';OFF='BMI30-0000000000000005'
TOKENS={'a'*48:A,'b'*48:A2,'c'*48:B,'d'*48:NULL,'e'*48:OFF}


class Tests(unittest.TestCase):
    def setUp(self):
        FIXTURES.mkdir(parents=True,exist_ok=True)
        self.root=FIXTURES/('fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.db=sqlite3.connect(':memory:',check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE devices(device_id TEXT,state TEXT,system_id INTEGER); CREATE TABLE systems(id INTEGER,active INTEGER);INSERT INTO systems VALUES(1,1),(2,1),(3,0);')
        self.db.executemany('INSERT INTO devices VALUES(?,?,?)',[(A,'approved',1),(A2,'approved',1),(B,'approved',2),(NULL,'approved',None),(OFF,'approved',3)])
        class DB:
            def fetchone(inner,sql,params):return self.db.execute(sql,params).fetchone()
        def approved(token):
            if token not in TOKENS:raise HTTPException(status_code=403)
            return TOKENS[token]
        self.approved=approved;self.resolve=m.system_scope_resolver(DB())
        self.store=m.CentralStore(self.root,m.Limits(capture_bytes=200,device_bytes=300,total_bytes=500,min_free_bytes=0,sessions=10,device_sessions=8));self.store.ensure_layout()
        self.client=self.make_client(False);self.captures=self.make_client(True)

    def make_client(self,captures):
        app=FastAPI();app.include_router(m.build_central_router(self.store,self.approved,self.resolve,enable_captures=captures))
        return TestClient(app,base_url='https://hub.invalid')

    def tearDown(self):
        self.client.close();self.captures.close();self.db.close()
        if self.root.resolve().parent!=FIXTURES.resolve()or not self.root.name.startswith('fixture-'):raise AssertionError('Unsafe fixture cleanup')
        shutil.rmtree(self.root)

    @staticmethod
    def headers(token='a'):return {'Authorization':'Bearer '+token*48}
    @staticmethod
    def record(ssid='Exact Lab',psk='fixture-password-1',security='wpa-psk',revision=33):
        return {'ssid':ssid,'psk':psk,'security':security,'origin':A,'revision':revision,'updated':123.0}
    def merge(self,records,token='a',**extra):
        return self.client.post('/api/v1/wifi/catalog/merge',headers=self.headers(token),json={'records':records,**extra})
    def get(self,token='a'):return self.client.get('/api/v1/wifi/catalog',headers=self.headers(token))
    def create(self,token='a',maximum=100,session=None):
        return self.captures.post('/api/v1/captures',headers=self.headers(token),json={'client_session_id':session or uuid.uuid4().hex,'max_bytes':maximum,'metadata':{'channel':'upper','tag':'fixture'}})
    def chunk(self,capture,seq,data=b'abc',token='a',sha=None):
        return self.captures.put(f'/api/v1/captures/{capture}/chunks/{seq}',headers={**self.headers(token),'X-Chunk-SHA256':sha or hashlib.sha256(data).hexdigest()},content=data)
    def commit(self,capture,data=b'abc',chunks=1,token='a'):
        return self.captures.post(f'/api/v1/captures/{capture}/commit',headers=self.headers(token),json={'chunks':chunks,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})

    def test_auth_https_query_and_unassigned_scope_fail_closed(self):
        self.assertEqual(self.client.get('/api/v1/wifi/catalog').status_code,401)
        self.assertEqual(self.get('x').status_code,403)
        self.assertEqual(self.client.get('http://hub.invalid/api/v1/wifi/catalog',headers=self.headers()).status_code,426)
        self.assertEqual(self.client.get('/api/v1/wifi/catalog?scope=system:2',headers=self.headers()).status_code,400)
        self.assertEqual(self.get('d').status_code,403);self.assertEqual(self.get('e').status_code,403)

    def test_approved_cross_tenant_cannot_receive_same_ssid_psk(self):
        self.assertEqual(self.merge({'wifi:Exact Lab':self.record()}).status_code,200)
        self.assertEqual(self.get('b').json()['records']['wifi:Exact Lab']['psk'],'fixture-password-1')
        self.assertEqual(self.get('c').json()['records'],{})
        self.assertEqual(self.merge({'wifi:Exact Lab':self.record(psk='different-tenant')},'c').status_code,200)
        self.assertEqual(self.get().json()['records']['wifi:Exact Lab']['psk'],'fixture-password-1')
        self.assertEqual(self.get('c').json()['records']['wifi:Exact Lab']['psk'],'different-tenant')

    def test_exact_utf8_case_and_spaces_are_independent_keys(self):
        for name in('Lab','lab',' Lab','Lab ','Cafe\u0301','Caf\u00e9'):
            self.assertEqual(self.merge({'wifi:'+name:self.record(name)}).status_code,200)
        self.assertEqual(len(self.get().json()['records']),6)

    def test_psk_encrypted_at_rest_key_stable_and_record_schema_compatible(self):
        key=self.store.key.read_bytes();r=self.record();self.merge({'wifi:Exact Lab':r})
        self.assertNotIn(r['psk'].encode(),self.store.catalog.read_bytes())
        result=self.get();self.assertEqual(set(result.json()['records']['wifi:Exact Lab']),m.RECORD_FIELDS)
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.store.ensure_layout();self.assertEqual(key,self.store.key.read_bytes())

    def test_cas_and_explicit_choice_required_for_different_saved_psk(self):
        self.merge({'wifi:Exact Lab':self.record()});before=self.store.catalog.read_bytes()
        changed={'wifi:Exact Lab':self.record(psk='replacement-psk')}
        for extra in({}, {'expected_revision':0,'explicit_replacements':['wifi:Exact Lab']},{'expected_revision':1}):
            self.assertEqual(self.merge(changed,**extra).status_code,409)
            self.assertEqual(before,self.store.catalog.read_bytes())
        self.assertEqual(self.merge(changed,expected_revision=1,explicit_replacements=['wifi:Exact Lab']).status_code,200)
        self.assertEqual(self.get().json()['revision'],2)

    def test_fresh_reported_connection_allows_cas_replacement_but_nm_timestamp_does_not(self):
        self.merge({'wifi:Exact Lab':self.record()})
        p={'wifi:Exact Lab':{'source':'active_station_server_reachable','verification':'client_reported_connected','client_observed_at':time.time()}}
        changed={'wifi:Exact Lab':self.record(psk='replacement-psk')}
        self.assertEqual(self.merge(changed,expected_revision=1,provenance=p).status_code,200)
        self.assertEqual(self.get().json()['provenance']['wifi:Exact Lab']['verification'],'client_reported_connected')
        p['wifi:Exact Lab']['client_observed_at']-=120
        self.assertEqual(self.merge(changed,expected_revision=2,provenance=p).status_code,400)

    def test_client_revision_boundary_cannot_poison_server_counter(self):
        r=self.record(revision=2**53-1);self.assertEqual(self.merge({'wifi:Exact Lab':r}).json()['revision'],1)
        self.assertEqual(self.merge({'wifi:Exact Lab':self.record(psk='replacement-psk')},expected_revision=1,explicit_replacements=['wifi:Exact Lab']).json()['revision'],2)

    def test_verified_same_password_upgrade_is_not_downgraded_by_saved_profile(self):
        records={'wifi:Exact Lab':self.record()};self.merge(records)
        p={'wifi:Exact Lab':{'source':'active_station_server_reachable','verification':'client_reported_connected','client_observed_at':time.time()}}
        self.assertEqual(self.merge(records,expected_revision=1,provenance=p).json()['revision'],2)
        self.assertEqual(self.merge(records).json()['revision'],2)
        self.assertEqual(self.get().json()['provenance']['wifi:Exact Lab']['verification'],'client_reported_connected')

    def test_system_deactivation_revokes_next_request_without_cache(self):
        self.merge({'wifi:Exact Lab':self.record()});self.assertEqual(self.get().status_code,200)
        self.db.execute('UPDATE systems SET active=0 WHERE id=1')
        self.assertEqual(self.get().status_code,403)

    def test_open_wpa64hex_sae_and_invalid_ap_or_psk(self):
        for name,pw,security in(('Open','','open'),('Hex','a'*64,'wpa-psk'),('SAE','x','sae')):
            self.assertEqual(self.merge({'wifi:'+name:self.record(name,pw,security)}).status_code,200)
        for key,record in(('ap:Exact Lab',self.record()),('wifi:Exact Lab',self.record(psk='short')),('wifi:Exact Lab',self.record(psk='z'*64)),('wifi:X',self.record())):
            self.assertEqual(self.merge({key:record}).status_code,400)

    def test_ciphertext_scope_swap_tamper_is_rejected_without_plaintext_error(self):
        self.merge({'wifi:Exact Lab':self.record()});self.merge({'wifi:Exact Lab':self.record(psk='different-tenant')},'c')
        d=json.loads(self.store.catalog.read_bytes());d['scopes']['system:2']['records']['wifi:Exact Lab']=d['scopes']['system:1']['records']['wifi:Exact Lab'];self.store.catalog.write_bytes(m.encoded(d))
        response=self.get('c');self.assertEqual(response.status_code,503);self.assertNotIn('fixture-password',response.text)

    def test_failed_atomic_wifi_replace_preserves_old_catalog_then_retry(self):
        self.merge({'wifi:Exact Lab':self.record()});before=self.store.catalog.read_bytes();real=m.os.replace
        def fail(source,target):
            if Path(target)==self.store.catalog:raise OSError(errno.EIO,'fixture')
            return real(source,target)
        with patch.object(m.os,'replace',side_effect=fail):self.assertEqual(self.merge({'wifi:New':self.record('New')}).status_code,503)
        self.assertEqual(before,self.store.catalog.read_bytes())
        self.assertEqual(self.merge({'wifi:New':self.record('New')}).status_code,200)

    def test_key_missing_does_not_silently_regenerate_or_reset_master(self):
        self.merge({'wifi:Exact Lab':self.record()});before=self.store.catalog.read_bytes();self.store.key.unlink()
        self.assertEqual(self.get().status_code,503);self.assertFalse(self.store.initialize_optional())
        self.assertFalse(self.store.key.exists());self.assertEqual(before,self.store.catalog.read_bytes())

    def test_replaced_valid_key_cannot_append_new_scope_or_mix_catalog(self):
        self.merge({'wifi:Exact Lab':self.record()});before=self.store.catalog.read_bytes()
        self.store.key.write_bytes(b'k'*32)
        self.assertEqual(self.get().status_code,503)
        self.assertEqual(self.get('c').status_code,503)
        self.assertEqual(self.merge({'wifi:New':self.record('New')},'c').status_code,503)
        self.assertEqual(self.store.catalog.read_bytes(),before)

    def test_init_only_refuses_existing_and_readback_no_secret(self):
        with self.assertRaises(m.StoreError):self.store.init_only()
        other=m.CentralStore(self.root/'empty');result=other.init_only()
        self.assertTrue(result['initialized']);self.assertEqual(result['scope_count'],0);key=other.key.read_bytes()
        with self.assertRaises(m.StoreError):other.init_only()
        self.assertEqual(key,other.key.read_bytes());self.assertNotIn('key',json.dumps(result).replace('key_exists',''))

    def test_json_input_bounds_and_no_request_echo(self):
        for data in(b'{"records":{},"records":{}}',b'{"records":1e999}',b'x'*65537):
            r=self.client.post('/api/v1/wifi/catalog/merge',headers=self.headers(),content=data)
            self.assertIn(r.status_code,(400,413));self.assertLess(len(r.content),200)
        r=self.merge({'wifi:Exact Lab':self.record()},scope='system:2');self.assertEqual(r.status_code,400)

    def test_capture_api_default_disabled(self):self.assertEqual(self.client.get('/api/v1/captures',headers=self.headers()).status_code,404)

    def test_capture_owner_isolation_and_create_idempotence(self):
        session=uuid.uuid4().hex;r=self.create(session=session);i=r.json()['capture_id']
        self.assertEqual(self.create(session=session).json()['capture_id'],i)
        self.assertEqual(self.create('c',session=session).status_code,409)
        for action in(lambda:self.captures.get('/api/v1/captures/'+i,headers=self.headers('c')),
          lambda:self.chunk(i,0,token='c'),lambda:self.commit(i,token='c'),
          lambda:self.captures.get('/api/v1/captures/'+i+'/download',headers=self.headers('c'))):self.assertEqual(action().status_code,404)

    def test_capture_order_hash_replay_commit_and_download(self):
        i=self.create().json()['capture_id']
        self.assertEqual(self.chunk(i,1).status_code,409);self.assertEqual(self.chunk(i,0,sha='0'*64).status_code,400)
        first=self.chunk(i,0);self.assertTrue(first.json()['durable']);self.assertEqual(self.chunk(i,0).json(),first.json())
        self.assertEqual(self.chunk(i,0,b'different').status_code,409)
        self.assertEqual(self.captures.get('/api/v1/captures/'+i+'/download',headers=self.headers()).status_code,409)
        self.assertEqual(self.commit(i,b'xyz').status_code,400);self.assertTrue(self.commit(i).json()['durable'])
        data=self.captures.get('/api/v1/captures/'+i+'/download',headers=self.headers());self.assertEqual(data.content,b'abc')
        self.assertEqual(data.headers['x-content-sha256'],hashlib.sha256(b'abc').hexdigest())
        self.assertEqual(self.chunk(i,1).status_code,409)

    def test_capture_quotas_never_delete_existing(self):
        self.create(maximum=150);self.create(maximum=150)
        self.assertEqual(self.create(maximum=1).status_code,507)
        self.assertEqual(len(self.captures.get('/api/v1/captures',headers=self.headers()).json()['captures']),2)

    def test_capture_failed_create_hidden_staging_does_not_poison_next(self):
        real=m.atomic
        def fail(path,data):
            if path.name=='manifest.json':raise OSError(errno.ENOSPC,'fixture')
            return real(path,data)
        with patch.object(m,'atomic',side_effect=fail):self.assertEqual(self.create().status_code,507)
        self.assertEqual(self.create().status_code,200)
        self.assertEqual(len(self.captures.get('/api/v1/captures',headers=self.headers()).json()['captures']),1)

    def test_capture_unacked_tail_recovery_preserves_acked_prefix(self):
        i=self.create().json()['capture_id'];self.chunk(i,0)
        with(self.store.captures/i/'data.bin').open('ab')as f:f.write(b'unacked-tail')
        self.assertEqual(self.chunk(i,1,b'def').status_code,200)
        self.assertTrue(self.commit(i,b'abcdef',2).json()['committed'])

    def test_capture_corrupt_acknowledged_data_never_commits(self):
        i=self.create().json()['capture_id'];self.chunk(i,0);(self.store.captures/i/'data.bin').write_bytes(b'bad')
        self.assertEqual(self.commit(i).status_code,503)
        self.assertFalse(self.captures.get('/api/v1/captures/'+i,headers=self.headers()).json()['committed'])

    def test_capture_path_query_chunk_bounds(self):
        i=self.create().json()['capture_id']
        self.assertEqual(self.captures.get('/api/v1/captures/not-a-uuid',headers=self.headers()).status_code,400)
        self.assertEqual(self.captures.get('/api/v1/captures/'+i+'?owner=x',headers=self.headers()).status_code,400)
        self.assertEqual(self.chunk(i,0,b'x'*(m.MAX_CHUNK+1)).status_code,413)

    def test_main_integration_is_only_four_reviewed_additions(self):
        base=W/'baseline/main.py'
        if not base.is_file():base=W.parent/'m04-update-as-s07-20261105/hub-server-source/main.py'
        baseline=base.read_bytes()
        candidate=(W/'candidate/bmi30hub/main.py').read_bytes();eol=b'\r\n'if b'\r\n'in baseline else b'\n'
        lines=[b'from .central_store import CentralStore, build_central_router, system_scope_resolver',
          b'CENTRAL_STORE = CentralStore(SETTINGS.root)',b'    CENTRAL_STORE.initialize_optional()',
          b'app.include_router(build_central_router(CENTRAL_STORE, FIRMWARE_STORAGE.authenticate_device, system_scope_resolver(DB), require_https=True, enable_captures=False))']
        for line in lines:
            self.assertEqual(candidate.count(line+eol),1);candidate=candidate.replace(line+eol,b'')
        self.assertEqual(candidate,baseline)
        tree=ast.parse((W/'candidate/bmi30hub/main.py').read_text())
        self.assertTrue(any(isinstance(n,ast.Name)and n.id=='FIRMWARE_STORAGE'for n in ast.walk(tree)))

    def test_init_cli_wrong_service_identity_never_writes(self):
        target=self.root/'cli-must-not-create'
        result=subprocess.run([sys.executable,str(SOURCE),'--root',str(target),'--init-only'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertFalse(target.exists())
        self.assertEqual(json.loads(result.stdout)['code'],'hub_service_identity_required')


if __name__=='__main__':unittest.main(verbosity=2)

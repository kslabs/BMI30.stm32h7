"""Independent in-process HTTP/AESGCM checks; no server/network/device access."""
import copy
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

W=Path(__file__).resolve().parent
sys.dont_write_bytecode=True
WORKSPACE=next(p for p in (W,*W.parents) if (p/'HostTools').is_dir()and(p/'.tmp').is_dir())
FIXTURES=WORKSPACE/'.tmp/hub-central-independent-fixtures-20261105'
assert not FIXTURES.is_symlink()and FIXTURES.resolve().parent==(WORKSPACE/'.tmp').resolve()
FIXTURES.mkdir(exist_ok=True)
SOURCE=W/'candidate/bmi30hub/central_store.py'
spec=importlib.util.spec_from_file_location('independent_central',SOURCE)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
A='BMI30-0000000000000001';B='BMI30-0000000000000002';C='BMI30-0000000000000003'
SECRET='independent-fixture-password'

class Independent(unittest.TestCase):
    def setUp(self):
        self.root=FIXTURES/('independent-fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.db=sqlite3.connect(':memory:',check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE devices(device_id TEXT,state TEXT,system_id INTEGER);CREATE TABLE systems(id INTEGER,active INTEGER);INSERT INTO systems VALUES(1,1),(2,1);')
        self.db.executemany('INSERT INTO devices VALUES(?,?,?)',[(A,'approved',1),(B,'approved',1),(C,'approved',2)])
        class DB:
            def fetchone(inner,sql,params):return self.db.execute(sql,params).fetchone()
        self.resolve=m.system_scope_resolver(DB())
        def auth(token):
            if token=='a'*48:return A
            if token=='b'*48:return B
            if token=='c'*48:return C
            raise HTTPException(403,detail=SECRET)
        self.store=m.CentralStore(self.root);self.store.ensure_layout()
        app=FastAPI();app.include_router(m.build_central_router(self.store,auth,self.resolve))
        self.app=app;self.client=TestClient(app,base_url='https://independent.invalid')
    def tearDown(self):
        self.client.close();self.db.close()
        assert self.root.resolve().parent==FIXTURES.resolve()and self.root.name.startswith('independent-fixture-')
        shutil.rmtree(self.root)
    def headers(self,who='a'):return {'Authorization':'Bearer '+who*48}
    def get(self,who='a',path='/api/v1/wifi/catalog'):
        return self.client.get(path,headers=self.headers(who))
    def record(self,ssid='Shared',psk=SECRET,revision=9):
        return {'ssid':ssid,'psk':psk,'security':'wpa-psk','origin':A,'revision':revision,'updated':1.0}
    def merge(self,records,who='a',**kwargs):
        return self.client.post('/api/v1/wifi/catalog/merge',headers=self.headers(who),json={'records':records,**kwargs})
    def seed(self):
        response=self.merge({'wifi:Shared':self.record()});self.assertEqual(response.status_code,200);return response.json()['revision']
    def test_real_scope_resolver_revokes_next_request_and_prevents_tenant_read(self):
        self.seed();self.assertEqual(self.get('b').json()['records']['wifi:Shared']['psk'],SECRET)
        self.assertEqual(self.get('c').json()['records'],{})
        self.db.execute('UPDATE systems SET active=0 WHERE id=1');self.db.commit()
        response=self.get('b');self.assertEqual(response.status_code,403);self.assertNotIn(SECRET,response.text)
        self.db.execute('UPDATE systems SET active=1 WHERE id=1');self.db.execute('UPDATE devices SET state="pending" WHERE device_id=?',(A,));self.db.commit()
        self.assertEqual(self.get().status_code,403)
    def test_aad_rejects_ciphertext_transplant_between_systems(self):
        self.seed();self.merge({'wifi:Shared':self.record(psk='other-system-password')},'c')
        data=json.loads(self.store.catalog.read_bytes());data['scopes']['system:2']['records']['wifi:Shared']=copy.deepcopy(data['scopes']['system:1']['records']['wifi:Shared'])
        self.store.catalog.write_bytes(m.encoded(data));response=self.get('c')
        self.assertEqual(response.status_code,503);self.assertNotIn(SECRET,response.text)
        self.assertEqual(self.get().json()['records']['wifi:Shared']['psk'],SECRET)
    def test_key_and_nonce_security_with_provenance_upgrade(self):
        revision=self.seed();key=self.store.key.read_bytes();data=json.loads(self.store.catalog.read_bytes());old=data['scopes']['system:1']['records']['wifi:Shared']['psk']
        self.assertEqual(len(key),32);self.assertNotIn(SECRET.encode(),self.store.catalog.read_bytes())
        evidence={'source':'active_station_server_reachable','verification':'client_reported_connected','client_observed_at':time.time()}
        response=self.merge({'wifi:Shared':self.record()},expected_revision=revision,provenance={'wifi:Shared':evidence})
        self.assertEqual(response.status_code,200);data=json.loads(self.store.catalog.read_bytes());new=data['scopes']['system:1']['records']['wifi:Shared']['psk']
        self.assertNotEqual(old['nonce'],new['nonce']);self.assertEqual(self.store.key.read_bytes(),key)
        self.assertEqual(set(self.get().json()['records']['wifi:Shared']),m.RECORD_FIELDS)
        self.store.key.write_bytes(bytes(v^1 for v in key));response=self.get();self.assertEqual(response.status_code,503);self.assertNotIn(SECRET,response.text)
    def test_replaced_valid_key_cannot_append_a_new_scope_or_mix_keys(self):
        self.seed();before=self.store.catalog.read_bytes();key=self.store.key.read_bytes()
        self.store.key.write_bytes(bytes(v^1 for v in key))
        response=self.merge({'wifi:New Scope':self.record('New Scope')},'c')
        self.assertEqual(response.status_code,503);self.assertEqual(self.store.catalog.read_bytes(),before)
    def test_concurrent_CAS_has_single_winner(self):
        revision=self.seed();barrier=threading.Barrier(2)
        def replace(value):
            with TestClient(self.app,base_url='https://independent.invalid')as client:
                barrier.wait();return client.post('/api/v1/wifi/catalog/merge',headers=self.headers(),json={'expected_revision':revision,'records':{'wifi:Shared':self.record(psk=value)},'explicit_replacements':['wifi:Shared']}).status_code
        with ThreadPoolExecutor(2)as pool:results=list(pool.map(replace,['first-new-password','second-new-password']))
        self.assertEqual(sorted(results),[200,409]);self.assertEqual(self.get().json()['revision'],revision+1)
    def test_multirecord_validation_is_atomic_and_error_does_not_echo(self):
        before=self.store.catalog.read_bytes();bad=self.record('Bad',psk='x')
        response=self.merge({'wifi:Good':self.record('Good'),'wifi:Bad':bad})
        self.assertEqual(response.status_code,400);self.assertEqual(self.store.catalog.read_bytes(),before);self.assertNotIn(SECRET,response.text)
    def test_strong_choice_stale_provenance_and_revision_overflow_boundary(self):
        revision=self.seed();before=self.store.catalog.read_bytes();changed=self.record(psk='new-fixture-password',revision=2**53-1)
        self.assertEqual(self.merge({'wifi:Shared':changed},expected_revision=revision).status_code,409)
        proof={'source':'active_station_server_reachable','verification':'client_reported_connected','client_observed_at':time.time()-120}
        self.assertEqual(self.merge({'wifi:Shared':changed},expected_revision=revision,provenance={'wifi:Shared':proof}).status_code,400)
        self.assertEqual(before,self.store.catalog.read_bytes())
        response=self.merge({'wifi:Shared':changed},expected_revision=revision,explicit_replacements=['wifi:Shared']);self.assertEqual(response.status_code,200);self.assertEqual(response.json()['revision'],revision+1)
        response=self.merge({'wifi:Next':self.record('Next',revision=2**53-1)},expected_revision=revision+1);self.assertEqual(response.status_code,200);self.assertEqual(response.json()['revision'],revision+2)
    def test_owner_canonical_origin_cannot_be_forged(self):
        record=self.record();record['origin']=C;self.merge({'wifi:Shared':record})
        data=self.get().json();self.assertEqual(data['records']['wifi:Shared']['origin'],A);self.assertEqual(data['provenance']['wifi:Shared']['client_record_origin'],C)
    def test_auth_HTTP_query_and_disabled_capture_routes_fail_closed(self):
        self.seed();response=self.get('x');self.assertEqual(response.status_code,403);self.assertNotIn(SECRET,response.text)
        self.assertEqual(self.client.get('/api/v1/wifi/catalog').status_code,401)
        self.assertEqual(self.client.get('http://independent.invalid/api/v1/wifi/catalog',headers=self.headers()).status_code,426)
        self.assertEqual(self.get(path='/api/v1/wifi/catalog?scope=system:2').status_code,400)
        self.assertEqual(self.get(path='/api/v1/captures').status_code,404)
        self.assertEqual(self.get().headers['cache-control'],'no-store')
    def test_duplicate_keys_and_oversized_body_do_not_mutate(self):
        before=self.store.catalog.read_bytes();headers={**self.headers(),'Content-Type':'application/json'}
        response=self.client.post('/api/v1/wifi/catalog/merge',headers=headers,content=b'{"records":{},"records":{}}');self.assertEqual(response.status_code,400)
        response=self.client.post('/api/v1/wifi/catalog/merge',headers=headers,content=b' '*65537);self.assertEqual(response.status_code,413);self.assertEqual(before,self.store.catalog.read_bytes())
    def test_init_only_refuses_existing_key_and_catalog_without_changes(self):
        key=self.store.key.read_bytes();catalog=self.store.catalog.read_bytes()
        with self.assertRaises(m.StoreError)as ctx:self.store.init_only()
        self.assertEqual(ctx.exception.status,409);self.assertEqual(key,self.store.key.read_bytes());self.assertEqual(catalog,self.store.catalog.read_bytes())
    def test_actual_existing_Hub_auth_callback_is_reused_and_ambiguous_tokens_deny(self):
        source=WORKSPACE/'.tmp/m04-update-as-s07-20261105/hub-server-source/firmware.py'
        tree=ast.parse(source.read_text(encoding='utf8'))
        error=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='FirmwareError')
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='FirmwareStorage')
        auth=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='authenticate_device')
        import hmac
        ns={'token_hash':lambda token:hashlib.sha256(token.encode()).hexdigest(),'constant_time_equal':hmac.compare_digest}
        exec(compile(ast.Module(body=[error,auth],type_ignores=[]),'<actual-r36-auth>','exec'),ns)
        self.db.execute('ALTER TABLE devices ADD COLUMN api_token_hash TEXT')
        self.db.execute('UPDATE devices SET api_token_hash=? WHERE device_id=?',(hashlib.sha256(('a'*48).encode()).hexdigest(),A));self.db.commit()
        class DB:
            def fetchall(inner,sql,params=()):return self.db.execute(sql,params).fetchall()
        from types import SimpleNamespace
        storage=SimpleNamespace(database=DB())
        app=FastAPI();app.include_router(m.build_central_router(self.store,lambda token:ns['authenticate_device'](storage,token),self.resolve))
        with TestClient(app,base_url='https://independent.invalid')as client:
            self.assertEqual(client.get('/api/v1/wifi/catalog',headers=self.headers()).status_code,200)
            self.assertEqual(client.get('/api/v1/wifi/catalog',headers=self.headers('x')).status_code,403)
            self.db.execute('UPDATE devices SET api_token_hash=(SELECT api_token_hash FROM devices WHERE device_id=?) WHERE device_id=?',(A,B));self.db.commit()
            response=client.get('/api/v1/wifi/catalog',headers=self.headers());self.assertEqual(response.status_code,403);self.assertNotIn(SECRET,response.text)

if __name__=='__main__':unittest.main(verbosity=2)

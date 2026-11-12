import ast
import copy
import http.client
import http.server
import json
import pathlib
import threading
import types
import unittest
from http import HTTPStatus
from unittest.mock import patch
from urllib.parse import urlsplit
import wifi_profiles as module
from patch_portal import patch as usb_patch
from patch_wifi_profiles import patch as wifi_patch

HERE=pathlib.Path(__file__).parent
ROOT=HERE.parents[1]
ID='11111111-2222-3333-4444-555555555555'

def settings(identity=ID,mode='infrastructure',security='wpa-psk'):
    return {'connection':{'type':'802-11-wireless','id':'guest profile','uuid':identity,'autoconnect':True},
            '802-11-wireless':{'ssid':b'invite','mode':mode},
            '802-11-wireless-security':{'key-mgmt':security}}

class Manager:
    def __init__(self):self.data={'/profile':settings(),'/ap':settings(mode='ap')};self.reads=[]
    def active(self):return {ID}
    def connections(self):return list(self.data)
    def settings(self,path):return self.data[path]
    def secrets(self,path):self.reads.append(path);return {'802-11-wireless-security':{'psk':'fixture-secret-only'}}

class Profiles(unittest.TestCase):
    def test_list_never_reads_or_returns_secrets_and_skips_own_ap(self):
        nm=Manager();data=module.saved_profiles(nm)
        self.assertEqual(nm.reads,[]);self.assertEqual(len(data['networks']),1)
        self.assertEqual(data['networks'][0]['ssid'],'invite');self.assertTrue(data['networks'][0]['active'])
        self.assertNotIn('password',data['networks'][0]);self.assertNotIn('fixture-secret',json.dumps(data))
    def test_only_selected_uuid_reveals_password_and_preserves_special_characters(self):
        nm=Manager();nm.data['/profile']['802-11-wireless']['ssid']='a:<script>\\☃'.encode()
        value=module.saved_profiles(nm,reveal=ID)
        self.assertEqual(nm.reads,['/profile']);self.assertEqual(value['password'],'fixture-secret-only')
        self.assertEqual(value['ssid'],'a:<script>\\☃')
    def test_invalid_or_missing_uuid_does_not_read_secrets(self):
        for identity in ('../other','00000000-0000-0000-0000-000000000000'):
            nm=Manager()
            with self.assertRaises(module.Unavailable):module.saved_profiles(nm,reveal=identity)
            self.assertEqual(nm.reads,[])
    def test_missing_keyring_secret_is_reported_unavailable(self):
        nm=Manager();nm.secrets=lambda path:{}
        self.assertFalse(module.saved_profiles(nm,reveal=ID)['available'])
    def test_open_and_enterprise_profiles_do_not_request_a_psk(self):
        for security in ('','wpa-eap'):
            nm=Manager();nm.data['/profile']=settings(security=security)
            self.assertFalse(module.saved_profiles(nm,reveal=ID)['available']);self.assertEqual(nm.reads,[])
    def test_dbus_error_does_not_escape_into_api_error(self):
        with patch.object(module,'NetworkManager',side_effect=RuntimeError('fixture-secret-only')):
            with self.assertRaises(module.Unavailable) as error:module.dispatch('list')
        self.assertNotIn('fixture-secret',str(error.exception))

class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=usb_patch((ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py').read_text(encoding='utf-8'))
        source=wifi_patch(source);assert wifi_patch(source)==source
        tree=ast.parse(source)
        original=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HotspotInfoHandler')
        original.body=[n for n in original.body if isinstance(n,ast.FunctionDef) and n.name in
                       ('_handle_request','do_GET','do_HEAD','do_POST','_send_json_response')]
        cls.calls=[]
        def dispatch(action,data=None):
            cls.calls.append(action)
            return module.saved_profiles(Manager(),reveal=data['uuid'] if action=='password' else None)
        scope={'BaseHTTPRequestHandler':http.server.BaseHTTPRequestHandler,'HTTPStatus':HTTPStatus,'json':json,
               'urlsplit':urlsplit,'pathlib':pathlib,'bmi30_wifi_profiles_module':lambda:types.SimpleNamespace(dispatch=dispatch),
               'bmi30_usb_module':lambda:types.SimpleNamespace(MODULE=HERE)}
        origin=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='bmi30_usb_origin_allowed')
        unit=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),origin,original],type_ignores=[])
        exec(compile(ast.fix_missing_locations(unit),'isolated_wifi_portal','exec'),scope)
        class Handler(scope['HotspotInfoHandler']):
            def _get_portal_session(self):return {'user':'test'} if self.headers.get('X-Test-Session')=='1' else None
            def _require_portal_session(self):
                value=self._get_portal_session()
                if value is None:self._send_json_response({'ok':False},403)
                return value
            def log_message(self,*args):pass
        cls.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,method='GET',headers=None,body=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        c.request(method,path,body,headers or {});r=c.getresponse();result=r.status,r.read(),r.headers;c.close();return result
    def test_anonymous_page_list_and_reveal_are_forbidden(self):
        for path in ('/portal-wifi-profiles','/api/wifi-profiles'):
            self.assertEqual(self.request(path)[0],403)
        self.assertEqual(self.request('/api/wifi-profiles/password','POST',body='{}')[0],403)
    def test_authenticated_list_contains_no_password_and_is_not_cached(self):
        status,body,headers=self.request('/api/wifi-profiles',headers={'X-Test-Session':'1'})
        self.assertEqual(status,200);self.assertNotIn(b'fixture-secret',body);self.assertIn('no-store',headers['Cache-Control'])
    def test_reveal_requires_custom_header_and_same_origin(self):
        for headers in ({'X-Test-Session':'1'},{'X-Test-Session':'1','X-BMI30-WIFI':'1','Origin':'https://foreign.invalid'}):
            self.assertEqual(self.request('/api/wifi-profiles/password','POST',headers,json.dumps({'uuid':ID}))[0],403)
        headers={'X-Test-Session':'1','X-BMI30-WIFI':'1','Content-Type':'application/json'}
        status,body,response_headers=self.request('/api/wifi-profiles/password','POST',headers,json.dumps({'uuid':ID}))
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['password'],'fixture-secret-only')
        self.assertIn('no-store',response_headers['Cache-Control'])
        self.assertEqual(self.request('/api/wifi-profiles/password','POST',headers,'x'*5000)[0],409)

if __name__=='__main__':unittest.main()

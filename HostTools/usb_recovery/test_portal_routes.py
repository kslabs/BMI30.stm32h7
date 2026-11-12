"""Execute the patched handler in an isolated HTTP server, without device writes."""
import ast,http.client,http.server,json,pathlib,threading,types,unittest
from http import HTTPStatus
from urllib.parse import urlsplit
from patch_portal import patch

ROOT=pathlib.Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py'

class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse(patch(SOURCE.read_text(encoding='utf-8')))
        original=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HotspotInfoHandler')
        original.body=[n for n in original.body if isinstance(n,ast.FunctionDef) and n.name in
                       ('_handle_request','do_GET','do_HEAD','do_POST','_send_json_response')]
        module=types.SimpleNamespace(MODULE=pathlib.Path(__file__).parent,Refused=RuntimeError)
        cls.calls=[]
        def dispatch(action,data=None):cls.calls.append(action);return {'ok':True,'action':action}
        module.dispatch=dispatch
        scope={'BaseHTTPRequestHandler':http.server.BaseHTTPRequestHandler,'HTTPStatus':HTTPStatus,
               'json':json,'urlsplit':urlsplit,'pathlib':pathlib,'bmi30_usb_module':lambda:module}
        origin=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='bmi30_usb_origin_allowed')
        unit=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),origin,original],type_ignores=[])
        exec(compile(ast.fix_missing_locations(unit),'isolated_usb_portal','exec'),scope)
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
        c.request(method,path,body,headers or {});r=c.getresponse();result=r.status,r.read();c.close();return result
    def test_unauthenticated_status_and_page_are_refused(self):
        for path in ('/api/usb-recovery/status','/portal-recovery'):self.assertEqual(self.request(path)[0],403)
    def test_authenticated_page_and_status_work(self):
        status,body=self.request('/portal-recovery',headers={'X-Test-Session':'1'})
        self.assertEqual(status,200);self.assertIn('Аварийная USB-система'.encode(),body)
        self.assertEqual(self.request('/api/usb-recovery/status',headers={'X-Test-Session':'1'})[0],200)
    def test_start_requires_session_custom_header_and_matching_origin(self):
        for headers in ({},{'X-Test-Session':'1'},
            {'X-Test-Session':'1','X-BMI30-USB':'1','Origin':'https://foreign.invalid'}):
            self.assertEqual(self.request('/api/usb-recovery/start','POST',headers,'{}')[0],403)
    def test_valid_plan_dispatch_and_oversized_body(self):
        headers={'X-Test-Session':'1','X-BMI30-USB':'1','Content-Type':'application/json'}
        self.assertEqual(self.request('/api/usb-recovery/plan','POST',headers,'{}')[0],200)
        self.assertEqual(self.request('/api/usb-recovery/start','POST',headers,'x'*5000)[0],409)

if __name__=='__main__':unittest.main()

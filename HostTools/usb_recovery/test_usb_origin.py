import json,pathlib,types,unittest
from urllib.parse import urlsplit
from patch_portal import ORIGIN_HELPER,patch
ROOT=pathlib.Path(__file__).resolve().parents[2]

class UsbOriginTests(unittest.TestCase):
    def allowed(self,origin,host,peer='127.0.0.1',server='https://www.teiots.net/bmi30'):
        path=types.SimpleNamespace(read_text=lambda:json.dumps({'server_url':server}))
        scope={'pathlib':types.SimpleNamespace(Path=lambda _:path),'json':json,'urlsplit':urlsplit}
        exec(ORIGIN_HELPER,scope)
        handler=types.SimpleNamespace(headers={'Origin':origin,'Host':host},client_address=(peer,1))
        return scope['bmi30_usb_origin_allowed'](handler)
    def test_direct_offline_lan_request_does_not_require_agent_server_config(self):
        self.assertTrue(self.allowed('http://10.42.0.1','10.42.0.1',peer='10.42.0.2',server=''))
    def test_cloud_request_with_local_host_rewrite_is_allowed_only_from_loopback(self):
        self.assertTrue(self.allowed('https://www.teiots.net','127.0.0.1:80'))
        self.assertTrue(self.allowed('https://teiots.net','localhost:80'))
        self.assertFalse(self.allowed('https://www.teiots.net','127.0.0.1:80',peer='192.168.1.20'))
    def test_foreign_site_null_origin_http_and_wrong_port_are_rejected(self):
        for origin in ('https://attacker.test','null','http://www.teiots.net','https://www.teiots.net:444'):
            self.assertFalse(self.allowed(origin,'127.0.0.1:80'),origin)
    def test_earlier_patched_portal_is_upgraded_once(self):
        old=(ROOT/'FirmwareReleases/RaspberryPi/2026-10-28-142027/review/bundle/project/hotspot_info_server.py').read_text(encoding='utf-8')
        new=patch(old);self.assertEqual(new,patch(new))
        self.assertEqual(new.count('def bmi30_usb_origin_allowed('),1)

if __name__=='__main__':unittest.main()

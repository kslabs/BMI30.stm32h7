import json,threading,types,unittest
from unittest.mock import Mock
from patch_group_status import READ

class LocalInternetTests(unittest.TestCase):
    def read(self,local,nodes=(),elapsed=1):
        state={'clock_mode':'monotonic-v2','checked_mono':100,'local':local,'nodes':list(nodes)}
        path=Mock();path.stat.return_value.st_size=512;path.read_text.return_value=json.dumps(state)
        root=Mock();root.__truediv__=Mock(return_value=path)
        context={'json':json,'pathlib':types.SimpleNamespace(Path=lambda p:root),'time':types.SimpleNamespace(monotonic=lambda:100+elapsed)}
        exec(READ,context);return context['read_bmi30_group_file']('status.json',60)
    def test_direct_local_probe_survives_missing_own_network_record(self):
        s=self.read({'id':'BMI30-S07','internet':{'ok':True,'rtt_ms':77},'private_password':'never-export'})
        self.assertEqual(s['local']['internet']['ok'],True)
        self.assertEqual(s['local']['age_s'],1);self.assertNotIn('private_password',s['local'])
    def test_direct_probe_replaces_an_old_reflected_own_record(self):
        s=self.read({'id':'BMI30-S07','internet':{'ok':True}},[{'id':'BMI30-S07','internet':{'ok':False},'age_s':2}])
        self.assertEqual(len(s['nodes']),1);self.assertTrue(s['local']['internet']['ok'])
    def test_old_status_file_is_not_reported_as_current_internet(self):
        self.assertFalse(self.read({'id':'BMI30-S07','internet':{'ok':True}},elapsed=80)['available'])

if __name__=='__main__':unittest.main()

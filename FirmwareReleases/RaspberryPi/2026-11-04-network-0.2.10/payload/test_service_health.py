"""Execute exact candidate methods offline; never import/start the server."""
import ast
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'HostTools/bmi30_network'))
import network
import policy

SOURCE=Path(__file__).with_name('service.py')


def methods():
    tree=ast.parse(SOURCE.read_text(encoding='utf8'));agent=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Agent')
    wanted={'tick','route','shared_credentials','note_tick_error'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='routing_status']
    nodes.extend(n for n in agent.body if isinstance(n,ast.FunctionDef) and n.name in wanted)
    clock=SimpleNamespace(time=lambda:2000.,monotonic=lambda:1000.)
    net=SimpleNamespace(connection_uuid=network.connection_uuid,probe=Mock(return_value={'ok':True,'rtt_ms':20,'loss':0}),nm_value=Mock(return_value='test-secret'),atomic=Mock())
    cohesion=SimpleNamespace(observe=Mock(return_value={'cohesion':{'available':False,'reason':'test_unknown'},'root_lan':None}),inherit_root=Mock(return_value=None))
    ns={'time':clock,'network':net,'json':json,'math':math,'ROUTING_STALE_S':75.,'policy':policy,'VERSION':'0.2.10','cohesion':cohesion,
        'access_policy':SimpleNamespace(maintenance=lambda:False,offline_mode=lambda:False,forgotten=lambda ssid:False)}
    future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future,*nodes],type_ignores=[])),str(SOURCE),'exec'),ns)
    return ns


def agent(ns,ap=False):
    local={'id':'BMI30-LOCAL','ssid':'Home','bssid':'aa:bb:cc:dd:ee:ff','freq':5180,'signal':70,'uplink':'wifi','iface':'wlan0',
           'clients':0,'station_uuid':'300d845c-eef7-5cbd-aa60-d0cc558e0e7e','ap_uuid':'97d67fea-4a00-5c55-8e3f-bac0a10533a6' if ap else '--',
           'ap':{'ssid':'BMI30-LOCAL' if ap else '','bssid':'11:22:33:44:55:66' if ap else '','address':'10.70.1.1/24'},
           '_cohesion_observation':{'addr_rows':[{'ifname':'wlan0'}],'neighbor_rows':[],'core':{'ok':True}}}
    a=SimpleNamespace(config={'id':'BMI30-LOCAL','automatic':True},network=SimpleNamespace(permissions=lambda:{'wifi':True},snapshot=lambda:deepcopy(local),visible=lambda **kw:[],remote_desktop_active=lambda:False),
       lock=threading.RLock(),records=SimpleNamespace(nodes=lambda:[]),profile_cache={'Home':{'ssid':'Home','uuid':local['station_uuid']}},
       last_profiles=1000.,profile_station_uuid=local['station_uuid'],last_scan=1000.,hold_until=0.,session_start=0.,offline=0,
       secrets={'ap:BMI30-LOCAL':{'ssid':'BMI30-LOCAL','psk':'test-secret'},'ap:BMI30-OTHER':{'ssid':'BMI30-OTHER'},'wifi:Home':{'ssid':'Home'}},
       local=None,state={'status':'online','checked_mono':900.,'checked_at':1900.,'clock_mode':'monotonic-v2'},
       own_secret=Mock(),gossip=Mock(),decision=SimpleNamespace(choose=lambda *args:None),history=[],bluetooth=SimpleNamespace(snapshot=lambda:{}),legacy_recovery=SimpleNamespace(active=SimpleNamespace(maybe_visit=lambda local:False)))
    a.publish=Mock(side_effect=lambda value:setattr(a,'local',value));a.router_secret=lambda ssid:{'ssid':ssid}
    for name in ('tick','route','shared_credentials','note_tick_error'):setattr(a,name,MethodType(ns[name],a))
    return a


class CandidateHealthTests(unittest.TestCase):
    def test_missing_ap_keeps_observation_fresh_and_never_queries_missing_profile_secret(self):
        ns=methods();a=agent(ns);a.tick()
        ns['network'].nm_value.assert_not_called()
        self.assertEqual(a.state['status'],'degraded');self.assertFalse(a.state['automatic'])
        self.assertEqual(a.state['checked_mono'],1000.);self.assertFalse(a.state['stale']);self.assertTrue(a.state['routing_tick_ok'])
        self.assertTrue(a.local['internet']['ok']);self.assertFalse(a.local['ap']['available'])
        self.assertNotIn('_cohesion_observation',a.local)
        self.assertTrue(ns['cohesion'].observe.call_args.args[0]['internet']['ok'])
        self.assertNotIn('ap:BMI30-LOCAL',a.shared_credentials())
        self.assertIn('ap:BMI30-LOCAL',a.secrets)  # repair credential stays stored
        self.assertIn('ap:BMI30-OTHER',a.shared_credentials());self.assertIn('wifi:Home',a.shared_credentials())
        self.assertNotIn('test-secret',json.dumps(a.state));self.assertNotIn('"psk"',json.dumps(a.state))

    def test_healthy_ap_path_still_reads_and_advertises_only_its_known_profile(self):
        ns=methods();a=agent(ns,ap=True);a.tick()
        ns['network'].nm_value.assert_called_once_with('97d67fea-4a00-5c55-8e3f-bac0a10533a6','802-11-wireless-security.psk',secrets=True)
        self.assertEqual(a.state['status'],'online');self.assertTrue(a.state['automatic'])
        self.assertIn('ap:BMI30-LOCAL',a.shared_credentials())
        self.assertNotIn('test-secret',json.dumps(a.state));self.assertNotIn('"psk"',json.dumps(a.state))

    def test_valid_uuid_and_ssid_are_insufficient_when_observed_interface_is_not_ap(self):
        ns=methods();a=agent(ns,ap=True);snapshot=a.network.snapshot();snapshot['ap']['available']=False;a.network.snapshot=lambda:snapshot
        a.tick();ns['network'].nm_value.assert_not_called()
        self.assertFalse(a.local['ap']['available']);self.assertEqual(a.state['status'],'degraded')

    def test_tick_error_never_relabels_old_observation_as_fresh_or_exposes_error_text(self):
        ns=methods();a=agent(ns);original=deepcopy(a.state)
        a.note_tick_error(RuntimeError('private credential text must not appear'))
        self.assertEqual(a.state['checked_at'],original['checked_at']);self.assertEqual(a.state['checked_mono'],original['checked_mono'])
        self.assertEqual(a.state['status'],'degraded');self.assertTrue(a.state['stale']);self.assertFalse(a.state['automatic'])
        self.assertEqual(a.state['routing_error_type'],'RuntimeError')
        written=json.loads(ns['network'].atomic.call_args.args[1]);self.assertNotIn('private credential',json.dumps(written))
        a.publish.assert_not_called();a.gossip.assert_not_called()

    def test_status_http_freshness_is_monotonic_and_does_not_mutate_group_state(self):
        ns=methods();group={'available':True,'age_s':.2};state={'status':'online','automatic':True,'checked_mono':10.,'checked_at':1234.,'clock_mode':'monotonic-v2','service_transports':group}
        out=ns['routing_status'](state,now_mono=100.,now_wall=-99999.)
        self.assertTrue(out['stale']);self.assertEqual(out['checked_age_s'],90.);self.assertEqual(out['status'],'degraded')
        self.assertEqual(state['status'],'online');self.assertEqual(group,{'available':True,'age_s':.2})
        fresh=ns['routing_status'](state,now_mono=20.,now_wall=999999.)
        self.assertFalse(fresh['stale']);self.assertEqual(fresh['status'],'online')

    def test_relay_root_is_inherited_only_after_existing_path_validation(self):
        ns=methods();a=agent(ns);local={'id':'BMI30-LOCAL','ssid':'BMI30-PARENT','bssid':'00:11:22:33:44:55','uplink':'wifi','internet':{'ok':True},'freq':5180,'signal':60}
        parent={'id':'BMI30-PARENT','ap':{'bssid':local['bssid']},'internet':{'ok':True},'path':['BMI30-PARENT'],'cost':60}
        a.route(local,[parent]);ns['cohesion'].inherit_root.assert_called_once();self.assertEqual(local['parent'],'BMI30-PARENT')
        ns['cohesion'].inherit_root.reset_mock();parent['path']=['BMI30-PARENT','BMI30-LOCAL']
        a.route(local,[parent]);ns['cohesion'].inherit_root.assert_not_called()

    def test_legacy_and_group_features_outside_reviewed_scope_are_preserved(self):
        old=ast.parse(SOURCE.parents[1].joinpath('active_m02_service.py').read_text(encoding='utf8'));new=ast.parse(SOURCE.read_text(encoding='utf8'))
        old_defs={n.name:ast.dump(n,include_attributes=False) for n in old.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))}
        new_defs={n.name:ast.dump(n,include_attributes=False) for n in new.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))}
        changed={'Agent','serve','main'}
        for name,node in old_defs.items():
            if name not in changed:self.assertEqual(node,new_defs[name],name)
        for filename in ('service.py','service_s07.py'):
            tree=ast.parse(SOURCE.with_name(filename).read_text(encoding='utf8'))
            version=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
            self.assertEqual(version,'0.2.10')


if __name__=='__main__':unittest.main()

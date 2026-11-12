"""AP/STA UUID/file separation and checkpoint/rollback order, without NM I/O."""
import unittest
import io
import json
from unittest.mock import MagicMock, Mock, patch
import uuid

import network

AP = '97d67fea-4a00-5c55-8e3f-bac0a10533a6'
STA = '300d845c-eef7-5cbd-aa60-d0cc558e0e7e'
CHOICE = {'ssid':'BMI30-PARENT','bssid':'2e:cf:67:50:12:42','freq':5180,'channel':36}


class ProfileRoleGuardTests(unittest.TestCase):
    def adapter(self):
        return network.Network({'id':'BMI30-E29805C7C'})

    def ident(self):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS,'BMI30-E29805C7C:sta:'+CHOICE['bssid']))

    def test_new_role_filename_and_uuid_cannot_reuse_the_actual_legacy_ap_alias(self):
        legacy = str(uuid.uuid5(uuid.NAMESPACE_DNS,'BMI30-E29805C7C:bmi30-net-2ecf67501242'))
        self.assertEqual(legacy,AP)
        a=self.adapter()
        with patch.object(a,'active',return_value=AP),patch('network.run',return_value=AP) as run,patch('network.atomic') as write:
            ident=a.profile(CHOICE,{'psk':'test-secret'},protected_ap_uuid=AP)
        self.assertEqual(ident,self.ident());self.assertNotEqual(ident,AP)
        self.assertEqual(write.call_args.args[0].name,'bmi30-net-sta-2ecf67501242.nmconnection')
        self.assertIn('uuid='+ident,write.call_args.args[1])
        self.assertEqual(run.call_args.args[:3],('nmcli','con','load'))

    def test_prospective_uuid_equal_to_active_ap_refuses_before_any_write_or_load(self):
        a=self.adapter()
        with patch.object(a,'active',return_value=self.ident()),patch('network.run') as run,patch('network.atomic') as write:
            with self.assertRaisesRegex(RuntimeError,'aliases'):a.profile(CHOICE,{'psk':'test-secret'})
        run.assert_not_called();write.assert_not_called()

    def test_existing_target_file_ap_cannot_be_overwritten_even_with_a_new_sta_uuid(self):
        a=self.adapter()
        target=MagicMock();target.is_symlink.return_value=False;target.exists.return_value=True
        root=MagicMock();root.__truediv__.return_value=target
        fields={('connection','id'):'BMI30-LOCAL',('connection','uuid'):AP,
                ('connection','interface-name'):'wlan0ap',('wifi','mode'):'ap'}
        parser=Mock();parser.get.side_effect=lambda section,key,fallback='':fields.get((section,key),fallback)
        with patch('network.Path',return_value=root),patch('network.configparser.ConfigParser',return_value=parser),patch.object(a,'active',return_value=AP),patch('network.run',return_value='') as run,patch('network.atomic') as write:
            with self.assertRaisesRegex(RuntimeError,'not the owned station'):a.profile(CHOICE,{'psk':'test-secret'})
        parser.read.assert_called_once_with(target)
        write.assert_not_called();self.assertFalse(any('load' in call.args for call in run.call_args_list))

    def test_existing_loaded_target_with_nonstation_role_is_not_modified(self):
        a=self.adapter();role={'connection.id':'oldAP','connection.uuid':self.ident(),'connection.interface-name':'wlan0ap','802-11-wireless.mode':'ap'}
        with patch.object(a,'active',return_value=AP),patch('network.run',return_value=self.ident()) as run,patch('network.nm_fields',return_value=role),patch('network.atomic') as write:
            with self.assertRaisesRegex(RuntimeError,'not the owned station'):a.profile(CHOICE,{'psk':'test-secret'})
        write.assert_not_called();self.assertFalse(any('load' in call.args for call in run.call_args_list))

    def test_checkpoint_precedes_profile_mutation_and_profile_failure_rolls_back(self):
        a=self.adapter();order=[];checkpoint=Mock()
        def checkpoint_create(*args):order.append('checkpoint');return checkpoint
        def profile(*args,**kwargs):order.append('profile');raise RuntimeError('refused')
        with patch.object(a,'snapshot',return_value={'ap_uuid':AP,'station_uuid':STA}),patch('network.nm_fields',return_value={'802-11-wireless.mode':'ap','connection.interface-name':'wlan0ap'}),patch('network.Checkpoint',side_effect=checkpoint_create),patch.object(a,'profile',side_effect=profile),patch.object(a,'wait_restored'):
            with self.assertRaisesRegex(RuntimeError,'refused'):a.activate(CHOICE,{'psk':'test-secret'})
        self.assertEqual(order,['checkpoint','profile']);checkpoint.rollback.assert_called_once()

    def test_failed_checkpoint_and_missing_ap_never_mutate_a_profile(self):
        for old in ({'ap_uuid':'--','station_uuid':STA},{'ap_uuid':AP,'station_uuid':STA}):
            a=self.adapter()
            with patch.object(a,'snapshot',return_value=old),patch('network.nm_fields',return_value={'802-11-wireless.mode':'ap','connection.interface-name':'wlan0ap'}),patch('network.Checkpoint',side_effect=RuntimeError('unavailable')),patch.object(a,'profile') as profile:
                with self.assertRaises(RuntimeError):a.activate(CHOICE,{'psk':'test-secret'})
            profile.assert_not_called()

    def test_rollback_waits_for_both_station_and_original_ap(self):
        a=self.adapter()
        with patch('network.time.monotonic',side_effect=[0,0,1]),patch('network.time.sleep'),patch('network.run',return_value='connected'),patch.object(a,'active',side_effect=[STA,'--',STA,AP]) as active:
            a.wait_restored({'station_uuid':STA,'ap_uuid':AP})
        self.assertEqual(active.call_count,4)

    def test_missing_ap_is_tolerated_in_observation_not_a_fake_station_success(self):
        a=self.adapter()
        with patch('network.nm_fields',side_effect=RuntimeError('NM unavailable')):
            self.assertEqual(a.active('wlan0ap'),'--')
            with self.assertRaises(RuntimeError):a.active('wlan0')

    def test_snapshot_collects_only_readonly_cohesion_inputs_and_core_failure_is_unknown(self):
        for core_fails in (False,True):
            a=network.Network({'id':'BMI30-LOCAL','ap_address':'10.70.1.1/24'})
            portal={'sync_mode':{'device_responded':True,'value':'master','code':'M02'},'group_state':{'stm32_uid96':'3336353430325113000C002A'}}
            core={'ok':True,'connection':{'connected':True}}
            opener=Mock()
            def open_url(url,**kwargs):
                if ':8765/' in url and core_fails:raise TimeoutError('Core unavailable')
                return io.StringIO(json.dumps(core if ':8765/' in url else portal))
            opener.open.side_effect=open_url
            def run(*args,**kwargs):
                if args[:4]==('iw','dev','wlan0','link'):return 'Connected to aa:bb:cc:dd:ee:ff\nSSID: Home\nfreq: 5180\nsignal: -50'
                if args[:4]==('iw','dev','wlan0ap','info'):return 'addr 02:11:22:33:44:55\nssid BMI30-LOCAL\ntype AP'
                if args==('ip','-j','route'):return json.dumps([{'dst':'default','dev':'wlan0','gateway':'192.168.0.1'},{'dst':'10.70.1.0/24','dev':'wlan0ap','prefsrc':'10.70.1.1'}])
                if args==('ip','-j','-4','address'):return json.dumps([{'ifname':'wlan0','addr_info':[{'family':'inet','scope':'global','local':'192.168.0.154','prefixlen':24}]}])
                if args[:3]==('ip','-j','neigh'):
                    # iproute2 omits 'dev' when the command filters by dev.
                    # Keep full JSON rows; cohesion performs exact filtering.
                    self.assertEqual(args,('ip','-j','neigh','show'))
                    return json.dumps([{'dev':'wlan0','dst':'192.168.0.1','lladdr':'02:aa:bb:cc:dd:ee','state':['REACHABLE']}])
                if args[:4]==('iw','dev','wlan0ap','station'):return ''
                self.fail('Unexpected command: '+str(args))
            with patch('network.urllib.request.build_opener',return_value=opener),patch('network.run',side_effect=run),patch.object(a,'active',side_effect=lambda iface:AP if iface=='wlan0ap' else STA):
                snapshot=a.snapshot()
            self.assertTrue(snapshot['ap']['available']);self.assertNotIn('root_lan',snapshot)
            self.assertEqual(snapshot['_cohesion_observation']['core'],{} if core_fails else core)
            self.assertEqual(opener.open.call_count,2)


if __name__=='__main__':unittest.main()

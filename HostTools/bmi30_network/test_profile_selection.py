"""Duplicate saved SSIDs must not replace an active, working password."""
import unittest
from unittest.mock import patch
import network
from service import Agent

class ProfileSelection(unittest.TestCase):
    def choose(self,active,profiles):
        instance=network.Network.__new__(network.Network);instance.sta='wlan0'
        def fields(kind,ident,names,secrets=False):
            self.assertTrue(secrets)
            name,priority,password=profiles[ident]
            return {'connection.id':name,'connection.autoconnect-priority':str(priority),
                    '802-11-wireless.mode':'infrastructure','802-11-wireless.ssid':'invite',
                    '802-11-wireless-security.key-mgmt':'wpa-psk','802-11-wireless-security.psk':password}
        with patch.object(instance,'active',return_value=active),patch.object(network,'nm_fields',side_effect=fields),patch.object(
                network,'run',return_value='\n'.join(key+':802-11-wireless' for key in profiles)):
            return instance.profiles()
    def test_active_secret_wins_even_when_old_duplicate_has_higher_priority(self):
        data={'working':('working',50,'correct-test-secret'),'old':('recovered',100,'incorrect-test-secret')}
        self.assertEqual(self.choose('working',data)['invite']['uuid'],'working')
    def test_verified_donor_seed_wins_over_inactive_recovered_duplicate(self):
        data={'donor':('donor',100,'correct-test-secret'),'own':('own',50,'incorrect-test-secret')}
        self.assertEqual(self.choose('elsewhere',data)['invite']['uuid'],'donor')
    def test_active_managed_connection_is_eligible_for_credential_distribution(self):
        data={'active':('bmi30-net-router',0,'correct-test-secret')}
        self.assertIn('invite',self.choose('active',data));self.assertEqual(self.choose('other',data),{})

class SharedRouterPassword(unittest.TestCase):
    def agent(self):
        agent=Agent.__new__(Agent)
        agent.profile_cache={'invite':{'ssid':'invite','psk':'old-wrong-test','uuid':'old-local'}}
        agent.secrets={'wifi:invite':{'ssid':'invite','psk':'peer-working-test','origin':'BMI30-OTHER'}}
        agent.local={'ssid':'dedicated-router','uplink':'wifi','station_uuid':'dedicated','internet':{'ok':True}}
        return agent
    def test_peer_password_is_used_instead_of_stored_wrong_password(self):
        agent=self.agent();self.assertEqual(agent.router_secret('invite')['psk'],'peer-working-test')
    def test_verified_current_station_password_wins_over_old_peer_record(self):
        agent=self.agent();agent.local.update(ssid='invite',station_uuid='old-local')
        self.assertEqual(agent.router_secret('invite')['psk'],'old-wrong-test')
    def test_failed_current_station_uses_received_password(self):
        agent=self.agent();agent.local.update(ssid='invite',station_uuid='old-local',internet={'ok':False})
        self.assertEqual(agent.router_secret('invite')['psk'],'peer-working-test')
    def test_stale_profile_cache_is_not_mistaken_for_working_station(self):
        agent=self.agent();agent.local.update(ssid='invite',station_uuid='new-corrected')
        self.assertEqual(agent.router_secret('invite')['psk'],'peer-working-test')
    def test_local_saved_profile_remains_available_without_service_peer(self):
        agent=self.agent();agent.secrets={}
        self.assertEqual(agent.router_secret('invite')['uuid'],'old-local')

if __name__=='__main__':unittest.main()

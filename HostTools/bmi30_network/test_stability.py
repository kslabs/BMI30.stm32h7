"""Regression scenarios from fleet band oscillation observed on 2026-10-05."""
import unittest

import policy


def uplink(freq=5180, signal=63, rtt=100, loss=0):
    health = {'ok': True, 'rtt_ms': rtt, 'loss': loss}
    return {'id':'BMI30-A','path':['BMI30-A'],'uplink':'wifi','clients':0,
            'ap':{'address':'10.70.1.1/24'},'bssid':'00:00:00:00:00:01',
            'freq':freq,'signal':signal,'ssid':'router-5' if freq>=4900 else 'router-24',
            'internet':health,'cost':policy.radio_cost(signal,freq)+policy.internet_cost(health)}


def radios(local, signal5=63, signal24=89):
    return [
        {'ssid':'router-5','freq':5180,'signal':signal5,
         'bssid':local['bssid'] if local['freq']>=4900 else '00:00:00:00:00:05'},
        {'ssid':'router-24','freq':2417,'signal':signal24,
         'bssid':local['bssid'] if local['freq']<4900 else '00:00:00:00:00:24'}]


def choices(local, **signals):
    return policy.candidates(local,[],radios(local,**signals),{'router-5':{},'router-24':{}})


class StabilityTests(unittest.TestCase):
    def test_unknown_wan_does_not_create_an_improvement_during_latency_spikes(self):
        # With equal reception, the old policy repeatedly preferred whichever
        # SSID had the unmeasured 15-point WAN cost, then switched back later.
        for freq in (2417,5180):
            for rtt,loss in ((100,0),(900,0),(5000,.5)):
                local=uplink(freq=freq,signal=70,rtt=rtt,loss=loss)
                ranked=choices(local,signal5=70,signal24=70)
                current=next(c for c in ranked if c['bssid']==local['bssid'])
                other=next(c for c in ranked if c['bssid']!=local['bssid'])
                # WAN spikes cannot change the relative radio score.
                self.assertAlmostEqual(abs(current['cost']-other['cost']),12)

    def test_healthy_5ghz_stays_on_band_across_repeated_probes(self):
        decision=policy.Decision()
        for i in range(90):
            local=uplink(signal=63,rtt=(100,1000,5000)[i%3],loss=(0,.5,0)[i%3])
            self.assertIsNone(decision.choose(local,choices(local),1000+i*20))

    def test_usable_5ghz_returns_after_dwell_and_persistent_observation(self):
        local=uplink(freq=2417,signal=89)
        ranked=choices(local,signal5=63)
        decision=policy.Decision()
        decision.last_switch=100
        self.assertIsNone(decision.choose(local,ranked,399))
        self.assertIsNone(decision.choose(local,ranked,400))
        self.assertIsNone(decision.choose(local,ranked,420))
        choice=decision.choose(local,ranked,440)
        self.assertEqual(choice['freq'],5180)

    def test_signal_hysteresis_and_weak_5ghz_fallback(self):
        for signal in (40,44,45,50,54):
            local=uplink(signal=signal)
            self.assertIsNone(policy.Decision(confirmations=1).choose(local,choices(local,signal5=signal),1000))
        local=uplink(signal=30)
        choice=policy.Decision(confirmations=1).choose(local,choices(local,signal5=30),1000)
        self.assertEqual(choice['freq'],2417)
        local=uplink(freq=2417,signal=89)
        self.assertIsNone(policy.Decision(confirmations=1).choose(local,choices(local,signal5=54),1000))

    def test_failed_internet_can_leave_5ghz_and_preserves_confirmation(self):
        local=uplink(signal=70)
        local['internet']['ok']=False
        ranked=choices(local,signal5=70,signal24=99)
        decision=policy.Decision()
        self.assertIsNone(decision.choose(local,ranked,1000))
        self.assertIsNone(decision.choose(local,ranked,1020))
        self.assertEqual(decision.choose(local,ranked,1040)['freq'],2417)

    def test_connected_ap_client_interrupts_pending_band_change(self):
        local=uplink(freq=2417,signal=89)
        ranked=choices(local)
        decision=policy.Decision()
        self.assertIsNone(decision.choose(local,ranked,1000))
        self.assertIsNone(decision.choose({**local,'clients':1},ranked,1020))
        self.assertIsNone(decision.choose(local,ranked,1040))
        self.assertIsNone(decision.choose(local,ranked,1060))
        self.assertEqual(decision.choose(local,ranked,1080)['freq'],5180)


if __name__=='__main__':
    unittest.main()

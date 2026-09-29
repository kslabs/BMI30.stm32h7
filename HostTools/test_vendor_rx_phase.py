import struct
import unittest
from vendor_rx_phase import decode_rx_phase, read_phase


def packet():
    raw=bytearray(112)
    struct.pack_into('<4sBBH8I',raw,0,b'RXP1',1,2,112,2,2000,3,80,0,123,456,1990)
    for ch,phase in enumerate((0,1)):
        struct.pack_into('<BBBBh5HII4H',raw,40+32*ch,
            phase,1,95,8,phase*17000,990,6000,30,30,120,1900,2,10,1000,64000,63000)
    struct.pack_into('<II',raw,104,1,2)
    return raw


class RxPhaseTests(unittest.TestCase):
    def test_independent_channels_and_clipping(self):
        result=decode_rx_phase(packet())
        self.assertEqual([c['phase_deg'] for c in result['channels']],[0,180])
        self.assertEqual(result['channels'][1]['age_ms'],100)
        self.assertEqual(result['channels'][0]['roi_clipped_fraction'],.01)
        self.assertEqual(result['channels'][0]['clipped_fraction'],.12)

    def test_torn_short_wrong_version(self):
        for index,value in [(8,3),(108,4),(4,2),(0,0)]:
            raw=packet();raw[index]=value
            with self.assertRaises(ValueError):decode_rx_phase(raw)
        with self.assertRaises(ValueError):decode_rx_phase(packet()[:-1])

    def test_no_decision_and_millisecond_wrap(self):
        raw=packet();raw[40]=255;raw[41]=0
        struct.pack_into('<I',raw,12,20)
        struct.pack_into('<I',raw,88,0xfffffff0)
        result=decode_rx_phase(raw)
        self.assertIsNone(result['channels'][0]['phase_deg'])
        self.assertIsNone(result['channels'][0]['age_ms'])
        self.assertEqual(result['channels'][1]['age_ms'],36)

    def test_read_only_control_transfer(self):
        class Device:
            def ctrl_transfer(self,*args,**kwargs):
                self.args=args;self.kwargs=kwargs
                return packet()
        device=Device();read_phase(device)
        self.assertEqual(device.args,(0xc0,0x4e,0,0,112))
        self.assertEqual(device.kwargs,dict(timeout=500))


if __name__=='__main__':unittest.main()

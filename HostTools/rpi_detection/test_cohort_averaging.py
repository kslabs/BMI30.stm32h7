"""Offline cohort arithmetic, common-origin and loss-boundary tests."""
import unittest

import numpy as np

from bmi30_cohort_averaging import CohortAverager, MASK


def source(cycles, dma0=1000, seq0=50, first_phase=1):
    result = []
    for cycle in range(cycles):
        for side, phase in enumerate((first_phase, 1-first_phase)):
            for ch in (0, 1):
                frame = {'channel':ch, 'phase':phase, 'seq':(seq0+2*cycle+side) & MASK,
                         'reserved':(dma0+8*cycle+side) & MASK, 'reserved2':phase,
                         'timestamp_ticks':100+20*cycle+3*side, 'samples':200, 'flags':(1 << ch) | 4,
                         'completion_timing':{'host_monotonic':10+.02*cycle+.003*side+.0001*ch}}
                samples = np.full(200, 32000+100*ch+10*phase+cycle, dtype='<u2')
                result.append((frame, samples.tobytes()))
    return result


def collect(averager, rows):
    return [made for f,p in rows if (made := averager.feed(f,p)) is not None]


class CohortTests(unittest.TestCase):
    def test_both_supported_groups_every_offset_share_both_adc_phase_origins(self):
        for group in (2,4):
            for offset in range(group):
                with self.subTest(group=group, offset=offset):
                    a = CohortAverager(group, offset)
                    made = collect(a, source(offset+2*group))
                    self.assertEqual(len(made), 8)
                    for ch in (0,1):
                        frames = [f for f,p in made if f['channel']==ch]
                        self.assertEqual([f['seq'] for f in frames], list(range(4)))
                        self.assertEqual([f['phase'] for f in frames], [1,0,1,0])
                    for f,p in made:
                        provenance=f['provenance'];start=offset+group*provenance['group_index']
                        phase,ch=f['phase'],f['channel']
                        expected=(sum(32000+100*ch+10*phase+i for i in range(start,start+group))+group//2)//group
                        np.testing.assert_array_equal(np.frombuffer(p,dtype='<u2'), np.full(200,expected))
                        self.assertEqual(provenance['source_dma'], [1000+8*i+(phase==0) for i in range(start,start+group)])
                    self.assertEqual(a.diagnostics()['counts'].get('offset_packets_skipped',0),4*offset)

    def test_stm_rounding_is_half_up_not_numpy_bankers_and_no_uint16_overflow(self):
        rows=source(2)
        for index,(f,p) in enumerate(rows):
            value=32768 if index<4 else 32769
            rows[index]=(f,np.full(200,value,dtype='<u2').tobytes())
        made=collect(CohortAverager(2),rows)
        self.assertTrue(all(np.frombuffer(p,dtype='<u2')[0]==32769 for f,p in made))
        rows=[(f,np.full(200,65535,dtype='<u2').tobytes()) for f,p in source(4)]
        self.assertTrue(all(np.frombuffer(p,dtype='<u2')[0]==65535 for f,p in collect(CohortAverager(4),rows)))

    def test_timestamp_from_last_constituent_and_source_provenance_not_relabelled(self):
        rows=source(2);made=collect(CohortAverager(2),rows);f,p=made[0]
        self.assertEqual(f['timestamp_ticks'],120)
        self.assertEqual(f['completion_timing']['host_monotonic'],10.02)
        self.assertEqual(f['provenance']['source_sequences'],[50,52])
        self.assertEqual(f['seq'],0);self.assertTrue(f['synthetic'])
        self.assertEqual(f['flags'] & 4,0)
        self.assertFalse(f['provenance']['source_ADC_recovered'])
        rows[4][0]['completion_timing']['host_monotonic']=999
        self.assertEqual(f['completion_timing']['host_monotonic'],10.02)

    def test_initial_one_adc_late_phase_does_not_shift_its_window(self):
        a=CohortAverager(2)
        made=collect(a,source(4)[3:])
        self.assertEqual(len(made),4)
        self.assertEqual(a.diagnostics()['counts']['initial_partial_packets_skipped'],1)
        self.assertTrue(all(f['provenance']['source_dma'][0] in (1008,1009) for f,p in made))

    def test_usb_gap_clears_every_phase_and_adc_then_reanchors(self):
        callbacks=[];a=CohortAverager(2,on_reset=lambda reason,detail:callbacks.append((reason,detail)))
        rows=source(8);del rows[4]
        made=collect(a,rows)
        self.assertTrue(callbacks)
        self.assertIn('source_USB_sequence_gap',a.diagnostics()['resets'])
        for f,p in made:
            seqs=f['provenance']['source_sequences'];self.assertEqual((seqs[1]-seqs[0]) & MASK,2)
            self.assertEqual((f['provenance']['source_dma'][1]-f['provenance']['source_dma'][0]) & MASK,8)

    def test_dma_gap_detected_even_when_usb_sequence_is_clean(self):
        rows=source(8)
        for f,p in rows:
            if f['seq']>=54:f['reserved']+=8
        a=CohortAverager(2);made=collect(a,rows)
        self.assertIn('source_phase_DMA_stride',a.diagnostics()['resets'])
        self.assertTrue(made)
        self.assertTrue(all((f['provenance']['source_dma'][1]-f['provenance']['source_dma'][0]) & MASK==8 for f,p in made))

    def test_external_crc_reset_discards_partial_groups_and_never_reuses_virtual_seq(self):
        a=CohortAverager(2);first=collect(a,source(3))
        self.assertEqual(len(first),4)
        a.reset('crc_invalid',{'offset':123})
        second=collect(a,source(2,dma0=2000,seq0=100))
        self.assertEqual(len(second),4)
        self.assertEqual([f['seq'] for f,p in second if f['channel']==0],[2,3])
        self.assertEqual(a.diagnostics()['counts']['discarded_partial_group_packets'],4)

    def test_u32_wrap_is_continuous_not_a_gap(self):
        a=CohortAverager(2);made=collect(a,source(4,dma0=(1<<32)-8,seq0=(1<<32)-2))
        self.assertEqual(len(made),8);self.assertEqual(a.diagnostics()['resets'],{})

    def test_no_fabricated_anchor_with_missing_adc_or_incoherent_dma_pair(self):
        a=CohortAverager(2);rows=[r for r in source(10) if r[0]['channel']==0]
        self.assertEqual(collect(a,rows),[]);self.assertFalse(a.diagnostics()['anchor_established'])
        rows=source(4)
        for f,p in rows:
            if f['phase']==0:f['reserved']+=2
        b=CohortAverager(2);self.assertEqual(collect(b,rows),[])
        self.assertFalse(b.diagnostics()['anchor_established'])

    def test_invalid_source_resets_fail_closed(self):
        a=CohortAverager(2);f,p=source(1)[0];f['phase']=0
        with self.assertRaises(ValueError):a.feed(f,p)
        self.assertEqual(a.diagnostics()['resets'],{'invalid_source_frame':1})
        with self.assertRaises(ValueError):CohortAverager(3)
        with self.assertRaises(ValueError):CohortAverager(2,2)


if __name__=='__main__':
    unittest.main()

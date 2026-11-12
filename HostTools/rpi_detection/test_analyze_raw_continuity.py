"""Protocol fixtures exercise wraps, phase grouping, capture edges and headroom."""
import base64
import binascii
from contextlib import redirect_stderr
import io
import struct
import sys
import unittest
from unittest.mock import patch

import analyze_raw_continuity
from analyze_raw_continuity import ContinuityAnalyzer, FrameDecoder, delta32, strict_roi_verdict, strict_average_verdict


def frame(channel,seq,dma,ticks,phase,samples=(32767,32768,32769),bad_crc=False):
    # Serialize the documented firmware header; CRC covers its first30bytes.
    payload = struct.pack('<'+'H'*len(samples),*samples)
    header = struct.pack('<HBBIIHHIIIH',0xa55a,1,(1<<channel)|4,seq,ticks,len(samples),0,0,0,dma,phase)
    crc = binascii.crc_hqx(header+payload,0xffff)
    return header+struct.pack('<H',crc ^ int(bad_crc))+payload


def transfer(data,usb_at=10.,mono=20.):
    return {'kind':'transfer','usb_at':usb_at,'monotonic':mono,'at':1000.+mono,
            'length':len(data),'captured':len(data),'result':0,'truncated':False,
            'data':base64.b64encode(data).decode()}


def strict_report(dma=(100,101,102),mode=1,pair_dma_offset=0,times=(0,.0025,.005),omit_first_a=False):
    analyzer = ContinuityAnalyzer()
    status = {'mode':{'stream_mode':mode,'base_buf_len':200,'avg_n':8}}
    analyzer.record({'kind':'ready','status':status},1)
    counts = [0,0]
    for index,generation in enumerate(dma):
        packets = []
        for channel in (0,1):
            if channel==0 and index==0 and omit_first_a:
                continue
            packets.append(frame(channel,500+index,generation+(pair_dma_offset if channel else 0),
                                 1000+round(index*2.5),index&1,(32768,)*200))
            counts[channel] += 1
        analyzer.record(transfer(b''.join(packets),10+times[index],20+times[index]),index+2)
    analyzer.record({'kind':'end','status':status,'summary':{'transfers':len(dma),'wire':{'crc_verified_frames':counts},
        'queue_dropped':0,'kernel_dropped':0,'errors':[],'config_unchanged':True,
        'core_process_unchanged':True,'monitor_restored':True}},len(dma)+2)
    report = analyzer.summary()
    report['archive_read_complete'] = True
    return report


def average_report(n,actual_n=None,status_n=None,change_profile=False,mode=2,
                   rpi_depth=1,averaging_depth=None,selected=5):
    actual_n = n if actual_n is None else actual_n
    status_n = n if status_n is None else status_n
    analyzer = ContinuityAnalyzer()
    status = {'mode':{'selected':selected,'stream_mode':mode,'base_buf_len':200,'avg_n':status_n,'rpi_avg_n':rpi_depth},
              'averaging':{'depth':rpi_depth if averaging_depth is None else averaging_depth}}
    analyzer.record({'kind':'ready','status':status},1)
    for index in range(6):
        generation = 100+(index//2)*(2*actual_n)+(index&1)
        data = b''.join(frame(ch,500+index,generation,1000+round(index*2.5*n),index&1,(32768,)*200) for ch in (0,1))
        analyzer.record(transfer(data,10+index*.0025*n,20+index*.0025*n),index+2)
        if change_profile and index==2:
            analyzer.record({'kind':'status','status':{**status,'mode':{**status['mode'],'avg_n':2 if n!=2 else 4}}},100)
    analyzer.record({'kind':'end','status':status,'summary':{'transfers':6,'wire':{'crc_verified_frames':[6,6]},
        'queue_dropped':0,'kernel_dropped':0,'errors':[],'config_unchanged':True,
        'core_process_unchanged':True,'monitor_restored':True}},101)
    report = analyzer.summary()
    report['archive_read_complete'] = True
    return report


class ContinuityTests(unittest.TestCase):
    def test_explicit_rpi12_preserves_stm4_wire_stride_and_default1_rejects_it(self):
        report = average_report(4,rpi_depth=12,selected=6)
        self.assertEqual(strict_average_verdict(report,4)['verdict'],'INCOMPLETE')
        result = strict_average_verdict(report,4,expected_rpi_depth=12)
        self.assertEqual(result['verdict'],'PASS')
        self.assertEqual(result['expected_raspberry_depth'],12)
        for ch in ('0','1'):
            for phase in ('0','1'):
                self.assertEqual(result['checks'][f'channel_{ch}_phase_{phase}_dma_stride']['observed']['expected_stride'],8)
        wrong_stm_stride = strict_average_verdict(average_report(4,actual_n=8,rpi_depth=12),4,expected_rpi_depth=12)
        self.assertEqual(wrong_stm_stride['verdict'],'FAIL')

    def test_rpi_expectation_checks_both_saved_and_actual_depth_and_range(self):
        self.assertEqual(strict_average_verdict(average_report(4),4,expected_rpi_depth=12)['verdict'],'INCOMPLETE')
        for saved,actual in ((12,1),(1,12)):
            with self.subTest(saved=saved,actual=actual):
                report = average_report(4,rpi_depth=saved,averaging_depth=actual)
                self.assertEqual(strict_average_verdict(report,4,expected_rpi_depth=12)['verdict'],'INCOMPLETE')
        self.assertEqual(strict_average_verdict(average_report(4),4)['verdict'],'PASS')
        for depth in (0,13,True,1.5):
            with self.assertRaises(ValueError):
                strict_average_verdict(average_report(4),4,expected_rpi_depth=depth)

    def test_explicit_rpi_cli_option_requires_strict_average_before_reading_archive(self):
        for depth in (1,12):
            with self.subTest(depth=depth),patch.object(sys,'argv',['analyze_raw_continuity.py','not-opened.jsonl.gz',
                    '--expect-rpi-depth',str(depth)]),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    analyze_raw_continuity.main()
                self.assertEqual(error.exception.code,2)

    def test_average_two_and_four_qualify_by_actual_source_stride_not_requested_label(self):
        for n in (2,4):
            result = strict_average_verdict(average_report(n),n)
            self.assertEqual(result['verdict'],'PASS',n)
            self.assertEqual(result['checks']['channel_0_phase_0_dma_stride']['observed']['expected_stride'],2*n)
            self.assertEqual(result['checks']['channel_0_informative_rate']['status'],'PASS')
            clamped = strict_average_verdict(average_report(n,actual_n=8),n)
            self.assertEqual(clamped['verdict'],'FAIL')
            self.assertIn('channel_0_phase_0_dma_stride',clamped['required_failed_checks'])
            actual_snapshot_eight = strict_average_verdict(average_report(n,actual_n=8,status_n=8),n)
            self.assertEqual(actual_snapshot_eight['verdict'],'INCOMPLETE')

    def test_average_profile_change_nonavg_mode_or_missing_phase_cannot_pass(self):
        self.assertEqual(strict_average_verdict(average_report(4,change_profile=True),4)['verdict'],'INCOMPLETE')
        self.assertEqual(strict_average_verdict(average_report(4,mode=1),4)['verdict'],'INCOMPLETE')
        report = average_report(4)
        report['channels']['0']['per_phase']['1']['crc_verified_frames'] = 1
        self.assertEqual(strict_average_verdict(report,4)['verdict'],'INCOMPLETE')
        report = average_report(4)
        report['recorded_acquisition_profiles'][0]['profile']['rpi_avg_n'] = 8
        self.assertEqual(strict_average_verdict(report,4)['verdict'],'INCOMPLETE')
        with self.assertRaises(ValueError):
            strict_average_verdict(report,3)

    def test_strict_roi_requires_dma_continuity_even_with_perfect_crc_and_usb_sequence(self):
        report = strict_report()
        verdict = strict_roi_verdict(report)
        self.assertEqual(verdict['verdict'],'PASS')
        self.assertTrue(verdict['source_frames_complete'])
        self.assertEqual(verdict['checks']['channel_0_informative_rate']['status'],'PASS')
        gap = strict_roi_verdict(strict_report(dma=(100,101,103)))
        self.assertEqual(gap['verdict'],'FAIL')
        self.assertIn('channel_0_source_dma_plus1',gap['required_failed_checks'])
        self.assertEqual(gap['checks']['channel_0_usb_seq_plus1']['status'],'PASS')

    def test_strict_roi_matches_cross_channel_dma_and_allows_only_capture_edge_imbalance(self):
        mismatch = strict_roi_verdict(strict_report(pair_dma_offset=1))
        self.assertEqual(mismatch['verdict'],'FAIL')
        self.assertIn('cross_channel_same_source_dma',mismatch['required_failed_checks'])
        boundary = strict_roi_verdict(strict_report(omit_first_a=True))
        self.assertEqual(boundary['verdict'],'PASS')
        self.assertEqual(boundary['checks']['cross_channel_full_overlap_and_phase']['status'],'PASS')

    def test_strict_roi_does_not_call_late_complete_data_missing_or_promise_a_timing_sla(self):
        result = strict_roi_verdict(strict_report(times=(0,.0025,.2)))
        self.assertEqual(result['verdict'],'PASS')
        self.assertEqual(result['checks']['channel_0_informative_rate']['status'],'FAIL')
        self.assertFalse(result['checks']['channel_0_informative_rate']['required_for_source_continuity'])
        self.assertTrue(any(row['kind']=='usb_delivery_pause' for row in result['warnings']))

    def test_strict_roi_wrong_mode_or_monitor_loss_is_incomplete_rather_than_device_loss_proof(self):
        self.assertEqual(strict_roi_verdict(strict_report(mode=2))['verdict'],'INCOMPLETE')
        report = strict_report()
        report['recording_integrity']['kernel_dropped'] = 1
        result = strict_roi_verdict(report)
        self.assertEqual(result['verdict'],'INCOMPLETE')
        self.assertIsNone(result['source_frames_complete'])
        self.assertEqual(strict_roi_verdict({})['verdict'],'INCOMPLETE')

    def test_runtime_epoch_changes_are_not_setting_variants_and_late_mode_changes_remain_visible(self):
        analyzer = ContinuityAnalyzer()
        for index in range(500):
            analyzer.record({'kind':'status','status':{'mode':{'stream_mode':0},
                            'averaging':{'depth':1,'active':False,'epoch':index,'counts':[index]*4}}},index+1)
        self.assertEqual(len(analyzer.summary()['recorded_input_settings']),1)
        self.assertNotIn('settings_variant_limit',analyzer.summary()['observations'])
        analyzer.record(transfer(frame(0,1,1,100,0)),501)
        analyzer.record(transfer(frame(0,2,2,105,1)),502)
        self.assertTrue(analyzer.summary()['channels']['0']['source_generation_coverage']['available'])
        analyzer.record({'kind':'status','status':{'mode':{'stream_mode':2}}},503)
        self.assertEqual(analyzer.summary()['recorded_stream_modes'],[0,2])
        self.assertFalse(analyzer.summary()['channels']['0']['source_generation_coverage']['available'])

    def test_mode0_source_coverage_handles_wrap_and_duplicate_without_adc_loss_claim(self):
        analyzer = ContinuityAnalyzer()
        analyzer.record({'kind':'ready','status':{'mode':{'stream_mode':0}}},1)
        for index,seq in enumerate((0xfffffffe,0xffffffff,0xffffffff,1)):
            analyzer.record(transfer(frame(0,seq,seq,100+index, index&1),10+index*.01,20+index*.01),index+2)
        coverage = analyzer.summary()['channels']['0']['source_generation_coverage']
        self.assertTrue(coverage['available'])
        self.assertEqual(coverage['wrap_safe_generation_span_inclusive'],4)
        self.assertEqual(coverage['crc_verified_observed_generations'],3)
        self.assertEqual(coverage['observed_generation_fraction'],.75)
        self.assertEqual(coverage['unobserved_generations_inside_span'],1)
        # Same numerical counters are insufficient when the recorded mode is averaged.
        analyzer.record({'kind':'status','status':{'mode':{'stream_mode':2,'avg_n':8}}},7)
        self.assertFalse(analyzer.summary()['channels']['0']['source_generation_coverage']['available'])

    def test_wraps_do_not_become_huge_forward_losses_and_phase_groups_stay_separate(self):
        analyzer = ContinuityAnalyzer()
        sequence = [(0xfffffffe,0xfffffffe,0xffffff00,0),
                    (0xffffffff,0xffffffff,0xffffff05,1),
                    (0,0,1,0),(2,32,21,0)]
        for index,(seq,dma,ticks,phase) in enumerate(sequence):
            data = b''.join(frame(ch,seq,dma,ticks,phase) for ch in (0,1))
            analyzer.record(transfer(data,10+index*.02,20+index*.02),index+1)
        report = analyzer.summary()
        ch = report['channels']['0']
        self.assertEqual(ch['crc_verified_frames'],4)
        self.assertEqual(ch['stream_seq_deltas']['most_common_signed_deltas'],{'1':2,'2':1})
        self.assertEqual(ch['stream_seq_deltas']['classes']['forward_wrap'],1)
        self.assertEqual(ch['stream_seq_forward_jumps'],1)
        self.assertEqual(ch['unobserved_sequence_numbers_between_verified_frames'],1)
        self.assertEqual(ch['same_phase_adjacent'],1)
        self.assertEqual(ch['per_phase']['0']['stream_seq_deltas']['most_common_signed_deltas'],{'2':2})
        self.assertEqual(ch['dma_reserved_deltas']['most_common_signed_deltas'],{'1':2,'32':1})
        self.assertEqual(report['equal_seq_cross_channel']['equal_seq_pairs'],4)
        self.assertEqual(report['equal_seq_cross_channel'].get('phase_mismatch',0),0)
        self.assertNotIn('adc_losses',report)

    def test_half_range_is_ambiguous_and_short_backward_delta_is_not_unsigned_billions(self):
        self.assertEqual(delta32(0,0xffffffff),(1,1))
        self.assertEqual(delta32(99,100),(0xffffffff,-1))
        self.assertEqual(delta32(0x80000001,1),(0x80000000,-0x80000000))
        analyzer = ContinuityAnalyzer()
        analyzer.record(transfer(frame(0,1,100,100,0)),1)
        analyzer.record(transfer(frame(0,0x80000001,99,90,1),10.01,20.01),2)
        ch = analyzer.summary()['channels']['0']
        self.assertEqual(ch['stream_seq_deltas']['classes']['half_range_ambiguous'],1)
        self.assertEqual(ch['stream_seq_forward_jumps'],0)
        self.assertEqual(ch['dma_reserved_deltas']['signed']['min'],-1)
        self.assertEqual(ch['timestamp_tick_deltas']['signed']['min'],-10)

    def test_header_and_payload_split_across_transfers_keep_completion_time_and_sample_count(self):
        found,problems = [],[]
        decoder = FrameDecoder(lambda f,p:found.append((f,p)),lambda k,c:problems.append(k))
        first = frame(0,7,21,42,0,(0,1,2,3))
        second = frame(1,7,21,42,1,(10,11,12,13,14,15))
        decoder.feed(b'capture-start-tail'+first[:13],{'transfer_index':1,'usb_at':10.,'host_monotonic':20.})
        decoder.feed(first[13:]+second+b'\x5a\xa5\x01',{'transfer_index':2,'usb_at':10.02,'host_monotonic':20.03})
        self.assertEqual([f['samples'] for f,p in found],[4,6])
        self.assertEqual(found[0][0]['header_timing']['transfer_index'],1)
        self.assertEqual(found[0][0]['completion_timing']['transfer_index'],2)
        self.assertEqual(found[1][0]['header_timing']['transfer_index'],2)
        self.assertEqual(decoder.summary()['trailing_fragment_bytes'],3)
        self.assertEqual(problems,[])

    def test_crc_rejection_has_a_contextual_sequence_gap_without_inventing_boundary_frames(self):
        analyzer = ContinuityAnalyzer()
        data = b'partial-prefix'+frame(0,10,70,100,0)+frame(0,11,71,105,1,bad_crc=True)+frame(0,12,72,110,0)
        analyzer.record(transfer(data),1)
        report = analyzer.summary()
        self.assertEqual(report['decoder']['crc_verified_frames'],2)
        self.assertEqual(report['decoder']['crc_invalid_candidates'],1)
        self.assertEqual(report['channels']['0']['stream_seq_forward_jumps'],1)
        self.assertEqual(report['channels']['0']['first']['seq'],10)
        self.assertEqual(report['channels']['0']['last']['seq'],12)
        # A capture beginning atseq10 does not imply ten earlier missing frames.
        self.assertEqual(report['channels']['0']['unobserved_sequence_numbers_between_verified_frames'],1)

    def test_headroom_is_measured_from_received_u16_and_partitioned_by_phase(self):
        analyzer = ContinuityAnalyzer(near_rail_margin=64)
        analyzer.record(transfer(frame(0,10,100,1000,0,(0,64,65471,65535))),1)
        analyzer.record(transfer(frame(0,11,101,1005,1,(10,32768,65500)),10.005,20.005),2)
        ch = analyzer.summary()['channels']['0']
        all_samples = ch['received_u16_headroom']
        self.assertEqual((all_samples['samples'],all_samples['zero_samples'],all_samples['full_scale_samples']),(7,1,1))
        self.assertEqual((all_samples['near_low_samples'],all_samples['near_high_samples']),(3,3))
        self.assertEqual(ch['per_phase']['0']['received_u16_headroom']['mean_u16'],32767.5)
        self.assertEqual(ch['per_phase']['1']['received_u16_headroom']['minimum_rail_margin_u16'],10)

    def test_transfer_batching_and_reader_delay_are_not_adc_gap_estimates(self):
        analyzer = ContinuityAnalyzer()
        analyzer.record(transfer(frame(0,1,100,1000,0)+frame(0,2,102,1005,1)),1)
        analyzer.record(transfer(frame(0,3,104,1010,0),10.01,20.03),2)
        report = analyzer.summary()
        ch = report['channels']['0']
        self.assertEqual(ch['kernel_completion_interval_ms']['min'],0.)
        self.assertAlmostEqual(ch['host_reader_interval_ms']['max'],30.)
        self.assertAlmostEqual(report['transfer_timing']['host_minus_kernel_interval_ms']['mean'],20.)
        self.assertEqual(ch['stream_seq_forward_jumps'],0)
        self.assertEqual(ch['dma_reserved_deltas']['most_common_signed_deltas'],{'2':2})


if __name__=='__main__':
    unittest.main()

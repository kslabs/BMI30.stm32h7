"""Integrity checks for passive wire capture using serialized protocol fixtures."""
import base64
import binascii
import gzip
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from passive_raw_capture import AVERAGE_DEPTHS, WireCheck, _acquisition_profile, _healthy, capture_complete, select_transfer, settings_fingerprint
from run_passive_raw_capture import build_payload, operator_event, operator_heartbeat, transport_path, verify_archive


def adc_frame(channel, seq, phase=0, bad_crc=False, sample_count=200):
    # Independent serialization of the documented 32-byte vendor frame.
    payload = struct.pack('<' + str(sample_count) + 'H', *[32768+(i % 13) for i in range(sample_count)])
    header = struct.pack('<HBBIIHHIIIH', 0xA55A, 1, 4 | (1 << channel), seq,
                         seq*40000 & 0xffffffff, sample_count, int(sample_count == 200),
                         0, sample_count if sample_count == 200 else 0, seq if sample_count == 600 else seq//2, phase)
    crc = binascii.crc_hqx(header+payload, 0xffff)
    return header+struct.pack('<H', crc ^ int(bad_crc))+payload


def usb_header(bus=2, address=3, endpoint=0x83, event=b'C', length=432, captured=432, result=0):
    header = bytearray(64)
    header[8:12] = event+bytes([3, endpoint, address])
    struct.pack_into('<H', header, 12, bus)
    struct.pack_into('<qi', header, 16, 100, 250000)
    struct.pack_into('<iII', header, 28, result, length, captured)
    return bytes(header)


class WireCaptureTests(unittest.TestCase):
    def average_status(self,n=4):
        return {**self.acquisition_status(),'mode':{'selected':5,'stream_mode':2,'base_buf_len':200,'avg_n':n,'rpi_avg_n':1},
                'averaging':{'depth':1},'dc_guard':{'enabled':False,'last_ok':False}}

    def test_average_acquisition_requires_exact_known_tuple_and_actual_raspberry_depth_one(self):
        for n in AVERAGE_DEPTHS:
            status = self.average_status(n)
            self.assertTrue(_healthy(status,True))
            self.assertFalse(_healthy(status))
            self.assertEqual(_acquisition_profile(status),'averaged_roi_'+str(n))
        status = self.average_status()
        for key,value in (('selected',6),('selected',5.0),('stream_mode',1),('base_buf_len',600),
                          ('avg_n',1),('avg_n',3),('avg_n',12),('avg_n',4.),('rpi_avg_n',8),('rpi_avg_n',True)):
            changed = {**status,'mode':{**status['mode'],key:value}}
            self.assertFalse(_healthy(changed,True),(key,value))
        self.assertFalse(_healthy({**status,'averaging':{'depth':8}},True))
        self.assertFalse(_healthy({**status,'averaging':{}},True))
        # DC health does not weaken or strengthen the explicit acquisition tuple.
        self.assertTrue(_healthy({**status,'dc_guard':{'enabled':True,'last_ok':True}},True))

    def test_average_readback_binds_initial_snapshot_depth_and_rejects_midrecord_change(self):
        status = self.average_status(4)
        wire = b''.join(adc_frame(ch,seq,seq&1) for seq in (10,11) for ch in (0,1))
        checker = WireCheck(200);checker.feed(wire)
        records = [{'kind':'ready','ready':True,'metadata':{'acquisition_only':True,'expected_average':4},'status':status},
                   {'kind':'status','status':status},
                   {'kind':'transfer','captured':len(wire),'data':base64.b64encode(wire).decode()},
                   {'kind':'end','status':status,'summary':{'acquisition_only':True,'capture_complete':True,
                    'transfers':1,'status_samples':1,'wire':checker.summary()}}]
        def readback(rows):
            return verify_archive(io.BytesIO(gzip.compress(''.join(json.dumps(row)+'\n' for row in rows).encode())))
        result = readback(records)
        self.assertEqual(result['acquisition_profile'],'averaged_roi_4')
        self.assertEqual(result['readback_errors'],[])
        clamped = [{**records[0],'status':self.average_status(8)},records[1],*records[2:]]
        self.assertIn('acquisition_average_does_not_match_requested_depth',readback(clamped)['readback_errors'])
        changed = [records[0],{**records[1],'status':self.average_status(2)},*records[2:]]
        self.assertIn('acquisition_profile_changed',readback(changed)['readback_errors'])

    def acquisition_status(self):
        return {'ok': True, 'busy': False, 'stale': False, 'connection': {'connected': True},
                'mode': {'selected': 4, 'stream_mode': 1, 'base_buf_len': 200},
                'detector': {'active': False, 'worker': {'alive': False}}}

    def test_acquisition_only_requires_explicit_lossless_mode_and_keeps_default_detector_guard(self):
        status = self.acquisition_status()
        self.assertTrue(_healthy(status, acquisition_only=True))
        self.assertFalse(_healthy(status))
        for key, value in (('selected', 6), ('stream_mode', 2), ('base_buf_len', 600)):
            changed = {**status, 'mode': {**status['mode'], key: value}}
            self.assertFalse(_healthy(changed, acquisition_only=True), key)
        for changes in ({'ok': False}, {'stale': True}, {'busy': True},
                        {'connection': {'connected': False}}):
            self.assertFalse(_healthy({**status, **changes}, acquisition_only=True), changes)
        detector = {**status, 'detector': {'active': True, 'worker': {'alive': True}}}
        self.assertTrue(_healthy(detector))

    def test_acquisition_heartbeat_identifies_mode_and_does_not_require_detector(self):
        row = {'kind': 'status', 'at': 100, 'monotonic': 30, 'acquisition_only': True,
               'status': self.acquisition_status()}
        heartbeat = operator_heartbeat(row)
        self.assertTrue(heartbeat['healthy'])
        self.assertTrue(heartbeat['acquisition_only'])
        self.assertEqual(heartbeat['recording_mode'], 'acquisition_only')
        self.assertEqual((heartbeat['at'], heartbeat['monotonic']), (100, 30))
        row['status']['mode']['stream_mode'] = 2
        self.assertFalse(operator_heartbeat(row)['healthy'])

    def test_latest_full_acquisition_is_explicit_and_requires_exact_mode_three_tuple(self):
        status = {**self.acquisition_status(), 'mode': {'selected': 3, 'stream_mode': 0, 'base_buf_len': 600}}
        self.assertTrue(_healthy(status, acquisition_only=True))
        self.assertFalse(_healthy(status))
        heartbeat = operator_heartbeat({'kind': 'status', 'acquisition_only': True, 'status': status})
        self.assertTrue(heartbeat['healthy'])
        self.assertEqual(heartbeat['acquisition_profile'], 'latest_full')
        for key, value in (('selected', 1), ('selected', 4), ('stream_mode', 1), ('base_buf_len', 200)):
            self.assertFalse(_healthy({**status, 'mode': {**status['mode'], key: value}}, True), (key, value))

    def test_latest_full_wire_readback_and_midrecord_transport_change(self):
        status = {**self.acquisition_status(), 'mode': {'selected': 3, 'stream_mode': 0, 'base_buf_len': 600}}
        wire = b''.join(adc_frame(ch, seq, seq & 1, sample_count=600) for seq in (10, 11) for ch in (0, 1))
        checker = WireCheck(600)
        checker.feed(wire)
        records = [{'kind': 'ready', 'ready': True, 'metadata': {'acquisition_only': True}, 'status': status},
                   {'kind': 'status', 'acquisition_only': True, 'status': status},
                   {'kind': 'transfer', 'captured': len(wire), 'data': base64.b64encode(wire).decode()},
                   {'kind': 'end', 'status': status, 'summary': {'acquisition_only': True,
                    'capture_complete': True, 'transfers': 1, 'status_samples': 1, 'wire': checker.summary()}}]

        def readback(rows):
            data = gzip.compress(''.join(json.dumps(row)+'\n' for row in rows).encode())
            return verify_archive(io.BytesIO(data))

        result = readback(records)
        self.assertEqual(result['readback_errors'], [])
        self.assertEqual(result['acquisition_profile'], 'latest_full')
        self.assertEqual(result['wire_readback']['crc_verified_frames'], [2, 2])
        changed = [records[0], {**records[1], 'status': self.acquisition_status()}, *records[2:]]
        self.assertIn('acquisition_profile_changed', readback(changed)['readback_errors'])

    def test_acquisition_completeness_needs_advancing_usb_and_retains_integrity_guards(self):
        summary = {'acquisition_only': True, 'monitor_restored': True, 'settings_unchanged': True,
                   'config_unchanged': True, 'core_process_unchanged': True, 'max_status_gap_s': .1,
                   'wire': {'crc_verified_frames': [2, 2], 'crc_bad': 0, 'crc_missing': 0,
                            'sequence_discontinuities': [0, 0], 'sequence_duplicates': [0, 0]}}
        self.assertTrue(capture_complete(summary))
        for frames in ([0, 0], [1, 1], [2, 1], [0, 2]):
            self.assertFalse(capture_complete({**summary, 'wire': {**summary['wire'], 'crc_verified_frames': frames}}))
        for key, value in (('crc_bad', 1), ('crc_missing', 1), ('sequence_discontinuities', [1, 0]),
                           ('sequence_duplicates', [0, 1])):
            self.assertFalse(capture_complete({**summary, 'wire': {**summary['wire'], key: value}}))
        for key in ('queue_dropped', 'kernel_dropped', 'usb_errors', 'truncated_transfers'):
            self.assertFalse(capture_complete({**summary, key: 1}))
        self.assertFalse(capture_complete({**summary, 'errors': ['acquisition_unhealthy']}))

    def test_acquisition_readback_requires_mode_guards_and_consistent_summary(self):
        wire = adc_frame(0, 10)+adc_frame(1, 10)+adc_frame(0, 11, 1)+adc_frame(1, 11, 1)
        checker = WireCheck(200)
        checker.feed(wire)
        status = self.acquisition_status()
        records = [{'kind': 'ready', 'ready': True, 'metadata': {'acquisition_only': True}, 'status': status},
                   {'kind': 'status', 'acquisition_only': True, 'status': status},
                   {'kind': 'transfer', 'captured': len(wire), 'data': base64.b64encode(wire).decode()},
                   {'kind': 'end', 'status': status, 'summary': {'acquisition_only': True,
                    'capture_complete': True, 'transfers': 1, 'status_samples': 1, 'wire': checker.summary()}}]

        def readback(rows):
            data = gzip.compress(''.join(json.dumps(row)+'\n' for row in rows).encode())
            return verify_archive(io.BytesIO(data))

        result = readback(records)
        self.assertTrue(result['acquisition_only'])
        self.assertEqual(result['recording_mode'], 'acquisition_only')
        self.assertEqual(result['readback_errors'], [])
        wrong_mode = {**status, 'mode': {**status['mode'], 'selected': 6}}
        changed = [records[0], {**records[1], 'status': wrong_mode}, *records[2:]]
        self.assertIn('acquisition_status_unhealthy', readback(changed)['readback_errors'])
        changed = [*records[:-1], {**records[-1], 'status': {**status, 'stale': True}}]
        self.assertIn('acquisition_final_status_unhealthy', readback(changed)['readback_errors'])
        changed = [*records[:-1], {**records[-1], 'summary': {**records[-1]['summary'], 'acquisition_only': False}}]
        self.assertIn('acquisition_mode_summary_mismatch', readback(changed)['readback_errors'])

    def test_real_ring_dependency_bundle_imports_without_starting_input(self):
        here = Path(__file__).resolve().parent
        names = ('ring_gestures', 'remote_pass_logger', 'passive_ring_input')
        dependencies = {name:(here/(name+'.py')).read_text(encoding='utf-8') for name in names}
        source = ('from __future__ import annotations\nfrom passive_ring_input import session_id,HOSTNAME\n'
                  'def run(options):\n global observed\n observed=(HOSTNAME,session_id(options["session_id"]))\n')
        expected = '12345678-1234-1234-1234-123456789abc'
        namespace = {}
        previous = {name:sys.modules.get(name) for name in names}
        try:
            exec(compile(build_payload(source, {'session_id': expected}, dependencies), 'test_ring_bundle', 'exec'), namespace)
            self.assertEqual(namespace['observed'], ('BMI30-C9775FBAE', expected))
        finally:
            for name, old in previous.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old

    def test_streamed_modules_keep_future_imports_and_run_with_exact_options(self):
        source = ('from __future__ import annotations\nfrom _capture_test_dependency import VALUE\n'
                  'def run(options):\n global observed\n observed=(VALUE,options)\n')
        dependency = 'from __future__ import annotations\nVALUE=17\n'
        payload = build_payload(source, {'ring': {'mode': 'calibrate', 'session_id': 'x'}},
                                {'_capture_test_dependency': dependency})
        namespace = {}
        try:
            exec(compile(payload, 'test_bundle', 'exec'), namespace)
            self.assertEqual(namespace['observed'], (17, {'ring': {'mode': 'calibrate', 'session_id': 'x'}}))
        finally:
            sys.modules.pop('_capture_test_dependency', None)

    def test_operator_heartbeat_reports_detector_health_and_preserves_remote_time(self):
        healthy = {'kind': 'status', 'at': 100, 'monotonic': 30, 'status': {
                   'ok': True, 'stale': False, 'connection': {'connected': True},
                   'detector': {'active': True, 'worker': {'alive': True}}}}
        heartbeat = operator_heartbeat(healthy)
        self.assertTrue(heartbeat['healthy'])
        self.assertEqual((heartbeat['at'], heartbeat['monotonic']), (100, 30))
        healthy['status']['stale'] = True
        self.assertFalse(operator_heartbeat(healthy)['healthy'])
        self.assertIsNone(operator_heartbeat({'kind': 'transfer'}))

    def test_operator_receipts_preserve_remote_times_without_bulk_status(self):
        record = {'kind': 'pass_end', 'pass_id': 3, 'epoch': 100., 'observed_mono': 20.,
                  'start_mono': 19., 'human_result': 'hit', 'status': {'large': True}}
        event = operator_event(record)
        self.assertNotIn('status', event)
        self.assertEqual(event, {k: v for k, v in record.items() if k != 'status'})
        self.assertIsNone(operator_event({'kind': 'transfer', 'data': 'large'}))
        self.assertIsNone(operator_event({'kind': 'status', 'status': {'large': True}}))
        self.assertEqual(operator_event({'kind': 'control_error', 'error': 'ValueError'}),
                         {'kind': 'control_error', 'error': 'ValueError'})

    def test_transport_selection_is_explicit_and_unknown_hosts_fail_closed(self):
        m02 = transport_path('BMI30-E29805C7C', '490df72e29805c7c')
        s07 = transport_path('BMI30-C9775FBAE', 'abdd2dbc9775fbae')
        self.assertEqual(m02.name, 'device_remote.py')
        self.assertEqual(s07.name, 's07_remote.py')
        self.assertNotEqual(m02, s07)
        for name in ('M02', 'S07', '192.168.0.153', 'BMI30-E29805C7D', None):
            with self.assertRaises(ValueError):
                transport_path(name, 'abdd2dbc9775fbae')
        for name, serial in (('BMI30-E29805C7C', 'abdd2dbc9775fbae'),
                             ('BMI30-C9775FBAE', '490df72e29805c7c'),
                             ('BMI30-C9775FBAE', '0000000c9775fbae')):
            with self.assertRaises(ValueError):
                transport_path(name, serial)

    def test_both_channels_split_across_usb_reads_and_non_adc_messages(self):
        # Transfer boundaries do not necessarily match ADC packet boundaries.
        wire = b'ACK\x00'+adc_frame(0, 10)+adc_frame(1, 10, 1)+adc_frame(0, 11, 1)+adc_frame(1, 11)
        reader = WireCheck(200)
        for pos in range(0, len(wire), 37):
            reader.feed(wire[pos:pos+37])
        s = reader.summary()
        self.assertEqual(s['crc_verified_frames'], [2, 2])
        self.assertEqual(s['sequence_discontinuities'], [0, 0])
        self.assertEqual(s['phase_counts'], [{'0': 1, '1': 1}, {'0': 1, '1': 1}])

    def test_crc_failure_missing_and_sequence_gap_are_separate(self):
        reader = WireCheck(200)
        reader.feed(adc_frame(0, 3)+adc_frame(0, 4, bad_crc=True)+adc_frame(0, 5)+adc_frame(0, 5))
        s = reader.summary()
        self.assertEqual(s['crc_verified_frames'], [3, 0])
        self.assertEqual(s['crc_bad'], 1)
        self.assertEqual(s['sequence_discontinuities'], [1, 0])
        self.assertEqual(s['sequence_duplicates'], [1, 0])

    def test_sequence_wrap_and_partial_final_frame(self):
        reader = WireCheck(200)
        reader.feed(adc_frame(1, 0xffffffff)+adc_frame(1, 0)+adc_frame(1, 1)[:90])
        self.assertEqual(reader.seq_gaps, [0, 0])
        self.assertEqual(reader.summary()['trailing_fragment_bytes'], 90)

    def test_only_target_completed_bulk_in_is_selected(self):
        kwargs = dict(bus=2, address=3, endpoint=0x83)
        payload = adc_frame(0, 10)
        for changes in ({'bus': 1}, {'address': 4}, {'endpoint': 1}, {'event': b'S'}):
            self.assertIsNone(select_transfer(usb_header(**changes), payload, **kwargs))
        transfer = select_transfer(usb_header(), payload, **kwargs)
        self.assertEqual(transfer['data'], payload)
        self.assertEqual(transfer['usb_at'], 100.25)
        self.assertFalse(transfer['truncated'])

    def test_usb_truncation_and_failed_completion_are_retained(self):
        transfer = select_transfer(usb_header(captured=100, result=-71), b'x'*100,
                                   bus=2, address=3, endpoint=0x83)
        self.assertTrue(transfer['truncated'])
        self.assertEqual(transfer['result'], -71)
        with self.assertRaises(ValueError):
            select_transfer(usb_header(captured=433), b'x'*433, bus=2, address=3, endpoint=0x83)

    def test_completeness_requires_both_channels_and_all_integrity_conditions(self):
        summary = {'monitor_restored': True, 'settings_unchanged': True, 'max_status_gap_s': .12,
                   'config_unchanged': True, 'core_process_unchanged': True,
                   'wire': {'crc_verified_frames': [100, 100], 'crc_bad': 0, 'crc_missing': 0,
                            'sequence_discontinuities': [0, 0], 'sequence_duplicates': [0, 0]}}
        self.assertTrue(capture_complete(summary))
        for key in ('queue_dropped', 'kernel_dropped', 'usb_errors', 'truncated_transfers'):
            self.assertFalse(capture_complete({**summary, key: 1}), key)
        self.assertFalse(capture_complete({**summary, 'max_status_gap_s': .6}))
        self.assertFalse(capture_complete({**summary, 'config_unchanged': False}))
        self.assertFalse(capture_complete({**summary, 'core_process_unchanged': False}))
        self.assertFalse(capture_complete({**summary, 'ring_requested': True,
                                           'ring': {'ring_input_complete': False}}))
        self.assertTrue(capture_complete({**summary, 'ring_requested': True,
                                          'ring': {'ring_input_complete': True}}))
        self.assertFalse(capture_complete({**summary, 'wire': {**summary['wire'], 'crc_verified_frames': [100, 0]}}))

    def test_settings_match_ignores_signal_but_detects_channel_parameter_changes(self):
        first = {'mode': {'rpi_avg_n': 8}, 'detector': {'settings': {}, 'channels': {
                 'upper': {'confirm_count': 4, 'threshold': 2, 'noise_avg': 10}}}}
        dynamic = {'mode': {'rpi_avg_n': 8}, 'detector': {'settings': {}, 'channels': {
                   'upper': {'confirm_count': 4, 'threshold': 2, 'noise_avg': 20}}}}
        self.assertEqual(settings_fingerprint(first), settings_fingerprint(dynamic))
        dynamic['detector']['channels']['upper']['confirm_count'] = 3
        self.assertNotEqual(settings_fingerprint(first), settings_fingerprint(dynamic))

    def test_dynamic_dc_profile_changes_are_allowed_but_persistent_config_changes_are_not(self):
        first = {'dc_guard': {'enabled': True, 'settle_ms': 200, 'last_age_s': 1, 'fix_count': 1}}
        dynamic = {'dc_guard': {'enabled': False, 'settle_ms': 100, 'last_age_s': 2, 'fix_count': 2}}
        self.assertEqual(settings_fingerprint(first), settings_fingerprint(dynamic))
        summary = {'monitor_restored': True, 'settings_unchanged': True, 'max_status_gap_s': .12,
                   'config_unchanged': True, 'core_process_unchanged': True,
                   'wire': {'crc_verified_frames': [100, 100], 'crc_bad': 0, 'crc_missing': 0,
                            'sequence_discontinuities': [0, 0], 'sequence_duplicates': [0, 0]}}
        self.assertTrue(capture_complete(summary))
        self.assertFalse(capture_complete({**summary, 'config_unchanged': False}))

    def test_archive_readback_requires_end_and_valid_gzip(self):
        wire = adc_frame(0, 10)+adc_frame(1, 10)
        checker = WireCheck(200)
        checker.feed(wire)
        records = [{'kind': 'ready', 'ready': True, 'status': {'mode': {'base_buf_len': 200}}},
                   {'kind': 'input_ready', 'probe': 'x'},
                   {'kind': 'transfer', 'captured': len(wire), 'data': base64.b64encode(wire).decode()},
                   {'kind': 'end', 'summary': {'capture_complete': True, 'transfers': 1,
                                              'status_samples': 0, 'wire': checker.summary()}}]
        archive = gzip.compress(''.join(json.dumps(r)+'\n' for r in records).encode())
        result = verify_archive(io.BytesIO(archive))
        self.assertTrue(result['end_seen'])
        self.assertEqual(result['mark_probe'], 'x')
        self.assertEqual(result['readback_errors'], [])
        late = records+[{'kind': 'input_ready', 'probe': 'late'}]
        result = verify_archive(io.BytesIO(gzip.compress(''.join(json.dumps(r)+'\n' for r in late).encode())))
        self.assertIn('record_after_end', result['readback_errors'])
        missing = [r for r in records if r['kind'] != 'transfer']
        result = verify_archive(io.BytesIO(gzip.compress(''.join(json.dumps(r)+'\n' for r in missing).encode())))
        self.assertIn('transfer_count_mismatch', result['readback_errors'])
        self.assertIn('wire_integrity_readback_mismatch', result['readback_errors'])
        with self.assertRaises(EOFError):
            verify_archive(io.BytesIO(archive[:-5]))


if __name__ == '__main__':
    unittest.main()

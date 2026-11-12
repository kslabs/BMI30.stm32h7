"""Prevent a closed but unusable observation from being reported as a good trial."""
import copy
import json
import struct
import unittest

from analyze_tag_capture import comparison_profile
from remote_tag_capture import capture_readiness, completion_quality, frame_header
from run_m02_tag_capture import capture_summary


def snapshot():
    return {'core_pid': '123', 'root': '/dev/mmcblk0p2', 'active_env': 'engine=verified', 'files': {},
            'status': {'ok': True, 'connection': {'connected': True},
                       'mode': {'selected': 6, 'stream_mode': 2, 'avg_n': 16, 'rpi_avg_n': 8},
                       'detector': {'active': True, 'settings': {'smooth_mode': 4},
                                    'channels': {'upper': {'enabled': True, 'confirm_count': 5,
                                                          'threshold': 2.0, 'noise_avg': 123.0},
                                                 'lower': {'enabled': True, 'confirm_count': 5,
                                                           'threshold': 2.0}}}}}


class CaptureQualityTests(unittest.TestCase):
    def quality(self, counts=None, final=None, **changes):
        options = dict(counts=counts or {'sample': 10, 'waveform': 5}, dropped={}, initial=snapshot(),
                       final=final or snapshot(), workers_alive=False, read_ring=False,
                       delivery_verified=False)
        options.update(changes)
        return completion_quality(**options)

    def test_ring_connection_and_historical_calibration_do_not_prove_delivery(self):
        self.assertFalse(capture_readiness(True, True, False)['ready'])
        self.assertFalse(capture_readiness(True, False, True)['ready'])
        self.assertTrue(capture_readiness(True, True, True)['pass_marking_ready'])
        self.assertTrue(capture_readiness(False)['ready'])
        self.assertFalse(capture_readiness(False)['pass_marking_ready'])

    def test_valid_background_is_complete_but_never_exact_raw_replay(self):
        result = self.quality()
        self.assertTrue(result['observer_complete'])
        self.assertFalse(result['exact_raw_replay'])

    def test_api_and_input_failures_invalidate_even_normally_ended_stream(self):
        for kind in ('status_error', 'waveform_error', 'input_error', 'control_error', 'fatal'):
            with self.subTest(kind=kind):
                result = self.quality(counts={'sample': 10, 'waveform': 5, kind: 1})
                self.assertFalse(result['observer_complete'])
                self.assertIn(kind, result['incomplete_reasons'])

    def test_final_state_error_and_failed_shutdown_invalidate_observation(self):
        self.assertFalse(self.quality(final={'error': 'HTTP timeout'})['observer_complete'])
        self.assertFalse(self.quality(workers_alive=True)['observer_complete'])
        self.assertFalse(self.quality(dropped={'waveform': 1})['observer_complete'])
        self.assertFalse(self.quality(counts={'sample': 10})['observer_complete'])

    def test_restart_and_changed_config_are_not_clean_trials(self):
        for key, value in (('core_pid', '124'), ('root', '/dev/sda2')):
            final = snapshot()
            final[key] = value
            self.assertFalse(self.quality(final=final)['observer_complete'])
        final = snapshot()
        final['status']['detector']['channels']['upper']['confirm_count'] = 6
        self.assertIn('profile_changed', self.quality(final=final)['incomplete_reasons'])

    def test_missing_ring_and_disconnect_invalidate_marking_only(self):
        missing = self.quality(read_ring=True)
        self.assertTrue(missing['observation_complete'])
        self.assertFalse(missing['pass_marking_complete'])
        valid = self.quality(read_ring=True, delivery_verified=True,
                             counts={'sample': 10, 'waveform': 5, 'pass_marking_ready': 1})
        self.assertTrue(valid['observer_complete'])
        interrupted = self.quality(read_ring=True, delivery_verified=True,
            counts={'sample': 10, 'waveform': 5, 'pass_marking_ready': 1, 'input_removed': 1})
        self.assertFalse(interrupted['pass_marking_complete'])

    def test_summary_separates_archive_closure_from_observation_errors(self):
        end = {'observer_complete': True, 'observation_complete': True, 'pass_marking_complete': True}
        result = capture_summary({'status_error': 1}, end, None, True, 0)
        self.assertTrue(result['archive_complete'])
        self.assertTrue(result['transport_complete'])
        self.assertFalse(result['observer_finished'])
        self.assertFalse(capture_summary({}, end, None, False, 0)['archive_complete'])
        self.assertFalse(capture_summary({}, end, None, True, 1)['observer_finished'])
        self.assertFalse(capture_summary({}, None, None, True, 0)['observer_finished'])

    def test_old_end_without_marking_integrity_does_not_prove_ready_trial(self):
        result = capture_summary({}, {'observer_complete': True}, None, True, 0)
        self.assertFalse(result['observer_finished'])

    def test_full_resolution_both_channel_payload_must_be_present(self):
        header = {'available': True, 'count': 2, 'base_buf_len': 2, 'step': 1,
                  'arrays': ['data0_even', 'data0_odd', 'prod0', 'data1_even', 'data1_odd', 'prod1']}

        def packet(h):
            encoded = json.dumps(h).encode()
            prefix = b'BMF1' + struct.pack('<I', len(encoded)) + encoded
            return prefix + b'\0' * (len(prefix) % 2 + 2 * h['count'] * len(h['arrays']))

        valid = packet(header)
        self.assertEqual(frame_header(valid)['count'], 2)
        with self.assertRaisesRegex(ValueError, 'payload'):
            frame_header(valid[:-1])
        with self.assertRaisesRegex(ValueError, 'both channels'):
            frame_header(packet({**header, 'arrays': header['arrays'][:3]}))
        with self.assertRaisesRegex(ValueError, 'full-resolution'):
            frame_header(packet({**header, 'step': 2}))


class ComparableProfileTests(unittest.TestCase):
    def session(self):
        return {'start': {'initial': snapshot()}}

    def test_channel_threshold_confirmation_and_both_averages_must_match(self):
        baseline = self.session()
        for channel, key, value in (('upper', 'confirm_count', 4), ('lower', 'threshold', 1.5)):
            changed = copy.deepcopy(baseline)
            changed['start']['initial']['status']['detector']['channels'][channel][key] = value
            self.assertNotEqual(comparison_profile(baseline), comparison_profile(changed))
        for key, value in (('avg_n', 8), ('rpi_avg_n', 16), ('stream_mode', 1)):
            changed = copy.deepcopy(baseline)
            changed['start']['initial']['status']['mode'][key] = value
            self.assertNotEqual(comparison_profile(baseline), comparison_profile(changed))

    def test_evolving_noise_and_view_controls_do_not_create_profile_mismatch(self):
        baseline = self.session()
        changed = copy.deepcopy(baseline)
        changed['start']['initial']['status']['detector']['channels']['upper']['noise_avg'] = 999.0
        changed['start']['initial']['status']['mode']['view_mode'] = 3
        self.assertEqual(comparison_profile(baseline), comparison_profile(changed))


if __name__ == '__main__':
    unittest.main()

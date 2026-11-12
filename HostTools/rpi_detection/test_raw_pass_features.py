"""Meaningful guards for the production-source passive waveform diagnostics."""
import base64
import binascii
import copy
from contextlib import contextmanager
import gzip
import json
from pathlib import Path
import struct
import unittest
import uuid

import numpy as np

from analyze_raw_pass_features import DEFAULT_HOST, DSP, ENGINE_NAME, prepare, run, sha256


def settings():
    return {'smooth_mode': 4, 'noise_divisions': 6, 'noise_area_percent': 60,
            'phase_shift_penalty': .02, 'phase_max_shift': 12,
            'peak_index_min': 20, 'peak_index_max': 66,
            'base_width_max': 40, 'base_width_enabled': True,
            'tag_filter_slot1': '', 'tag_filter_slot2': '', 'tag_filter_slot3': '',
            'filter_casino': False, 'filter_barkhausen': False,
            'filter_microwire': False, 'filter_paper': False}


def frame(ch, seq, dma, phase):
    signal = np.full(200, 32768, dtype='<u2')
    signal[35:50] += 50
    payload = signal.tobytes()
    header = struct.pack('<HBBIIHHIIIHH', 0xa55a, 1, (1 << ch) | 4,
                         seq, seq*5, 200, 0, 0, 200, dma, phase, 0)
    crc = binascii.crc_hqx(header[:30]+payload, 0xffff)
    return header[:30]+struct.pack('<H', crc)+payload


def session():
    status = {'mode': {'selected': 6, 'stream_mode': 2, 'rpi_avg_n': 1,
                       'avg_n': 2, 'base_buf_len': 200},
              'averaging': {'depth': 1}, 'detector': {'settings': settings(), 'channels': {}}}
    ready = {'kind': 'ready', 'at': 1000., 'monotonic': 0., 'status': status,
             'metadata': {'engine_sha256': sha256(DEFAULT_HOST/ENGINE_NAME)}}
    rows = [ready, {'kind': 'pass_start', 'pass_id': 1, 'start_mono': 1.}]
    # A same-phase jump at the final odd cohort on both antennas.
    for seq, dma, phase in ((1, 100, 0), (2, 101, 1), (3, 104, 0), (4, 109, 1)):
        for ch in (0, 1):
            data = frame(ch, seq, dma, phase)
            mono = seq + ch*.001
            rows.append({'kind': 'transfer', 'at': 1000.+mono, 'monotonic': mono,
                         'usb_at': 1000.+mono, 'result': 0, 'truncated': False,
                         'captured': len(data), 'length': len(data),
                         'data': base64.b64encode(data).decode()})
    rows.extend([{'kind': 'pass_end', 'pass_id': 1, 'human_result': 'miss',
                  'start_mono': 1., 'end_mono': 5., 'observed_mono': 5., 'epoch': 1005.},
                 {'kind': 'end', 'at': 1006., 'monotonic': 6., 'status': copy.deepcopy(status),
                  'summary': {'capture_complete': True, 'wire': {'crc_verified_frames': [4, 4]}}}])
    return rows


def write(path, rows):
    with gzip.open(path, 'wt', encoding='utf8') as stream:
        for row in rows:
            stream.write(json.dumps(row)+'\n')


@contextmanager
def temporary_session():
    # Keep tiny reproducible fixtures inside the authorized workspace. No
    # recursive cleanup or outside-system-TEMP access is needed.
    root = Path(__file__).resolve().parents[2]/'.tmp/raw-pass-feature-tests'
    root.mkdir(parents=True, exist_ok=True)
    path = root/uuid.uuid4().hex
    path.mkdir()
    yield path


class RawFeatureTests(unittest.TestCase):
    def test_global_peak_is_not_replaced_by_in_gate_peak(self):
        s = settings()
        s['smooth_mode'] = 0
        dsp = DSP(DEFAULT_HOST, s)
        raw = np.full(200, 32768, dtype=np.int32)
        raw[40], raw[120] = 32818, 32868
        row, _, product = dsp.pair_features((raw, raw), 0)
        self.assertEqual((row['global']['index'], row['global']['product']), (120, 10000))
        self.assertEqual((row['inside_gate']['index'], row['inside_gate']['product']), (40, 2500))
        self.assertFalse(row['global_peak_position_ok'])
        self.assertEqual(product.dtype, np.dtype('int64'))
        self.assertGreater(row['inside_gate']['noise_ref'], 0)

    def test_smoothing_and_fixed_shift_use_production_helpers(self):
        dsp = DSP(DEFAULT_HOST, settings())
        raw = np.arange(200, dtype=np.int32)+32768
        row, smooth, product = dsp.pair_features((raw, raw+7), 2)
        self.assertEqual(row['shift'], 2)
        np.testing.assert_array_equal(smooth[0][:4], raw[:4])
        np.testing.assert_array_equal(product[-2:], [0, 0])
        self.assertEqual(int(product[30]), int((smooth[0][30]-32768)*(smooth[1][32]-32768)))

    def test_named_profile_and_wrong_engine_fail_closed(self):
        s = settings()
        s['tag_filter_slot1'] = 'Casino'
        with self.assertRaisesRegex(ValueError, 'slots'):
            DSP(DEFAULT_HOST, s)
        with self.assertRaisesRegex(ValueError, 'SHA'):
            DSP(DEFAULT_HOST, settings(), '0'*64)

    def test_wire_pass_gaps_representatives_and_human_miss_preserved(self):
        with temporary_session() as folder:
            folder = Path(folder)
            source = folder/'capture.jsonl.gz'
            write(source, session())
            report = run(source, folder/'output', fixed_shifts=(0, 0))
            self.assertTrue(report['diagnostic_input_complete'])
            self.assertFalse(report['exact_replay'])
            self.assertTrue(report['source_cohort_gaps_observed'])
            self.assertEqual(len(report['cohort_gaps']), 2)  # one shared event
            self.assertEqual(report['wire_frame_counts'], [4, 4])
            self.assertEqual(report['pair_diagnostics']['fresh_pairs'], {'upper': 3, 'lower': 3})
            windows = [r for r in report['windows'] if r['pass_id'] == 1]
            self.assertEqual(len(windows), 2)
            self.assertTrue(all(r['human_result'] == 'miss' and r['acquisition_affected_by_cohort_gap'] for r in windows))
            with np.load(folder/'output/representatives.npz', allow_pickle=False) as data:
                self.assertTrue(data.files)
                self.assertEqual(data[data.files[0]].shape, (200,))
            with self.assertRaises(FileExistsError):
                run(source, folder/'output', fixed_shifts=(0, 0))

    def test_profile_change_rejected_before_output(self):
        with temporary_session() as folder:
            source = Path(folder)/'capture.jsonl.gz'
            rows = session()
            rows[-1]['status']['detector']['settings']['noise_area_percent'] = 66
            write(source, rows)
            with self.assertRaisesRegex(ValueError, 'profile changed'):
                prepare(source)

    def test_bad_crc_cannot_be_called_complete(self):
        with temporary_session() as folder:
            source = Path(folder)/'capture.jsonl.gz'
            rows = session()
            payload = bytearray(base64.b64decode(rows[2]['data']))
            payload[-1] ^= 1
            rows[2]['data'] = base64.b64encode(payload).decode()
            write(source, rows)
            report = run(source, Path(folder)/'output', fixed_shifts=(0, 0))
            self.assertFalse(report['diagnostic_input_complete'])
            self.assertTrue(report['issues'])

    def test_incomplete_and_reused_pass_marks_are_never_valid_windows(self):
        with temporary_session() as folder:
            source = Path(folder)/'capture.jsonl.gz'
            rows = session()
            rows.insert(-1, copy.deepcopy(rows[-2]))
            write(source, rows)
            _, _, valid, invalid = prepare(source)
            self.assertEqual(valid, [])
            self.assertTrue(invalid['invalid'])

    def test_open_last_pass_keeps_diagnostic_window_without_inventing_result(self):
        with temporary_session() as folder:
            source = Path(folder)/'capture.jsonl.gz'
            rows = [r for r in session() if r.get('kind') != 'pass_end']
            write(source, rows)
            _, _, windows, invalid = prepare(source)
            self.assertEqual(invalid['pending'], [1])
            self.assertEqual(windows[0]['human_result'], 'unlabelled')
            self.assertFalse(windows[0]['mark_window_complete'])


if __name__ == '__main__':
    unittest.main()

"""Counted physical confirmation, device/session freshness and mark integrity."""
import copy
import struct
import unittest

from passive_ring_input import (HOSTNAME, MAC, SERIAL, RingInputBuffer, RingMarks, build_verification,
                                session_id, validate_verification)
from ring_gestures import TouchGestures

RUN_ID = 'b30ce190-d953-4d86-82fb-faeef8ec22ea'
OTHER_ID = 'dd503ed1-2559-45d1-9d39-15fccbdd577b'
SEQUENCE = ['up']*3 + ['down']*3 + ['center']*3


def observation():
    def row(kind, **fields):
        return {'kind': kind, 'hostname': HOSTNAME, 'mac': MAC,
                'ring_session_id': RUN_ID, 'epoch': 980, **fields}
    rows = [row('ring_observation_start', serial=SERIAL, mode='calibrate'), row('ring_input_ready')]
    mapping = {'up': 'y-', 'down': 'y+', 'center': 'tap'}
    rows.extend(row('ring_gesture', gesture=mapping[button], epoch=981+index,
                    start_mono=10+index, end_mono=10.1+index)
                for index, button in enumerate(SEQUENCE))
    rows.append(row('ring_observation_end', ring_input_complete=True))
    return rows


def verified():
    return build_verification(observation(), RUN_ID, SEQUENCE, True, True, now=1000)


class PhysicalConfirmationTests(unittest.TestCase):
    def test_nine_received_gestures_alone_do_not_verify_physical_delivery(self):
        for order, count in ((False, False), (True, False), (False, True)):
            with self.subTest(order=order, count=count), self.assertRaisesRegex(ValueError, 'physical'):
                build_verification(observation(), RUN_ID, SEQUENCE, order, count, now=1000)

    def test_manifest_binds_observed_mapping_to_s07_and_exact_fresh_session(self):
        manifest = verified()
        self.assertEqual(manifest['button_gestures'], {'up': 'y-', 'down': 'y+', 'center': 'tap'})
        self.assertEqual(validate_verification(manifest, RUN_ID, now=1001),
                         {'y-': 'hit', 'y+': 'miss', 'tap': 'start'})
        self.assertEqual(len(manifest['source_sha256']), 64)
        with self.assertRaisesRegex(ValueError, 'identity/session'):
            validate_verification(manifest, OTHER_ID, now=1001)
        with self.assertRaisesRegex(ValueError, 'stale'):
            validate_verification(manifest, RUN_ID, now=5000)

    def test_historical_s10_wrong_mac_and_unknown_session_are_rejected(self):
        for key, value in (('hostname', 'BMI30-D5EB8F26C'), ('mac', '00:11:22:33:44:55'),
                           ('serial', 'wrong'), ('session_id', OTHER_ID)):
            manifest = verified()
            manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_verification(manifest, RUN_ID, now=1001)
        for value in ('', None, RUN_ID.replace('-', '')):
            with self.assertRaises((ValueError, AttributeError)):
                session_id(value)

    def test_incomplete_extra_ambiguous_and_mixed_observations_cannot_be_verified(self):
        variants = []
        missing = observation()
        del missing[2]
        variants.append(missing)
        extra = observation()
        extra.insert(-1, copy.deepcopy(extra[2]))
        variants.append(extra)
        ambiguous = observation()
        ambiguous[2]['gesture'] = 'unknown'
        variants.append(ambiguous)
        interrupted = observation()
        interrupted.append({**interrupted[0], 'kind': 'ring_error', 'error': 'SYN_DROPPED'})
        variants.append(interrupted)
        mixed = observation()
        mixed[2]['ring_session_id'] = OTHER_ID
        variants.append(mixed)
        variants.append(observation()[:-1])
        for records in variants:
            with self.subTest(records=records), self.assertRaises(ValueError):
                build_verification(records, RUN_ID, SEQUENCE, True, True, now=1000)

    def test_physical_order_changes_cannot_silently_keep_the_old_mapping(self):
        wrong_order = SEQUENCE.copy()
        wrong_order[0], wrong_order[3] = wrong_order[3], wrong_order[0]
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            build_verification(observation(), RUN_ID, wrong_order, True, True, now=1000)


class RingMarkTests(unittest.TestCase):
    def marker(self):
        return RingMarks({'tap': 'start', 'y-': 'hit', 'y+': 'miss'})

    def test_pass_boundaries_use_remote_input_times_and_preserve_outcome(self):
        marker = self.marker()
        start = marker.gesture({'gesture': 'tap', 'start_mono': 10.125})[0]
        finish = marker.gesture({'gesture': 'y+', 'start_mono': 12.625})[0]
        self.assertEqual((start['kind'], start['start_mono']), ('pass_start', 10.125))
        self.assertEqual((finish['kind'], finish['human_result'], finish['end_mono']),
                         ('pass_end', 'miss', 12.625))
        self.assertEqual(marker.summary()['marked_passes'], 1)

    def test_orphan_repeat_and_stop_never_become_invented_misses(self):
        marker = self.marker()
        self.assertEqual(marker.gesture({'gesture': 'y-', 'start_mono': 1})[0]['kind'], 'ignored_mark')
        marker.gesture({'gesture': 'tap', 'start_mono': 2})
        rows = marker.gesture({'gesture': 'tap', 'start_mono': 3})
        self.assertEqual(rows[0]['human_result'], 'unlabelled')
        self.assertEqual(marker.finish(4)[0]['human_result'], 'unlabelled')
        self.assertEqual(marker.summary()['unlabelled_passes'], 2)
        self.assertEqual(marker.summary()['invalid_marks'], 2)
        self.assertEqual(marker.summary()['marked_passes'], 2)

    def test_uncalibrated_button_is_preserved_as_ignored_and_invalidates_ground_truth(self):
        marker = self.marker()
        row = marker.gesture({'gesture': 'x+', 'start_mono': 1})[0]
        self.assertEqual(row['reason'], 'uncalibrated_gesture')
        self.assertEqual(marker.summary()['invalid_marks'], 1)
        self.assertEqual(marker.summary()['marked_passes'], 0)


class InterfaceDiagnosticTests(unittest.TestCase):
    def packet(self, kind, code, value, usec=250000):
        return struct.pack('llHHi', 100, usec, kind, code, value)

    def tap(self):
        return b''.join(self.packet(kind, code, value, usec=index)
                        for index, (kind, code, value) in enumerate(
                            ((3, 53, 100), (3, 54, 100), (3, 57, 7),
                             (0, 0, 0), (3, 57, -1), (0, 0, 0))))

    def test_companion_consumer_keys_are_saved_with_remote_times(self):
        companion = RingInputBuffer('/dev/input/event5', 'companion')
        rows = list(companion.feed(self.packet(1, 115, 1) + self.packet(1, 115, 0)))
        self.assertEqual([row['value'] for row in rows], [1, 0])
        self.assertTrue(all(row['type'] == 1 and row['interface_role'] == 'companion' for row in rows))
        self.assertTrue(all(row['device'] == '/dev/input/event5' and row['input_mono'] == 100.25 for row in rows))
        self.assertEqual(companion.counts, {'1': 2})

    def test_only_selected_touch_generates_gestures(self):
        touch = RingInputBuffer('/dev/input/event6', 'touch', TouchGestures())
        companion = RingInputBuffer('/dev/input/event5', 'companion')
        touch_rows = list(touch.feed(self.tap()))
        companion_rows = list(companion.feed(self.tap()))
        gestures = [row for row in touch_rows if row['kind'] == 'ring_gesture']
        self.assertEqual(len(gestures), 1)
        self.assertEqual(gestures[0]['gesture'], 'tap')
        self.assertEqual(gestures[0]['device'], '/dev/input/event6')
        self.assertTrue(all(row['kind'] == 'ring_input' for row in companion_rows))
        with self.assertRaises(ValueError):
            RingInputBuffer('/dev/input/event5', 'companion', TouchGestures())

    def test_mark_mode_companion_reports_are_diagnostic_and_never_double_mark(self):
        touch = RingInputBuffer('/dev/input/event6', 'touch', TouchGestures())
        companion = RingInputBuffer('/dev/input/event5', 'companion')
        marker = RingMarks({'tap': 'start', 'y-': 'hit', 'y+': 'miss'})
        # A companion can emit consumer keys AND touch-shaped reports. Neither
        # is a second gesture source, even while the primary tap starts a pass.
        consumer = self.packet(1, 28, 1) + self.packet(1, 28, 0)
        observed, marks = [], []
        for info, payload in ((companion, consumer+self.tap()), (touch, self.tap())):
            for row in info.feed(payload):
                observed.append(row)
                if row['kind'] == 'ring_gesture':
                    marks.extend(marker.gesture(row))
        self.assertEqual(sum(row['kind'] == 'ring_gesture' for row in observed), 1)
        self.assertEqual([row['kind'] for row in marks], ['pass_start'])
        self.assertEqual(marker.summary()['pass_started'], 1)
        self.assertEqual(marker.summary()['invalid_marks'], 0)
        companion_rows = [row for row in observed if row['device'] == companion.device]
        self.assertEqual(len(companion_rows), 8)
        self.assertTrue(all(row['kind'] == 'ring_input' for row in companion_rows))

    def test_partial_events_from_distinct_interfaces_never_mix(self):
        touch = RingInputBuffer('/dev/input/event6', 'touch', TouchGestures())
        companion = RingInputBuffer('/dev/input/event5', 'companion')
        raw = self.packet(3, 53, 1234)
        self.assertEqual(list(touch.feed(raw[:5])), [])
        self.assertEqual(list(companion.feed(self.packet(1, 114, 1)))[0]['code'], 114)
        rows = list(touch.feed(raw[5:]))
        self.assertEqual((rows[0]['type'], rows[0]['code'], rows[0]['value']), (3, 53, 1234))
        self.assertEqual(touch.pending, b'')
        self.assertEqual(companion.pending, b'')

    def test_companion_lost_input_is_retained_and_invalidates_observation(self):
        companion = RingInputBuffer('/dev/input/event5', 'companion')
        events = companion.feed(self.packet(0, 3, 0))
        self.assertEqual(next(events)['code'], 3)
        with self.assertRaisesRegex(ValueError, 'SYN_DROPPED'):
            next(events)


if __name__ == '__main__':
    unittest.main()

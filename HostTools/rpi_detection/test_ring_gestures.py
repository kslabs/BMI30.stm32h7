import unittest

from ring_gestures import TouchGestures, decode_records


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.decoder = TouchGestures()
        self.time = 0

    def frame(self, *events):
        self.time += 0.01
        for code, value in events:
            self.decoder.update(3, code, value, self.time)
        return self.decoder.update(0, 0, 0, self.time)

    def test_repeated_taps_with_omitted_unchanged_coordinates(self):
        self.frame((57, 1), (53, 1904), (54, 1904))
        self.assertEqual(self.frame((57, -1))["gesture"], "tap")
        self.frame((57, 2))
        self.assertEqual(self.frame((57, -1))["gesture"], "tap")

    def test_gesture_starts_at_first_complete_report(self):
        self.frame((57, 1), (53, 1904), (54, 1904))
        self.frame((57, -1))
        self.frame((57, 2), (54, 3200))
        self.frame((54, 2000))
        self.frame((54, 800))
        result = self.frame((57, -1))
        self.assertEqual((result["gesture"], result["dx"], result["dy"]), ("y-", 0, -2400))

    def test_horizontal_stroke_counts_once_at_release(self):
        self.assertIsNone(self.frame((57, 1), (53, 300), (54, 1159)))
        for x in (600, 1200, 1800, 2400, 3050):
            self.assertIsNone(self.frame((53, x)))
        self.assertEqual(self.frame((57, -1))["gesture"], "x+")
        self.assertIsNone(self.frame())

    def test_round_trip_is_not_a_tap(self):
        self.frame((57, 1), (53, 300), (54, 1000))
        self.frame((53, 3000))
        self.frame((53, 300))
        self.assertEqual(self.frame((57, -1))["gesture"], "unknown")

    def test_missing_coordinates_and_reconnect_do_not_make_taps(self):
        self.frame((57, 1), (53, 1904), (54, 1904))
        self.decoder.reset()
        self.frame((57, 2))
        self.assertEqual(self.frame((57, -1))["gesture"], "unknown")

    def test_dropped_events_reject_calibration(self):
        with self.assertRaises(ValueError):
            self.decoder.update(0, 3, 0, 1)

    def test_recording_started_on_connected_device_uses_initial_state(self):
        records = [{"kind": "input_ready", "device": "ring",
                    "absolute_state": {"53": 1904, "54": 1904}}]
        for mono, kind, code, value in ((1, 3, 57, 7), (1, 0, 0, 0),
                                        (2, 3, 57, -1), (2, 0, 0, 0)):
            records.append({"kind": "input", "device": "ring", "epoch": mono,
                            "input_mono": mono, "type": kind, "code": code, "value": value})
        self.assertEqual([g["gesture"] for g in decode_records(records)], ["tap"])


if __name__ == "__main__":
    unittest.main()

"""Tests of data integrity: held keys and incomplete human-labelled passages."""
import unittest

from remote_pass_logger import KeyPresses, PassMarks, validate_status


class PassageTests(unittest.TestCase):
    def test_held_key_counts_once_until_release(self):
        keys = KeyPresses({10: "start"})
        self.assertEqual(keys.update(1, 10, 1), "start")
        for value in (1, 2, 2, 1):
            self.assertIsNone(keys.update(1, 10, value))
        self.assertIsNone(keys.update(1, 10, 0))
        self.assertEqual(keys.update(1, 10, 1), "start")

    def test_result_without_start_is_not_a_pass(self):
        state = PassMarks()
        self.assertEqual(state.mark("miss", 2)[0]["kind"], "ignored_mark")
        self.assertEqual(state.completed, [])

    def test_repeated_start_preserves_unlabelled_pass(self):
        state = PassMarks()
        state.mark("start", 10)
        records = state.mark("start", 20)
        self.assertEqual(records[0]["human_result"], "unlabelled")
        self.assertEqual(records[0]["pass_id"], 1)
        self.assertEqual(records[1]["pass_id"], 2)
        self.assertEqual(state.mark("hit", 23)[0]["human_result"], "hit")
        self.assertEqual(len(state.completed), 2)
        self.assertEqual(state.mark("miss", 24)[0]["kind"], "ignored_mark")

    def test_hit_miss_and_unfinished_are_distinct(self):
        state = PassMarks()
        state.mark("start", 1)
        state.mark("hit", 2)
        state.mark("start", 3)
        state.mark("miss", 4)
        state.mark("start", 5)
        state.finish("unlabelled", 6)
        self.assertEqual([p["human_result"] for p in state.completed], ["hit", "miss", "unlabelled"])

    def test_refuses_stale_status_and_player_mode(self):
        good = {"ok": True, "connection": {"connected": True},
                "detector": {"active": True, "channels": {"upper": {"enabled": True}}}}
        validate_status(good, "upper")
        for change in ({"stale": True}, {"busy": True}, {"ok": False},
                       {"player_recorder": {"recording": True}},
                       {"player_recorder": {"playback_active": True}},
                       {"connection": {"connected": False}}):
            with self.assertRaises(ValueError):
                validate_status({**good, **change}, "upper")
        with self.assertRaises(ValueError):
            validate_status(good, "lower")


if __name__ == "__main__":
    unittest.main()

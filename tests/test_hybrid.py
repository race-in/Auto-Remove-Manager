import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from hybrid import ModeTracker


class ModeTrackerTests(unittest.TestCase):
    def test_switches_to_racing_after_five_continuous_seconds(self):
        tracker = ModeTracker(100_000_000, 5)
        self.assertIsNone(tracker.sample(100_000_000, 0))
        self.assertIsNone(tracker.sample(110_000_000, 4.9))
        self.assertEqual(tracker.sample(100_000_000, 5), "racing")

    def test_high_speed_dip_resets_streak(self):
        tracker = ModeTracker(100_000_000, 5)
        self.assertIsNone(tracker.sample(110_000_000, 0))
        self.assertIsNone(tracker.sample(99_000_000, 2))
        self.assertIsNone(tracker.sample(110_000_000, 3))
        self.assertIsNone(tracker.sample(110_000_000, 7.9))
        self.assertEqual(tracker.sample(110_000_000, 8), "racing")

    def test_returns_to_normal_after_five_seconds_below_threshold(self):
        tracker = ModeTracker(100_000_000, 5)
        tracker.sample(110_000_000, 0)
        tracker.sample(110_000_000, 5)
        self.assertIsNone(tracker.sample(99_000_000, 6))
        self.assertIsNone(tracker.sample(99_000_000, 10.9))
        self.assertEqual(tracker.sample(99_000_000, 11), "normal")

    def test_low_speed_dip_resets_return_streak(self):
        tracker = ModeTracker(100_000_000, 5)
        tracker.sample(110_000_000, 0)
        tracker.sample(110_000_000, 5)
        tracker.sample(99_000_000, 6)
        self.assertIsNone(tracker.sample(100_000_000, 8))
        self.assertIsNone(tracker.sample(99_000_000, 9))
        self.assertIsNone(tracker.sample(99_000_000, 13.9))
        self.assertEqual(tracker.sample(99_000_000, 14), "normal")

    def test_invalid_sample_resets_both_streaks_without_switching(self):
        tracker = ModeTracker(100_000_000, 5)
        tracker.sample(110_000_000, 0)
        self.assertIsNone(tracker.sample(float("nan"), 3))
        self.assertIsNone(tracker.sample(110_000_000, 4))
        self.assertIsNone(tracker.sample(110_000_000, 8.9))
        self.assertEqual(tracker.sample(110_000_000, 9), "racing")


if __name__ == "__main__":
    unittest.main()

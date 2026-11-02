import pathlib
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import Mock, patch

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import manager
import racing
from hybrid import ModeTracker, _normal_cleanup, _normal_removal_allowed, _racing_cleanup


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

    def test_recovery_state_requires_fresh_high_streak(self):
        tracker = ModeTracker(100_000_000, 5, initial_mode=None)
        self.assertIsNone(tracker.sample(110_000_000, 10))
        self.assertIsNone(tracker.sample(110_000_000, 14.9))
        self.assertEqual(tracker.sample(110_000_000, 15), "racing")

    def test_recovery_state_requires_fresh_low_streak(self):
        tracker = ModeTracker(100_000_000, 5, initial_mode=None)
        self.assertIsNone(tracker.sample(90_000_000, 10))
        self.assertIsNone(tracker.sample(90_000_000, 14.9))
        self.assertEqual(tracker.sample(90_000_000, 15), "normal")

    def test_recovery_state_high_dip_resets_classification(self):
        tracker = ModeTracker(100_000_000, 5, initial_mode=None)
        self.assertIsNone(tracker.sample(110_000_000, 10))
        self.assertIsNone(tracker.sample(90_000_000, 12))
        self.assertIsNone(tracker.sample(110_000_000, 13))
        self.assertIsNone(tracker.sample(110_000_000, 17.9))
        self.assertEqual(tracker.sample(110_000_000, 18), "racing")



class NormalRemovalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.task = {"delete_data": True}
        self.safe_hybrid = {
            "preview_only": False,
            "allow_removal": True,
            "allow_data_deletion": True,
        }
        self.safe_global = {
            "preview_only": False,
            "allow_removal": True,
            "allow_data_deletion": True,
        }

    def test_all_safeguards_are_required_for_payload_deletion(self):
        self.assertTrue(_normal_removal_allowed(
            self.task, self.safe_hybrid, self.safe_global
        ))

    def test_hybrid_removal_switch_is_required(self):
        hybrid = dict(self.safe_hybrid, allow_removal=False)
        self.assertFalse(_normal_removal_allowed(self.task, hybrid, self.safe_global))

    def test_hybrid_payload_switch_is_required(self):
        hybrid = dict(self.safe_hybrid, allow_data_deletion=False)
        self.assertFalse(_normal_removal_allowed(self.task, hybrid, self.safe_global))

    def test_global_safety_switches_are_required(self):
        for key, value in (("preview_only", True), ("allow_removal", False),
                           ("allow_data_deletion", False)):
            settings = dict(self.safe_global, **{key: value})
            self.assertFalse(_normal_removal_allowed(
                self.task, self.safe_hybrid, settings
            ))

    def test_non_payload_removal_does_not_need_payload_opt_in(self):
        task = {"delete_data": False}
        hybrid = dict(self.safe_hybrid, allow_data_deletion=False)
        settings = dict(self.safe_global, allow_data_deletion=False)
        self.assertTrue(_normal_removal_allowed(task, hybrid, settings))



class HybridCleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.state_dir = pathlib.Path(self.tempdir.name)
        self.logger = Mock()

    def test_racing_preview_never_calls_payload_removal(self):
        torrent = {
            "hash": "abc123",
            "name": "eligible seed",
            "state": "uploading",
            "ratio": 1.5,
            "upspeed": 100,
        }
        api = Mock()
        api.client = "qbittorrent"
        api.torrents.return_value = [torrent]
        config = {
            "preview_only": True,
            "allow_removal": True,
            "allow_data_deletion": True,
            "ratio_min": 1.0,
            "upload_max_mb": 10.0,
        }
        settings = {
            "preview_only": False,
            "allow_removal": True,
            "allow_data_deletion": True,
        }

        _racing_cleanup("qb", api, config, settings, self.state_dir, self.logger)

        api.remove_with_data.assert_not_called()
        api.torrents.assert_called_once()

    def test_normal_cleanup_preserves_original_strategy_and_delete_data(self):
        task = {
            "client": "qbittorrent",
            "delete_data": True,
            "strategies": {
                "remove": "(ratio > 1 or ratio = 1) and (seeding_time > 300 or seeding_time = 300) and upload_speed < 1024"
            },
        }
        args = Namespace(state_dir=self.state_dir, log_dir=self.state_dir / "logs")
        hybrid = {
            "preview_only": True,
            "allow_removal": False,
            "allow_data_deletion": False,
        }
        settings = {
            "preview_only": True,
            "allow_removal": False,
            "allow_data_deletion": False,
        }
        observed = {}

        def inspect_upstream(child_args, preview):
            with pathlib.Path(child_args.upstream_config).open(encoding="utf-8") as stream:
                observed["task"] = yaml.safe_load(stream)
            observed["preview"] = preview
            return 0

        with patch.object(manager, "invoke_upstream", side_effect=inspect_upstream):
            _normal_cleanup("qb", task, args, hybrid, settings, self.logger)

        self.assertEqual(observed["task"], {"qb": task})
        self.assertTrue(observed["task"]["qb"]["delete_data"])
        self.assertEqual(
            observed["task"]["qb"]["strategies"]["remove"],
            task["strategies"]["remove"],
        )
        self.assertTrue(observed["preview"])

    def test_manager_and_racing_share_the_same_exclusive_lock(self):
        manager_lock = manager.acquire_lock(self.state_dir)
        self.assertIsNotNone(manager_lock)
        try:
            self.assertIsNone(racing._try_run_lock(self.state_dir))
        finally:
            manager_lock.close()

        racing_lock = racing._try_run_lock(self.state_dir)
        self.assertIsNotNone(racing_lock)
        try:
            self.assertIsNone(manager.acquire_lock(self.state_dir))
        finally:
            racing_lock.close()


if __name__ == "__main__":
    unittest.main()

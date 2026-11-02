import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import racing


class RacingSafetyTests(unittest.TestCase):
    def test_destructive_mode_requires_explicit_json_booleans(self):
        safe = {"preview_only": False, "allow_removal": True, "allow_data_deletion": True}
        self.assertTrue(racing.removal_authorized(safe))
        for key in safe:
            invalid = dict(safe)
            invalid[key] = str(safe[key]).lower()
            with self.subTest(key=key):
                self.assertFalse(racing.removal_authorized(invalid))

    def test_qbittorrent_delete_is_confirmed_only_after_hash_disappears(self):
        api = racing.ClientAPI("test", {"client": "qbittorrent", "host": "http://127.0.0.1:9001"})
        with mock.patch.object(api, "_qb_post"), mock.patch.object(api, "torrents", side_effect=[[{"hash": "x"}], []]), mock.patch.object(racing.time, "sleep") as sleep:
            succeeded, unconfirmed = api.remove_with_data(["x"])
        self.assertEqual(succeeded, ["x"])
        self.assertEqual(unconfirmed, [])
        sleep.assert_called_once_with(0.25)

    def test_qbittorrent_delete_does_not_claim_success_if_hash_remains(self):
        api = racing.ClientAPI("test", {"client": "qbittorrent", "host": "http://127.0.0.1:9001"})
        with mock.patch.object(api, "_qb_post"), mock.patch.object(api, "torrents", return_value=[{"hash": "x"}]), mock.patch.object(racing.time, "sleep"):
            succeeded, unconfirmed = api.remove_with_data(["x"])
        self.assertEqual(succeeded, [])
        self.assertEqual(unconfirmed, ["x"])

    def test_qbittorrent_verification_error_fails_closed(self):
        api = racing.ClientAPI("test", {"client": "qbittorrent", "host": "http://127.0.0.1:9001"})
        with mock.patch.object(api, "_qb_post"), mock.patch.object(api, "torrents", side_effect=RuntimeError("API unavailable")):
            succeeded, unconfirmed = api.remove_with_data(["x"])
        self.assertEqual(succeeded, [])
        self.assertEqual(unconfirmed, ["x"])


if __name__ == "__main__":
    unittest.main()

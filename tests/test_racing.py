import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import racing


class SpeedGateTests(unittest.TestCase):
    def test_requires_full_five_second_streak(self):
        gate = racing.SpeedGate(100_000_000, 5)
        self.assertFalse(gate.sample(110_000_000, 10.0))
        self.assertFalse(gate.sample(110_000_000, 14.9))
        self.assertTrue(gate.sample(110_000_000, 15.0))
        self.assertFalse(gate.sample(110_000_000, 16.0))

    def test_dip_resets_full_streak(self):
        gate = racing.SpeedGate(100_000_000, 5)
        self.assertFalse(gate.sample(110_000_000, 0.0))
        self.assertFalse(gate.sample(99_999_999, 4.0))
        self.assertFalse(gate.sample(110_000_000, 4.1))
        self.assertFalse(gate.sample(110_000_000, 9.0))
        self.assertTrue(gate.sample(110_000_000, 9.1))

    def test_invalid_sample_resets_streak(self):
        gate = racing.SpeedGate(100_000_000, 5)
        self.assertFalse(gate.sample(110_000_000, 0.0))
        self.assertFalse(gate.sample(None, 2.0))
        self.assertFalse(gate.sample(110_000_000, 2.1))
        self.assertFalse(gate.sample(110_000_000, 7.0))
        self.assertTrue(gate.sample(110_000_000, 7.1))

    def test_gate_rearms_after_speed_falls_below_threshold(self):
        gate = racing.SpeedGate(100_000_000, 5)
        self.assertFalse(gate.sample(110_000_000, 0.0))
        self.assertTrue(gate.sample(110_000_000, 5.0))
        self.assertFalse(gate.sample(90_000_000, 6.0))
        self.assertFalse(gate.sample(110_000_000, 7.0))
        self.assertTrue(gate.sample(110_000_000, 12.0))


class EligibilityTests(unittest.TestCase):
    def test_qbittorrent_uses_ratio_and_live_upload_only(self):
        torrents = [
            {"hash": "a", "state": "uploading", "ratio": 1.0, "upspeed": 0},
            {"hash": "b", "state": "stalledUP", "ratio": 2.0, "upspeed": 10_000_000},
            {"hash": "c", "state": "uploading", "ratio": 0.99, "upspeed": 0},
            {"hash": "d", "state": "uploading", "ratio": 2.0, "upspeed": 10_000_001},
            {"hash": "e", "state": "downloading", "ratio": 3.0, "upspeed": 0},
            {"hash": "f", "state": "uploading", "ratio": None, "upspeed": 0},
            {"hash": "g", "state": "uploading", "ratio": 2.0},
        ]
        result = racing.eligible_torrents("qbittorrent", torrents)
        self.assertEqual([item["hash"] for item in result], ["a", "b"])

    def test_deluge_uses_ratio_and_live_upload_only(self):
        torrents = [
            {"hash": "a", "state": "Seeding", "ratio": 1.0, "upload_payload_rate": 0},
            {"hash": "b", "state": "Seeding", "ratio": 1.5, "upload_payload_rate": 10_000_000},
            {"hash": "c", "state": "Seeding", "ratio": 0.9, "upload_payload_rate": 0},
            {"hash": "d", "state": "Seeding", "ratio": 2.0, "upload_payload_rate": 10_000_001},
            {"hash": "e", "state": "Downloading", "ratio": 2.0, "upload_payload_rate": 0},
            {"hash": "f", "state": "Seeding", "ratio": 2.0},
        ]
        result = racing.eligible_torrents("deluge", torrents)
        self.assertEqual([item["hash"] for item in result], ["a", "b"])

    def test_seeding_time_is_not_required(self):
        torrent = {"hash": "fresh", "state": "uploading", "ratio": 1.0, "upspeed": 1}
        self.assertEqual(racing.eligible_torrents("qbittorrent", [torrent]), [torrent])

    def test_unknown_client_is_rejected(self):
        with self.assertRaises(ValueError):
            racing.eligible_torrents("transmission", [])



class RevalidationTests(unittest.TestCase):
    def test_only_original_candidates_still_eligible_are_returned(self):
        preview = [
            {"hash": "keep", "state": "uploading", "ratio": 1.2, "upspeed": 0},
            {"hash": "stale", "state": "uploading", "ratio": 1.2, "upspeed": 0},
        ]
        latest = [
            {"hash": "keep", "state": "uploading", "ratio": 1.2, "upspeed": 0},
            {"hash": "stale", "state": "downloading", "ratio": 1.2, "upspeed": 0},
            {"hash": "new", "state": "uploading", "ratio": 2.0, "upspeed": 0},
        ]
        self.assertEqual(
            racing.revalidated_hashes("qbittorrent", preview, latest),
            ["keep"],
        )

    def test_ratio_or_upload_change_revokes_eligibility(self):
        preview = [
            {"hash": "ratio", "state": "uploading", "ratio": 1.1, "upspeed": 0},
            {"hash": "upload", "state": "uploading", "ratio": 1.1, "upspeed": 0},
        ]
        latest = [
            {"hash": "ratio", "state": "uploading", "ratio": 0.9, "upspeed": 0},
            {"hash": "upload", "state": "uploading", "ratio": 1.1, "upspeed": 11_000_000},
        ]
        self.assertEqual(
            racing.revalidated_hashes("qbittorrent", preview, latest),
            [],
        )


class QbittorrentConnectionTests(unittest.TestCase):
    def test_authentication_precedes_plain_text_version_check(self):
        from unittest.mock import patch

        calls = []

        class FakeResponse:
            def __init__(self, status_code=200, text="", json_data=None):
                self.status_code = status_code
                self.text = text
                self.json_data = json_data

            def raise_for_status(self):
                return None

            def json(self):
                if self.json_data is None:
                    raise AssertionError("Unexpected JSON parsing")
                return self.json_data

        class FakeSession:
            trust_env = True

            def post(self, url, **kwargs):
                calls.append(("POST", url))
                return FakeResponse(status_code=204)

            def get(self, url, **kwargs):
                calls.append(("GET", url))
                if url.endswith("/api/v2/app/webapiVersion"):
                    return FakeResponse(text="2.16.2")
                if url.endswith("/api/v2/transfer/info"):
                    return FakeResponse(json_data={"dl_info_speed": 0})
                raise AssertionError("Unexpected endpoint: " + url)

        session = FakeSession()

        with patch("requests.Session", return_value=session):
            api = racing.ClientAPI(
                "test-qb",
                {
                    "client": "qbittorrent",
                    "host": "http://127.0.0.1:9001",
                    "username": "test",
                    "password": "test",
                },
            )
            api.connect()

        self.assertEqual(
            [method for method, _ in calls],
            ["POST", "GET", "GET"],
        )
        self.assertTrue(calls[0][1].endswith("/api/v2/auth/login"))
        self.assertTrue(calls[1][1].endswith("/api/v2/app/webapiVersion"))
        self.assertTrue(calls[2][1].endswith("/api/v2/transfer/info"))


if __name__ == "__main__":
    unittest.main()

import pathlib
import sys
import unittest
import threading

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



class QbittorrentRemovalTests(unittest.TestCase):
    def _api(self, session):
        api = racing.ClientAPI(
            "qb-test",
            {"client": "qbittorrent", "host": "http://127.0.0.1:9001"},
        )
        api.session = session
        return api

    def _fake_clock(self):
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        return now, sleep

    def test_delete_batch_waits_and_reports_partial_timeout(self):
        from unittest.mock import Mock, patch

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"hash": "def456"}]

        session = Mock()
        session.post.return_value = response
        session.get.return_value = response
        api = self._api(session)
        now, fake_sleep = self._fake_clock()

        with patch.object(racing.time, "monotonic",
                          side_effect=lambda: now[0]), \
             patch.object(racing.time, "sleep", side_effect=fake_sleep):
            succeeded, failed = api.remove_with_data(["abc123", "def456"])

        self.assertEqual(succeeded, ["abc123"])
        self.assertEqual(failed, ["def456"])
        self.assertEqual(now[0], racing.QB_DELETE_VERIFY_TIMEOUT)
        self.assertGreater(session.get.call_count, 1)
        self.assertEqual(
            session.post.call_args[1]["data"],
            {"hashes": "abc123|def456", "deleteFiles": "true"},
        )

    def test_delete_stops_when_all_hashes_disappear(self):
        from unittest.mock import Mock, patch

        accepted = Mock()
        accepted.raise_for_status.return_value = None

        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = [{"hash": "def456"}]

        second = Mock()
        second.raise_for_status.return_value = None
        second.json.return_value = []

        session = Mock()
        session.post.return_value = accepted
        session.get.side_effect = [first, second]

        api = self._api(session)
        now, fake_sleep = self._fake_clock()

        with patch.object(racing.time, "monotonic",
                          side_effect=lambda: now[0]), \
             patch.object(racing.time, "sleep", side_effect=fake_sleep):
            succeeded, failed = api.remove_with_data(["abc123", "def456"])

        self.assertEqual(succeeded, ["abc123", "def456"])
        self.assertEqual(failed, [])
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(now[0], racing.QB_DELETE_VERIFY_INTERVAL)

    def test_accepted_delete_finishes_verification_after_shutdown(self):
        from threading import Event
        from unittest.mock import Mock, patch

        stop = Event()
        accepted = Mock()
        accepted.raise_for_status.return_value = None
        still_present = Mock()
        still_present.raise_for_status.return_value = None
        still_present.json.return_value = [{"hash": "abc123"}]
        gone = Mock()
        gone.raise_for_status.return_value = None
        gone.json.return_value = []

        session = Mock()
        session.post.return_value = accepted

        def verify_side_effect(*args, **kwargs):
            if session.get.call_count == 1:
                stop.set()
                return still_present
            return gone

        session.get.side_effect = verify_side_effect
        api = self._api(session)
        now, fake_sleep = self._fake_clock()

        with patch.object(racing.time, "monotonic",
                          side_effect=lambda: now[0]), \\
             patch.object(racing.time, "sleep", side_effect=fake_sleep):
            succeeded, failed = api.remove_with_data(["abc123"], stop_event=stop)

        self.assertEqual(succeeded, ["abc123"])
        self.assertEqual(failed, [])
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(session.get.call_count, 2)

    def test_delete_http_error_is_not_reported_as_success(self):
        from unittest.mock import Mock

        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("HTTP failure")

        session = Mock()
        session.post.return_value = response
        api = self._api(session)

        with self.assertRaises(RuntimeError):
            api.remove_with_data(["abc123"])

        session.get.assert_not_called()

    def test_verification_http_error_propagates(self):
        from unittest.mock import Mock

        accepted = Mock()
        accepted.raise_for_status.return_value = None

        failed_response = Mock()
        failed_response.raise_for_status.side_effect = RuntimeError(
            "verification HTTP failure"
        )

        session = Mock()
        session.post.return_value = accepted
        session.get.return_value = failed_response
        api = self._api(session)

        with self.assertRaises(RuntimeError):
            api.remove_with_data(["abc123"])


    def test_malformed_verification_entries_fail_closed(self):
        from unittest.mock import Mock
        malformed_entries = [[{"name": "missing hash"}], [{"hash": ""}], [{"hash": "   "}], ["not-a-dictionary"], [None]]
        for entries in malformed_entries:
            with self.subTest(entries=entries):
                response = Mock()
                response.raise_for_status.return_value = None
                response.json.return_value = entries
                session = Mock()
                session.post.return_value = response
                session.get.return_value = response
                api = self._api(session)
                with self.assertRaises(RuntimeError):
                    api.remove_with_data(["abc123"])

    def test_non_list_verification_response_fails_closed(self):
        from unittest.mock import Mock
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"error": "invalid response"}
        session = Mock()
        session.post.return_value = response
        session.get.return_value = response
        api = self._api(session)
        with self.assertRaises(RuntimeError):
            api.remove_with_data(["abc123"])


class DelugeAdapterTests(unittest.TestCase):
    def test_torrent_status_uses_mapping_keys_as_hashes(self):
        from unittest.mock import patch

        calls = []

        class FakeRPC:
            def call(self, method, *args):
                calls.append((method, args))
                if method == "core.get_torrents_status":
                    return {
                        "abc123": {
                            "name": "seeded",
                            "state": "Seeding",
                            "ratio": 1.5,
                            "upload_payload_rate": 100,
                        }
                    }
                raise AssertionError("Unexpected RPC method: " + method)

        api = racing.ClientAPI(
            "deluge-test",
            {"client": "deluge", "host": "127.0.0.1:58846"},
        )
        api.rpc = FakeRPC()
        torrents = api.torrents()

        self.assertEqual(torrents[0]["hash"], "abc123")
        self.assertEqual(torrents[0]["name"], "seeded")
        method, args = calls[0]
        self.assertEqual(method, "core.get_torrents_status")
        self.assertEqual(args[0], {})
        self.assertNotIn("hash", args[1])
        self.assertIn("upload_payload_rate", args[1])


    def test_deluge_removal_stops_before_next_hash(self):
        stop = threading.Event()
        calls = []

        class FakeRPC:
            def call(self, method, *args):
                calls.append((method, args))
                stop.set()
                return True

        api = racing.ClientAPI.__new__(racing.ClientAPI)
        api.client = "deluge"
        api.rpc = FakeRPC()

        succeeded, failed = api.remove_with_data(
            ["first", "second", "third"], stop_event=stop
        )

        self.assertEqual(succeeded, ["first"])
        self.assertEqual(failed, ["second", "third"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "core.remove_torrent")

    def test_deluge_removal_does_nothing_if_already_stopped(self):
        stop = threading.Event()
        stop.set()
        calls = []

        class FakeRPC:
            def call(self, method, *args):
                calls.append((method, args))
                return True

        api = racing.ClientAPI.__new__(racing.ClientAPI)
        api.client = "deluge"
        api.rpc = FakeRPC()

        succeeded, failed = api.remove_with_data(
            ["first", "second"], stop_event=stop
        )

        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["first", "second"])
        self.assertEqual(calls, [])

    def test_qbittorrent_removal_does_not_submit_after_stop(self):
        from unittest.mock import Mock

        stop = threading.Event()
        stop.set()
        session = Mock()
        api = racing.ClientAPI.__new__(racing.ClientAPI)
        api.client = "qbittorrent"
        api.session = session
        api.host = "http://127.0.0.1:8080"

        succeeded, failed = api.remove_with_data(
            ["abc123", "def456"], stop_event=stop
        )

        self.assertEqual(succeeded, [])
        self.assertEqual(failed, ["abc123", "def456"])
        session.post.assert_not_called()
        session.get.assert_not_called()



class RacingSafetyGateTests(unittest.TestCase):
    def test_only_explicit_false_and_true_booleans_authorize_removal(self):
        import racing

        safe = {
            "preview_only": False,
            "allow_removal": True,
            "allow_data_deletion": True,
        }
        self.assertTrue(racing._removal_authorized(safe))

        for field, invalid in (
            ("preview_only", "false"),
            ("preview_only", 0),
            ("allow_removal", "true"),
            ("allow_removal", 1),
            ("allow_data_deletion", "true"),
            ("allow_data_deletion", 1),
        ):
            with self.subTest(field=field, invalid=invalid):
                config = dict(safe)
                config[field] = invalid
                self.assertFalse(racing._removal_authorized(config))

    def test_missing_preview_flag_remains_preview_only(self):
        import racing

        self.assertFalse(racing._removal_authorized({
            "allow_removal": True,
            "allow_data_deletion": True,
        }))

if __name__ == "__main__":
    unittest.main()

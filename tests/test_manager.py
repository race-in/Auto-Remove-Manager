import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import manager


class ManagerTests(unittest.TestCase):
    def test_detection_reports_api_as_unverified(self):
        with mock.patch.object(manager, "process_inventory", return_value=[
            {"user": "seed", "command": "qbittorrent-nox", "args": "qbittorrent-nox"}
        ]), mock.patch.object(manager, "service_inventory", return_value=[]), mock.patch.object(
            manager, "package_inventory", return_value=[]
        ), mock.patch.object(manager, "config_inventory", return_value=[]):
            found = manager.detect_clients("root")
        qb = next(item for item in found["supported"] if item["client"] == "qbittorrent")
        self.assertTrue(qb["detected"])
        self.assertFalse(qb["api_verified"])
        self.assertEqual(qb["status"], "candidate")

    def test_package_inventory_detects_installed_client(self):
        with mock.patch.object(manager, "process_inventory", return_value=[]), mock.patch.object(
            manager, "service_inventory", return_value=[]
        ), mock.patch.object(manager, "package_inventory", return_value=["qbittorrent-nox"]), mock.patch.object(
            manager, "config_inventory", return_value=[]
        ):
            found = manager.detect_clients("root")
        qb = next(item for item in found["supported"] if item["client"] == "qbittorrent")
        self.assertTrue(qb["detected"])
        self.assertEqual(qb["packages"], ["qbittorrent-nox"])

    def test_rtorrent_is_report_only(self):
        with mock.patch.object(manager, "process_inventory", return_value=[
            {"user": "seed", "command": "rtorrent", "args": "rtorrent"}
        ]), mock.patch.object(manager, "service_inventory", return_value=[]), mock.patch.object(
            manager, "package_inventory", return_value=[]
        ), mock.patch.object(manager, "config_inventory", return_value=[]):
            found = manager.detect_clients("root")
        rtorrent = found["detection_only"][0]
        self.assertTrue(rtorrent["detected"])
        self.assertEqual(rtorrent["status"], "detected_but_no_upstream_adapter")

    def test_rtorrent_package_is_reported_without_adapter(self):
        with mock.patch.object(manager, "process_inventory", return_value=[]), mock.patch.object(
            manager, "service_inventory", return_value=[]
        ), mock.patch.object(manager, "package_inventory", return_value=["rtorrent"]), mock.patch.object(
            manager, "config_inventory", return_value=[]
        ):
            found = manager.detect_clients("root")
        rtorrent = found["detection_only"][0]
        self.assertTrue(rtorrent["detected"])
        self.assertEqual(rtorrent["packages"], ["rtorrent"])
        self.assertEqual(rtorrent["status"], "detected_but_no_upstream_adapter")

    def test_process_details_do_not_expose_raw_arguments(self):
        with mock.patch.object(manager, "process_inventory", return_value=[
            {"user": "seed", "command": "qbittorrent-nox", "args": "qbittorrent-nox --password=secret"}
        ]), mock.patch.object(manager, "service_inventory", return_value=[]), mock.patch.object(
            manager, "package_inventory", return_value=[]
        ), mock.patch.object(manager, "config_inventory", return_value=[]):
            found = manager.detect_clients("root")
        qb = next(item for item in found["supported"] if item["client"] == "qbittorrent")
        self.assertEqual(qb["processes"], [{"user": "seed", "command": "qbittorrent-nox"}])
        self.assertNotIn("secret", str(found))

    def test_qbittorrent_port_is_detected_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = pathlib.Path(tmp) / "qBittorrent.conf"
            config.write_text("[Preferences]\nWebUI\\Port=17890\n", encoding="utf-8")
            self.assertEqual(manager.read_qbittorrent_port([{"path": str(config)}]), 17890)

    def test_default_hosts(self):
        self.assertEqual(manager.default_host("deluge", []), "127.0.0.1:58846")
        self.assertEqual(manager.default_host("transmission", []), "http://127.0.0.1:9091")
        self.assertEqual(manager.default_host("utorrent", []), "http://127.0.0.1:8080")

    def test_atomic_write_sets_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "secret.yml"
            manager.atomic_write(target, "test: true\n", 0o600)
            self.assertEqual(target.read_text(encoding="utf-8"), "test: true\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_manager_config_missing_defaults_to_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = manager.load_json(pathlib.Path(tmp) / "missing.json")
        self.assertTrue(cfg["preview_only"])
        self.assertFalse(cfg["allow_removal"])
        self.assertFalse(cfg["allow_data_deletion"])

    def test_upstream_yaml_empty_file_means_no_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.yml"
            path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(manager.load_upstream_yaml(path), {})

    def test_upstream_yaml_rejects_non_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.yml"
            path.write_text("- invalid\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                manager.load_upstream_yaml(path)


if __name__ == "__main__":
    unittest.main()

import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import manager


class ManagerTests(unittest.TestCase):
    def test_upstream_executable_is_found_in_active_virtualenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = pathlib.Path(tmp)
            bindir = venv / "bin"
            bindir.mkdir()
            executable = bindir / "autoremove-torrents"
            executable.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
            executable.chmod(0o755)
            with mock.patch.object(manager.sys, "prefix", str(venv)), mock.patch.object(
                manager.sys, "executable", str(bindir / "python")
            ), mock.patch.object(manager.shutil, "which", return_value=None):
                self.assertEqual(manager.find_upstream_executable(), str(executable))

    def test_upstream_executable_falls_back_to_path(self):
        with mock.patch.object(manager.sys, "prefix", "/missing/venv"), mock.patch.object(
            manager.sys, "executable", "/missing/venv/bin/python"
        ), mock.patch.object(manager.shutil, "which", return_value="/usr/local/bin/autoremove-torrents"):
            self.assertEqual(
                manager.find_upstream_executable(),
                "/usr/local/bin/autoremove-torrents",
            )

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

    def test_feral_qbittorrent_config_is_discovered_rootless(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            feral_config = home / ".qbittorrent/qBittorrent/config/qBittorrent.conf"
            feral_config.parent.mkdir(parents=True)
            feral_config.write_text("[Preferences]\nWebUI\\Port=34021\n", encoding="utf-8")
            with mock.patch.object(manager, "homes_to_scan", return_value=[home]):
                configs = manager.config_inventory("rootless")
            qb_configs = [item for item in configs if item["client"] == "qbittorrent"]
            self.assertEqual(len(qb_configs), 1)
            self.assertEqual(pathlib.Path(qb_configs[0]["path"]), feral_config)
            self.assertEqual(manager.read_qbittorrent_port(qb_configs), 34021)

    def test_feral_qbittorrent_does_not_assume_loopback_webui(self):
        configs = [{
            "client": "qbittorrent",
            "path": "/home/seed/.qbittorrent/qBittorrent/config/qBittorrent.conf",
            "home": "/home/seed",
        }]
        self.assertTrue(manager.is_feral_qbittorrent_config(configs))
        self.assertEqual(manager.default_host("qbittorrent", configs), "")

    def test_feral_deluge_core_conf_is_discovered_and_rpc_port_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            core = home / ".config/deluge/core.conf"
            core.parent.mkdir(parents=True)
            core.write_text('{"daemon_port": 28602, "allow_remote": true}\n', encoding="utf-8")
            with mock.patch.object(manager, "homes_to_scan", return_value=[home]):
                configs = manager.config_inventory("rootless")
            deluge_configs = [item for item in configs if item["client"] == "deluge"]
            self.assertEqual(len(deluge_configs), 1)
            self.assertEqual(manager.read_deluge_port(deluge_configs), 28602)
            self.assertEqual(manager.default_host("deluge", deluge_configs), "127.0.0.1:28602")

    def test_deluge_port_falls_back_when_core_conf_has_no_valid_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = pathlib.Path(tmp) / "core.conf"
            core.write_text('{"daemon_port": 70000}\n', encoding="utf-8")
            self.assertEqual(manager.read_deluge_port([{"client": "deluge", "path": str(core)}]), 58846)

    def test_qbittorrent_port_is_detected_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = pathlib.Path(tmp) / "qBittorrent.conf"
            config.write_text("[Preferences]\nWebUI\\Port=17890\n", encoding="utf-8")
            self.assertEqual(manager.read_qbittorrent_port([{"path": str(config)}]), 17890)

    def test_qbittorrent_port_is_detected_from_modern_webui_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = pathlib.Path(tmp) / "qBittorrent.conf"
            config.write_text("[Preferences]\nDownloads\\SavePath=/data\n[WebUI]\nPort=9001\n", encoding="utf-8")
            self.assertEqual(manager.read_qbittorrent_port([{"path": str(config)}]), 9001)

    def test_qbittorrent_login_accepts_http_204(self):
        self.assertTrue(manager.qbittorrent_login_response_succeeded(204, ""))

    def test_qbittorrent_login_accepts_legacy_http_200_ok(self):
        self.assertTrue(manager.qbittorrent_login_response_succeeded(200, "Ok."))

    def test_qbittorrent_login_rejects_legacy_http_200_fails(self):
        self.assertFalse(manager.qbittorrent_login_response_succeeded(200, "Fails."))

    def test_qbittorrent_login_rejects_http_401(self):
        self.assertFalse(manager.qbittorrent_login_response_succeeded(401, "Unauthorized"))

    def test_default_hosts(self):
        self.assertEqual(manager.default_host("deluge", []), "127.0.0.1:58846")
        self.assertEqual(manager.default_host("transmission", []), "http://127.0.0.1:9091")
        self.assertEqual(manager.default_host("utorrent", []), "http://127.0.0.1:8080")

    def test_upstream_log_rotation_is_limited_to_five_mib_and_one_backup(self):
        from autoremovetorrents.logger import Logger

        with tempfile.TemporaryDirectory() as tmp:
            manager.init_upstream_logger(tmp)
            handler = Logger.file_handler
            try:
                self.assertIsInstance(handler, manager.logging.handlers.RotatingFileHandler)
                self.assertEqual(handler.maxBytes, 5 * 1024 * 1024)
                self.assertEqual(handler.backupCount, 1)
                self.assertTrue(pathlib.Path(tmp, handler.baseFilename.split("/")[-1]).parent.exists())
            finally:
                handler.close()
                Logger.console_handler.close()

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

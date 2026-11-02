#!/usr/bin/env python3
"""Auto-Remove-Manager: safe discovery and orchestration around upstream ART."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import getpass
import json
import logging
import logging.handlers
import os
import pathlib
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import yaml

SUPPORTED = {
    "qbittorrent": ("qbittorrent-nox", "qbittorrent"),
    "deluge": ("deluged",),
    "transmission": ("transmission-daemon", "transmission-gtk"),
    "utorrent": ("utserver", "utorrent"),
}
DETECTION_ONLY = {"rtorrent": ("rtorrent",)}
MARKER = "AUTO-REMOVE-MANAGER-OWNED-V1"


def atomic_write(path: pathlib.Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".arm-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def process_inventory() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "user=,comm=,args="],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    inventory: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        user, command = parts[0], parts[1]
        args = parts[2] if len(parts) > 2 else command
        inventory.append({"user": user, "command": command, "args": args})
    return inventory


def service_inventory() -> list[str]:
    if not shutil.which("systemctl"):
        return []
    commands = [
        ["systemctl", "list-units", "--all", "--type=service", "--no-legend", "--no-pager"],
        ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
    ]
    if os.geteuid() != 0:
        commands.extend([
            ["systemctl", "--user", "list-units", "--all", "--type=service", "--no-legend", "--no-pager"],
            ["systemctl", "--user", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
        ])
    found: set[str] = set()
    for command in commands:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields:
                found.add(fields[0])
    return sorted(found)


def package_inventory() -> list[str]:
    """Read package names from common Linux package databases, without mutations."""
    commands = []
    if shutil.which("dpkg-query"):
        commands.append(["dpkg-query", "-W", "-f=${binary:Package}\\n"])
    if shutil.which("rpm"):
        commands.append(["rpm", "-qa", "--qf", "%{NAME}\\n"])
    if shutil.which("pacman"):
        commands.append(["pacman", "-Qq"])
    if shutil.which("apk"):
        commands.append(["apk", "info"])
    for command in commands:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return sorted({line.strip().lower() for line in result.stdout.splitlines() if line.strip()})
    return []


def homes_to_scan(mode: str) -> list[pathlib.Path]:
    homes: set[pathlib.Path] = set()
    if mode == "root" and os.geteuid() == 0:
        service_accounts = {"debian-transmission", "transmission", "deluge", "qbittorrent"}
        for entry in pwd.getpwall():
            home = pathlib.Path(entry.pw_dir)
            home_text = str(home)
            if home_text in ("", "/") or home_text.startswith("/nonexistent"):
                continue
            known_service_home = (
                entry.pw_name in service_accounts
                or home_text.startswith(("/var/lib/", "/srv/", "/media/", "/mnt/"))
            )
            if (entry.pw_uid == 0 or entry.pw_uid >= 1000 or known_service_home) and home.is_dir():
                homes.add(home)
    else:
        homes.add(pathlib.Path.home())
    return sorted(homes)


def config_inventory(mode: str) -> list[dict[str, str]]:
    configs: list[dict[str, str]] = []
    for home in homes_to_scan(mode):
        candidates = [
            ("qbittorrent", home / ".config/qBittorrent/qBittorrent.conf"),
            ("qbittorrent", home / ".config/qBittorrent/config/qBittorrent.conf"),
            # Feral Hosting stores qBittorrent settings outside XDG_CONFIG_HOME.
            ("qbittorrent", home / ".qbittorrent/qBittorrent/config/qBittorrent.conf"),
            ("qbittorrent", home / ".var/app/org.qbittorrent.qBittorrent/config/qBittorrent/qBittorrent.conf"),
            ("deluge", home / ".config/deluge/auth"),
            # Feral uses the standard Deluge profile directory for core settings.
            ("deluge", home / ".config/deluge/core.conf"),
            ("transmission", home / ".config/transmission-daemon/settings.json"),
            ("transmission", home / ".var/app/com.transmissionbt.Transmission/config/transmission-daemon/settings.json"),
            ("utorrent", home / ".config/utserver/utserver.conf"),
            ("utorrent", home / ".utserver.conf"),
            ("rtorrent", home / ".rtorrent.rc"),
            ("rtorrent", home / ".config/rtorrent/rtorrent.rc"),
        ]
        for client, path in candidates:
            try:
                if path.is_file():
                    configs.append({"client": client, "path": str(path), "home": str(home)})
            except OSError:
                continue
    for client, path in (
        ("transmission", pathlib.Path("/etc/transmission-daemon/settings.json")),
        ("transmission", pathlib.Path("/var/lib/transmission-daemon/info/settings.json")),
        ("transmission", pathlib.Path("/var/lib/transmission/.config/transmission-daemon/settings.json")),
        ("rtorrent", pathlib.Path("/etc/rtorrent.rc")),
    ):
        try:
            if path.is_file():
                configs.append({"client": client, "path": str(path), "home": "system"})
        except OSError:
            continue
    return configs


def detect_clients(mode: str) -> dict[str, Any]:
    processes = process_inventory()
    services = service_inventory()
    packages = package_inventory()
    configs = config_inventory(mode)
    package_patterns = {
        "qbittorrent": ("qbittorrent",),
        "deluge": ("deluge", "python3-deluge"),
        "transmission": ("transmission-daemon", "transmission-gtk", "transmission-qt"),
        "utorrent": ("utserver", "utorrent"),
    }
    result: dict[str, Any] = {"supported": [], "detection_only": []}

    for client, names in SUPPORTED.items():
        process_hits = [
            {"user": item["user"], "command": item["command"]}
            for item in processes
            if item["command"].lower() in {name.lower() for name in names}
            or any(re.search(r"(^|[/ ])" + re.escape(name) + r"([ /]|$)", item["args"], re.I) for name in names)
        ]
        service_hits = [unit for unit in services if any(name.replace("-", "").lower() in unit.replace("-", "").lower() for name in names)]
        config_hits = [entry for entry in configs if entry["client"] == client]
        package_hits = [
            package for package in packages
            if any(
                package == pattern
                or package.startswith(pattern + "-")
                or package.startswith(pattern + ":")
                for pattern in package_patterns[client]
            )
        ]
        detected = bool(process_hits or service_hits or config_hits or package_hits)
        result["supported"].append({
            "client": client,
            "detected": detected,
            "processes": process_hits,
            "services": service_hits,
            "packages": package_hits,
            "configs": config_hits,
            "api_verified": False,
            "status": "candidate" if detected else "not_detected",
        })

    for client, names in DETECTION_ONLY.items():
        process_hits = [
            {"user": item["user"], "command": item["command"]}
            for item in processes
            if item["command"].lower() in {name.lower() for name in names}
            or any(re.search(r"(^|[/ ])" + re.escape(name) + r"([ /]|$)", item["args"], re.I) for name in names)
        ]
        service_hits = [unit for unit in services if any(name in unit.lower() for name in names)]
        package_hits = [
            package for package in packages
            if any(package == name or package.startswith(name + "-") or package.startswith(name + ":") for name in names)
        ]
        config_hits = [entry for entry in configs if entry["client"] == client]
        detected = bool(process_hits or service_hits or package_hits or config_hits)
        result["detection_only"].append({
            "client": client,
            "detected": detected,
            "processes": process_hits,
            "services": service_hits,
            "packages": package_hits,
            "configs": config_hits,
            "status": "detected_but_no_upstream_adapter" if detected else "not_detected",
        })
    return result


def read_qbittorrent_port(configs: list[dict[str, str]]) -> int:
    """Read WebUI port from both modern [WebUI] and legacy Preferences keys."""
    for item in configs:
        section = ""
        try:
            lines = pathlib.Path(item["path"]).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
                continue
            if "=" not in stripped:
                continue
            key, value = (part.strip() for part in stripped.split("=", 1))
            if not (
                (section == "webui" and key.lower() == "port")
                or key.lower() == r"webui\port"
            ):
                continue
            try:
                port = int(value)
            except ValueError:
                continue
            if 1 <= port <= 65535:
                return port
    return 8080


def read_deluge_port(configs: list[dict[str, str]]) -> int:
    """Read Deluge's daemon RPC port from a discovered core.conf."""
    for item in configs:
        if item.get("client") != "deluge" or not item.get("path", "").endswith("core.conf"):
            continue
        try:
            raw = pathlib.Path(item["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("daemon_port")
            try:
                port = int(value)
            except (TypeError, ValueError):
                port = 0
            if 1 <= port <= 65535:
                return port
        match = re.search(r"daemon_port\s*:\s*(\d+)", raw)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
    return 58846


def qbittorrent_login_response_succeeded(status_code: int, response_text: str) -> bool:
    """Recognize qBittorrent's legacy and v5.2+ successful login responses."""
    if status_code == 204:
        return True
    return status_code == 200 and response_text.strip() != "Fails."


@contextmanager
def upstream_login_compatibility(client: str):
    """Work around autoremove-torrents 1.5.5 rejecting qBittorrent's HTTP 204 login."""
    if client != "qbittorrent":
        yield
        return

    from unittest.mock import patch
    from autoremovetorrents.client.qbittorrent import qBittorrent
    from autoremovetorrents.exception.loginfailure import LoginFailure

    def compatible_login(self, username, password):
        try:
            response = self._request_handler.login(username, password)
        except Exception as exc:
            from autoremovetorrents.exception.connectionfailure import ConnectionFailure
            raise ConnectionFailure(str(exc))
        if qbittorrent_login_response_succeeded(response.status_code, response.text):
            return
        raise LoginFailure("The server returned HTTP %d or rejected the credentials." % response.status_code)

    with patch.object(qBittorrent, "login", compatible_login):
        yield

def is_feral_qbittorrent_config(configs: list[dict[str, str]]) -> bool:
    """Return True when discovery found Feral's non-XDG qBittorrent config."""
    return any(
        pathlib.Path(item.get("path", "")).as_posix().endswith(
            "/.qbittorrent/qBittorrent/config/qBittorrent.conf"
        )
        for item in configs
        if item.get("client") == "qbittorrent"
    )


def default_host(client: str, configs: list[dict[str, str]]) -> str:
    if client == "qbittorrent":
        # On Feral, the configured port may belong to an isolated client
        # container and is not necessarily reachable on the user's loopback.
        # Require an explicitly supplied, API-verified endpoint instead.
        if is_feral_qbittorrent_config(configs):
            return ""
        return f"http://127.0.0.1:{read_qbittorrent_port(configs)}"
    if client == "deluge":
        return f"127.0.0.1:{read_deluge_port(configs)}"
    if client == "transmission":
        return "http://127.0.0.1:9091"
    if client == "utorrent":
        return "http://127.0.0.1:8080"
    raise ValueError(f"Unsupported client: {client}")


LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 1


def init_upstream_logger(log_path: str, file_debug_log: bool = False,
                         output_debug_log: bool = False) -> None:
    """Initialize upstream ART logging with a bounded 5 MiB rotating file."""
    from autoremovetorrents.logger import Logger

    pathlib.Path(log_path).mkdir(parents=True, exist_ok=True)
    Logger.log_path = log_path
    filename = Logger.LOG_FILE_NAME % __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_path, filename),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG if file_debug_log else logging.INFO)
    file_handler.setFormatter(logging.Formatter(Logger.FILE_FORMAT, datefmt=Logger.DATE_FORMAT))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if output_debug_log else logging.INFO)
    console_handler.setFormatter(logging.Formatter(Logger.OUTPUT_FORMAT, datefmt=Logger.DATE_FORMAT))

    Logger.file_handler = file_handler
    Logger.console_handler = console_handler


def verify_client_connection(task_name: str, task_conf: dict[str, Any], log_dir: pathlib.Path) -> bool:
    """Authenticate and read basic client state without listing or removing torrents."""
    try:
        from autoremovetorrents.task import Task
        log_dir.mkdir(parents=True, exist_ok=True)
        init_upstream_logger(str(log_dir))
        with upstream_login_compatibility(str(task_conf.get("client", "")).lower()):
            task = Task(task_name, task_conf, remove_torrents=False)
            task._login()
        return True
    except Exception as exc:  # Never print exception text: adapters may include endpoint details.
        print(f"  API verification failed ({type(exc).__name__}); check host, credentials, and client API settings.")
        return False


def configure_clients(mode: str, upstream_config: pathlib.Path, log_dir: pathlib.Path) -> int:
    if not sys.stdin.isatty():
        print("Client configuration requires an interactive terminal.", file=sys.stderr)
        return 2
    discovery = detect_clients(mode)
    candidates = [item for item in discovery["supported"] if item["detected"]]
    if not candidates:
        print("No supported torrent clients detected.")
        print("You can still edit the upstream YAML manually after installation.")
        return 0

    print("Detected supported clients (API access is not yet verified):")
    for index, item in enumerate(candidates, 1):
        owners = sorted({p["user"] for p in item["processes"]})
        suffix = f" — process owner(s): {', '.join(owners)}" if owners else ""
        print(f"  {index}. {item['client']}{suffix}")
    print("  A client will be added only after you enter and save its connection details.")

    existing: dict[str, Any] = {}
    if upstream_config.exists():
        try:
            existing = yaml.safe_load(upstream_config.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"Cannot read existing YAML: {exc}", file=sys.stderr)
            return 1
    if not isinstance(existing, dict):
        print("Existing YAML must contain a task mapping.", file=sys.stderr)
        return 1

    for item in candidates:
        client = item["client"]
        answer = input(f"Configure {client}? [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            continue
        task_name = client
        if task_name in existing:
            task_name = f"{client}_2"
            suffix = 2
            while task_name in existing:
                suffix += 1
                task_name = f"{client}_{suffix}"
        suggested_host = default_host(client, item["configs"])
        if suggested_host:
            host = input(f"  API/RPC host [{suggested_host}]: ").strip() or suggested_host
        else:
            print("  Feral qBittorrent may expose its WebUI through a provider-managed proxy.")
            print("  The port in its config may not be reachable on 127.0.0.1; use the provider's reachable API endpoint.")
            host = input("  API/RPC host (required): ").strip()
        if not host:
            print("  No endpoint entered; task was not saved.")
            continue
        username = input("  Username (blank if not used): ").strip()
        password = getpass.getpass("  Password (input hidden; blank if not used): ")
        task_conf = {
            "client": client,
            "host": host,
            "username": username,
            "password": password,
            "strategies": {},
            "delete_data": False,
        }
        print("  Checking API login and basic client status (read-only)...")
        if not verify_client_connection(task_name, task_conf, log_dir):
            print("  Task was not saved because the API check failed.")
            continue
        existing[task_name] = task_conf
        print(f"  Added verified task '{task_name}' (no removal strategy has been enabled).")

    try:
        rendered = yaml.safe_dump(existing, sort_keys=False, allow_unicode=True)
        atomic_write(upstream_config, rendered, 0o600)
    except (OSError, yaml.YAMLError) as exc:
        print(f"Failed to write client configuration: {exc}", file=sys.stderr)
        return 1
    print(f"Saved private upstream config: {upstream_config}")
    print("Next: run 'autoremove-manager preview' and inspect the logs.")
    return 0


def _validate_boolean_fields(value: dict[str, Any], fields: tuple[str, ...],
                             context: str) -> None:
    """Reject non-JSON-boolean values in safety-sensitive configuration."""
    for field in fields:
        if field in value and type(value[field]) is not bool:
            raise ValueError(
                "{}.{} must be a JSON boolean (true or false).".format(
                    context, field
                )
            )



def _schedule_enabled(settings: dict[str, Any]) -> bool:
    """Only an explicit boolean true enables scheduled execution."""
    schedule = settings.get("schedule", {})
    return isinstance(schedule, dict) and schedule.get("enabled") is True

def load_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1, "preview_only": True, "allow_removal": False,
            "allow_data_deletion": False, "schedule": {"enabled": False},
            "racing": {"enabled": False, "preview_only": True,
                       "allow_removal": False, "allow_data_deletion": False,
                       "download_threshold_mb": 100.0, "upload_max_mb": 10.0,
                       "ratio_min": 1.0, "sustain_seconds": 5.0,
                       "poll_interval_seconds": 1.0},
            "hybrid": {"enabled": False, "preview_only": True, "allow_removal": False,
                       "allow_data_deletion": False, "download_threshold_mb": 100.0,
                       "upload_max_mb": 10.0, "ratio_min": 1.0, "sustain_seconds": 5.0,
                       "poll_interval_seconds": 1.0, "cleanup_interval_seconds": 60.0},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Manager settings must be a JSON object.")

    _validate_boolean_fields(
        value,
        ("preview_only", "allow_removal", "allow_data_deletion"),
        "manager",
    )

    for section, fields in (
        ("schedule", ("enabled",)),
        ("racing", ("enabled", "preview_only", "allow_removal",
                    "allow_data_deletion")),
        ("hybrid", ("enabled", "preview_only", "allow_removal",
                    "allow_data_deletion")),
    ):
        if section in value:
            if not isinstance(value[section], dict):
                raise ValueError("manager.{} must be a JSON object.".format(section))
            _validate_boolean_fields(
                value[section], fields, "manager." + section
            )

    return value


def load_upstream_yaml(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Upstream YAML must contain a mapping of task names.")

    for task_name, task in value.items():
        if not isinstance(task, dict):
            raise ValueError(
                "Upstream task {!r} must be a mapping.".format(task_name)
            )
        if "delete_data" in task and type(task["delete_data"]) is not bool:
            raise ValueError(
                "Upstream task {!r}: delete_data must be YAML true or false.".format(
                    task_name
                )
            )
    return value


def find_upstream_executable() -> str | None:
    """Find ART in the active virtualenv before falling back to PATH."""
    candidates = (
        pathlib.Path(sys.prefix) / "bin" / "autoremove-torrents",
        pathlib.Path(sys.executable).absolute().parent / "autoremove-torrents",
    )
    seen: set[str] = set()
    for candidate in candidates:
        path = str(candidate)
        if path in seen:
            continue
        seen.add(path)
        if candidate.is_file() and os.access(path, os.X_OK):
            return path
    return shutil.which("autoremove-torrents")


def _terminate_child_process(process, grace_seconds: float = 10.0) -> None:
    """Stop and reap the entire upstream process group, even after repeated Ctrl-C."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            process.wait(timeout=remaining)
            break
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            # Do not let a second Ctrl-C bypass the mandatory SIGKILL/reap.
            continue

    # Always kill surviving descendants, even if the group leader exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    # Always reap the direct child; repeated interrupts must not escape cleanup.
    while True:
        try:
            process.wait()
            break
        except KeyboardInterrupt:
            continue


def install_shutdown_handlers(stop_event):
    """Translate supervisor termination signals into orderly worker shutdown."""
    previous = {}

    def request_shutdown(signum, frame):
        stop_event.set()

    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)

    def restore():
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    return restore


def invoke_upstream(args: argparse.Namespace, preview: bool,
                    stop_event=None) -> int:
    executable = find_upstream_executable()
    if not executable:
        print("autoremove-torrents executable not found in the active environment or PATH.", file=sys.stderr)
        return 127

    if stop_event is not None and stop_event.is_set():
        print("Hybrid stop requested; upstream process was not started.")
        return 130

    args.log_dir.mkdir(parents=True, exist_ok=True)

    bootstrap = """
import logging
import logging.handlers
import os
import runpy
import sys
from datetime import datetime
from unittest.mock import patch
from autoremovetorrents.logger import Logger
from autoremovetorrents.client.qbittorrent import qBittorrent
from autoremovetorrents.exception.connectionfailure import ConnectionFailure
from autoremovetorrents.exception.loginfailure import LoginFailure

def init_bounded_logger(log_path):
    os.makedirs(log_path, exist_ok=True)
    Logger.log_path = log_path
    filename = Logger.LOG_FILE_NAME % datetime.now().strftime("%Y-%m-%d")
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_path, filename), maxBytes=5 * 1024 * 1024,
        backupCount=1, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(Logger.FILE_FORMAT, datefmt=Logger.DATE_FORMAT))
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(Logger.OUTPUT_FORMAT, datefmt=Logger.DATE_FORMAT))
    Logger.file_handler = handler
    Logger.console_handler = console

def compatible_login(self, username, password):
    try:
        response = self._request_handler.login(username, password)
    except Exception as exc:
        raise ConnectionFailure(str(exc))
    if response.status_code == 204:
        return
    if response.status_code == 200 and response.text.strip() != "Fails.":
        return
    raise LoginFailure("The server returned HTTP %d or rejected the credentials." % response.status_code)

script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
log_dir = sys.argv[sys.argv.index("--log") + 1]
init_bounded_logger(log_dir)
with patch.object(qBittorrent, "login", compatible_login):
    runpy.run_path(script, run_name="__main__")
"""

    command = [
        sys.executable, "-c", bootstrap, executable,
        "--conf", str(args.upstream_config), "--log", str(args.log_dir)
    ]
    if preview:
        command.append("--view")

    print("Running upstream AutoRemove Torrents in " +
          ("PREVIEW" if preview else "REMOVAL") + " mode.")
    print("Configuration:", args.upstream_config)

    process = None
    try:
        process = subprocess.Popen(command, start_new_session=True)
        deadline = time.monotonic() + 3600.0

        while True:
            if stop_event is not None and stop_event.is_set():
                print("Stop requested; terminating upstream AutoRemove Torrents.")
                _terminate_child_process(process)
                return 130

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("AutoRemove Torrents timed out after one hour.", file=sys.stderr)
                _terminate_child_process(process)
                return 124

            try:
                return process.wait(timeout=min(0.25, remaining))
            except subprocess.TimeoutExpired:
                continue

    except KeyboardInterrupt:
        # start_new_session isolates the child from terminal SIGINT. Always
        # terminate and reap its whole process group before releasing our lock.
        if process is not None:
            print("Interrupt received; terminating upstream process group.")
            _terminate_child_process(process)
        return 130
    except OSError as exc:
        print("Unable to launch or stop AutoRemove Torrents: {}".format(exc),
              file=sys.stderr)
        return 1

def acquire_lock(state_dir: pathlib.Path):
    import fcntl
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (state_dir / "run.lock").open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-Remove-Manager")
    parser.add_argument("--mode", choices=("root", "rootless"), required=True)
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--upstream-config", type=pathlib.Path, required=True)
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--log-dir", type=pathlib.Path, required=True)
    parser.add_argument("--confirm-removal", action="store_true",
                        help="required in addition to allow_removal=true for manual deletion runs")
    parser.add_argument("--yes", action="store_true",
                        help="skip the interactive confirmation in the one-click uninstaller")
    parser.add_argument("command", nargs="?", default="status",
                        choices=("status", "detect", "configure", "test", "preview",
                                 "scheduled", "run", "racing", "hybrid", "logs", "uninstall"))
    args = parser.parse_args()

    # Uninstall must not depend on valid settings/YAML; it must still work after
    # a malformed config or interrupted update.
    if args.command == "uninstall":
        script = pathlib.Path(__file__).resolve().parents[1] / "uninstall.sh"
        if not script.is_file():
            print("Uninstaller is missing. Run the standalone uninstall.sh script.", file=sys.stderr)
            return 1
        command = ["bash", str(script), "--mode", args.mode]
        if args.yes:
            command.append("--yes")
        return subprocess.run(command, check=False).returncode

    try:
        settings = load_json(args.config)
        tasks = load_upstream_yaml(args.upstream_config)
        if settings.get("mode", args.mode) != args.mode:
            raise ValueError("Installed mode and manager settings mode do not match.")
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.command == "status":
        print(json.dumps({
            "mode": args.mode,
            "preview_only": settings.get("preview_only", True) is not False,
            "allow_removal": settings.get("allow_removal") is True,
            "allow_data_deletion": settings.get("allow_data_deletion") is True,
            "configured_tasks": list(tasks.keys()),
            "schedule": settings.get("schedule", {}),
            "racing": settings.get("racing", {"enabled": False, "preview_only": True}),
            "hybrid": settings.get("hybrid", {"enabled": False, "preview_only": True}),
        }, indent=2))
        return 0
    if args.command == "detect":
        print(json.dumps(detect_clients(args.mode), indent=2))
        return 0
    if args.command == "configure":
        return configure_clients(args.mode, args.upstream_config, args.log_dir)
    if args.command == "test":
        if not tasks:
            print("No configured client tasks. Run 'autoremove-manager configure' first.")
            return 0
        try:
            from autoremovetorrents.task import Task
            args.log_dir.mkdir(parents=True, exist_ok=True)
            init_upstream_logger(str(args.log_dir))
        except Exception as exc:
            print(f"Unable to initialize upstream client test: {type(exc).__name__}", file=sys.stderr)
            return 1
        failures = 0
        for task_name, task_conf in tasks.items():
            if not isinstance(task_conf, dict):
                print(f"{task_name}: invalid task configuration.")
                failures += 1
                continue
            try:
                with upstream_login_compatibility(str(task_conf.get("client", "")).lower()):
                    task = Task(task_name, task_conf, remove_torrents=False)
                    task._login()
                print(f"{task_name}: API connection successful (read-only test).")
            except Exception as exc:
                print(f"{task_name}: API connection failed ({type(exc).__name__}).")
                failures += 1
        return 1 if failures else 0
    if args.command == "logs":
        print(args.log_dir)
        if args.log_dir.exists():
            for path in sorted(args.log_dir.glob("*")):
                print(f"{path.name}\t{path.stat().st_size} bytes")
        return 0

    if args.command == "hybrid":
        hybrid_settings = settings.get("hybrid", {})
        if not isinstance(hybrid_settings, dict):
            print("Invalid Hybrid Mode configuration; expected an object.", file=sys.stderr)
            return 2
        if not hybrid_settings.get("enabled") is True:
            print("Hybrid Mode is disabled. Set hybrid.enabled=true in manager.json to start it.")
            return 0
        if not tasks:
            print("No configured client tasks. Run 'autoremove-manager configure' first.")
            return 0
        from hybrid import run_hybrid
        stop_event = threading.Event()
        restore_handlers = install_shutdown_handlers(stop_event)
        try:
            return run_hybrid(
                tasks, hybrid_settings, args, settings, stop_event=stop_event
            )
        finally:
            restore_handlers()

    if args.command == "racing":
        racing_settings = settings.get("racing", {})
        if not isinstance(racing_settings, dict):
            print("Invalid Racing Mode configuration; expected an object.", file=sys.stderr)
            return 2
        if not racing_settings.get("enabled") is True:
            print("Racing Mode is disabled. Set racing.enabled=true in manager.json to start it.")
            return 0
        if not tasks:
            print("No configured client tasks. Run 'autoremove-manager configure' first.")
            return 0
        from racing import run_monitor
        return run_monitor(tasks, racing_settings, args.state_dir, args.log_dir)

    if args.command == "scheduled" and not _schedule_enabled(settings):
        print("Scheduled runs are disabled in manager.json.")
        return 0
    if not tasks:
        print("No client tasks configured. Run 'autoremove-manager detect' then 'autoremove-manager configure'.")
        return 0
    if args.command == "run" and not args.confirm_removal:
        print("Refusing removal: pass --confirm-removal after enabling allow_removal in manager.json.", file=sys.stderr)
        return 3
    allow_removal = settings.get("allow_removal") is True
    preview_only = settings.get("preview_only", True) is not False
    removal_enabled = allow_removal and not preview_only
    if args.command == "run" and not removal_enabled:
        print("Refusing removal: set allow_removal=true and preview_only=false in manager.json first.", file=sys.stderr)
        return 3

    removal_mode = args.command == "run" or (args.command == "scheduled" and removal_enabled)
    allow_data_deletion = settings.get("allow_data_deletion") is True
    if removal_mode and not allow_data_deletion:
        deleting_tasks = [name for name, task in tasks.items()
                          if isinstance(task, dict) and task.get("delete_data", False) is True]
        if deleting_tasks:
            print("Refusing data deletion: tasks enable delete_data but allow_data_deletion is false: "
                  + ", ".join(deleting_tasks), file=sys.stderr)
            return 3

    lock = acquire_lock(args.state_dir)
    if lock is None:
        print("Another Auto-Remove-Manager run is already active; skipping.")
        return 0
    stop_event = threading.Event()
    restore_handlers = install_shutdown_handlers(stop_event)
    try:
        preview = not removal_mode
        return invoke_upstream(args, preview, stop_event=stop_event)
    finally:
        lock.close()
        restore_handlers()


if __name__ == "__main__":
    raise SystemExit(main())

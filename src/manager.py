#!/usr/bin/env python3
"""Auto-Remove-Manager: safe discovery and orchestration around upstream ART."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import pathlib
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
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
    """Return installed Debian package names without making package changes."""
    if not shutil.which("dpkg-query"):
        return []
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${binary:Package}\\n"],
            check=False, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return sorted({line.strip().lower() for line in result.stdout.splitlines() if line.strip()})


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
            ("qbittorrent", home / ".var/app/org.qbittorrent.qBittorrent/config/qBittorrent/qBittorrent.conf"),
            ("deluge", home / ".config/deluge/auth"),
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
            {"user": item["user"], "command": item["command"], "args": item["args"]}
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
            {"user": item["user"], "command": item["command"], "args": item["args"]}
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
    for item in configs:
        try:
            for line in pathlib.Path(item["path"]).read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"\s*WebUI\\Port\s*=\s*(\d+)", line, re.I)
                if match:
                    port = int(match.group(1))
                    if 1 <= port <= 65535:
                        return port
        except OSError:
            continue
    return 8080


def default_host(client: str, configs: list[dict[str, str]]) -> str:
    if client == "qbittorrent":
        return f"http://127.0.0.1:{read_qbittorrent_port(configs)}"
    if client == "deluge":
        return "127.0.0.1:58846"
    if client == "transmission":
        return "http://127.0.0.1:9091"
    if client == "utorrent":
        return "http://127.0.0.1:8080"
    raise ValueError(f"Unsupported client: {client}")


def verify_client_connection(task_name: str, task_conf: dict[str, Any], log_dir: pathlib.Path) -> bool:
    """Authenticate and read basic client state without listing or removing torrents."""
    try:
        from autoremovetorrents.logger import Logger
        from autoremovetorrents.task import Task
        log_dir.mkdir(parents=True, exist_ok=True)
        Logger.init(str(log_dir))
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
        host = input(f"  API/RPC host [{default_host(client, item['configs'])}]: ").strip()
        if not host:
            host = default_host(client, item["configs"])
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


def load_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1, "preview_only": True, "allow_removal": False,
            "allow_data_deletion": False, "schedule": {"enabled": False},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Manager settings must be a JSON object.")
    return value


def load_upstream_yaml(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Upstream YAML must contain a mapping of task names.")
    return value


def invoke_upstream(args: argparse.Namespace, preview: bool) -> int:
    executable = shutil.which("autoremove-torrents")
    if not executable:
        print("autoremove-torrents executable not found in PATH.", file=sys.stderr)
        return 127
    args.log_dir.mkdir(parents=True, exist_ok=True)
    command = [executable, "--conf", str(args.upstream_config), "--log", str(args.log_dir)]
    if preview:
        command.append("--view")
    print("Running upstream AutoRemove Torrents in " + ("PREVIEW" if preview else "REMOVAL") + " mode.")
    print("Configuration:", args.upstream_config)
    try:
        result = subprocess.run(command, check=False, timeout=3600)
    except subprocess.TimeoutExpired:
        print("AutoRemove Torrents timed out after one hour.", file=sys.stderr)
        return 124
    except OSError as exc:
        print(f"Unable to launch AutoRemove Torrents: {exc}", file=sys.stderr)
        return 1
    return result.returncode


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
                                 "scheduled", "run", "logs", "uninstall"))
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
            "preview_only": bool(settings.get("preview_only", True)),
            "allow_removal": bool(settings.get("allow_removal", False)),
            "allow_data_deletion": bool(settings.get("allow_data_deletion", False)),
            "configured_tasks": list(tasks.keys()),
            "schedule": settings.get("schedule", {}),
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
            from autoremovetorrents.logger import Logger
            from autoremovetorrents.task import Task
            args.log_dir.mkdir(parents=True, exist_ok=True)
            Logger.init(str(args.log_dir))
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

    if args.command == "scheduled" and not bool(settings.get("schedule", {}).get("enabled", False)):
        print("Scheduled runs are disabled in manager.json.")
        return 0
    if not tasks:
        print("No client tasks configured. Run 'autoremove-manager detect' then 'autoremove-manager configure'.")
        return 0
    if args.command == "run" and not args.confirm_removal:
        print("Refusing removal: pass --confirm-removal after enabling allow_removal in manager.json.", file=sys.stderr)
        return 3
    allow_removal = bool(settings.get("allow_removal", False))
    preview_only = bool(settings.get("preview_only", True))
    removal_enabled = allow_removal and not preview_only
    if args.command == "run" and not removal_enabled:
        print("Refusing removal: set allow_removal=true and preview_only=false in manager.json first.", file=sys.stderr)
        return 3

    removal_mode = args.command == "run" or (args.command == "scheduled" and removal_enabled)
    allow_data_deletion = bool(settings.get("allow_data_deletion", False))
    if removal_mode and not allow_data_deletion:
        deleting_tasks = [name for name, task in tasks.items()
                          if isinstance(task, dict) and bool(task.get("delete_data", False))]
        if deleting_tasks:
            print("Refusing data deletion: tasks enable delete_data but allow_data_deletion is false: "
                  + ", ".join(deleting_tasks), file=sys.stderr)
            return 3

    lock = acquire_lock(args.state_dir)
    if lock is None:
        print("Another Auto-Remove-Manager run is already active; skipping.")
        return 0
    try:
        preview = not removal_mode
        return invoke_upstream(args, preview)
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())

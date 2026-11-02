# Auto-Remove-Manager

[![CI](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/ci.yml)
[![ShellCheck](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/shellcheck.yml/badge.svg?branch=main)](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/shellcheck.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**A rootless-friendly installer, configuration manager, and safety layer for [AutoRemove Torrents](https://github.com/jerrymakesjelly/autoremove-torrents).** It discovers torrent clients, verifies their APIs, creates separate upstream tasks, and provides preview, scheduled, Racing, and per-client Hybrid cleanup workflows.

Auto-Remove-Manager does not replace the upstream cleanup engine. It orchestrates it, manages configuration and execution, and adds explicit safeguards around removal.

> **Important:** Torrent removal and downloaded-file deletion are different operations. This project starts in preview-only mode. Read the [Safety model](#safety-model) before enabling any deletion. If payload deletion is enabled, matching downloaded files can be permanently deleted.

---

## Contents

- [Highlights](#highlights)
- [Supported clients](#supported-clients)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Rootless / per-user](#rootless--per-user-installation)
  - [Root / system-wide](#root--system-wide-installation)
  - [Interactive installer](#interactive-installer)
- [First-time setup](#first-time-setup)
- [Files and directories](#files-and-directories)
- [CLI reference](#cli-reference)
- [How cleanup works](#how-cleanup-works)
  - [Normal mode](#normal-mode)
  - [Racing mode](#racing-mode)
  - [Hybrid mode](#hybrid-mode)
- [Hybrid configuration reference](#hybrid-configuration-reference)
- [Safety model and deletion permissions](#safety-model-and-deletion-permissions)
- [Scheduling and long-running processes](#scheduling-and-long-running-processes)
- [Logs and diagnostics](#logs-and-diagnostics)
- [Updating](#updating)
- [Uninstallation](#uninstallation)
- [Troubleshooting](#troubleshooting)
- [Development and tests](#development-and-tests)
- [Upstream project and attribution](#upstream-project-and-attribution)
- [License](#license)

## Highlights

- Root/system-wide and rootless/per-user installation modes.
- One-command installer with ownership checks and guarded updates.
- Local discovery through running processes, systemd units, package databases, and common configuration paths.
- API verification before a client task is saved.
- qBittorrent, Deluge, Transmission, and µTorrent upstream task support.
- rTorrent discovery/reporting only; no rTorrent cleanup adapter is provided by the upstream engine.
- Separate upstream task for each configured client.
- Keeps cleanup expressions in the upstream YAML format; the manager does not silently invent or rewrite your rules.
- Preview-first operation and separate switches for torrent removal and payload deletion.
- Optional standalone Racing monitor for qBittorrent and Deluge.
- Optional Hybrid controller: each qBittorrent/Deluge client independently switches between the existing Normal strategy and Racing rules.
- Shared process lock to prevent overlapping cleanup runs.
- Fail-closed behaviour on API errors and invalid/missing Racing data.
- Owner-only configuration files; credentials are not printed by the manager.
- Bounded upstream log rotation.
- Marker-scoped uninstallation that leaves torrent clients and download directories alone.

## Supported clients

| Client | Discovery | Cleanup adapter | Connection method |
|---|---|---|---|
| qBittorrent | Process, systemd, common config paths | Yes | Web API |
| Deluge | Process, systemd, auth file and profile | Yes | RPC |
| Transmission | Process, systemd, common config paths | Yes | RPC |
| µTorrent | Process, systemd, common config paths | Yes | Web API |
| rTorrent | Process and systemd | No | Detection/report only |

Discovery is not the same as API verification. A process, package, or configuration file only identifies a possible client. Use `test` to verify credentials and API connectivity.

Racing and Hybrid modes currently support **qBittorrent and Deluge only**. Transmission and µTorrent remain available through the upstream task workflow, but they do not get Racing/Hybrid workers.

See [Supported Clients](docs/SUPPORTED_CLIENTS.md) and [Feral Hosting](docs/FERAL.md).

## Requirements

- Linux.
- Python 3.8 or newer, with virtual-environment support.
- Git.
- A supported package manager for automatic dependency installation in root mode; rootless mode expects the required tools to be available.
- Network/API access from the manager process to each configured torrent client.
- Valid client API/WebUI/RPC credentials.

The installer creates an isolated Python virtual environment. Project dependencies are pinned or bounded in `requirements.txt`; the Feral-compatible urllib3 constraint is intentional for older OpenSSL environments.

## Installation

Choose the installation mode that matches the account that owns the torrent clients. **Do not use rootless mode with sudo.**

### Rootless / per-user installation

Recommended for shared seedboxes such as Feral Hosting. Run as the same user that owns qBittorrent/Deluge:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh) --mode rootless
```

No root privileges are required. The manager, virtual environment, configuration, state, logs, and command wrapper are kept under that user's home directory.

The rootless installer uses user systemd when available. If user systemd is unavailable and crontab is available, it installs a **marked hourly scheduled job**. That hourly job runs the ordinary scheduled command; it does **not** start Racing or Hybrid.

### Root / system-wide installation

Run as root or with sudo:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh | sudo bash -s -- --mode root
```

The manager is installed system-wide. When invoked through sudo by a normal user, the manager's configuration and scheduled task are owned by the invoking user, and management commands should be run as that user (without sudo). If installation is performed directly as root, manage it as root.

### Interactive installer

To let the installer ask which mode to use, run it from an interactive terminal:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh)
```

### Installation paths

| Item | Rootless / per-user | Root / system-wide |
|---|---|---|
| Source and virtual environment | `~/.local/share/auto-remove-manager` | `/opt/auto-remove-manager` |
| Configuration | `~/.config/auto-remove-manager` | `/etc/auto-remove-manager` |
| State | `~/.local/state/auto-remove-manager` | `/var/lib/auto-remove-manager` |
| Logs | `~/.local/state/auto-remove-manager/logs` | `/var/log/auto-remove-manager` |
| CLI wrapper | `~/.local/bin/autoremove-manager` | `/usr/local/bin/autoremove-manager` |

For rootless installations, add `~/.local/bin` to `PATH` if the shell cannot find `autoremove-manager`.

## First-time setup

Run these commands as the account that owns the installation:

```bash
autoremove-manager detect
autoremove-manager configure
autoremove-manager test
autoremove-manager preview
```

### 1. Discover clients

`detect` reports local client candidates. It is read-only and does not prove that an API is reachable.

### 2. Configure clients

`configure` presents supported candidates and asks for the required endpoint and credentials. The manager creates a separate upstream task for each selected client.

A task is saved only after the upstream adapter can authenticate and read basic client status. Some secrets cannot be recovered from client configuration. For example, qBittorrent stores a WebUI password hash; enter the actual WebUI password when prompted.

The wizard deliberately creates an empty `strategies` mapping. This avoids silently applying a deletion rule before you have reviewed it.

### 3. Verify API access

`test` performs a read-only authentication test for every configured task. Fix any endpoint, port, or credential problem before continuing.

### 4. Define your cleanup strategy

Edit the upstream YAML file:

- Rootless: `~/.config/auto-remove-manager/config.yml`
- Root: `/etc/auto-remove-manager/config.yml`

The manager preserves and passes your upstream task strategy to AutoRemove Torrents. Strategy expression syntax and available fields are defined by the [upstream documentation](https://autoremove-torrents.readthedocs.io/en/latest/).

Example structure (illustrative only):

```yaml
seedbox_qb:
  client: qbittorrent
  host: http://127.0.0.1:8080
  username: YOUR_USERNAME
  password: YOUR_PASSWORD
  strategies:
    reviewed_rule:
      remove: seeding_time > 1209600 and ratio > 1
  delete_data: false
```

Use the actual API endpoint exposed by your host. Do not assume that a detected port is reachable from the manager's network namespace. Store real credentials only in your private configuration file; do not commit them to Git.

### 5. Preview the result

```bash
autoremove-manager preview
```

Review the complete candidate list and confirm that the strategy matches your tracker and retention requirements. Preview does not remove torrents or downloaded data.

## Files and directories

The two configuration files have different responsibilities:

| File | Purpose |
|---|---|
| `manager.json` | Installation mode, safety permissions, schedule metadata, Racing settings, Hybrid settings |
| `config.yml` | Upstream AutoRemove Torrents tasks, credentials, strategy expressions, and per-task `delete_data` setting |

The manager writes private configuration with owner-only permissions. Do not loosen those permissions or share the files publicly.

The state directory contains process/run locks, ownership metadata, and runtime state. The log directory contains manager/upstream logs. Do not manually delete lock or state files while a manager process is running.

## CLI reference

| Command | Purpose | Removes torrents? |
|---|---|---|
| `autoremove-manager status` | Print mode, safety settings, configured tasks, schedule, Racing and Hybrid configuration | No |
| `autoremove-manager detect` | Discover local torrent-client candidates | No |
| `autoremove-manager configure` | Interactively configure and verify client tasks | No |
| `autoremove-manager test` | Read-only API authentication test | No |
| `autoremove-manager preview` | Run upstream strategies in preview mode | No |
| `autoremove-manager scheduled` | Run only when the manager schedule is enabled | Depends on explicit scheduled-run safety settings |
| `autoremove-manager run --confirm-removal` | Manually run with explicit confirmation | Only if safety settings permit |
| `autoremove-manager racing` | Start the standalone Racing monitor | Depends on Racing safety settings |
| `autoremove-manager hybrid` | Start the per-client Normal/Racing controller | Depends on Global and Hybrid safety settings |
| `autoremove-manager logs` | Show the log directory and its files | No |
| `autoremove-manager uninstall` | Start the guarded uninstaller | No torrent data is removed |

The `run` command refuses to perform removal unless `--confirm-removal` is supplied in addition to the required configuration switches.

## How cleanup works

There are three different execution styles. They are not interchangeable.

### Normal mode

Normal mode runs the strategy defined for that client in `config.yml` through the upstream AutoRemove Torrents engine.

- The manager does not replace, reinterpret, or merge your strategy with Racing rules.
- Each Hybrid Normal worker creates a temporary task-only YAML file containing that client's original task.
- The temporary file is created with restrictive permissions and removed after the run.
- The upstream engine evaluates the configured strategy and reports the matching torrents.
- In Hybrid, Normal cleanup is attempted on the configured cleanup interval (default: 60 seconds), subject to the shared run lock.

Your Normal-mode conditions are entirely determined by your YAML strategy. Read the upstream documentation for expression semantics.

### Racing mode

Racing mode is an alternative eligibility rule for qBittorrent and Deluge. It is designed to react to a client's active download burst.

Default thresholds:

| Setting | Default |
|---|---:|
| Aggregate client download threshold | 100 MB/s |
| Time threshold must be sustained | 5 seconds |
| Minimum torrent ratio | 1.0 |
| Maximum current torrent upload speed | 10 MB/s |
| Hybrid sampling interval | 1 second |
| Hybrid cleanup interval | 60 seconds |

Racing candidate rules:

1. The torrent must be in a recognized seeding state.
2. Its reported ratio must be at least `ratio_min` (default `1.0`).
3. Its current upload speed must be at or below `upload_max_mb` (default `10 MB/s`).
4. Seeding time is **not** part of the Racing eligibility rule.
5. Missing, malformed, or non-finite API fields are not treated as zero; such torrents are excluded.

The supported seeding states are checked according to the client's API representation. For qBittorrent, the implementation recognizes `uploading`, `stalledup`, `queuedup`, and `forcedup`. For Deluge, it checks the `seeding` state.

Standalone Racing mode is a long-running monitor started with:

```bash
autoremove-manager racing
```

It requires `racing.enabled=true`. It is not started by the hourly scheduled job.

### Hybrid mode

Hybrid mode combines the existing Normal YAML strategy with Racing eligibility. It does not run both strategies against a client at the same time.

Each configured qBittorrent or Deluge task gets its **own independent worker, speed samples, mode state, API session, and cleanup timer**.

#### Per-client mode switching

```text
                         Download speed >= 100 MB/s
                         continuously for 5 seconds
              +------------------------------------------+
              |                                          v
        +-----------+                               +-----------+
        |  NORMAL   |                               |  RACING   |
        | YAML rule |                               | ratio /   |
        | unchanged |                               | upload    |
        +-----------+                               +-----------+
              ^                                          |
              +------------------------------------------+
                         Download speed < 100 MB/s
                         continuously for 5 seconds
```

- A client's own aggregate download speed is sampled every second by default.
- At or above the configured threshold continuously for the sustain period, that client switches from Normal to Racing.
- Below the threshold continuously for the sustain period, that client switches back to Normal.
- A dip or invalid speed sample resets the relevant transition streak.
- A qBittorrent mode change does not force Deluge to change mode. Each client switches independently.
- After an API/worker error, cleanup is suspended for that client, its API session is closed, and its mode classification is reset. After reconnecting, a fresh uninterrupted speed streak is required before cleanup resumes.
- An API failure for one client does not intentionally stop the other client's worker.

#### Cleanup timing and locking

- Speed monitoring interval: `poll_interval_seconds` (default 1 second).
- Cleanup cadence: `cleanup_interval_seconds` (default 60 seconds).
- A mode transition makes the next cleanup eligible immediately.
- Normal and Racing cleanup share the manager's process lock with ordinary manager runs. If another run owns the lock, the current cycle is skipped rather than overlapping.
- A log message such as `shared run lock busy; ... cycle skipped` indicates lock contention; it is not by itself an API or deletion failure.

Hybrid is a long-running foreground process:

```bash
autoremove-manager hybrid
```

It starts only when `hybrid.enabled=true` and at least one supported qBittorrent/Deluge task is configured. The command remains running while its workers monitor their clients. Stop it with Ctrl+C or a normal termination signal; the controller signals its workers and waits for them to exit.

**The hourly schedule does not start Hybrid.** Use a persistent terminal/session manager or a separately configured process supervisor if Hybrid must survive logout or restart. The project installer does not silently add Hybrid to cron or systemd.

## Hybrid configuration reference

The Hybrid object is stored in `manager.json`. The default structure is:

```json
{
  "hybrid": {
    "enabled": false,
    "preview_only": true,
    "allow_removal": false,
    "allow_data_deletion": false,
    "download_threshold_mb": 100.0,
    "upload_max_mb": 10.0,
    "ratio_min": 1.0,
    "sustain_seconds": 5.0,
    "poll_interval_seconds": 1.0,
    "cleanup_interval_seconds": 60.0
  }
}
```

| Key | Meaning |
|---|---|
| `enabled` | Allows the `hybrid` command to start |
| `preview_only` | Keeps Hybrid cleanup in preview mode when true |
| `allow_removal` | Hybrid-level permission for torrent removal |
| `allow_data_deletion` | Hybrid-level permission for downloaded-payload deletion |
| `download_threshold_mb` | Per-client download speed threshold for entering Racing |
| `upload_max_mb` | Maximum current upload speed of an eligible Racing torrent |
| `ratio_min` | Minimum ratio for Racing eligibility |
| `sustain_seconds` | Continuous high/low speed period required for a mode transition |
| `poll_interval_seconds` | How often each worker samples client download speed |
| `cleanup_interval_seconds` | Minimum interval between cleanup attempts for a worker |

The top-level manager settings are separate from this object. Hybrid does not become live merely because one set of switches is enabled. See the complete [Configuration guide](docs/CONFIGURATION.md) for the exact deletion requirements.

## Safety model

The default configuration is deliberately conservative:

- Top-level `preview_only: true`
- Top-level `allow_removal: false`
- Top-level `allow_data_deletion: false`
- Racing and Hybrid disabled, preview-only, and with their removal permissions off
- No torrent or downloaded payload is removed by a default installation

### Normal and Hybrid Normal deletion

For Hybrid Normal mode, both the top-level manager object and the nested `hybrid` object must authorize removal:

- `preview_only: false`
- `allow_removal: true`

If the corresponding YAML task has `delete_data: true`, both top-level and Hybrid `allow_data_deletion: true` are also required. If the task has `delete_data: false`, the manager does not grant payload deletion through that task.

For ordinary manual `run`, the separate `--confirm-removal` command-line confirmation is also required.

### Hybrid Racing deletion

Hybrid Racing has a stricter six-switch gate. **All six conditions must be explicitly satisfied:**

| Location | Required value |
|---|---|
| Top-level manager settings | `preview_only: false` |
| Top-level manager settings | `allow_removal: true` |
| Top-level manager settings | `allow_data_deletion: true` |
| Nested `hybrid` object | `preview_only: false` |
| Nested `hybrid` object | `allow_removal: true` |
| Nested `hybrid` object | `allow_data_deletion: true` |

Before Racing removal, Hybrid reads the torrent list again and keeps only hashes that were in the original candidate list and still qualify. It does not add newly eligible torrents during that final check. qBittorrent removal is followed by a fresh torrent-list check; this verifies that the torrent entry is no longer listed, not an independent forensic check of filesystem contents.

### Standalone Racing deletion

Standalone Racing uses its own nested `racing` safety settings. The Racing object must have `preview_only: false`, `allow_removal: true`, and `allow_data_deletion: true`. The standalone monitor is separate from Hybrid.

### Fail-closed behaviour

- API/authentication/worker errors suspend cleanup for the affected client.
- Invalid or missing speed samples reset the mode-transition streak.
- Invalid or missing Racing torrent fields do not qualify as zero values.
- The shared lock prevents overlapping cleanup operations.
- Racing candidates are revalidated immediately before deletion.
- Hybrid Normal cleanup invokes only the selected client's temporary task configuration.

**Payload deletion is permanent from the manager's perspective.** When `delete_data: true` is configured and all required permissions are enabled, downloaded files can be removed along with the torrent entry. Confirm the candidate list, API endpoint, tracker rules, and backups before enabling it.

## Scheduling and long-running processes

The installer configures the ordinary scheduled command (hourly when the platform supports the selected scheduler). That scheduled command is separate from the long-running Racing and Hybrid commands.

| Workflow | How it starts | Does the installer schedule it? |
|---|---|---|
| Ordinary scheduled cleanup | systemd timer or marked cron fallback | Yes, where supported |
| Standalone Racing | `autoremove-manager racing` | No |
| Hybrid | `autoremove-manager hybrid` | No |

The schedule is controlled by `schedule.enabled` in `manager.json`. A disabled schedule does not disable a manually started Hybrid process. Conversely, enabling the ordinary schedule does not start Hybrid.

For a long-running monitor, use a persistent terminal such as `tmux` or `screen`, or configure your own supervisor for the installing user. Do not add a second ordinary cleanup cron job if Hybrid Normal workers already run the same YAML strategy; duplicate cleanup schedules can compete for the shared lock and create unnecessary runs.

## Logs and diagnostics

Use:

```bash
autoremove-manager status
autoremove-manager logs
autoremove-manager test
autoremove-manager preview
```

- `status` prints the active installation mode, global safety switches, configured tasks, schedule, and Racing/Hybrid settings.
- `logs` prints the log directory and lists its files.
- `test` checks configured client API authentication without removing torrents.
- `preview` evaluates upstream YAML strategies without removal.
- Racing/Hybrid runtime activity is written to the manager's Racing/Hybrid log (commonly `racing.log`); console output can be redirected separately when starting a long-running process.

For rootless installs, the usual locations are:

```text
~/.local/state/auto-remove-manager/
~/.local/state/auto-remove-manager/logs/
```

Upstream daily logs rotate at 5 MiB and retain one previous segment (`.1`). The active log and its single backup are retained.

Useful log messages:

| Message | Meaning |
|---|---|
| `API authenticated; monitoring enabled` | That worker authenticated and began monitoring |
| `switched to RACING mode` | That client's high-speed threshold was sustained |
| `switched to NORMAL mode` | That client's speed remained below threshold for the sustain period |
| `no Racing-eligible seeding torrents` | No current torrent met the Racing conditions |
| `Racing preview only; no torrent data was changed` | Racing candidates were found, but safety settings prevented deletion |
| `shared run lock busy; ... cycle skipped` | Another manager cleanup owns the shared lock |
| `API/worker error ... cleanup suspended` | The affected worker stopped cleanup and will reconnect/fail closed |

## Updating

Use the installer again in the same installation mode and as the same installing account:

Rootless:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh) --mode rootless
```

Root/system-wide:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh | sudo bash -s -- --mode root
```

The installer checks its ownership markers before updating the managed source tree. It updates the project checkout and virtual-environment dependencies. Review release notes and back up configuration before upgrades. Do not run a rootless update with sudo.

## Uninstallation

Guided uninstall:

```bash
autoremove-manager uninstall
```

Rootless direct uninstaller:

```bash
bash ~/.local/share/auto-remove-manager/uninstall.sh --mode rootless
```

Root/system-wide direct uninstaller (as root):

```bash
bash /opt/auto-remove-manager/uninstall.sh --mode root
```

The uninstaller asks for the word `REMOVE` unless `--yes` is supplied. It stops/removes only the exact manager-owned service/timer names, removes the CLI wrapper only when its marker matches, and deletes only manager directories containing the expected ownership marker.

It removes the manager's own configuration (including saved client credentials), virtual environment, state, logs, and manager-owned files within those directories.

It does **not** remove or modify:

- qBittorrent, Deluge, Transmission, µTorrent, or rTorrent.
- Torrent entries, downloaded payloads, or download directories.
- Torrent-client configuration, credentials, services, or systemd units.
- Unrelated cron entries or other applications.
- Shared system packages such as Python or Git.

If an ownership marker is missing or a managed path is a symlink, the uninstaller leaves that item untouched rather than risking deletion outside its scope. See [Uninstallation](docs/UNINSTALL.md).

## Troubleshooting

### `autoremove-manager: command not found`

For rootless mode, use the full path:

```bash
~/.local/bin/autoremove-manager status
```

Or add `~/.local/bin` to your `PATH`.

### No client detected

Check that the client is running or that its configuration exists in a supported discovery path. Detection is Linux-focused and does not scan arbitrary mounted filesystems or remote hosts.

### API login fails

Check the API/WebUI/RPC setting, reachable host and port, and credentials for that exact client instance. A running process or config file does not prove API reachability.

### Feral qBittorrent endpoint is not reachable on localhost

Feral may run qBittorrent in an isolated container or network namespace. The configured WebUI port is not guaranteed to be reachable at `127.0.0.1:<port>`. Use the endpoint actually exposed to your account and verify it with `autoremove-manager test`. A provider panel/proxy URL returning `403 Forbidden` is not proof of a working qBittorrent API endpoint.

### Deluge RPC connection fails

Check the configured daemon RPC port and credentials. The Feral discovery helper can read a valid `daemon_port` from `~/.config/deluge/core.conf`, but the endpoint and credentials still need to be verified.

### Preview finds no candidates

The configuration wizard intentionally creates empty strategy mappings. Add a reviewed strategy to the relevant task in `config.yml`, then run `autoremove-manager preview` again.

### Hybrid does not start

Check that `hybrid.enabled` is exactly `true`, at least one qBittorrent or Deluge task is configured, and the manager configuration is valid JSON. Start it in a persistent terminal and inspect the runtime log for API or worker errors.

### Hybrid stops after logout or terminal closure

Hybrid is a long-running process and is not started by the hourly schedule. Use a persistent session or a user-level process supervisor. The installer does not install a Hybrid service automatically.

### A cleanup cycle is skipped because the lock is busy

Another manager operation currently owns the shared run lock. The worker skips that cycle instead of running two cleanup operations concurrently. Check for duplicate manual/scheduled processes if this happens continuously.

### User systemd timer does not run after logout

Some distributions stop user services when the session ends. The host may require user lingering, or the rootless crontab fallback where available.

### Uninstall leaves a path behind

The uninstaller intentionally refuses to remove directories without its ownership marker or paths that are symlinks. Inspect the path manually; do not force a recursive delete.

## Development and tests

Run from a checkout:

```bash
bash -n install.sh
bash -n uninstall.sh
shellcheck --severity=style install.sh uninstall.sh
python3 -m pip install -r requirements.txt
python3 -m compileall -q src tests
python3 -m unittest discover -s tests -v
```

The CI workflow tests Python 3.8 and the current Python 3.x line, compiles the source/tests, runs the unit suite, and validates the Feral-compatible urllib3 dependency. Shell validation is performed in CI as well.

See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), and [Configuration](docs/CONFIGURATION.md).

## Upstream project and attribution

- Cleanup engine and adapters: [jerrymakesjelly/autoremove-torrents](https://github.com/jerrymakesjelly/autoremove-torrents), MIT License.
- Strategy syntax and behaviour reference: [AutoRemove Torrents documentation](https://autoremove-torrents.readthedocs.io/en/latest/).
- Installer UX inspiration: [QuickBox Lite](https://github.com/amefs/quickbox-lite). No QuickBox Lite code is copied into this project.

Auto-Remove-Manager is an independent installer and orchestration project. It does not claim authorship of the upstream cleanup engine. The upstream dependency retains its own license and notices.

## License

The manager's own code is licensed under the MIT License. See [LICENSE](LICENSE).

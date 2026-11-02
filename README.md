# Auto-Remove-Manager

[![CI](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/ci.yml)
[![ShellCheck](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/shellcheck.yml/badge.svg?branch=main)](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/shellcheck.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**A safe one-command installer and manager for AutoRemove Torrents.**

Two separate installation modes are supported:
- **Root/system-wide:** system paths, dedicated systemd service/timer, service runs as the invoking sudo user when installed through sudo.
- **Rootless/per-user:** all program, config, logs and units stay under the user's home; uses user systemd or a marked per-user cron entry.

## One-click installation

### Root mode

Run as root or with sudo:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh | sudo bash -s -- --mode root
```

### Rootless mode

Run as the torrent-client owner. Do not use sudo:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh) --mode rootless
```

When already logged in as root, omit `sudo` and use `bash` instead. For the interactive selector, run the script from a terminal with stdin attached:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh)
```

## Features

- Detects supported clients using running processes, installed packages (dpkg/rpm/pacman/apk), systemd service/unit files, and common per-user or system-wide configuration paths. Includes Feral-specific qBittorrent and Deluge config discovery.
- Supports qBittorrent, Deluge, Transmission, and µTorrent through upstream adapters.
- Reports rTorrent if detected, but does not create an rTorrent task because the upstream engine has no rTorrent adapter.
- Tests client authentication and reads basic client status before saving a task.
- Creates a separate upstream task for each verified client.
- Stores the upstream YAML with owner-only permissions; credentials are never printed by this manager.
- Uses upstream strategy expressions; does not invent cleanup rules.
- Runs an hourly preview schedule by default.
- Protects against concurrent runs with a process lock.
- Rotates upstream daily log files at 5 MiB and retains one backup.
- Uses explicit deletion and payload-deletion safeguards.
- Includes an optional Racing Mode monitor for independent qBittorrent and Deluge speed triggers, with per-client workers and fail-closed API handling.
- One-click uninstall removes manager-owned files, configuration, credentials, logs, virtual environment, service/timer and marked cron entry.

## First-time setup

After installation:

```bash
autoremove-manager detect
autoremove-manager configure
autoremove-manager test
autoremove-manager preview
```

The configure wizard lists every supported local candidate and asks for the endpoint and missing credentials. A task is saved only after the upstream adapter successfully authenticates and reads client status. For a root installation run through sudo, run the management commands as the same normal user that invoked sudo (without sudo); that user owns the manager configuration and scheduled service. If the installer was run directly as root, run them as root. Some secrets cannot be extracted from client configuration. For example, qBittorrent stores a WebUI password hash, so the password must be entered interactively.

The wizard creates empty strategy mappings. This is deliberate: a client can be connected without automatically applying a removal rule you have not reviewed. Add your preferred conditions to the upstream YAML configuration after reading the [official strategy documentation](https://autoremove-torrents.readthedocs.io/en/latest/).

## Commands

```bash
autoremove-manager status
autoremove-manager detect
autoremove-manager configure
autoremove-manager test
autoremove-manager preview
autoremove-manager racing  # standalone Racing monitor (legacy)
autoremove-manager hybrid  # per-client automatic Normal/Racing switching
autoremove-manager logs
autoremove-manager uninstall
```

Actual removal is disabled by default. To manually enable it, edit `manager.json` and set both:

```json
{
  "preview_only": false,
  "allow_removal": true
}
```

Then run:

```bash
autoremove-manager run --confirm-removal
```

Scheduled runs remain preview-only until the same settings are deliberately changed. Removing downloaded payload data has an additional separate opt-in: `allow_data_deletion: true`, plus `delete_data: true` in the relevant task. Read [Configuration](docs/CONFIGURATION.md) before enabling either option.

## Racing Mode (optional)

Racing Mode is a separate, long-running monitor and is disabled by default. It samples each configured qBittorrent or Deluge client independently. A client's own download speed must remain at or above 100 MB/s for five seconds before that same client is evaluated. All of that client's seeding torrents with ratio >= 1.0 and live upload speed <= 10 MB/s are eligible; seeding time is ignored. Eligible torrents are removed with their payload data only after all three Racing Mode safety switches are deliberately enabled. API errors fail closed for the affected client and do not stop the other client's worker.

Run `autoremove-manager racing` in a persistent terminal/session after enabling `racing.enabled`. The initial settings are preview-only and all deletion switches are off. See [Configuration](docs/CONFIGURATION.md) before enabling removal. The monitor is not automatically added to cron or systemd.

## Hybrid Mode (per-client automatic switching)

Hybrid Mode runs one independent worker for each configured qBittorrent or Deluge task. Each worker samples that client's aggregate download speed every second:

- At or above 100 MB/s continuously for five seconds: that client switches from Normal to Racing.
- Below 100 MB/s continuously for five seconds: that client switches back to Normal.
- Racing cleanup uses ratio >= 1.0 and live upload <= 10 MB/s; seeding time is ignored.
- Normal cleanup invokes the existing upstream YAML strategy unchanged, using a temporary task-only configuration so another client's mode cannot affect it.
- Each client's cleanup cadence defaults to 60 seconds. A shared lock serializes cleanup operations, and API errors suspend cleanup for the affected client.

Hybrid Mode is disabled by default and starts only with `autoremove-manager hybrid` after `hybrid.enabled=true`. Its own `preview_only` defaults to true. Keep it in preview mode while reviewing `racing.log`; actual removal requires the Hybrid removal switches plus the existing Normal-mode safety settings where applicable. Do not enable deletion until preview results have been verified.

## Uninstallation

One-click, with confirmation.

For a root/system-wide installation:

```bash
sudo autoremove-manager uninstall
```

For a rootless/per-user installation, run as the installing user:

```bash
~/.local/bin/autoremove-manager uninstall
```

Or run the standalone uninstaller directly:

Root mode:

```bash
bash /opt/auto-remove-manager/uninstall.sh --mode root
```

Rootless mode:

```bash
bash ~/.local/share/auto-remove-manager/uninstall.sh --mode rootless
```

The uninstaller uses ownership markers and removes only the exact manager-owned paths and scheduled jobs. It does not remove or modify torrent clients, client settings, torrents, download directories, or payload data. See [Uninstallation](docs/UNINSTALL.md).

## Supported clients

| Client | Detection | Upstream adapter | Method |
|---|---|---|---|
| qBittorrent | Process, systemd, common config | Yes | Web API |
| Deluge | Process, systemd, auth file | Yes | RPC |
| Transmission | Process, systemd, common config | Yes | RPC |
| µTorrent | Process, systemd, common config | Yes | Web API |
| rTorrent | Process and systemd | No | Report only |

See [Supported Clients](docs/SUPPORTED_CLIENTS.md), [Feral Hosting](docs/FERAL.md), and [Troubleshooting](docs/TROUBLESHOOTING.md).

## Safety model

1. Discovery is read-only and does not report an API as verified.
2. Client tasks are saved only after API login/basic status verification.
3. Preview is the default for manual and scheduled runs.
4. Actual removal needs explicit settings and, for a manual run, a confirmation flag.
5. Downloaded-data removal needs a second explicit opt-in.
6. Overlapping jobs are prevented with a lock.
7. Uninstall checks project ownership markers before deleting managed directories.

## Development

```bash
bash -n install.sh
bash -n uninstall.sh
shellcheck --severity=style install.sh uninstall.sh
python3 -m pip install -r requirements.txt
python3 -m compileall -q src tests
python3 -m unittest discover -s tests -v
```

See [Contributing](CONTRIBUTING.md) and [Security](SECURITY.md).

## Upstream and attribution

- Cleanup engine: [jerrymakesjelly/autoremove-torrents](https://github.com/jerrymakesjelly/autoremove-torrents), MIT License.
- Behavior and strategy reference: [official documentation](https://autoremove-torrents.readthedocs.io/en/latest/).
- Installer UX inspiration: [QuickBox Lite](https://github.com/amefs/quickbox-lite). No QuickBox Lite code is copied into this project.

Auto-Remove-Manager is an independent installer/orchestration project. It does not claim authorship of the upstream cleanup engine.

## License

The manager's own code is licensed under MIT. See [LICENSE](LICENSE). The upstream dependency retains its own license and notices.

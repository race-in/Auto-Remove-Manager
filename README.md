# Auto-Remove-Manager

[![CI](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/race-in/Auto-Remove-Manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A safe one-command installer and lifecycle manager for AutoRemove Torrents.

Supports two separate installation modes:
- **Root/system-wide:** dedicated system paths and systemd service/timer.
- **Rootless/per-user:** user-owned paths and user systemd, with cron fallback planned.

Safety defaults: preview-first, downloaded-data deletion disabled, no changes to torrent-client configuration, and scoped uninstallation.

## Supported upstream adapters

| Client | Adapter | Integration |
|---|---|---|
| qBittorrent | Yes | Web API |
| Deluge | Yes | RPC |
| Transmission | Yes | RPC |
| µTorrent | Yes | Web API |
| rTorrent | No | Detection/report only; no upstream adapter |

Detection of a process or config file is not proof that the API is reachable. Tasks must only be generated after endpoint/authentication verification. Some credentials cannot be recovered from client config (qBittorrent WebUI password hashes, for example), so the installer must ask rather than guess.

## Install

Root/system-wide, run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh -o /tmp/arm-install.sh
bash /tmp/arm-install.sh --mode root
```

Rootless, run as the torrent-client owner (do not use sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh -o /tmp/arm-install.sh
bash /tmp/arm-install.sh --mode rootless
```

Interactive mode selection:

```bash
bash /tmp/arm-install.sh
```

## Commands

```bash
autoremove-manager status
autoremove-manager detect
autoremove-manager test
autoremove-manager preview
autoremove-manager run
autoremove-manager uninstall
```

The initial release is preview-only. Actual torrent deletion and deletion of payload data are separate settings and must never be enabled implicitly.

## Uninstall

Root mode:

```bash
bash uninstall.sh --mode root
```

Rootless mode, as the installing user:

```bash
bash uninstall.sh --mode rootless
```

The uninstaller is scoped to this manager's own installation paths, unit files, and marked cron entries. It must not remove torrent clients, torrent data, downloads, or client configuration.

## Upstream

- [AutoRemove Torrents source](https://github.com/jerrymakesjelly/autoremove-torrents)
- [AutoRemove Torrents documentation](https://autoremove-torrents.readthedocs.io/en/latest/)
- [QuickBox Lite](https://github.com/amefs/quickbox-lite) for general one-key installer UX inspiration. No QuickBox Lite source is copied.

The upstream AutoRemove Torrents project is MIT licensed. This manager's own code is also MIT licensed; see [LICENSE](LICENSE).

> Development status: the repository scaffold and safety foundations are being built. Do not use automatic destructive cleanup until client discovery, API validation, preview, and uninstall tests are complete.

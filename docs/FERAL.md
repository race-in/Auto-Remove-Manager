# Feral Hosting (rootless)

This guide documents the Feral-specific discovery behavior. It does not change
the normal Linux, root, or other rootless installation paths.

## Install

Run the installer as the Feral account that owns the torrent clients. Do not
use `sudo`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh) --mode rootless
```

The manager is installed under the account's home directory. It does not need
root privileges and does not restart, reconfigure, or modify qBittorrent or
Deluge.

## Feral qBittorrent

Feral's qBittorrent configuration can be located at:

```text
~/.qbittorrent/qBittorrent/config/qBittorrent.conf
```

The manager detects this path and can read the configured WebUI port. However,
Feral may run qBittorrent in an isolated container/network namespace. The
configured port is therefore **not guaranteed** to be reachable at
`127.0.0.1:<port>` from the user's shell.

For this reason, the configure wizard does not invent a loopback endpoint
when it detects this Feral-specific path. Enter the API endpoint that Feral
actually exposes to your account, then allow the wizard to verify it before
saving the task. A provider panel URL or proxy URL that returns `403 Forbidden`
is not proof of a working qBittorrent API endpoint.

Do not infer the reachable API port from the config file alone. Verify the
endpoint using the read-only API test in the manager.

## Feral Deluge

Feral's Deluge profile is normally under:

```text
~/.config/deluge/
```

The manager detects both `auth` and `core.conf`. When `core.conf` contains a
valid `daemon_port`, the configure wizard uses that value for the suggested
RPC endpoint (`127.0.0.1:<daemon_port>`). If no valid port is present, it
retains the upstream default of `58846`. Credentials still need to be entered
and verified; the manager does not read or print the Deluge password.

## Scheduling and safety

Feral rootless installations use the per-user cron fallback when user systemd
is unavailable. The installer creates a marked hourly schedule. It preserves
unrelated cron entries and replaces only the manager's own marked entry.

The installer starts in preview-only mode. Before enabling any removal, inspect
the preview and read [Configuration](CONFIGURATION.md). Actual torrent removal
and downloaded-data deletion are separate safety controls. Never enable
payload deletion until the endpoint, matching rules, and preview output have
been independently checked.

## Read-only diagnostics

These commands inspect the local client setup without restarting clients:

```bash
~/.local/bin/autoremove-manager detect
~/.local/bin/autoremove-manager test
~/.local/bin/autoremove-manager preview
```

A detected process or config file is only a discovery candidate; it does not
prove that the API endpoint is reachable or authenticated.
## Log rotation

Feral rootless installs use the same bounded upstream log policy: each daily
log file rotates when it reaches 5 MiB and keeps one previous segment (`.1`).
The active log and its single backup are retained; older rotated content is
replaced on the next rollover. Rotation is handled by the manager's Python
logging handler and does not require cron changes or client restarts.

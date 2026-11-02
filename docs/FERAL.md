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


## Optional Racing Mode

Racing Mode is not started by the hourly cron entry. It is a separate long-running process and remains disabled after installation. After both qBittorrent and Deluge API endpoints have been configured and verified, enable the nested `racing` settings in `~/.config/auto-remove-manager/manager.json` and start it in a persistent session:

```bash
~/.local/bin/autoremove-manager racing
```

Each client has its own worker and speed timer. A client must sustain at least 100 MB/s download speed for five seconds; then its own seeding torrents qualify at ratio >= 1.0 and live upload <= 10 MB/s. Seeding time is ignored. Preview-only is the default. Permanent torrent and payload deletion requires all three Racing Mode switches (`preview_only=false`, `allow_removal=true`, `allow_data_deletion=true`) to be deliberately enabled. API failures fail closed for that client.


## Optional Hybrid Mode

Hybrid Mode is a separate long-running process; the hourly cron schedule does not start it. It independently monitors each configured qBittorrent and Deluge task, switching that client from Normal to Racing after download speed remains at or above 100 MB/s for five seconds, and back to Normal after speed remains below 100 MB/s for five seconds.

Enable `hybrid.enabled=true` in `~/.config/auto-remove-manager/manager.json`, then start it in a persistent terminal/session:

```bash
~/.local/bin/autoremove-manager hybrid
```

Keep `hybrid.preview_only=true`, `hybrid.allow_removal=false`, and `hybrid.allow_data_deletion=false` while validating. Hybrid uses the existing upstream YAML strategy in Normal mode and the Racing eligibility rules in Racing mode. Both modes share the manager cleanup lock. Review `racing.log` in the manager log directory and use `~/.local/bin/autoremove-manager logs` to display the log directory and its files.

**Hybrid Racing deletion requires all six safeguards:** the top-level `preview_only=false`, `allow_removal=true`, and `allow_data_deletion=true`, plus the same three settings inside the `hybrid` object. Normal-mode removal additionally requires the top-level and Hybrid removal switches; if the task sets `delete_data: true`, both levels must also authorize data deletion. Do not enable these switches until API connectivity and preview candidates have been independently verified.

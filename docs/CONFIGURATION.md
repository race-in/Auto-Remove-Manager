# Configuration

The manager uses two separate files:

- `manager.json`: installation mode, safety switches, and schedule metadata.
- `config.yml`: upstream AutoRemove Torrents task definitions.

Run `autoremove-manager detect` to list local candidates, then
`autoremove-manager configure` to create one independent upstream task per
selected client. The wizard asks for missing connection information and writes
the YAML file with owner-only permissions. Credentials are never printed.

## Discovery limitations

Detection uses process arguments, systemd service names, and common per-user
configuration paths. It is advisory only. A running process or a config file
does not prove API availability. Use `autoremove-manager test` or
`autoremove-manager preview` to check client login and task execution through
the upstream engine.

qBittorrent stores its WebUI password as a hash; the manager cannot recover it.
Deluge auth-file discovery may be blocked by filesystem permissions. In these
cases, enter the password interactively.

## Strategy setup

The wizard creates tasks with an empty `strategies` mapping. This prevents
surprising cleanup rules from being installed on your behalf. Add your own
named strategy blocks after reading the upstream reference:

https://autoremove-torrents.readthedocs.io/en/latest/

Example upstream task structure:

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

This is an example only. Adjust the strategy to your tracker rules and review
the output before enabling any real removal.

## Safety switches

The manager settings file is created with:

- `preview_only: true`
- `allow_removal: false`
- `allow_data_deletion: false`

Manual deletion requires both `allow_removal: true` and
`preview_only: false` in `manager.json`, then:

```bash
autoremove-manager run --confirm-removal
```

Scheduled runs remain preview-only until those two settings are deliberately
changed. Deleting downloaded payloads additionally requires
`allow_data_deletion: true` and a task with `delete_data: true`. Do not
enable payload deletion unless you have a verified backup and understand the
tracker's retention requirements.


## Racing Mode

Racing Mode is independent of the upstream strategy scheduler. It supports configured qBittorrent and Deluge tasks only. It is a foreground, long-running monitor; it is **not** automatically scheduled or started by installation. Run `autoremove-manager racing` in a persistent terminal/session after reviewing the settings.

The installer initializes this block safely in `manager.json`:

```json
{
  "racing": {
    "enabled": false,
    "preview_only": true,
    "allow_removal": false,
    "allow_data_deletion": false,
    "download_threshold_mb": 100.0,
    "upload_max_mb": 10.0,
    "ratio_min": 1.0,
    "sustain_seconds": 5.0,
    "poll_interval_seconds": 1.0
  }
}
```

Rules are evaluated separately for each client task:

- That client's aggregate download speed must stay at or above 100 MB/s for five continuous seconds.
- After its own trigger, all of that client's torrents in a seeding state are checked.
- A torrent qualifies when ratio is at least 1.0 and its current upload speed is at most 10 MB/s.
- Seeding time is deliberately not checked.
- Qualified torrents are removed with downloaded data when, and only when, `preview_only=false`, `allow_removal=true`, and `allow_data_deletion=true` are all set inside the `racing` object.

If any API request or authentication check fails, that client's speed streak is reset and no deletion is attempted for that client. The other client's monitor runs in its own worker. A speed dip below the threshold resets the five-second timer and rearms that client's trigger. The monitor shares the manager's run lock before deleting so it cannot overlap an ordinary manager cleanup.

**Warning:** Enabling all three switches permits permanent removal of torrent entries and their downloaded files. First leave Racing Mode in preview-only mode, inspect `racing.log`, verify both APIs and the matching list, and make sure the download directory contains no data you need.


## Hybrid Mode

Hybrid mode uses independent qBittorrent and Deluge workers. Each worker enters Racing after download speed stays at or above 100 MB/s for five seconds, and returns to Normal after speed stays below 100 MB/s for five seconds. Invalid samples reset the transition timer. Normal mode runs that task's unchanged upstream YAML rule. Racing mode selects seeding torrents with ratio >= 1.0 and live upload <= 10 MB/s; seeding time is ignored. Cleanup cadence defaults to 60 seconds and all cleanup operations share the manager lock.

The `hybrid` block is disabled by default. Set `hybrid.enabled=true` and run `autoremove-manager hybrid`. Keep `hybrid.preview_only=true` during validation. Racing deletion requires `hybrid.preview_only=false`, `hybrid.allow_removal=true`, and `hybrid.allow_data_deletion=true`. Normal deletion additionally requires both Hybrid-level switches (`hybrid.allow_removal=true` and `hybrid.preview_only=false`) and the top-level `allow_removal=true` and `preview_only=false` settings. If the task has `delete_data: true`, both top-level and Hybrid `allow_data_deletion=true` are also required. API errors suspend cleanup for the affected client.

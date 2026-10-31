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

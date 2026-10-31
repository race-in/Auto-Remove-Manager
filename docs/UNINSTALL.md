# Uninstallation

Use the installed command for a guided one-click removal:

```bash
autoremove-manager uninstall
```

Or run the uninstaller directly.

Root/system-wide, as root:

```bash
bash /opt/auto-remove-manager/uninstall.sh --mode root
```

Rootless, as the installing user:

```bash
bash ~/.local/share/auto-remove-manager/uninstall.sh --mode rootless
```

The uninstaller asks for the word `REMOVE` before proceeding. It stops and
removes only the exact Auto-Remove-Manager service/timer names, removes the
CLI wrapper only when its ownership marker matches, and deletes only manager
directories containing the expected ownership marker.

The removed directories include the manager's own configuration (including
saved client credentials), virtual environment, state, logs, and backups
stored inside those managed directories.

It does **not** remove or modify:

- qBittorrent, Deluge, Transmission, µTorrent, or rTorrent.
- Any torrent, torrent metadata, downloaded payload, or download directory.
- Any torrent-client configuration, credentials, service, or systemd unit.
- Unrelated cron entries or other applications.

If an ownership marker is missing or a managed path is a symlink, the
uninstaller leaves that item untouched and reports the reason instead of
risking deletion outside its scope.

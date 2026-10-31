# Installation

## Root / system-wide

Run the installer as root:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh -o /tmp/arm-install.sh
sudo bash /tmp/arm-install.sh --mode root
```

The system-wide layout is:

- Program and virtual environment: `/opt/auto-remove-manager`
- Configuration and credentials: `/etc/auto-remove-manager`
- State and lock: `/var/lib/auto-remove-manager`
- Logs: `/var/log/auto-remove-manager`
- CLI: `/usr/local/bin/autoremove-manager`
- systemd unit/timer: `/etc/systemd/system/auto-remove-manager.*`

When invoked through sudo, the invoking user owns the configuration/state paths
and runs the service. When invoked directly as root, root owns the installation.

## Rootless / per-user

Run as the torrent-client owner. Do not use sudo:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh -o /tmp/arm-install.sh
bash /tmp/arm-install.sh --mode rootless
```

The rootless layout is entirely under the user's home:

- Program and virtual environment: `~/.local/share/auto-remove-manager`
- Configuration and credentials: `~/.config/auto-remove-manager`
- State and logs: `~/.local/state/auto-remove-manager`
- CLI: `~/.local/bin/autoremove-manager`
- systemd user units: `~/.config/systemd/user/`

The installer enables a user systemd timer when the user systemd manager is
available. Otherwise it adds a marked per-user cron entry when crontab exists.
For jobs that must run after logout, user lingering may be required on the host.

## Installer behavior

- Does not upgrade the whole OS.
- Installs only required packages on Debian/Ubuntu root installs when Python,
  venv support, or Git is missing.
- Does not change torrent-client settings or restart torrent clients.
- Enables an hourly preview schedule; preview does not remove torrents.
- Keeps actual torrent removal and payload deletion disabled.

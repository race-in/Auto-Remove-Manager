# Installation

Auto-Remove-Manager is designed for Linux hosts, not for one particular seedbox
panel. Swizzin, QuickBox, Feral-style managed slots, plain VPS installs, and
other Linux environments are supported when the host provides the required
runtime and permissions. The manager does not call panel-specific APIs or
modify panel/client configuration.

## Compatibility

| Environment | Root mode | Rootless mode |
|---|---|---|
| Debian / Ubuntu | Supported | Supported |
| Fedora / RHEL / Rocky / AlmaLinux | Supported | Supported with Python, venv and Git preinstalled |
| openSUSE | Supported | Supported with Python, venv and Git preinstalled |
| Arch Linux | Supported | Supported with Python, venv and Git preinstalled |
| Alpine Linux | Supported | Supported with Python, venv and Git preinstalled |
| Other Linux distributions | Manual dependency setup may be needed | Manual dependency setup may be needed |
| Non-Linux / Windows | Not supported | Not supported |

Root installation can install missing dependencies using apt, dnf, yum, zypper,
pacman, or apk. Rootless installation never invokes a package manager or asks
for elevated privileges; install Python 3.8+, its venv support, and Git first.

The manager can run without systemd. It uses a marked cron entry when crontab is
available; if neither systemd nor cron is available, installation completes
with scheduling disabled and the CLI remains usable manually. Containers and
managed seedbox environments may restrict systemd, cron, package installation,
or process visibility; the installer reports unavailable capabilities rather
than attempting panel-specific changes.

## Root / system-wide

Run as root or with sudo:

```bash
curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh | sudo bash -s -- --mode root
```

System-wide paths:

- Program and virtual environment: `/opt/auto-remove-manager`
- Configuration and credentials: `/etc/auto-remove-manager`
- State: `/var/lib/auto-remove-manager`
- Logs: `/var/log/auto-remove-manager`
- CLI: `/usr/local/bin/autoremove-manager`
- systemd units: `/etc/systemd/system/`

When invoked through sudo, the invoking user owns configuration/state and runs
the service. Run management commands as that same normal user, without sudo.
When invoked directly as root, root owns and runs the installation.

## Rootless / per-user

Run as the torrent-client owner. Do not use sudo:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/race-in/Auto-Remove-Manager/main/install.sh) --mode rootless
```

All manager data remains under the user's home:

- Program and virtual environment: `~/.local/share/auto-remove-manager`
- Configuration and credentials: `~/.config/auto-remove-manager`
- State and logs: `~/.local/state/auto-remove-manager`
- CLI: `~/.local/bin/autoremove-manager`
- user systemd units: `~/.config/systemd/user/`

## Scheduler behavior

The installer prefers systemd timers. If systemd is unavailable, it installs a
marked cron entry if crontab exists. If neither is available, manual CLI
commands remain available and the installer prints a warning. User systemd
jobs may require lingering to continue after logout.

## Installer behavior

- Does not upgrade the whole OS.
- Installs only missing runtime dependencies in root mode using a recognized
  system package manager.
- Does not change torrent-client settings or restart torrent clients.
- Enables an hourly preview schedule when systemd or cron is available.
- Keeps actual torrent removal and payload deletion disabled.

# Troubleshooting

## Command not found

Root mode installs `/usr/local/bin/autoremove-manager`. Rootless mode installs
`~/.local/bin/autoremove-manager`; add `~/.local/bin` to PATH if necessary.

## No client detected

Check that the client is running or that its configuration exists in one of
the common locations listed in the discovery output. Detection is local and
does not scan arbitrary mounted filesystems or remote hosts.

## API login fails

Confirm the API/WebUI/RPC is enabled, the host and port are correct, and the
credentials belong to that client instance. A process being detected does not
prove that the WebUI/RPC accepts connections.

## Preview shows no candidates

The configuration wizard deliberately creates empty strategy mappings. Read
the upstream strategy documentation and add rules to `config.yml`; then run
`autoremove-manager preview` again.

## systemd user timer does not run after logout

Some distributions stop user services when the session ends. The host may
require user lingering, or use the rootless crontab fallback where available.

## Uninstall leaves a path behind

The uninstaller intentionally refuses to remove directories without its
ownership marker or paths that are symlinks. Inspect the path manually rather
than forcing a recursive delete.

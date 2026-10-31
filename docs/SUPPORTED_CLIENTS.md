# Supported clients

The manager aligns its task generation with the adapters present in the
upstream AutoRemove Torrents engine.

| Client | Detection | Upstream adapter | Method |
|---|---|---|---|
| qBittorrent | Process, systemd, common config | Yes | Web API |
| Deluge | Process, systemd, auth file | Yes | RPC |
| Transmission | Process, systemd, common config | Yes | RPC |
| µTorrent | Process, systemd, common config | Yes | Web API |
| rTorrent | Process and systemd | No | Detection/report only |

The current upstream repository does not provide an rTorrent adapter. The
manager must not generate an rTorrent task or claim it is supported. A future
adapter would need independent API coverage and tests before being enabled.

The current discovery layer is Linux-focused and targets Debian/Ubuntu
seedboxes. Client API endpoints, authentication, and compatibility must be
verified by the upstream client adapter at runtime.

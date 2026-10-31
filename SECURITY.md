# Security Policy

Do not publish WebUI passwords, RPC credentials, client config files containing
secrets, or logs containing tokens.

Please report vulnerabilities privately through GitHub Security Advisories.

The manager defaults to preview-only operation. Actual torrent removal requires
an explicit setting change and a manual confirmation; scheduled destructive
runs require preview mode to be disabled explicitly. Downloaded-data deletion
has a separate opt-in. Secrets are stored in an owner-only configuration file
and must never be printed to logs.

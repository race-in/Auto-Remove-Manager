# Contributing

Contributions should include tests and documentation.

Run locally:

```bash
bash -n install.sh
bash -n uninstall.sh
shellcheck install.sh uninstall.sh
python3 -m pip install -r requirements.txt
python3 -m compileall -q src tests
python3 -m unittest discover -s tests -v
```

Do not introduce destructive defaults, log secrets, or write to torrent-client
configuration. Discovery must not report an API as verified until a real
authentication/API test succeeds. Uninstall must only remove files and jobs
owned by this project and must leave torrent payloads and client data untouched.

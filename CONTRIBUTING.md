# Contributing

Contributions are welcome through focused pull requests. Do not commit credentials, tokens, household data, local certificates, or environment-specific endpoints.

Before opening a pull request, run:

```bash
PYTHONPATH=hauzer_utility_exporter/rootfs/usr/lib .venv/bin/python -m unittest discover -s tests -v
docker build --build-arg BUILD_VERSION=0.1.0 -t hauzer-home-assistant-os:test hauzer_utility_exporter
HAUZER_TEST_IMAGE=hauzer-home-assistant-os:test PYTHONPATH=hauzer_utility_exporter/rootfs/usr/lib .venv/bin/python -m unittest tests.test_container_smoke -v
```

Keep changes compatible with Home Assistant OS, preserve least-privilege app settings, and add regression coverage for behavior changes.

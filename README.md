# Hauzer for Home Assistant

Send electricity import/export, gas, and water statistics from Home Assistant OS to your Hauzer household.

[![Add Hauzer to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhardimpactdev%2Fhauzer-home-assistant-os)

## Requirements

- Home Assistant OS with Supervisor
- Utility statistics recorded by Home Assistant
- A Hauzer household and its generated Home Assistant source token

## Installation

1. Use the button above, or add `https://github.com/hardimpactdev/hauzer-home-assistant-os` as a Home Assistant app repository.
2. Install **Hauzer Utility Exporter**.
3. In Hauzer, open Utilities, connect Home Assistant, and copy the token shown once.
4. Paste the token into the app configuration and start the app.
5. Check the app log for the first successful import.

Automatic discovery uses the utility statistics configured in Home Assistant Energy. Endpoint, host, TLS, backfill, and statistic ID fields are advanced overrides; production users normally keep their defaults.

Home Assistant credentials remain on the Home Assistant system. Only selected utility intervals and the exporter version are sent to Hauzer.

See [the app documentation](hauzer_utility_exporter/DOCS.md) for updates, troubleshooting, token rotation, and disconnecting.

## Development

```bash
PYTHONPATH=hauzer_utility_exporter/rootfs/usr/lib .venv/bin/python -m unittest discover -s tests -v
docker build --build-arg BUILD_VERSION=0.1.0 -t hauzer-home-assistant-os:test hauzer_utility_exporter
HAUZER_TEST_IMAGE=hauzer-home-assistant-os:test PYTHONPATH=hauzer_utility_exporter/rootfs/usr/lib .venv/bin/python -m unittest tests.test_container_smoke -v
```

Licensed under the [MIT License](LICENSE). Security issues should follow [SECURITY.md](SECURITY.md).

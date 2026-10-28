# Hauzer Utility Exporter

Home Assistant OS app for sending selected utility statistics and floor, area and device registries to Hauzer.

[![Add Hauzer to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhardimpactdev%2Fhauzer-home-assistant-os)

Install the app, create a Home Assistant source in your Hauzer household, and paste its one-time token into `hauzer_token`. Automatic discovery handles electricity import/export, gas, and water statistics configured in Home Assistant Energy.

Home Assistant credentials remain on the Home Assistant system. Selected utility intervals, the exporter version, and (by default) floor, area and device registries are sent to Hauzer. Registry snapshots contain no entities or states. Set `registry_sync` to `false` to send only utility readings.

See [DOCS.md](DOCS.md) for configuration, updates, troubleshooting, token rotation, and disconnecting.

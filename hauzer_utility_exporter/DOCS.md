# Hauzer Utility Exporter

[![Add Hauzer to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhardimpactdev%2Fhauzer-home-assistant-os)

## Install and connect

1. Add the repository with the button above and install **Hauzer Utility Exporter**.
2. In Hauzer, open Utilities and connect Home Assistant.
3. Copy the source token shown once and paste it into `hauzer_token`.
4. Keep the other settings at their defaults, save, and start the app.
5. Open the app log and confirm that the first import completes.

The app runs every five minutes. It discovers cumulative electricity import/export, gas, and water statistics from Home Assistant Energy and sends completed utility intervals to Hauzer. Missing or ambiguous gas or water data does not block electricity.

## Configuration

- `hauzer_token`: token generated for this source in your Hauzer household.
- `registry_sync`: send floor, area and device registries, default `true`. Set to `false` to disable registry reads and posts while utility imports continue.
- `hauzer_url`: advanced endpoint override for development environments.
- `hauzer_host_ip`: advanced DNS override for development environments.
- `verify_tls`: advanced certificate-verification setting; keep enabled in production.
- `initial_backfill_hours`: advanced first-run history window, defaulting to seven days.
- Statistic fields: advanced comma-separated overrides for automatic discovery.

Blank statistic override fields keep automatic discovery enabled. An override replaces discovery only for that utility.

## Registry sync

Registry sync sends a complete snapshot at startup after the Supervisor WebSocket is ready, 10 seconds after the last floor, area or device registry change in a burst, and every 24 hours. It uses `config/floor_registry/list`, `config/area_registry/list` and `config/device_registry/list` on one persistent Supervisor WebSocket, with `subscribe_events` for the three registry update events. It reconnects with backoff when Home Assistant becomes unavailable.

Snapshots go to `POST /api/home-assistant/registry` on the same origin as `hauzer_url`, using the same Hauzer source token, JSON content and accept headers, and `X-Hauzer-Exporter-Version`. Every snapshot has `source: "home_assistant"` and a UTC ISO 8601 `generated_at` timestamp. Hauzer acknowledges queued snapshots with HTTP 202 and the floor, area and device counts.

The snapshot contains:

- Floors: ID, name and level.
- Areas: ID, name, floor ID, icon and aliases.
- Devices: ID, name, user-assigned name, manufacturer, model, model ID, serial number, software and hardware versions, area ID, parent device ID, entry type, and whether the device is disabled.

All devices are included, including disabled devices and service entries. If Home Assistant has no device name, the exporter uses its user-assigned name or its ID. No entities, states, device connections or integration identifiers are included. On older Home Assistant versions without a floor registry, floors are empty and area floor IDs are null.

Strings are truncated to 255 characters. If a snapshot exceeds 100 floors, 500 areas or 5,000 devices, the exporter logs a warning and skips the entire registry post. HTTP 401 and 422 failures log a safe authentication or validation reason without retrying. Network errors and server errors retry up to three times, waiting 1, 5 and 15 seconds. Rate limits honor bounded `Retry-After` guidance. A later registry change or daily refresh starts a new snapshot attempt.

Registry sync runs in its own worker. Its reads, delivery errors and retry waits do not delay or stop the five-minute utility import loop. Set `registry_sync` to `false` and restart the app to disable registry sync.

## Privacy and security

Home Assistant credentials remain on the Home Assistant system. The app uses the Supervisor-provided session credential internally and sends selected utility intervals, its exporter version, and the registry fields listed above to Hauzer when registry sync is enabled. Tokens and full payloads are never written to the app log.

## Updates

Home Assistant shows app updates from this repository. Review the changelog, create a backup when appropriate, and use the normal app update action. Import progress is stored in `/data/state.json`, so restarts continue from the last successful interval.

## Troubleshooting

- **No readings:** configure the utilities in Home Assistant Energy and check that recorder statistics exist.
- **Ambiguous statistic:** set the corresponding advanced statistic override.
- **Unauthorized:** generate a replacement token in Hauzer and update `hauzer_token`.
- **Registry sync skipped:** reduce the registry size below the logged limit; partial snapshots are never sent.
- **Temporary delivery errors:** keep the app running; bounded retries respect Hauzer's rate-limit guidance.
- **Certificate errors in development:** correct the local trust setup whenever possible. Disable verification only for an isolated development environment.

## Rotate or disconnect

To rotate access, generate a replacement token in Hauzer and immediately update the app. The previous token stops working. To disconnect, remove the source in Hauzer and uninstall or stop the app. Existing imported utility history remains in the household.

For support, open a GitHub issue without tokens, credentials, logs containing household data, or utility payloads. Report security issues according to the repository security policy.

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
- `hauzer_url`: advanced endpoint override for development environments.
- `hauzer_host_ip`: advanced DNS override for development environments.
- `verify_tls`: advanced certificate-verification setting; keep enabled in production.
- `initial_backfill_hours`: advanced first-run history window, defaulting to seven days.
- Statistic fields: advanced comma-separated overrides for automatic discovery.

Blank statistic override fields keep automatic discovery enabled. An override replaces discovery only for that utility.

## Privacy and security

Home Assistant credentials remain on the Home Assistant system. The app uses the Supervisor-provided session credential internally and sends only selected utility intervals plus its exporter version to Hauzer. Tokens and full payloads are never written to the app log.

## Updates

Home Assistant shows app updates from this repository. Review the changelog, create a backup when appropriate, and use the normal app update action. Import progress is stored in `/data/state.json`, so restarts continue from the last successful interval.

## Troubleshooting

- **No readings:** configure the utilities in Home Assistant Energy and check that recorder statistics exist.
- **Ambiguous statistic:** set the corresponding advanced statistic override.
- **Unauthorized:** generate a replacement token in Hauzer and update `hauzer_token`.
- **Temporary delivery errors:** keep the app running; bounded retries respect Hauzer's rate-limit guidance.
- **Certificate errors in development:** correct the local trust setup whenever possible. Disable verification only for an isolated development environment.

## Rotate or disconnect

To rotate access, generate a replacement token in Hauzer and immediately update the app. The previous token stops working. To disconnect, remove the source in Hauzer and uninstall or stop the app. Existing imported utility history remains in the household.

For support, open a GitHub issue without tokens, credentials, logs containing household data, or utility payloads. Report security issues according to the repository security policy.

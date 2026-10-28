# Changelog

## 0.2.0

- Push complete Home Assistant floor, area and device registry snapshots to Hauzer at startup, after registry changes (10-second debounce), and every 24 hours.
- Include disabled and service devices; send no entities or states in registry snapshots.
- Add `registry_sync`, enabled by default, with English and Dutch configuration descriptions.
- Keep registry reads, delivery retries and reconnects separate from utility imports. Support older Home Assistant versions without floors, cap strings at 255 characters, and skip snapshots above Hauzer's registry limits.

## 0.1.3

- Accept asynchronous HTTP 202 queued acknowledgements from the utility import API so exporter cursors advance after successful queueing.

## 0.1.2

- Discover electricity import and grid export using safe directional signals.
- Replay the configured backfill window when discovered utility mappings change.

## 0.1.1

- Send an explicit exporter user agent so Cloudflare accepts production imports.

## 0.1.0

- Add automatic electricity import/export, gas, and water discovery.
- Send completed utility intervals to Hauzer every five minutes.
- Support advanced endpoint, TLS, backfill, and statistic overrides.

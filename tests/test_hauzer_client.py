import unittest

from hauzer_utility_exporter.configuration import AppConfig
from hauzer_utility_exporter.hauzer import (
    HauzerClient,
    HauzerError,
    InvalidHauzerToken,
    InvalidImportPayload,
    RetryableHauzerError,
)


def app_config() -> AppConfig:
    return AppConfig.from_options(
        {
            "hauzer_url": "https://hauzer.app/api/utility-imports",
            "hauzer_token": "hsr_" + "A" * 40,
            "verify_tls": True,
            "initial_backfill_hours": 24,
            "electricity_consumption_statistics": "",
            "electricity_grid_export_statistics": "",
            "gas_consumption_statistics": "",
            "water_consumption_statistics": "",
        },
        "supervisor-secret",
    )


class HauzerClientTest(unittest.TestCase):
    def test_success_statuses_return_import_counts(self) -> None:
        for status in (200, 201):
            with self.subTest(status=status):
                client = HauzerClient(
                    app_config(),
                    transport=lambda *args: (
                        status,
                        {"processed": 4, "created": 3, "updated": 1},
                        {},
                    ),
                )

                result = client.post_batch([{"metric": "electricity_consumption"}])

                self.assertEqual((result.processed, result.created, result.updated), (4, 3, 1))

    def test_queued_202_returns_accepted_reading_count_without_row_claims(self) -> None:
        batch = [
            {"metric": "electricity_consumption"},
            {"metric": "gas_consumption"},
        ]
        client = HauzerClient(
            app_config(),
            transport=lambda *args: (
                202,
                {"status": "queued", "readings": 2},
                {},
            ),
        )

        result = client.post_batch(batch)

        self.assertEqual(result.processed, 2)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 0)

    def test_malformed_or_mismatched_202_is_rejected(self) -> None:
        batch = [{"metric": "electricity_consumption"}, {"metric": "gas_consumption"}]
        cases = (
            ({"status": "accepted", "readings": 2}, "invalid"),
            ({"status": "queued"}, "invalid"),
            ({"status": "queued", "readings": "2"}, "invalid"),
            ({"status": "queued", "readings": 2.0}, "invalid"),
            ({"status": "queued", "readings": True}, "invalid"),
            ({"readings": 2}, "invalid"),
            ({}, "invalid"),
            (None, "invalid"),
            ({"status": "queued", "readings": 1}, "mismatched"),
            ({"status": "queued", "readings": 3}, "mismatched"),
        )

        for payload, expected_fragment in cases:
            with self.subTest(payload=payload):
                client = HauzerClient(
                    app_config(),
                    transport=lambda *args, body=payload: (202, body, {}),
                )

                with self.assertRaisesRegex(HauzerError, expected_fragment):
                    client.post_batch(batch)

    def test_unauthorized_token_is_not_retried(self) -> None:
        calls = []
        client = HauzerClient(
            app_config(),
            transport=lambda *args: (calls.append(args) or (401, {}, {})),
            sleeper=lambda delay: self.fail("401 must not sleep"),
        )

        with self.assertRaises(InvalidHauzerToken):
            client.post_batch([])

        self.assertEqual(len(calls), 1)

    def test_invalid_payload_error_does_not_include_payload_or_response(self) -> None:
        secret_payload = {"metadata": {"secret": "raw-private-payload"}}
        client = HauzerClient(
            app_config(),
            transport=lambda *args: (
                422,
                {"message": "raw-private-response"},
                {},
            ),
        )

        with self.assertRaisesRegex(InvalidImportPayload, "rejected") as raised:
            client.post_batch([secret_payload])

        self.assertNotIn("raw-private-payload", str(raised.exception))
        self.assertNotIn("raw-private-response", str(raised.exception))

    def test_retryable_statuses_use_bounded_delays(self) -> None:
        for status in (429, 500, 503):
            with self.subTest(status=status):
                calls = []
                delays = []
                client = HauzerClient(
                    app_config(),
                    transport=lambda *args: (calls.append(args) or (status, {}, {})),
                    sleeper=delays.append,
                )

                with self.assertRaises(RetryableHauzerError):
                    client.post_batch([])

                self.assertEqual(len(calls), 4)
                self.assertEqual(delays, [1, 5, 15])

    def test_network_failures_are_retryable(self) -> None:
        delays = []

        def failing_transport(*args: object) -> object:
            raise OSError("network unavailable")

        client = HauzerClient(
            app_config(),
            transport=failing_transport,
            sleeper=delays.append,
        )

        with self.assertRaises(RetryableHauzerError) as raised:
            client.post_batch([])

        self.assertEqual(delays, [1, 5, 15])
        self.assertNotIn("network unavailable", str(raised.exception))
        self.assertNotIn("hsr_", str(raised.exception))

    def test_requests_include_the_exporter_identity(self) -> None:
        requests = []
        client = HauzerClient(
            app_config(),
            exporter_version="0.1.0",
            transport=lambda *args: (requests.append(args) or (200, {}, {})),
        )

        client.post_batch([])

        self.assertEqual(requests[0][1]["X-Hauzer-Exporter-Version"], "0.1.0")
        self.assertEqual(requests[0][1]["User-Agent"], "Hauzer-Utility-Exporter/0.1.0")

    def test_rate_limit_retry_after_overrides_default_delays(self) -> None:
        delays = []
        client = HauzerClient(
            app_config(),
            transport=lambda *args: (429, {}, {"Retry-After": "30"}),
            sleeper=delays.append,
        )

        with self.assertRaises(RetryableHauzerError):
            client.post_batch([])

        self.assertEqual(delays, [30, 30, 30])

    def test_invalid_retry_after_uses_default_delays_without_exposing_header(self) -> None:
        for value in ("invalid-secret-value", "999999"):
            with self.subTest(value=value):
                delays = []
                client = HauzerClient(
                    app_config(),
                    transport=lambda *args: (429, {}, {"Retry-After": value}),
                    sleeper=delays.append,
                )

                with self.assertRaises(RetryableHauzerError) as raised:
                    client.post_batch([])

                self.assertEqual(delays, [1, 5, 15])
                self.assertNotIn(value, str(raised.exception))

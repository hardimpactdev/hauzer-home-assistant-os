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
            transport=lambda *args: calls.append(args) or (401, {}, {}),
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
                    transport=lambda *args: calls.append(args) or (status, {}, {}),
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
            exporter_version="0.2.0",
            transport=lambda *args: requests.append(args) or (200, {}, {}),
        )

        client.post_batch([])

        self.assertEqual(requests[0][1]["X-Hauzer-Exporter-Version"], "0.2.0")
        self.assertEqual(requests[0][1]["User-Agent"], "Hauzer-Utility-Exporter/0.2.0")

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


class RegistryDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            "source": "home_assistant",
            "generated_at": "2026-10-28T18:00:00+00:00",
            "floors": [],
            "areas": [],
            "devices": [],
        }
        self.acknowledgement = {"status": "queued", "floors": 0, "areas": 0, "devices": 0}

    def test_registry_uses_same_origin_token_tls_and_version_headers(self) -> None:
        import json

        requests = []
        client = HauzerClient(
            app_config(),
            exporter_version="0.2.0",
            transport=lambda *args: requests.append(args) or (202, self.acknowledgement, {}),
        )
        self.assertIsNone(client.post_registry(self.snapshot))
        url, headers, body, verify_tls, timeout = requests[0]
        self.assertEqual(url, "https://hauzer.app/api/home-assistant/registry")
        self.assertEqual(
            headers,
            {
                "Authorization": f"Bearer {app_config().hauzer_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Hauzer-Exporter-Version": "0.2.0",
                "User-Agent": "Hauzer-Utility-Exporter/0.2.0",
            },
        )
        self.assertEqual(json.loads(body), self.snapshot)
        self.assertTrue(verify_tls)
        self.assertEqual(timeout, 30)

    def test_development_origin_and_port_are_preserved(self) -> None:
        from dataclasses import replace

        requests = []
        client = HauzerClient(
            replace(app_config(), hauzer_url="http://hauzer.example:8123/api/utility-imports"),
            transport=lambda *args: requests.append(args) or (202, self.acknowledgement, {}),
        )
        client.post_registry(self.snapshot)
        self.assertEqual(requests[0][0], "http://hauzer.example:8123/api/home-assistant/registry")

    def test_server_and_network_failures_retry_with_bounded_backoff(self) -> None:
        for failure in (500, 503, OSError("secret-network-detail")):
            calls = []
            delays = []

            def transport(*args):
                calls.append(args)
                if isinstance(failure, Exception):
                    raise failure
                return failure, {}, {}

            client = HauzerClient(app_config(), transport=transport, sleeper=delays.append)
            with self.subTest(failure=failure), self.assertRaises(RetryableHauzerError) as raised:
                client.post_registry(self.snapshot)
            self.assertEqual(len(calls), 4)
            self.assertEqual(delays, [1, 5, 15])
            self.assertNotIn("secret-network-detail", str(raised.exception))

    def test_transient_failure_can_recover_with_identical_full_snapshot(self) -> None:
        requests = []
        outcomes = [(503, {}, {}), (202, self.acknowledgement, {})]
        delays = []
        client = HauzerClient(
            app_config(),
            transport=lambda *args: requests.append(args) or outcomes.pop(0),
            sleeper=delays.append,
        )
        client.post_registry(self.snapshot)
        self.assertEqual(delays, [1])
        self.assertEqual(requests[0][2], requests[1][2])

    def test_malformed_queued_registry_counts_are_rejected(self) -> None:
        for response in (
            {},
            {**self.acknowledgement, "floors": True},
            {**self.acknowledgement, "areas": 1},
            {**self.acknowledgement, "devices": "0"},
        ):
            client = HauzerClient(app_config(), transport=lambda *args: (202, response, {}))
            with self.subTest(response=response), self.assertRaises(HauzerError):
                client.post_registry(self.snapshot)

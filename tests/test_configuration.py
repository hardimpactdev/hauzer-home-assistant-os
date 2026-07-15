import unittest

from hauzer_utility_exporter.configuration import (
    AppConfig,
    ConfigurationError,
    Metric,
    parse_statistic_ids,
)


def valid_options(**overrides: object) -> dict[str, object]:
    options: dict[str, object] = {
        "hauzer_url": "https://hauzer.app/api/utility-imports",
        "hauzer_token": "hsr_" + "A" * 40,
        "hauzer_host_ip": "",
        "verify_tls": True,
        "initial_backfill_hours": 24,
        "electricity_consumption_statistics": "",
        "electricity_grid_export_statistics": "",
        "gas_consumption_statistics": "",
        "water_consumption_statistics": "",
    }
    options.update(overrides)
    return options


class ConfigurationTest(unittest.TestCase):
    def test_options_accept_partial_overrides_and_supervisor_token(self) -> None:
        config = AppConfig.from_options(
            valid_options(
                electricity_consumption_statistics="sensor.grid_a, sensor.grid_b",
            ),
            supervisor_token="supervisor-secret",
        )

        self.assertEqual(
            config.overrides[Metric.ELECTRICITY_CONSUMPTION],
            ("sensor.grid_a", "sensor.grid_b"),
        )
        self.assertEqual(config.overrides[Metric.GAS_CONSUMPTION], ())
        self.assertEqual(config.supervisor_token, "supervisor-secret")

    def test_private_host_override_is_optional_and_validated(self) -> None:
        private_host = ".".join(("192", "168", "1", "170"))
        config = AppConfig.from_options(
            valid_options(hauzer_host_ip=private_host),
            "supervisor-secret",
        )

        self.assertEqual(config.hauzer_host_ip, private_host)

        for invalid_ip in ("8.8.8.8", "127.0.0.1", "not-an-ip"):
            with self.subTest(invalid_ip=invalid_ip):
                with self.assertRaisesRegex(ConfigurationError, "private IP"):
                    AppConfig.from_options(
                        valid_options(hauzer_host_ip=invalid_ip),
                        "supervisor-secret",
                    )

    def test_invalid_token_error_never_contains_secret(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError,
            "valid Hauzer import token",
        ) as raised:
            AppConfig.from_options(
                valid_options(hauzer_token="private-value"),
                "supervisor-secret",
            )

        self.assertNotIn("private-value", str(raised.exception))
        self.assertNotIn("supervisor-secret", str(raised.exception))

    def test_supervisor_token_is_required_without_echoing_values(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "Supervisor token") as raised:
            AppConfig.from_options(valid_options(), "")

        self.assertNotIn("hsr_", str(raised.exception))

    def test_hauzer_url_must_use_http_or_https(self) -> None:
        for invalid_url in ("file:///data/options.json", "hauzer.invalid/import"):
            with self.subTest(invalid_url=invalid_url):
                with self.assertRaisesRegex(ConfigurationError, "Hauzer URL"):
                    AppConfig.from_options(
                        valid_options(hauzer_url=invalid_url),
                        "supervisor-secret",
                    )

    def test_backfill_must_be_between_one_and_720_hours(self) -> None:
        for invalid_hours in (0, 721, True, "24"):
            with self.subTest(invalid_hours=invalid_hours):
                with self.assertRaisesRegex(ConfigurationError, "backfill"):
                    AppConfig.from_options(
                        valid_options(initial_backfill_hours=invalid_hours),
                        "supervisor-secret",
                    )

    def test_verify_tls_must_be_boolean(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "TLS"):
            AppConfig.from_options(
                valid_options(verify_tls="true"),
                "supervisor-secret",
            )

    def test_statistic_ids_are_trimmed_deduplicated_and_ordered(self) -> None:
        self.assertEqual(
            parse_statistic_ids(" sensor.grid_a, sensor.grid_b,sensor.grid_a "),
            ("sensor.grid_a", "sensor.grid_b"),
        )

    def test_empty_statistic_id_entries_are_rejected(self) -> None:
        for invalid_value in ("sensor.grid_a,,sensor.grid_b", ",sensor.grid_a"):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(ConfigurationError, "statistic ID"):
                    parse_statistic_ids(invalid_value)

    def test_statistic_ids_allow_only_safe_characters(self) -> None:
        for invalid_value in ("sensor.grid/a", "sensor.grid import", "sensor.grid,$x"):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(ConfigurationError, "statistic ID"):
                    parse_statistic_ids(invalid_value)

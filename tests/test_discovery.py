import unittest

from hauzer_utility_exporter.configuration import AppConfig, Metric
from hauzer_utility_exporter.discovery import discover_utilities


def app_config(**option_overrides: object) -> AppConfig:
    options: dict[str, object] = {
        "hauzer_url": "https://hauzer.app/api/utility-imports",
        "hauzer_token": "hsr_" + "A" * 40,
        "verify_tls": True,
        "initial_backfill_hours": 24,
        "electricity_consumption_statistics": "",
        "electricity_grid_export_statistics": "",
        "gas_consumption_statistics": "",
        "water_consumption_statistics": "",
    }
    options.update(option_overrides)
    return AppConfig.from_options(options, "supervisor-secret")


def metadata(*rows: tuple[str, str, bool]) -> list[dict[str, object]]:
    return [
        {"statistic_id": statistic_id, "unit_of_measurement": unit, "has_sum": has_sum}
        for statistic_id, unit, has_sum in rows
    ]


class DiscoveryTest(unittest.TestCase):
    def test_energy_dashboard_is_authoritative_for_grid_gas_and_multiple_meters(self) -> None:
        preferences = {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": "sensor.grid_import_a",
                    "stat_energy_to": "sensor.grid_export_a",
                },
                {
                    "type": "grid",
                    "stat_energy_from": "sensor.grid_import_b",
                    "stat_energy_to": None,
                },
                {"type": "gas", "stat_energy_from": "sensor.gas_total"},
            ],
            "device_consumption": [],
            "device_consumption_water": [],
        }
        result = discover_utilities(
            app_config(),
            preferences,
            metadata(
                ("sensor.grid_import_a", "kWh", True),
                ("sensor.grid_export_a", "kWh", True),
                ("sensor.grid_import_b", "kWh", True),
                ("sensor.gas_total", "m³", True),
            ),
            [],
        )

        mappings = {(mapping.metric, mapping.statistic_id, mapping.origin) for mapping in result.mappings}
        self.assertEqual(
            mappings,
            {
                (Metric.ELECTRICITY_CONSUMPTION, "sensor.grid_import_a", "energy_dashboard"),
                (Metric.ELECTRICITY_CONSUMPTION, "sensor.grid_import_b", "energy_dashboard"),
                (Metric.ELECTRICITY_GRID_EXPORT, "sensor.grid_export_a", "energy_dashboard"),
                (Metric.GAS_CONSUMPTION, "sensor.gas_total", "energy_dashboard"),
            },
        )

    def test_water_is_discovered_from_a_cumulative_volume_sensor(self) -> None:
        result = discover_utilities(
            app_config(),
            {"energy_sources": []},
            metadata(("sensor.main_water_total", "L", True)),
            [
                {
                    "entity_id": "sensor.main_water_total",
                    "attributes": {
                        "device_class": "water",
                        "state_class": "total_increasing",
                        "unit_of_measurement": "L",
                    },
                }
            ],
        )

        self.assertEqual(len(result.mappings), 1)
        self.assertEqual(result.mappings[0].metric, Metric.WATER_CONSUMPTION)
        self.assertEqual(result.mappings[0].origin, "automatic")

    def test_p1_import_and_export_are_directionally_discovered(self) -> None:
        result = discover_utilities(
            app_config(),
            {"energy_sources": []},
            metadata(
                ("sensor.p1_meter_energy_import", "kWh", True),
                ("sensor.p1_meter_energy_export", "kWh", True),
            ),
            [
                {
                    "entity_id": "sensor.p1_meter_energy_import",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                },
                {
                    "entity_id": "sensor.p1_meter_energy_export",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                },
            ],
        )

        self.assertEqual(
            {(mapping.metric, mapping.statistic_id) for mapping in result.mappings},
            {
                (
                    Metric.ELECTRICITY_CONSUMPTION,
                    "sensor.p1_meter_energy_import",
                ),
                (
                    Metric.ELECTRICITY_GRID_EXPORT,
                    "sensor.p1_meter_energy_export",
                ),
            },
        )

    def test_p1_export_under_a_solar_energy_source_uses_safe_automatic_discovery(self) -> None:
        result = discover_utilities(
            app_config(),
            {
                "energy_sources": [
                    {
                        "type": "solar",
                        "stat_energy_from": "sensor.p1_meter_energy_export",
                    }
                ]
            },
            metadata(("sensor.p1_meter_energy_export", "kWh", True)),
            [
                {
                    "entity_id": "sensor.p1_meter_energy_export",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                }
            ],
        )

        self.assertEqual(
            [
                (mapping.metric, mapping.statistic_id, mapping.origin)
                for mapping in result.mappings
            ],
            [
                (
                    Metric.ELECTRICITY_GRID_EXPORT,
                    "sensor.p1_meter_energy_export",
                    "automatic",
                )
            ],
        )

    def test_generic_solar_production_is_not_grid_export(self) -> None:
        result = discover_utilities(
            app_config(),
            {"energy_sources": []},
            metadata(("sensor.solar_production", "kWh", True)),
            [
                {
                    "entity_id": "sensor.solar_production",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                }
            ],
        )

        self.assertNotIn(
            Metric.ELECTRICITY_GRID_EXPORT,
            {mapping.metric for mapping in result.mappings},
        )

    def test_neutral_grid_energy_is_not_guessed_as_both_directions(self) -> None:
        result = discover_utilities(
            app_config(),
            {"energy_sources": []},
            metadata(("sensor.grid_energy", "kWh", True)),
            [
                {
                    "entity_id": "sensor.grid_energy",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                }
            ],
        )

        self.assertFalse(
            {
                mapping.metric
                for mapping in result.mappings
                if mapping.metric
                in {
                    Metric.ELECTRICITY_CONSUMPTION,
                    Metric.ELECTRICITY_GRID_EXPORT,
                }
            }
        )

    def test_ambiguous_directional_exports_require_an_override(self) -> None:
        result = discover_utilities(
            app_config(),
            {"energy_sources": []},
            metadata(
                ("sensor.p1_meter_energy_export", "kWh", True),
                ("sensor.smart_meter_energy_export", "kWh", True),
            ),
            [
                {
                    "entity_id": "sensor.p1_meter_energy_export",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                },
                {
                    "entity_id": "sensor.smart_meter_energy_export",
                    "attributes": {
                        "device_class": "energy",
                        "state_class": "total_increasing",
                    },
                },
            ],
        )

        self.assertNotIn(
            Metric.ELECTRICITY_GRID_EXPORT,
            {mapping.metric for mapping in result.mappings},
        )
        self.assertEqual(
            result.ambiguous[Metric.ELECTRICITY_GRID_EXPORT],
            (
                "sensor.p1_meter_energy_export",
                "sensor.smart_meter_energy_export",
            ),
        )

    def test_instantaneous_and_non_utility_statistics_are_excluded(self) -> None:
        result = discover_utilities(
            app_config(),
            {"energy_sources": []},
            metadata(
                ("sensor.house_power", "W", False),
                ("sensor.solar_total", "kWh", True),
                ("sensor.battery_total", "kWh", True),
                ("sensor.energy_price", "EUR/kWh", True),
                ("sensor.dishwasher_energy", "kWh", True),
            ),
            [],
        )

        self.assertEqual(result.mappings, ())
        self.assertEqual(dict(result.ambiguous), {})

    def test_override_replaces_automatic_mapping_for_only_its_metric(self) -> None:
        result = discover_utilities(
            app_config(electricity_consumption_statistics="sensor.manual_grid"),
            {
                "energy_sources": [
                    {"type": "grid", "stat_energy_from": "sensor.auto_grid"},
                    {"type": "gas", "stat_energy_from": "sensor.gas_total"},
                ]
            },
            metadata(
                ("sensor.manual_grid", "kWh", True),
                ("sensor.auto_grid", "kWh", True),
                ("sensor.gas_total", "m³", True),
            ),
            [],
        )

        by_metric = {}
        for mapping in result.mappings:
            by_metric.setdefault(mapping.metric, []).append(mapping)

        self.assertEqual(
            [(item.statistic_id, item.origin) for item in by_metric[Metric.ELECTRICITY_CONSUMPTION]],
            [("sensor.manual_grid", "override")],
        )
        self.assertEqual(by_metric[Metric.GAS_CONSUMPTION][0].statistic_id, "sensor.gas_total")

    def test_ambiguous_water_does_not_block_unambiguous_electricity(self) -> None:
        result = discover_utilities(
            app_config(),
            {
                "energy_sources": [
                    {"type": "grid", "stat_energy_from": "sensor.grid_import"},
                ]
            },
            metadata(
                ("sensor.grid_import", "kWh", True),
                ("sensor.water_meter_one", "m³", True),
                ("sensor.water_meter_two", "m³", True),
            ),
            [],
        )

        self.assertEqual(result.mappings[0].metric, Metric.ELECTRICITY_CONSUMPTION)
        self.assertEqual(
            result.ambiguous[Metric.WATER_CONSUMPTION],
            ("sensor.water_meter_one", "sensor.water_meter_two"),
        )

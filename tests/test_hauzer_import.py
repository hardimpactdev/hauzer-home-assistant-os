from datetime import datetime, timezone
import unittest

from hauzer_utility_exporter.configuration import Metric
from hauzer_utility_exporter.discovery import UtilityMapping
from hauzer_utility_exporter.readings import (
    build_readings,
    chunked,
    floor_to_five_minutes,
)


class HauzerImportTest(unittest.TestCase):
    def test_build_readings_derives_five_minute_intervals_and_deltas(self) -> None:
        mapping = UtilityMapping(
            metric=Metric.ELECTRICITY_CONSUMPTION,
            statistic_id="sensor.p1_meter_energy_import",
            unit="kWh",
            origin="energy_dashboard",
        )

        readings = build_readings(
            mapping,
            rows=[
                {"start": "2026-06-27T17:55:00+00:00", "sum": 10.0, "state": 100.0},
                {"start": "2026-06-27T18:00:00+00:00", "sum": 10.125, "state": 100.125},
                {"start": "2026-06-27T18:05:00+00:00", "sum": 10.5, "state": 100.5},
            ],
            start=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 27, 18, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(len(readings), 2)
        self.assertEqual(readings[0]["metric"], "electricity_consumption")
        self.assertEqual(readings[0]["interval_start"], "2026-06-27T18:00:00+00:00")
        self.assertEqual(readings[0]["interval_end"], "2026-06-27T18:05:00+00:00")
        self.assertEqual(readings[0]["value"], "100.125000")
        self.assertEqual(readings[0]["delta"], "0.125000")
        self.assertEqual(readings[1]["delta"], "0.375000")

    def test_build_readings_accepts_home_assistant_epoch_milliseconds(self) -> None:
        interval_start = datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc)
        readings = build_readings(
            UtilityMapping(
                Metric.ELECTRICITY_CONSUMPTION,
                "sensor.p1_meter_energy_import",
                "kWh",
                "energy_dashboard",
            ),
            rows=[
                {"start": interval_start.timestamp() * 1000, "sum": 10.125, "state": 100.125},
            ],
            start=interval_start,
            end=datetime(2026, 6, 27, 18, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]["interval_start"], "2026-06-27T18:00:00+00:00")
        self.assertEqual(readings[0]["metadata"]["ha_start"], interval_start.timestamp() * 1000)

    def test_build_readings_skips_negative_deltas_after_meter_reset(self) -> None:
        mapping = UtilityMapping(
            metric=Metric.WATER_CONSUMPTION,
            statistic_id="sensor.watermeter_total_water_usage",
            unit="m3",
            origin="automatic",
        )

        readings = build_readings(
            mapping,
            rows=[
                {"start": "2026-06-27T18:00:00+00:00", "sum": 10.0, "state": 100.0},
                {"start": "2026-06-27T18:05:00+00:00", "sum": 9.0, "state": 101.0},
            ],
            start=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 27, 18, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(readings[0]["delta"], "0.000000")
        self.assertEqual(readings[1]["delta"], "0.000000")

    def test_build_readings_normalizes_wh_and_liters(self) -> None:
        cases = (
            (Metric.ELECTRICITY_CONSUMPTION, "Wh", "kWh", "1.250000"),
            (Metric.WATER_CONSUMPTION, "L", "m3", "1.250000"),
        )

        for metric, source_unit, expected_unit, expected_delta in cases:
            with self.subTest(source_unit=source_unit):
                readings = build_readings(
                    UtilityMapping(metric, "sensor.total", source_unit, "automatic"),
                    rows=[
                        {"start": "2026-06-27T17:55:00+00:00", "sum": 1000, "state": 1000},
                        {"start": "2026-06-27T18:00:00+00:00", "sum": 2250, "state": 2250},
                    ],
                    start=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc),
                    end=datetime(2026, 6, 27, 18, 5, tzinfo=timezone.utc),
                )

                self.assertEqual(readings[0]["unit"], expected_unit)
                self.assertEqual(readings[0]["delta"], expected_delta)
                self.assertEqual(readings[0]["metadata"]["ha_unit"], source_unit)
                self.assertEqual(readings[0]["metadata"]["ha_sum"], "2250.000000")

    def test_incomplete_intervals_are_excluded(self) -> None:
        mapping = UtilityMapping(
            Metric.GAS_CONSUMPTION,
            "sensor.gas_total",
            "m3",
            "energy_dashboard",
        )
        readings = build_readings(
            mapping,
            rows=[
                {"start": "2026-06-27T18:00:00+00:00", "sum": 10},
                {"start": "2026-06-27T18:05:00+00:00", "sum": 11},
            ],
            start=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 27, 18, 7, tzinfo=timezone.utc),
        )

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]["interval_end"], "2026-06-27T18:05:00+00:00")

    def test_readings_are_sorted_by_start(self) -> None:
        mapping = UtilityMapping(Metric.GAS_CONSUMPTION, "sensor.gas", "m3", "automatic")
        readings = build_readings(
            mapping,
            rows=[
                {"start": "2026-06-27T18:05:00+00:00", "sum": "1.1234567"},
                {"start": "2026-06-27T18:00:00+00:00", "sum": "1.0000001"},
            ],
            start=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 27, 18, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(readings[0]["value"], "1.000000")
        self.assertEqual(readings[1]["delta"], "0.123457")

    def test_chunked_splits_readings_into_batches(self) -> None:
        self.assertEqual(
            list(chunked([{"id": 1}, {"id": 2}, {"id": 3}], 2)),
            [[{"id": 1}, {"id": 2}], [{"id": 3}]],
        )

    def test_floor_to_five_minutes(self) -> None:
        self.assertEqual(
            floor_to_five_minutes(datetime(2026, 6, 27, 18, 7, 59, tzinfo=timezone.utc)),
            datetime(2026, 6, 27, 18, 5, tzinfo=timezone.utc),
        )

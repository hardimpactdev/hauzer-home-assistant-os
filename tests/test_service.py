from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
import unittest

from hauzer_utility_exporter.configuration import AppConfig, Metric
from hauzer_utility_exporter.discovery import DiscoveryResult, UtilityMapping
from hauzer_utility_exporter.hauzer import ImportResult, RetryableHauzerError
from hauzer_utility_exporter.service import ImportService


NOW = datetime(2026, 7, 13, 12, 7, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc)


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


def mapping(metric: Metric = Metric.ELECTRICITY_CONSUMPTION) -> UtilityMapping:
    unit = "m3" if metric in {Metric.GAS_CONSUMPTION, Metric.WATER_CONSUMPTION} else "kWh"
    return UtilityMapping(metric, f"sensor.{metric.value}", unit, "energy_dashboard")


def rows(count: int, start: datetime = WINDOW_START - timedelta(minutes=5)) -> list[dict[str, object]]:
    return [
        {
            "start": (start + timedelta(minutes=5 * index)).isoformat(),
            "sum": index,
            "state": index,
        }
        for index in range(count)
    ]


class FakeHomeAssistant:
    def __init__(self, statistics: dict[str, list[dict[str, object]]]) -> None:
        self.statistics = statistics
        self.period_calls: list[tuple[tuple[str, ...], datetime, datetime]] = []

    def energy_preferences(self) -> dict[str, object]:
        return {}

    def statistics_metadata(self) -> list[dict[str, object]]:
        return []

    def states(self) -> list[dict[str, object]]:
        return []

    def statistics_during_period(
        self,
        statistic_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict[str, object]]]:
        self.period_calls.append((statistic_ids, start, end))
        return self.statistics


class FakeHauzer:
    def __init__(self, outcomes: Optional[list[object]] = None) -> None:
        self.outcomes = outcomes or []
        self.batches: list[list[dict[str, object]]] = []

    def post_batch(self, readings: list[dict[str, object]]) -> ImportResult:
        self.batches.append(readings)
        outcome = self.outcomes.pop(0) if self.outcomes else ImportResult(len(readings), len(readings), 0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def write_state(path: Path, window_end: datetime = WINDOW_START) -> None:
    path.write_text(
        json.dumps({"window_end": window_end.isoformat(), "last_success_at": None}),
        encoding="utf-8",
    )


class ImportServiceTest(unittest.TestCase):
    def test_no_mappings_is_a_successful_noop_without_cursor_advancement(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            service = ImportService(
                app_config(),
                FakeHomeAssistant({}),
                FakeHauzer(),
                state_path,
                discover=lambda *args: DiscoveryResult((), {}),
            )

            result = service.run_cycle(NOW)

            self.assertTrue(result.success)
            self.assertEqual(result.mapping_count, 0)
            self.assertFalse(state_path.exists())

    def test_partial_discovery_imports_available_mappings(self) -> None:
        electricity = mapping()
        gas = mapping(Metric.GAS_CONSUMPTION)
        home_assistant = FakeHomeAssistant(
            {
                electricity.statistic_id: rows(3),
                gas.statistic_id: rows(3),
            }
        )
        hauzer = FakeHauzer()

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            write_state(state_path)
            service = ImportService(
                app_config(),
                home_assistant,
                hauzer,
                state_path,
                discover=lambda *args: DiscoveryResult((electricity,), {Metric.GAS_CONSUMPTION: (gas.statistic_id,)}),
            )

            result = service.run_cycle(NOW)

        self.assertTrue(result.success)
        self.assertEqual(result.mapping_count, 1)
        self.assertTrue(hauzer.batches)
        self.assertEqual({item["metric"] for item in hauzer.batches[0]}, {"electricity_consumption"})

    def test_failed_second_batch_leaves_cursor_unchanged_and_repeats_window(self) -> None:
        electricity = mapping()
        window_start = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        home_assistant = FakeHomeAssistant({electricity.statistic_id: rows(252, window_start - timedelta(minutes=5))})
        failure = RetryableHauzerError("Hauzer is temporarily unavailable.")
        hauzer = FakeHauzer([ImportResult(250, 250, 0), failure, failure])

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            write_state(state_path, window_start)
            service = ImportService(
                app_config(),
                home_assistant,
                hauzer,
                state_path,
                discover=lambda *args: DiscoveryResult((electricity,), {}),
            )

            with self.assertRaises(RetryableHauzerError):
                service.run_cycle(NOW)
            self.assertEqual(json.loads(state_path.read_text())["window_end"], window_start.isoformat())

            with self.assertRaises(RetryableHauzerError):
                service.run_cycle(NOW)

        self.assertEqual(home_assistant.period_calls[0][1], home_assistant.period_calls[1][1])

    def test_full_success_advances_cursor_once(self) -> None:
        electricity = mapping()
        save_calls = []
        home_assistant = FakeHomeAssistant({electricity.statistic_id: rows(3)})

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            write_state(state_path)
            service = ImportService(
                app_config(),
                home_assistant,
                FakeHauzer(),
                state_path,
                discover=lambda *args: DiscoveryResult((electricity,), {}),
                state_saver=lambda state, path: (save_calls.append(state), state.save_atomic(path)),
            )

            result = service.run_cycle(NOW)
            stored = json.loads(state_path.read_text())

        self.assertTrue(result.success)
        self.assertEqual(len(save_calls), 1)
        self.assertEqual(stored["window_end"], "2026-07-13T12:05:00+00:00")
        self.assertEqual(stored["backfill_hours"], 24)

    def test_empty_statistics_leave_cursor_unchanged_for_retry(self) -> None:
        electricity = mapping()

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            write_state(state_path)
            service = ImportService(
                app_config(),
                FakeHomeAssistant({electricity.statistic_id: []}),
                FakeHauzer(),
                state_path,
                discover=lambda *args: DiscoveryResult((electricity,), {}),
            )

            result = service.run_cycle(NOW)
            stored = json.loads(state_path.read_text())

        self.assertTrue(result.success)
        self.assertEqual(result.reading_count, 0)
        self.assertEqual(stored["window_end"], WINDOW_START.isoformat())

    def test_logs_do_not_contain_either_secret(self) -> None:
        logger = logging.getLogger("hauzer-exporter-secret-test")
        logger.setLevel(logging.INFO)
        with TemporaryDirectory() as directory:
            service = ImportService(
                app_config(),
                FakeHomeAssistant({}),
                FakeHauzer(),
                Path(directory) / "state.json",
                discover=lambda *args: DiscoveryResult((), {}),
                logger=logger,
            )

            with self.assertLogs(logger, level="INFO") as captured:
                service.run_cycle(NOW)

        output = " ".join(captured.output)
        self.assertNotIn("hsr_", output)
        self.assertNotIn("supervisor-secret", output)

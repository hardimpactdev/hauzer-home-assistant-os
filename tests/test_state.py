from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hauzer_utility_exporter.state import ImportState, StateError


class ImportStateTest(unittest.TestCase):
    def test_missing_state_starts_at_the_initial_backfill_boundary(self) -> None:
        now = datetime(2026, 7, 13, 12, 3, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            state = ImportState.load(Path(directory) / "state.json", now, 24)

        self.assertEqual(
            state.window_end,
            datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(state.last_success_at)
        self.assertEqual(state.mapping_keys, ())

    def test_legacy_cursor_rewinds_to_an_increased_backfill_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "window_end": "2026-07-13T12:00:00+00:00",
                        "last_success_at": "2026-07-13T12:01:12+00:00",
                    }
                )
            )

            state = ImportState.load(
                path,
                datetime(2026, 7, 13, 14, 3, tzinfo=timezone.utc),
                168,
            )

        self.assertEqual(
            state.window_end,
            datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(state.backfill_hours, 168)
        self.assertEqual(state.mapping_keys, ())

    def test_mapping_keys_load_as_a_sorted_unique_tuple(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "window_end": "2026-07-13T12:00:00+00:00",
                        "last_success_at": None,
                        "backfill_hours": 24,
                        "mapping_keys": [
                            "electricity_grid_export:sensor.p1_meter_energy_export",
                            "electricity_consumption:sensor.p1_meter_energy_import",
                            "electricity_grid_export:sensor.p1_meter_energy_export",
                        ],
                    }
                )
            )

            state = ImportState.load(
                path,
                datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc),
                24,
            )

        self.assertEqual(
            state.mapping_keys,
            (
                "electricity_consumption:sensor.p1_meter_energy_import",
                "electricity_grid_export:sensor.p1_meter_energy_export",
            ),
        )

    def test_applied_backfill_window_does_not_rewind_again(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "window_end": "2026-07-13T12:00:00+00:00",
                        "last_success_at": "2026-07-13T12:01:12+00:00",
                        "backfill_hours": 168,
                    }
                )
            )

            state = ImportState.load(
                path,
                datetime(2026, 7, 13, 14, 3, tzinfo=timezone.utc),
                168,
            )

        self.assertEqual(
            state.window_end,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(state.backfill_hours, 168)

    def test_valid_cursor_resumes_exactly_at_its_window_end(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "window_end": "2026-07-13T12:00:00+00:00",
                        "last_success_at": "2026-07-13T12:01:12+00:00",
                    }
                )
            )

            state = ImportState.load(
                path,
                datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc),
                24,
            )

        self.assertEqual(
            state.window_end,
            datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        )

    def test_malformed_state_fails_safely(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"window_end":"not-a-date","token":"private"}')

            with self.assertRaisesRegex(StateError, "cursor state") as raised:
                ImportState.load(
                    path,
                    datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc),
                    24,
                )

        self.assertNotIn("private", str(raised.exception))

    def test_malformed_mapping_keys_fail_safely(self) -> None:
        malformed_mapping_keys = (
            "electricity_consumption:sensor.p1_meter_energy_import",
            [""],
            [123],
        )

        for mapping_keys in malformed_mapping_keys:
            with self.subTest(mapping_keys=mapping_keys):
                with TemporaryDirectory() as directory:
                    path = Path(directory) / "state.json"
                    path.write_text(
                        json.dumps(
                            {
                                "window_end": "2026-07-13T12:00:00+00:00",
                                "last_success_at": None,
                                "backfill_hours": 24,
                                "mapping_keys": mapping_keys,
                                "token": "private",
                            }
                        )
                    )

                    with self.assertRaisesRegex(StateError, "cursor state") as raised:
                        ImportState.load(
                            path,
                            datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc),
                            24,
                        )

                self.assertNotIn("private", str(raised.exception))

    def test_save_atomic_writes_valid_json_and_removes_temporary_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = ImportState(
                window_end=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
                last_success_at=datetime(2026, 7, 13, 12, 1, 12, tzinfo=timezone.utc),
                backfill_hours=168,
                mapping_keys=(
                    "electricity_consumption:sensor.p1_meter_energy_import",
                    "electricity_grid_export:sensor.p1_meter_energy_export",
                ),
            )

            state.save_atomic(path)

            self.assertEqual(
                json.loads(path.read_text()),
                {
                    "window_end": "2026-07-13T12:00:00+00:00",
                    "last_success_at": "2026-07-13T12:01:12+00:00",
                    "backfill_hours": 168,
                    "mapping_keys": [
                        "electricity_consumption:sensor.p1_meter_energy_import",
                        "electricity_grid_export:sensor.p1_meter_energy_export",
                    ],
                },
            )
            self.assertFalse(path.with_name("state.json.tmp").exists())

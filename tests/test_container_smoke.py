import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


class ContainerSmokeTest(unittest.TestCase):
    def test_one_shot_fixture_cycle_writes_cursor(self) -> None:
        image = os.environ.get("HAUZER_TEST_IMAGE")
        if not image:
            self.skipTest("Set HAUZER_TEST_IMAGE after building the app image.")

        with TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            fixture_directory = Path(directory) / "fixture"
            data.mkdir()
            fixture_directory.mkdir()
            (data / "options.json").write_text(
                json.dumps(
                    {
                        "hauzer_url": "https://hauzer.invalid/api/utility-imports",
                        "hauzer_token": "hsr_" + "A" * 40,
                        "verify_tls": True,
                        "initial_backfill_hours": 1,
                        "electricity_consumption_statistics": "",
                        "electricity_grid_export_statistics": "",
                        "gas_consumption_statistics": "",
                        "water_consumption_statistics": "",
                    }
                ),
                encoding="utf-8",
            )
            (fixture_directory / "fixture.json").write_text(
                json.dumps(
                    {
                        "now": "2026-07-13T12:07:00+00:00",
                        "energy_preferences": {
                            "energy_sources": [
                                {
                                    "type": "grid",
                                    "stat_energy_from": "sensor.grid_import",
                                }
                            ]
                        },
                        "metadata": [
                            {
                                "statistic_id": "sensor.grid_import",
                                "unit_of_measurement": "kWh",
                                "has_sum": True,
                            }
                        ],
                        "states": [],
                        "statistics": {
                            "sensor.grid_import": [
                                {"start": "2026-07-13T11:00:00+00:00", "sum": 10.0},
                                {"start": "2026-07-13T11:05:00+00:00", "sum": 10.2},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/usr/bin/python3",
                    "-e",
                    "SUPERVISOR_TOKEN=smoke-supervisor-token",
                    "-e",
                    "HAUZER_RUN_ONCE=1",
                    "-e",
                    "HAUZER_SMOKE_FIXTURE=/test/fixture.json",
                    "-e",
                    "PYTHONPATH=/usr/lib",
                    "-v",
                    f"{data}:/data",
                    "-v",
                    f"{fixture_directory}:/test:ro",
                    image,
                    "-m",
                    "hauzer_utility_exporter",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((data / "state.json").exists())
            self.assertTrue((data / "smoke-outbox.json").exists())

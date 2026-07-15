from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from hauzer_utility_exporter import __main__ as exporter_main


class MainTest(unittest.TestCase):
    def test_import_cycle_runs_every_five_minutes(self) -> None:
        self.assertEqual(exporter_main.RUN_INTERVAL_SECONDS, 5 * 60)

    def test_main_passes_the_runtime_version_to_the_hauzer_client(self) -> None:
        with TemporaryDirectory() as directory:
            options_path = Path(directory) / "options.json"
            options_path.write_text(
                json.dumps(
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
                ),
                encoding="utf-8",
            )
            hauzer_client = Mock()
            service = Mock()

            with ExitStack() as stack:
                stack.enter_context(patch.object(exporter_main, "OPTIONS_PATH", options_path))
                stack.enter_context(
                    patch.object(exporter_main, "STATE_PATH", Path(directory) / "state.json"),
                )
                stack.enter_context(patch.object(exporter_main, "install_host_override"))
                stack.enter_context(patch.object(exporter_main, "HomeAssistantClient"))
                stack.enter_context(patch.object(exporter_main, "HauzerClient", hauzer_client))
                stack.enter_context(
                    patch.object(exporter_main, "ImportService", return_value=service),
                )
                stack.enter_context(patch.object(exporter_main.signal, "signal"))
                stack.enter_context(
                    patch.dict(
                        exporter_main.os.environ,
                        {
                            "SUPERVISOR_TOKEN": "supervisor-secret",
                            "HAUZER_EXPORTER_VERSION": "0.1.0",
                            "HAUZER_RUN_ONCE": "1",
                        },
                        clear=True,
                    ),
                )
                result = exporter_main.main()

            self.assertEqual(result, 0)
            self.assertEqual(hauzer_client.call_args.kwargs["exporter_version"], "0.1.0")
            service.run_cycle.assert_called_once()

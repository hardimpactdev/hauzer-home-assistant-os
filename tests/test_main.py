from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from hauzer_utility_exporter import __main__ as exporter_main
from hauzer_utility_exporter.hauzer import HauzerError


class MainTest(unittest.TestCase):
    def run_main(self, registry_sync=True, registry_start_error=None, import_error=None):
        with TemporaryDirectory() as directory:
            options_path = Path(directory) / "options.json"
            options_path.write_text(
                json.dumps(
                    {
                        "hauzer_url": "https://hauzer.app/api/utility-imports",
                        "hauzer_token": "hsr_" + "A" * 40,
                        "verify_tls": True,
                        "initial_backfill_hours": 24,
                        "registry_sync": registry_sync,
                    }
                ),
                encoding="utf-8",
            )
            hauzer_client = Mock()
            service = Mock()
            service.run_cycle.side_effect = import_error
            registry_worker = Mock()
            registry_worker.return_value.start.side_effect = registry_start_error
            if registry_start_error:
                registry_worker.return_value.is_alive.return_value = False

            with ExitStack() as stack:
                stack.enter_context(patch.object(exporter_main, "OPTIONS_PATH", options_path))
                stack.enter_context(
                    patch.object(exporter_main, "STATE_PATH", Path(directory) / "state.json"),
                )
                stack.enter_context(patch.object(exporter_main, "install_host_override"))
                stack.enter_context(patch.object(exporter_main, "HomeAssistantClient"))
                stack.enter_context(patch.object(exporter_main, "RegistrySync", registry_worker))
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
                            "HAUZER_EXPORTER_VERSION": "0.2.0",
                            "HAUZER_RUN_ONCE": "1",
                        },
                        clear=True,
                    )
                )
                result = exporter_main.main()

            self.assertEqual(result, 0)
            return hauzer_client, service, registry_worker

    def test_import_cycle_runs_every_five_minutes(self) -> None:
        self.assertEqual(exporter_main.RUN_INTERVAL_SECONDS, 5 * 60)

    def test_main_passes_runtime_version_and_starts_and_stops_registry_worker(self) -> None:
        hauzer_client, service, registry_worker = self.run_main()
        self.assertEqual(hauzer_client.call_args.kwargs["exporter_version"], "0.2.0")
        service.run_cycle.assert_called_once()
        registry_worker.return_value.start.assert_called_once()
        registry_worker.return_value.join.assert_called_once_with(timeout=2)
        self.assertTrue(registry_worker.call_args.args[2].is_set())
        self.assertIs(
            hauzer_client.call_args.kwargs["stop_event"], registry_worker.call_args.args[2]
        )

    def test_registry_opt_out_does_not_start_worker_or_stop_imports(self) -> None:
        _, service, registry_worker = self.run_main(registry_sync=False)
        service.run_cycle.assert_called_once()
        registry_worker.assert_not_called()

    def test_registry_startup_failure_does_not_stop_utility_imports(self) -> None:
        with self.assertLogs("hauzer_utility_exporter", level="ERROR") as captured:
            _, service, _ = self.run_main(registry_start_error=RuntimeError("supervisor-secret"))
        service.run_cycle.assert_called_once()
        self.assertNotIn("supervisor-secret", " ".join(captured.output))

    def test_utility_failure_does_not_stop_registry_worker(self) -> None:
        with self.assertLogs("hauzer_utility_exporter", level="ERROR") as captured:
            _, service, registry_worker = self.run_main(
                import_error=RuntimeError("supervisor-secret")
            )
        service.run_cycle.assert_called_once()
        registry_worker.return_value.start.assert_called_once()
        self.assertNotIn("supervisor-secret", " ".join(captured.output))

    def test_stopped_utility_retry_does_not_log_an_error_during_shutdown(self) -> None:
        stop_event = exporter_main.Event()

        def stopped_retry(*args):
            stop_event.set()
            raise HauzerError("Hauzer delivery stopped.")

        with (
            patch.object(exporter_main, "Event", return_value=stop_event),
            self.assertNoLogs("hauzer_utility_exporter", level="ERROR"),
        ):
            _, service, _ = self.run_main(import_error=stopped_retry)
        service.run_cycle.assert_called_once()

    def test_utility_error_is_still_logged_while_running(self) -> None:
        with self.assertLogs("hauzer_utility_exporter", level="ERROR") as captured:
            self.run_main(import_error=HauzerError("Hauzer delivery stopped."))
        self.assertIn("Utility import cycle failed", " ".join(captured.output))

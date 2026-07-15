from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
from threading import Event

from hauzer_utility_exporter.configuration import AppConfig, ConfigurationError
from hauzer_utility_exporter.hauzer import HauzerClient, HauzerError
from hauzer_utility_exporter.home_assistant import HomeAssistantClient, HomeAssistantError
from hauzer_utility_exporter.networking import install_host_override
from hauzer_utility_exporter.service import ImportService
from hauzer_utility_exporter.state import StateError


OPTIONS_PATH = Path(os.environ.get("HAUZER_OPTIONS_PATH", "/data/options.json"))
STATE_PATH = Path(os.environ.get("HAUZER_STATE_PATH", "/data/state.json"))
RUN_INTERVAL_SECONDS = 5 * 60


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = logging.getLogger("hauzer_utility_exporter")

    try:
        options = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
        if not isinstance(options, dict):
            raise ConfigurationError("The app options must be an object.")
        config = AppConfig.from_options(
            options,
            os.environ.get("SUPERVISOR_TOKEN", ""),
        )
        install_host_override(config)
    except (OSError, json.JSONDecodeError, ConfigurationError) as error:
        logger.error("Exporter startup configuration is invalid: %s", error)
        return 1

    stop_event = Event()

    def request_stop(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    smoke_fixture_path = os.environ.get("HAUZER_SMOKE_FIXTURE")
    if smoke_fixture_path:
        from hauzer_utility_exporter.smoke import (
            FixtureHauzer,
            FixtureHomeAssistant,
            load_fixture,
        )

        fixture, fixture_now = load_fixture(Path(smoke_fixture_path))
        home_assistant = FixtureHomeAssistant(fixture)
        hauzer = FixtureHauzer(STATE_PATH.with_name("smoke-outbox.json"))
    else:
        fixture_now = None
        home_assistant = HomeAssistantClient(config.supervisor_token)
        hauzer = HauzerClient(
            config,
            exporter_version=os.environ.get("HAUZER_EXPORTER_VERSION", "development"),
        )

    service = ImportService(config, home_assistant, hauzer, STATE_PATH, logger=logger)
    run_once = os.environ.get("HAUZER_RUN_ONCE") == "1"

    while not stop_event.is_set():
        try:
            service.run_cycle(fixture_now or datetime.now(timezone.utc))
        except (HauzerError, HomeAssistantError, StateError) as error:
            logger.error("Utility import cycle failed: %s", error)
        except Exception:
            logger.error("Utility import cycle failed unexpectedly.")

        if run_once:
            break
        stop_event.wait(RUN_INTERVAL_SECONDS)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

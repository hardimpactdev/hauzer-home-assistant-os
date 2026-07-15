from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Callable, Optional

from hauzer_utility_exporter.configuration import AppConfig
from hauzer_utility_exporter.discovery import DiscoveryResult, discover_utilities
from hauzer_utility_exporter.hauzer import HauzerClient
from hauzer_utility_exporter.home_assistant import HomeAssistantClient
from hauzer_utility_exporter.readings import build_readings, chunked, floor_to_five_minutes
from hauzer_utility_exporter.state import ImportState


BATCH_SIZE = 250


@dataclass(frozen=True)
class CycleResult:
    success: bool
    mapping_count: int
    reading_count: int
    processed: int
    created: int
    updated: int


def _save_state(state: ImportState, path: Path) -> None:
    state.save_atomic(path)


class ImportService:
    def __init__(
        self,
        config: AppConfig,
        home_assistant: HomeAssistantClient,
        hauzer: HauzerClient,
        state_path: Path,
        discover: Callable[..., DiscoveryResult] = discover_utilities,
        state_saver: Callable[[ImportState, Path], None] = _save_state,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._config = config
        self._home_assistant = home_assistant
        self._hauzer = hauzer
        self._state_path = state_path
        self._discover = discover
        self._state_saver = state_saver
        self._logger = logger or logging.getLogger("hauzer_utility_exporter")

    def run_cycle(self, now: datetime) -> CycleResult:
        window_end = floor_to_five_minutes(now)
        state = ImportState.load(
            self._state_path,
            now,
            self._config.initial_backfill_hours,
        )
        discovery = self._discover(
            self._config,
            self._home_assistant.energy_preferences(),
            self._home_assistant.statistics_metadata(),
            self._home_assistant.states(),
        )

        if not discovery.mappings:
            self._logger.info("No utility statistic mappings are currently available.")
            return CycleResult(True, 0, 0, 0, 0, 0)

        self._logger.info(
            "Discovered utility mappings: %s",
            ", ".join(
                f"{mapping.metric.value}={mapping.statistic_id} ({mapping.origin})"
                for mapping in discovery.mappings
            ),
        )

        if window_end <= state.window_end:
            self._logger.info("No completed utility interval is ready.")
            return CycleResult(True, len(discovery.mappings), 0, 0, 0, 0)

        statistic_ids = tuple(mapping.statistic_id for mapping in discovery.mappings)
        statistics = self._home_assistant.statistics_during_period(
            statistic_ids,
            state.window_end - timedelta(minutes=5),
            window_end,
        )
        readings: list[dict[str, object]] = []
        for mapping in discovery.mappings:
            readings.extend(
                build_readings(
                    mapping,
                    statistics.get(mapping.statistic_id, []),
                    state.window_end,
                    window_end,
                )
            )

        readings.sort(
            key=lambda item: (
                str(item.get("interval_start", "")),
                str(item.get("metric", "")),
                str(item.get("statistic_id", "")),
            )
        )

        if not readings:
            self._logger.warning(
                "No recorder statistics returned for discovered mappings; cursor remains at %s.",
                state.window_end.isoformat(),
            )
            return CycleResult(True, len(discovery.mappings), 0, 0, 0, 0)

        processed = 0
        created = 0
        updated = 0
        for batch in chunked(readings, BATCH_SIZE):
            result = self._hauzer.post_batch(batch)
            processed += result.processed
            created += result.created
            updated += result.updated

        completed_state = ImportState(
            window_end=window_end,
            last_success_at=now.astimezone(timezone.utc),
            backfill_hours=state.backfill_hours,
        )
        self._state_saver(completed_state, self._state_path)
        self._logger.info(
            "Utility import cycle completed: mappings=%d readings=%d processed=%d created=%d updated=%d",
            len(discovery.mappings),
            len(readings),
            processed,
            created,
            updated,
        )

        return CycleResult(
            True,
            len(discovery.mappings),
            len(readings),
            processed,
            created,
            updated,
        )

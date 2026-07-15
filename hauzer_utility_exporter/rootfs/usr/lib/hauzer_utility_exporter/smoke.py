from datetime import datetime
import json
from pathlib import Path

from hauzer_utility_exporter.hauzer import ImportResult
from hauzer_utility_exporter.readings import parse_datetime


class FixtureHomeAssistant:
    def __init__(self, fixture: dict[str, object]) -> None:
        self._fixture = fixture

    def energy_preferences(self) -> dict[str, object]:
        return self._dictionary("energy_preferences")

    def statistics_metadata(self) -> list[dict[str, object]]:
        return self._list("metadata")

    def states(self) -> list[dict[str, object]]:
        return self._list("states")

    def statistics_during_period(
        self,
        statistic_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict[str, object]]]:
        statistics = self._dictionary("statistics")
        return {
            statistic_id: rows
            for statistic_id, rows in statistics.items()
            if statistic_id in statistic_ids and isinstance(rows, list)
        }

    def _dictionary(self, key: str) -> dict[str, object]:
        value = self._fixture.get(key, {})
        return value if isinstance(value, dict) else {}

    def _list(self, key: str) -> list[dict[str, object]]:
        value = self._fixture.get(key, [])
        return value if isinstance(value, list) else []


class FixtureHauzer:
    def __init__(self, outbox_path: Path) -> None:
        self._outbox_path = outbox_path

    def post_batch(self, readings: list[dict[str, object]]) -> ImportResult:
        existing: list[object] = []
        if self._outbox_path.exists():
            loaded = json.loads(self._outbox_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        existing.extend(readings)
        self._outbox_path.write_text(json.dumps(existing), encoding="utf-8")
        return ImportResult(len(readings), len(readings), 0)


def load_fixture(path: Path) -> tuple[dict[str, object], datetime]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict) or not isinstance(fixture.get("now"), str):
        raise ValueError("Invalid smoke fixture.")
    return fixture, parse_datetime(fixture["now"])

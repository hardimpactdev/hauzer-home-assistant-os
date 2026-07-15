from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Optional

from hauzer_utility_exporter.readings import floor_to_five_minutes


class StateError(RuntimeError):
    """Raised when durable import cursor state is invalid."""


@dataclass(frozen=True)
class ImportState:
    window_end: datetime
    last_success_at: Optional[datetime]
    backfill_hours: int

    @classmethod
    def load(
        cls,
        path: Path,
        now: datetime,
        initial_backfill_hours: int,
    ) -> "ImportState":
        if not path.exists():
            return cls(
                window_end=floor_to_five_minutes(now) - timedelta(hours=initial_backfill_hours),
                last_success_at=None,
                backfill_hours=initial_backfill_hours,
            )

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError
            window_end = _parse_utc(raw["window_end"])
            raw_success = raw.get("last_success_at")
            last_success_at = None if raw_success is None else _parse_utc(raw_success)
            backfill_hours = raw.get("backfill_hours", 24)
            if isinstance(backfill_hours, bool) or not isinstance(backfill_hours, int) or backfill_hours < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StateError("The utility import cursor state is invalid.") from error

        if initial_backfill_hours > backfill_hours:
            backfill_boundary = floor_to_five_minutes(now) - timedelta(hours=initial_backfill_hours)
            window_end = min(window_end, backfill_boundary)

        return cls(
            window_end=window_end,
            last_success_at=last_success_at,
            backfill_hours=max(backfill_hours, initial_backfill_hours),
        )

    def save_atomic(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f"{path.name}.tmp")
        payload = {
            "window_end": self.window_end.astimezone(timezone.utc).isoformat(),
            "last_success_at": (
                None
                if self.last_success_at is None
                else self.last_success_at.astimezone(timezone.utc).isoformat()
            ),
            "backfill_hours": self.backfill_hours,
        }

        with temporary_path.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())

        temporary_path.replace(path)


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)

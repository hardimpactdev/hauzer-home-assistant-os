from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Optional, Union

from hauzer_utility_exporter.discovery import UtilityMapping


PERIOD_DELTA = timedelta(minutes=5)
DECIMAL_PLACES = Decimal("0.000001")


def parse_datetime(value: Union[str, int, float]) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def floor_to_five_minutes(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return value.replace(
        minute=(value.minute // 5) * 5,
        second=0,
        microsecond=0,
    )


def build_readings(
    mapping: UtilityMapping,
    rows: Iterable[dict[str, object]],
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    readings: list[dict[str, object]] = []
    previous_sum: Optional[Decimal] = None
    factor, output_unit = _normalization(mapping.unit)

    for row in sorted(rows, key=lambda item: str(item.get("start", ""))):
        raw_start = row.get("start")
        if not isinstance(raw_start, (str, int, float)) or raw_start == "":
            continue

        interval_start = parse_datetime(raw_start)
        interval_end = interval_start + PERIOD_DELTA
        current_sum = _to_decimal(row.get("sum"))
        state = _to_decimal(row.get("state"))

        if interval_start < start:
            previous_sum = current_sum
            continue

        if interval_end > end:
            continue

        previous_sum_for_metadata = previous_sum
        delta: Optional[Decimal] = Decimal("0") if current_sum is not None else None
        if current_sum is not None and previous_sum is not None:
            candidate_delta = current_sum - previous_sum
            if candidate_delta >= 0:
                delta = candidate_delta

        previous_sum = current_sum
        value = state if state is not None else current_sum

        readings.append(
            {
                "metric": mapping.metric.value,
                "statistic_id": mapping.statistic_id,
                "interval_start": interval_start.isoformat(),
                "interval_end": interval_end.isoformat(),
                "value": _decimal_to_payload(_scaled(value, factor)),
                "delta": _decimal_to_payload(_scaled(delta, factor)),
                "unit": output_unit,
                "metadata": {
                    "ha_start": raw_start,
                    "ha_sum": _decimal_to_payload(current_sum),
                    "ha_state": _decimal_to_payload(state),
                    "ha_previous_sum": _decimal_to_payload(previous_sum_for_metadata),
                    "ha_unit": mapping.unit,
                    "origin": mapping.origin,
                },
            }
        )

    return readings


def chunked(
    readings: list[dict[str, object]],
    size: int,
) -> Iterable[list[dict[str, object]]]:
    for index in range(0, len(readings), size):
        yield readings[index : index + size]


def _normalization(unit: str) -> tuple[Decimal, str]:
    normalized = unit.strip().lower().replace("³", "3")
    if normalized == "wh":
        return Decimal("0.001"), "kWh"
    if normalized in {"l", "liter", "litre"}:
        return Decimal("0.001"), "m3"
    if normalized == "m3":
        return Decimal("1"), "m3"
    return Decimal("1"), unit


def _to_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _scaled(value: Optional[Decimal], factor: Decimal) -> Optional[Decimal]:
    if value is None:
        return None
    return value * factor


def _decimal_to_payload(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value.quantize(DECIMAL_PLACES, rounding=ROUND_HALF_UP), "f")

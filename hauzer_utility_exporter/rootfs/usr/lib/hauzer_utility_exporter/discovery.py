from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from hauzer_utility_exporter.configuration import AppConfig, Metric


@dataclass(frozen=True)
class UtilityMapping:
    metric: Metric
    statistic_id: str
    unit: str
    origin: str


@dataclass(frozen=True)
class DiscoveryResult:
    mappings: tuple[UtilityMapping, ...]
    ambiguous: Mapping[Metric, tuple[str, ...]]


ENERGY_UNITS = {"wh", "kwh", "mwh"}
VOLUME_UNITS = {"l", "liter", "litre", "m3", "m³"}
EXCLUDED_TERMS = {
    "appliance",
    "battery",
    "dishwasher",
    "dryer",
    "price",
    "solar",
    "tariff",
    "washer",
}


def discover_utilities(
    config: AppConfig,
    energy_preferences: Mapping[str, object],
    metadata: Iterable[Mapping[str, object]],
    states: Iterable[Mapping[str, object]],
) -> DiscoveryResult:
    metadata_by_id = _metadata_by_id(metadata)
    states_by_id = _states_by_id(states)
    energy_ids = _energy_dashboard_ids(energy_preferences)
    mappings: list[UtilityMapping] = []
    ambiguous: dict[Metric, tuple[str, ...]] = {}

    for metric in Metric:
        overrides = config.overrides[metric]
        if overrides:
            mappings.extend(
                UtilityMapping(
                    metric=metric,
                    statistic_id=statistic_id,
                    unit=_unit_for(statistic_id, metric, metadata_by_id, states_by_id),
                    origin="override",
                )
                for statistic_id in overrides
            )
            continue

        authoritative = energy_ids.get(metric, ())
        if authoritative:
            mappings.extend(
                UtilityMapping(
                    metric=metric,
                    statistic_id=statistic_id,
                    unit=_unit_for(statistic_id, metric, metadata_by_id, states_by_id),
                    origin="energy_dashboard",
                )
                for statistic_id in authoritative
            )
            continue

        candidates = _fallback_candidates(metric, metadata_by_id, states_by_id)
        high_confidence = [candidate for candidate in candidates if candidate[0] >= 7]
        if len(high_confidence) == 1:
            _, statistic_id = high_confidence[0]
            mappings.append(
                UtilityMapping(
                    metric=metric,
                    statistic_id=statistic_id,
                    unit=_unit_for(statistic_id, metric, metadata_by_id, states_by_id),
                    origin="automatic",
                )
            )
        elif len(candidates) > 1:
            ambiguous[metric] = tuple(candidate[1] for candidate in candidates)

    return DiscoveryResult(tuple(mappings), MappingProxyType(ambiguous))


def _energy_dashboard_ids(
    preferences: Mapping[str, object],
) -> dict[Metric, tuple[str, ...]]:
    discovered: dict[Metric, list[str]] = {metric: [] for metric in Metric}
    sources = preferences.get("energy_sources", [])

    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_type = source.get("type")
            if source_type == "grid":
                _append_ids(
                    discovered[Metric.ELECTRICITY_CONSUMPTION],
                    source.get("stat_energy_from"),
                )
                _append_ids(
                    discovered[Metric.ELECTRICITY_GRID_EXPORT],
                    source.get("stat_energy_to"),
                )
            elif source_type == "gas":
                _append_ids(
                    discovered[Metric.GAS_CONSUMPTION],
                    source.get("stat_energy_from"),
                )
            elif source_type == "water":
                _append_ids(
                    discovered[Metric.WATER_CONSUMPTION],
                    source.get("stat_energy_from"),
                )

    water_sources = preferences.get("device_consumption_water", [])
    if isinstance(water_sources, list):
        for source in water_sources:
            if isinstance(source, dict):
                _append_ids(
                    discovered[Metric.WATER_CONSUMPTION],
                    source.get("stat_consumption") or source.get("stat_energy_from"),
                )

    return {metric: tuple(statistic_ids) for metric, statistic_ids in discovered.items()}


def _append_ids(target: list[str], value: object) -> None:
    values = value if isinstance(value, list) else [value]
    for statistic_id in values:
        if isinstance(statistic_id, str) and statistic_id and statistic_id not in target:
            target.append(statistic_id)


def _metadata_by_id(
    metadata: Iterable[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for item in metadata:
        statistic_id = item.get("statistic_id")
        if isinstance(statistic_id, str):
            result[statistic_id] = item
    return result


def _states_by_id(
    states: Iterable[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for item in states:
        entity_id = item.get("entity_id")
        if isinstance(entity_id, str):
            result[entity_id] = item
    return result


def _fallback_candidates(
    metric: Metric,
    metadata_by_id: Mapping[str, Mapping[str, object]],
    states_by_id: Mapping[str, Mapping[str, object]],
) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []

    for statistic_id, item in metadata_by_id.items():
        normalized_id = statistic_id.lower()
        if any(term in normalized_id for term in EXCLUDED_TERMS):
            continue

        unit = str(item.get("unit_of_measurement") or "")
        if not _unit_matches(metric, unit):
            continue
        if item.get("has_sum") is not True:
            continue

        score = 3
        attributes = _attributes(states_by_id.get(statistic_id, {}))
        state_class = attributes.get("state_class")
        device_class = attributes.get("device_class")
        if state_class in {"total", "total_increasing"}:
            score += 3
        if device_class == _device_class(metric):
            score += 4
        if _metric_keyword(metric) in normalized_id:
            score += 2

        if score >= 5:
            candidates.append((score, statistic_id))

    return sorted(candidates, key=lambda candidate: (-candidate[0], candidate[1]))


def _attributes(state: Mapping[str, object]) -> Mapping[str, object]:
    attributes = state.get("attributes", {})
    return attributes if isinstance(attributes, dict) else {}


def _unit_for(
    statistic_id: str,
    metric: Metric,
    metadata_by_id: Mapping[str, Mapping[str, object]],
    states_by_id: Mapping[str, Mapping[str, object]],
) -> str:
    metadata = metadata_by_id.get(statistic_id, {})
    unit = metadata.get("unit_of_measurement")
    if isinstance(unit, str) and unit:
        return unit

    state_unit = _attributes(states_by_id.get(statistic_id, {})).get("unit_of_measurement")
    if isinstance(state_unit, str) and state_unit:
        return state_unit

    if metric in {Metric.GAS_CONSUMPTION, Metric.WATER_CONSUMPTION}:
        return "m³"
    return "kWh"


def _unit_matches(metric: Metric, unit: str) -> bool:
    normalized = unit.lower().replace("³", "3")
    if metric in {Metric.ELECTRICITY_CONSUMPTION, Metric.ELECTRICITY_GRID_EXPORT}:
        return normalized in ENERGY_UNITS
    return normalized in VOLUME_UNITS


def _device_class(metric: Metric) -> str:
    if metric in {Metric.ELECTRICITY_CONSUMPTION, Metric.ELECTRICITY_GRID_EXPORT}:
        return "energy"
    if metric is Metric.GAS_CONSUMPTION:
        return "gas"
    return "water"


def _metric_keyword(metric: Metric) -> str:
    if metric in {Metric.ELECTRICITY_CONSUMPTION, Metric.ELECTRICITY_GRID_EXPORT}:
        return "grid"
    if metric is Metric.GAS_CONSUMPTION:
        return "gas"
    return "water"

from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address
import re
from types import MappingProxyType
from typing import Mapping, Optional
from urllib.parse import urlparse


HAUZER_TOKEN_PATTERN = re.compile(r"hsr_[A-Za-z0-9]{40}")
STATISTIC_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+")


class ConfigurationError(ValueError):
    """Raised when app configuration is invalid."""


class Metric(str, Enum):
    ELECTRICITY_CONSUMPTION = "electricity_consumption"
    ELECTRICITY_GRID_EXPORT = "electricity_grid_export"
    GAS_CONSUMPTION = "gas_consumption"
    WATER_CONSUMPTION = "water_consumption"


OPTION_BY_METRIC = {
    Metric.ELECTRICITY_CONSUMPTION: "electricity_consumption_statistics",
    Metric.ELECTRICITY_GRID_EXPORT: "electricity_grid_export_statistics",
    Metric.GAS_CONSUMPTION: "gas_consumption_statistics",
    Metric.WATER_CONSUMPTION: "water_consumption_statistics",
}


def parse_statistic_ids(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ConfigurationError("Each statistic ID override must be text.")

    if not value.strip():
        return ()

    parsed: list[str] = []
    seen: set[str] = set()

    for raw_id in value.split(","):
        statistic_id = raw_id.strip()
        if not statistic_id or STATISTIC_ID_PATTERN.fullmatch(statistic_id) is None:
            raise ConfigurationError("Each statistic ID must use only safe characters.")
        if statistic_id not in seen:
            parsed.append(statistic_id)
            seen.add(statistic_id)

    return tuple(parsed)


@dataclass(frozen=True)
class AppConfig:
    hauzer_url: str
    hauzer_token: str
    hauzer_host_ip: Optional[str]
    verify_tls: bool
    initial_backfill_hours: int
    supervisor_token: str
    overrides: Mapping[Metric, tuple[str, ...]]

    @classmethod
    def from_options(
        cls,
        options: dict[str, object],
        supervisor_token: str,
    ) -> "AppConfig":
        hauzer_url = cls._read_url(options)
        hauzer_token = cls._read_token(options)
        hauzer_host_ip = cls._read_host_ip(options)
        verify_tls = cls._read_verify_tls(options)
        initial_backfill_hours = cls._read_backfill(options)

        if not supervisor_token:
            raise ConfigurationError("A Supervisor token is required.")

        overrides = {
            metric: parse_statistic_ids(cls._read_override(options, option_name))
            for metric, option_name in OPTION_BY_METRIC.items()
        }

        return cls(
            hauzer_url=hauzer_url,
            hauzer_token=hauzer_token,
            hauzer_host_ip=hauzer_host_ip,
            verify_tls=verify_tls,
            initial_backfill_hours=initial_backfill_hours,
            supervisor_token=supervisor_token,
            overrides=MappingProxyType(overrides),
        )

    @staticmethod
    def _read_url(options: dict[str, object]) -> str:
        value = options.get("hauzer_url")
        if not isinstance(value, str):
            raise ConfigurationError("The Hauzer URL must be an HTTP(S) URL.")

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("The Hauzer URL must be an HTTP(S) URL.")

        return value

    @staticmethod
    def _read_token(options: dict[str, object]) -> str:
        value = options.get("hauzer_token")
        if not isinstance(value, str) or HAUZER_TOKEN_PATTERN.fullmatch(value) is None:
            raise ConfigurationError("Configure a valid Hauzer import token.")

        return value

    @staticmethod
    def _read_host_ip(options: dict[str, object]) -> Optional[str]:
        value = options.get("hauzer_host_ip", "")
        if not isinstance(value, str):
            raise ConfigurationError("The Hauzer host override must be a private IP address.")
        if not value.strip():
            return None

        try:
            address = ip_address(value.strip())
        except ValueError as error:
            raise ConfigurationError("The Hauzer host override must be a private IP address.") from error

        if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ConfigurationError("The Hauzer host override must be a private IP address.")

        return address.compressed

    @staticmethod
    def _read_verify_tls(options: dict[str, object]) -> bool:
        value = options.get("verify_tls")
        if not isinstance(value, bool):
            raise ConfigurationError("The TLS verification option must be true or false.")

        return value

    @staticmethod
    def _read_backfill(options: dict[str, object]) -> int:
        value = options.get("initial_backfill_hours")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 720:
            raise ConfigurationError("The initial backfill must be between 1 and 720 hours.")

        return value

    @staticmethod
    def _read_override(options: dict[str, object], option_name: str) -> str:
        value = options.get(option_name, "")
        if not isinstance(value, str):
            raise ConfigurationError("Each statistic ID override must be text.")

        return value

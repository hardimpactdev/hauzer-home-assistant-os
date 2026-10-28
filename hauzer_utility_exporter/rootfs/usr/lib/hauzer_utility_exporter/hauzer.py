from dataclasses import dataclass
import json
import math
import ssl
import time
from collections.abc import Callable, Mapping
from typing import Optional, TypeVar
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hauzer_utility_exporter.configuration import AppConfig


T = TypeVar("T")


RETRY_DELAYS = (1, 5, 15)
REQUEST_TIMEOUT = 30.0
MAX_RETRY_AFTER_SECONDS = 300.0

Transport = Callable[
    [str, dict[str, str], bytes, bool, float],
    tuple[int, object, Mapping[str, str]],
]


class HauzerError(RuntimeError):
    """Base class for sanitized Hauzer delivery failures."""


class InvalidHauzerToken(HauzerError):
    """Raised when Hauzer rejects the configured import token."""


class InvalidImportPayload(HauzerError):
    """Raised when Hauzer rejects an import payload."""


class RetryableHauzerError(HauzerError):
    """Raised when a delivery can be retried safely."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ImportResult:
    processed: int
    created: int
    updated: int


def _default_transport(
    url: str,
    headers: dict[str, str],
    body: bytes,
    verify_tls: bool,
    timeout: float,
) -> tuple[int, object, Mapping[str, str]]:
    request = Request(url, data=body, headers=headers, method="POST")
    context = None if verify_tls else ssl._create_unverified_context()

    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            response_body = response.read().decode("utf-8")
            return (
                response.status,
                json.loads(response_body or "{}"),
                dict(response.headers.items()),
            )
    except HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        try:
            decoded: object = json.loads(response_body or "{}")
        except json.JSONDecodeError:
            decoded = {}
        return error.code, decoded, dict(error.headers.items())
    except URLError as error:
        raise OSError from error


class HauzerClient:
    def __init__(
        self,
        config: AppConfig,
        exporter_version: str = "development",
        transport: Transport = _default_transport,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._exporter_version = exporter_version
        self._transport = transport
        self._sleeper = sleeper

    def post_batch(self, readings: list[dict[str, object]]) -> ImportResult:
        return self._post(
            self._config.hauzer_url,
            {"source": "home_assistant", "period": "5minute", "readings": readings},
            lambda status, response, headers: self._classify(
                status,
                response,
                headers,
                submitted_count=len(readings),
            ),
        )

    def post_registry(self, snapshot: dict[str, object]) -> None:
        endpoint = urlsplit(self._config.hauzer_url)
        # hauzer_url is the utility endpoint; registry sync uses the same origin.
        url = urlunsplit((endpoint.scheme, endpoint.netloc, "/api/home-assistant/registry", "", ""))
        self._post(
            url,
            snapshot,
            lambda status, response, headers: self._classify_registry(
                status,
                response,
                headers,
                snapshot,
            ),
        )

    def _post(
        self,
        url: str,
        payload: dict[str, object],
        classify: Callable[[int, object, Mapping[str, str]], T],
    ) -> T:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._config.hauzer_token}",
            "Content-Type": "application/json",
            "User-Agent": f"Hauzer-Utility-Exporter/{self._exporter_version}",
            "X-Hauzer-Exporter-Version": self._exporter_version,
        }

        for attempt in range(len(RETRY_DELAYS) + 1):
            retry_after = None
            try:
                status, response, response_headers = self._transport(
                    url,
                    headers,
                    body,
                    self._config.verify_tls,
                    REQUEST_TIMEOUT,
                )
                return classify(status, response, response_headers)
            except RetryableHauzerError as error:
                if attempt >= len(RETRY_DELAYS):
                    raise
                retry_after = error.retry_after
            except OSError as error:
                if attempt >= len(RETRY_DELAYS):
                    raise RetryableHauzerError(
                        "Hauzer is temporarily unavailable.",
                    ) from error

            self._sleeper(
                retry_after if retry_after is not None else RETRY_DELAYS[attempt],
            )

        raise RetryableHauzerError("Hauzer is temporarily unavailable.")

    @staticmethod
    def _classify_registry(
        status: int,
        response: object,
        headers: Mapping[str, str],
        snapshot: dict[str, object],
    ) -> None:
        if status == 202:
            data = response if isinstance(response, dict) else {}
            if data.get("status") != "queued" or any(
                type(data.get(registry)) is not int or data[registry] != len(snapshot[registry])
                for registry in ("floors", "areas", "devices")
            ):
                raise HauzerError("Hauzer returned an invalid queued registry acknowledgement.")
            return
        if status == 401:
            raise InvalidHauzerToken("Hauzer rejected the registry source token (HTTP 401).")
        if status == 422:
            raise InvalidImportPayload(
                "Hauzer rejected the registry payload: validation failed (HTTP 422)."
            )
        if status == 429 or status >= 500:
            raise RetryableHauzerError(
                "Hauzer registry delivery is temporarily unavailable.",
                retry_after=_retry_after(headers) if status == 429 else None,
            )
        raise HauzerError(f"Hauzer rejected the registry with HTTP status {status}.")

    @staticmethod
    def _classify(
        status: int,
        response: object,
        headers: Mapping[str, str],
        submitted_count: int,
    ) -> ImportResult:
        if status in {200, 201}:
            data = response if isinstance(response, dict) else {}
            return ImportResult(
                processed=int(data.get("processed", 0)),
                created=int(data.get("created", 0)),
                updated=int(data.get("updated", 0)),
            )
        if status == 202:
            return _queued_import_result(response, submitted_count)
        if status == 401:
            raise InvalidHauzerToken("Hauzer rejected the configured import token.")
        if status == 422:
            raise InvalidImportPayload("Hauzer rejected the utility import payload.")
        if status == 429:
            raise RetryableHauzerError(
                "Hauzer is temporarily unavailable.",
                retry_after=_retry_after(headers),
            )
        if status >= 500:
            raise RetryableHauzerError("Hauzer is temporarily unavailable.")
        raise HauzerError(f"Hauzer rejected the import with HTTP status {status}.")


def _queued_import_result(response: object, submitted_count: int) -> ImportResult:
    data = response if isinstance(response, dict) else {}
    if data.get("status") != "queued":
        raise HauzerError("Hauzer returned an invalid queued import acknowledgement.")

    readings_count = data.get("readings")
    if not isinstance(readings_count, int) or isinstance(readings_count, bool):
        raise HauzerError("Hauzer returned an invalid queued import acknowledgement.")
    if readings_count != submitted_count:
        raise HauzerError("Hauzer returned a mismatched queued import acknowledgement.")

    return ImportResult(processed=readings_count, created=0, updated=0)


def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
    value = next(
        (header_value for key, header_value in headers.items() if key.lower() == "retry-after"),
        None,
    )
    if value is None:
        return None

    try:
        delay = float(value)
    except ValueError:
        return None

    if not math.isfinite(delay) or delay < 0 or delay > MAX_RETRY_AFTER_SECONDS:
        return None

    return delay

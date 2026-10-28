from datetime import datetime, timezone
import logging
from threading import Event, Thread
import time
from typing import Callable, Optional

from hauzer_utility_exporter.hauzer import HauzerClient, HauzerError
from hauzer_utility_exporter.home_assistant import (
    HomeAssistantError,
    RegistrySession,
    SupervisorWebSocket,
    UnsupportedCommand,
)


REGISTRY_LIMITS = {"floors": 100, "areas": 500, "devices": 5000}
REGISTRY_EVENTS = (
    "floor_registry_updated",
    "area_registry_updated",
    "device_registry_updated",
)
DEBOUNCE_SECONDS = 10
REFRESH_SECONDS = 24 * 60 * 60
RECONNECT_REFRESH_SECONDS = 5 * 60
RECONNECT_DELAYS = (1, 5, 15, 60)
DEVICE_FIELDS = (
    "name_by_user",
    "manufacturer",
    "model",
    "model_id",
    "serial_number",
    "sw_version",
    "hw_version",
    "area_id",
    "via_device_id",
)


class RegistryLimitExceeded(ValueError):
    """A full snapshot is too large to deliver safely."""


def _text(value: Optional[str]) -> Optional[str]:
    return value[:255] if value is not None else None


def build_snapshot(
    floors: list[dict[str, object]],
    areas: list[dict[str, object]],
    devices: list[dict[str, object]],
    generated_at: datetime,
    floors_supported: bool = True,
) -> dict[str, object]:
    for name, entries in (("floors", floors), ("areas", areas), ("devices", devices)):
        if len(entries) > REGISTRY_LIMITS[name]:
            raise RegistryLimitExceeded(
                f"Registry sync skipped: {len(entries)} {name} exceeds the limit of {REGISTRY_LIMITS[name]}."
            )

    return {
        "source": "home_assistant",
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "floors": [
            {
                "floor_id": _text(floor["floor_id"]),
                "name": _text(floor["name"]),
                "level": floor.get("level"),
            }
            for floor in floors
        ],
        "areas": [
            {
                "area_id": _text(area["area_id"]),
                "name": _text(area["name"]),
                "floor_id": _text(area.get("floor_id")) if floors_supported else None,
                "icon": _text(area.get("icon")),
                "aliases": sorted(_text(alias) for alias in (area.get("aliases") or [])),
            }
            for area in areas
        ],
        "devices": [
            {
                "device_id": _text(device["id"]),
                # HA permits an unnamed device. The Hauzer contract requires a name.
                "name": _text(device.get("name") or device.get("name_by_user") or device["id"]),
                **{field: _text(device.get(field)) for field in DEVICE_FIELDS},
                "entry_type": "service" if device.get("entry_type") == "service" else None,
                "disabled": device.get("disabled_by") is not None,
            }
            for device in devices
        ],
    }


def read_snapshot(session: RegistrySession, generated_at: datetime) -> dict[str, object]:
    floors_supported = True
    try:
        floors = session.command({"type": "config/floor_registry/list"})
    except UnsupportedCommand:
        floors = []
        floors_supported = False
    areas = session.command({"type": "config/area_registry/list"})
    devices = session.command({"type": "config/device_registry/list"})
    if any(not isinstance(entries, list) for entries in (floors, areas, devices)):
        raise HomeAssistantError("Home Assistant returned invalid registry lists.")
    return build_snapshot(floors, areas, devices, generated_at, floors_supported)


class RegistrySchedule:
    def __init__(self, now: float) -> None:
        self.refresh_at = now
        self.change_at: Optional[float] = None
        self.reconnect_at: Optional[float] = None
        self._reconnect_allowed_at = now

    def changed(self, now: float) -> None:
        self.change_at = now + DEBOUNCE_SECONDS

    def reconnected(self, now: float) -> None:
        self.reconnect_at = max(now + DEBOUNCE_SECONDS, self._reconnect_allowed_at)

    def due(self, now: float) -> bool:
        return (
            now >= self.refresh_at
            or (self.change_at is not None and now >= self.change_at)
            or (self.reconnect_at is not None and now >= self.reconnect_at)
        )

    def started(self, now: float) -> None:
        # Any fresh read covers changes missed while disconnected, including a
        # read triggered by an event or the daily refresh before reconnect_at.
        if self.reconnect_at is not None:
            self._reconnect_allowed_at = now + RECONNECT_REFRESH_SECONDS
        self.reconnect_at = None
        self.refresh_at = now + REFRESH_SECONDS
        self.change_at = None


class RegistrySync(Thread):
    """Keep registry reads, network delivery and retry waits off the utility loop."""

    def __init__(
        self,
        websocket: SupervisorWebSocket,
        hauzer: HauzerClient,
        stop_event: Event,
        logger: Optional[logging.Logger] = None,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        super().__init__(name="hauzer-registry-sync", daemon=True)
        self._websocket = websocket
        self._hauzer = hauzer
        self._stop_event = stop_event
        self._logger = logger or logging.getLogger("hauzer_utility_exporter")
        self._clock = clock
        self._utcnow = utcnow
        self._schedule = RegistrySchedule(clock())
        self._delivery: Optional[Thread] = None

    def _on_event(self, message: dict[str, object]) -> None:
        event = message.get("event", {})
        if isinstance(event, dict) and event.get("event_type") in REGISTRY_EVENTS:
            self._schedule.changed(self._clock())

    def sync_if_due(self, session: RegistrySession) -> None:
        # Only one full snapshot may be in flight. Keep reading events while it
        # waits for Hauzer; changes remain scheduled for after delivery finishes.
        if self._delivery is not None and self._delivery.is_alive():
            return
        now = self._clock()
        if not self._schedule.due(now):
            return
        # Events received during the reads must still trigger a later snapshot.
        self._schedule.started(now)
        try:
            snapshot = read_snapshot(session, self._utcnow())
        except RegistryLimitExceeded as error:
            self._logger.warning("%s", error)
            return
        except HomeAssistantError:
            # No delivery started: retry the read when the socket reconnects.
            self._schedule.refresh_at = now
            raise
        except Exception:
            self._logger.error("Registry sync failed unexpectedly.")
            return

        self._delivery = Thread(
            target=self._deliver,
            args=(snapshot,),
            name="hauzer-registry-delivery",
            daemon=True,
        )
        self._delivery.start()

    def _deliver(self, snapshot: dict[str, object]) -> None:
        try:
            self._hauzer.post_registry(snapshot)
            self._logger.info(
                "Registry snapshot queued: %s floors, %s areas, %s devices.",
                len(snapshot["floors"]),
                len(snapshot["areas"]),
                len(snapshot["devices"]),
            )
        except HauzerError as error:
            if not self._stop_event.is_set():
                self._logger.error("Registry sync failed: %s", error)
        except Exception:
            self._logger.error("Registry sync failed unexpectedly.")

    def run(self) -> None:
        failures = 0
        connected = False
        while not self._stop_event.is_set():
            try:
                with self._websocket.session(self._on_event) as session:
                    for event_type in REGISTRY_EVENTS:
                        session.command({"type": "subscribe_events", "event_type": event_type})
                    if connected:
                        self._schedule.reconnected(self._clock())
                    connected = True
                    while not self._stop_event.is_set():
                        self.sync_if_due(session)
                        session.poll(1.0)
                        failures = 0
            except Exception:
                # Never include transport exceptions: they can contain credentials.
                self._logger.warning("Registry WebSocket unavailable; reconnecting with backoff.")
                delay = RECONNECT_DELAYS[min(failures, len(RECONNECT_DELAYS) - 1)]
                failures += 1
                self._stop_event.wait(delay)

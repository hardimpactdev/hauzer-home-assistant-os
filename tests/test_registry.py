from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from hauzer_utility_exporter.discovery import DiscoveryResult
from hauzer_utility_exporter.hauzer import HauzerClient
from hauzer_utility_exporter.home_assistant import HomeAssistantError, UnsupportedCommand
from hauzer_utility_exporter.registry import (
    REFRESH_SECONDS,
    REGISTRY_EVENTS,
    REGISTRY_LIMITS,
    RegistryLimitExceeded,
    RegistrySync,
    build_snapshot,
    read_snapshot,
)
from hauzer_utility_exporter.service import ImportService
from tests.test_service import FakeHomeAssistant, FakeHauzer, NOW, app_config, mapping, rows


GENERATED_AT = datetime(2026, 10, 28, 18, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self, floors=None, areas=None, devices=None):
        self.registries = {"floors": floors or [], "areas": areas or [], "devices": devices or []}
        self.calls = []

    def command(self, command):
        self.calls.append(command)
        registry = command["type"].split("/")[1].replace("_registry", "s")
        result = self.registries[registry]
        if isinstance(result, Exception):
            raise result
        return result


class RegistryMappingTest(unittest.TestCase):
    def test_maps_only_contract_fields_including_all_nullable_fields(self) -> None:
        floors = [{"floor_id": "ground", "name": "Ground floor", "level": 0}]
        areas = [
            {
                "area_id": "living",
                "name": "Living room",
                "floor_id": "ground",
                "icon": "mdi:sofa",
                "aliases": {"Lounge", "Den"},
            }
        ]
        devices = [
            {
                "id": "plug",
                "name": "Plug TV",
                "manufacturer": "Shelly",
                "model": "Plug S",
                "model_id": "SNPL",
                "sw_version": "1.4.4",
                "area_id": "living",
                "disabled_by": "user",
                "entry_type": "service",
                "identifiers": ["do-not-send"],
                "connections": ["do-not-send"],
            }
        ]

        snapshot = build_snapshot(floors, areas, devices, GENERATED_AT)

        self.assertEqual(
            snapshot,
            {
                "source": "home_assistant",
                "generated_at": "2026-10-28T18:00:00+00:00",
                "floors": floors,
                "areas": [
                    {
                        "area_id": "living",
                        "name": "Living room",
                        "floor_id": "ground",
                        "icon": "mdi:sofa",
                        "aliases": ["Den", "Lounge"],
                    }
                ],
                "devices": [
                    {
                        "device_id": "plug",
                        "name": "Plug TV",
                        "name_by_user": None,
                        "manufacturer": "Shelly",
                        "model": "Plug S",
                        "model_id": "SNPL",
                        "serial_number": None,
                        "sw_version": "1.4.4",
                        "hw_version": None,
                        "area_id": "living",
                        "via_device_id": None,
                        "entry_type": "service",
                        "disabled": True,
                    }
                ],
            },
        )
        self.assertNotIn("do-not-send", json.dumps(snapshot))

    def test_missing_optional_fields_and_unnamed_devices(self) -> None:
        snapshot = build_snapshot(
            [{"floor_id": "floor", "name": "Floor"}],
            [{"area_id": "area", "name": "Area", "aliases": ["Kitchen"]}],
            [
                {"id": "one", "name": None, "name_by_user": "User name"},
                {"id": "two", "name": None, "disabled_by": None},
            ],
            GENERATED_AT.astimezone(timezone(timedelta(hours=2))),
        )
        self.assertIsNone(snapshot["floors"][0]["level"])
        self.assertIsNone(snapshot["areas"][0]["floor_id"])
        self.assertIsNone(snapshot["areas"][0]["icon"])
        self.assertEqual(snapshot["devices"][0]["name"], "User name")
        self.assertEqual(snapshot["devices"][1]["name"], "two")
        self.assertFalse(snapshot["devices"][1]["disabled"])
        self.assertEqual(snapshot["generated_at"], "2026-10-28T18:00:00+00:00")

    def test_missing_floor_registry_clears_area_floor_references(self) -> None:
        session = FakeSession(
            floors=UnsupportedCommand("unsupported"),
            areas=[{"area_id": "a", "name": "Area", "floor_id": "old-floor"}],
        )
        snapshot = read_snapshot(session, GENERATED_AT)
        self.assertEqual(snapshot["floors"], [])
        self.assertIsNone(snapshot["areas"][0]["floor_id"])
        self.assertEqual(
            [call["type"] for call in session.calls],
            [
                "config/floor_registry/list",
                "config/area_registry/list",
                "config/device_registry/list",
            ],
        )

    def test_floor_failure_other_than_unsupported_does_not_erase_floors(self) -> None:
        with self.assertRaises(HomeAssistantError):
            read_snapshot(FakeSession(floors=HomeAssistantError("unavailable")), GENERATED_AT)

    def test_every_string_is_truncated_to_255_characters(self) -> None:
        long = "é" * 300
        snapshot = build_snapshot(
            [{"floor_id": long, "name": long, "level": None}],
            [{"area_id": long, "name": long, "floor_id": long, "icon": long, "aliases": [long]}],
            [
                {
                    "id": long,
                    "name": long,
                    **{
                        field: long
                        for field in (
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
                    },
                }
            ],
            GENERATED_AT,
        )
        for registry in ("floors", "areas", "devices"):
            for value in snapshot[registry][0].values():
                if isinstance(value, str):
                    self.assertEqual(value, "é" * 255)
                if isinstance(value, list):
                    self.assertEqual(value, ["é" * 255])

    def test_limits_allow_exact_boundary_and_reject_one_over(self) -> None:
        entries = {
            "floors": {"floor_id": "f", "name": "Floor"},
            "areas": {"area_id": "a", "name": "Area"},
            "devices": {"id": "d", "name": "Device"},
        }
        registries = {name: [entries[name]] * limit for name, limit in REGISTRY_LIMITS.items()}
        snapshot = build_snapshot(**registries, generated_at=GENERATED_AT)
        for name, limit in REGISTRY_LIMITS.items():
            self.assertEqual(len(snapshot[name]), limit)
            oversized = {**registries, name: registries[name] + [entries[name]]}
            with self.subTest(registry=name), self.assertRaisesRegex(RegistryLimitExceeded, name):
                build_snapshot(**oversized, generated_at=GENERATED_AT)


class RegistrySyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.posts = []
        self.logger = logging.getLogger("registry-test")
        self.hauzer = HauzerClient(
            app_config(), transport=self.transport, sleeper=lambda delay: None
        )
        self.worker = RegistrySync(
            None,
            self.hauzer,
            Event(),
            logger=self.logger,
            clock=lambda: self.now,
            utcnow=lambda: GENERATED_AT,
        )
        self.session = FakeSession()

    def transport(self, url, headers, body, verify_tls, timeout):
        snapshot = json.loads(body)
        self.posts.append(snapshot)
        return 202, {"status": "queued", **{key: len(snapshot[key]) for key in REGISTRY_LIMITS}}, {}

    def event(self, event_type: str) -> None:
        self.worker._on_event({"type": "event", "event": {"event_type": event_type}})

    def test_startup_and_24_hour_timer(self) -> None:
        self.worker.sync_if_due(self.session)
        self.now += REFRESH_SECONDS - 1
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 1)
        self.now += 1
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 2)

    def test_burst_of_all_registry_events_is_debounced_for_ten_seconds(self) -> None:
        self.worker.sync_if_due(self.session)
        for event_type in REGISTRY_EVENTS:
            self.now += 2
            self.event(event_type)
            self.worker.sync_if_due(self.session)
        self.now += 9.99
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 1)
        self.now += 0.01
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 2)
        self.now += 10
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 2)

    def test_unrelated_events_do_not_trigger_sync(self) -> None:
        self.worker.sync_if_due(self.session)
        self.event("state_changed")
        self.now += 11
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 1)

    def test_new_snapshot_reads_all_registries_again(self) -> None:
        self.worker.sync_if_due(self.session)
        self.session.registries["devices"] = [{"id": "new", "name": "New device"}]
        self.event("device_registry_updated")
        self.now += 10
        self.worker.sync_if_due(self.session)
        self.assertEqual(self.posts[0]["devices"], [])
        self.assertEqual(self.posts[1]["devices"][0]["device_id"], "new")
        self.assertEqual(len(self.session.calls), 6)

    def test_limits_log_warning_and_skip_entire_post(self) -> None:
        for registry, limit in REGISTRY_LIMITS.items():
            self.worker._schedule.refresh_at = self.now
            session = FakeSession()
            session.registries[registry] = [{}] * (limit + 1)
            with (
                self.subTest(registry=registry),
                self.assertLogs(self.logger, level="WARNING") as captured,
            ):
                self.worker.sync_if_due(session)
            self.assertIn(f"{limit + 1} {registry}", " ".join(captured.output))
        self.assertEqual(self.posts, [])

    def test_rejected_posts_log_safe_reasons_without_retrying_or_stopping_worker(self) -> None:
        for status, reason in ((401, "token"), (422, "validation")):
            calls = []
            self.worker._schedule.refresh_at = self.now
            self.worker._hauzer = HauzerClient(
                app_config(),
                transport=lambda *args: (
                    calls.append(args)
                    or (status, {"message": "supervisor-secret " + app_config().hauzer_token}, {})
                ),
                sleeper=lambda delay: self.fail("Permanent error retried"),
            )
            with (
                self.subTest(status=status),
                self.assertLogs(self.logger, level="ERROR") as captured,
            ):
                self.worker.sync_if_due(self.session)
            output = " ".join(captured.output)
            self.assertIn(reason, output)
            self.assertIn(str(status), output)
            self.assertNotIn("supervisor-secret", output)
            self.assertNotIn(app_config().hauzer_token, output)
            self.assertNotIn("generated_at", output)
            self.assertEqual(len(calls), 1)

    def test_reconnect_subscribes_and_sends_a_new_startup_snapshot(self) -> None:
        stop = Event()
        connections = []
        worker = self.worker
        session = self.session
        real_command = session.command

        def command(command):
            if command["type"] == "subscribe_events":
                session.calls.append(command)
                return None
            return real_command(command)

        session.command = command

        def poll(timeout):
            if len(connections) == 1:
                raise HomeAssistantError("supervisor-secret")
            stop.set()

        session.poll = poll

        class WebSocket:
            @contextmanager
            def session(self, on_event):
                connections.append(on_event)
                yield session

        worker._websocket = WebSocket()
        worker._stop_event = stop
        # Keep the test immediate while retaining the real worker reconnect path.
        original_wait = stop.wait
        stop.wait = lambda delay: original_wait(0)
        with self.assertLogs(self.logger, level="WARNING") as captured:
            worker.run()
        self.assertEqual(len(connections), 2)
        self.assertEqual(len(self.posts), 2)
        subscriptions = [
            call["event_type"] for call in session.calls if call["type"] == "subscribe_events"
        ]
        self.assertEqual(subscriptions, list(REGISTRY_EVENTS) * 2)
        self.assertNotIn("supervisor-secret", " ".join(captured.output))

    def test_repeated_websocket_failures_use_capped_reconnect_backoff(self) -> None:
        stop = Event()
        delays = []

        class WebSocket:
            def session(self, on_event):
                raise OSError("supervisor-secret")

        def wait(delay):
            delays.append(delay)
            if len(delays) == 5:
                stop.set()

        stop.wait = wait
        worker = RegistrySync(WebSocket(), self.hauzer, stop, logger=self.logger)
        with self.assertLogs(self.logger, level="WARNING") as captured:
            worker.run()
        self.assertEqual(delays, [1, 5, 15, 60, 60])
        self.assertNotIn("supervisor-secret", " ".join(captured.output))

    def test_event_received_during_snapshot_reads_triggers_another_snapshot(self) -> None:
        command = self.session.command

        def command_with_event(request):
            if request["type"] == "config/device_registry/list":
                self.event("device_registry_updated")
            return command(request)

        self.session.command = command_with_event
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 1)
        self.now += 10
        self.worker.sync_if_due(self.session)
        self.assertEqual(len(self.posts), 2)

    def test_utility_imports_continue_during_failed_registry_retry_backoff(self) -> None:
        retry_started = Event()
        release_retry = Event()
        stop = Event()
        calls = []
        failures = []

        def transport(*args):
            calls.append(args)
            return 503, {}, {}

        def sleeper(delay):
            retry_started.set()
            if not release_retry.wait(2):
                failures.append("Utility loop did not release registry retry")

        session = self.session
        command = session.command
        session.command = lambda request: (
            None if request["type"] == "subscribe_events" else command(request)
        )
        session.poll = lambda timeout: stop.set()

        class WebSocket:
            @contextmanager
            def session(self, on_event):
                yield session

        worker = RegistrySync(
            WebSocket(),
            HauzerClient(app_config(), transport=transport, sleeper=sleeper),
            stop,
            logger=self.logger,
        )
        utility_hauzer = FakeHauzer()
        electricity = mapping()
        with TemporaryDirectory() as directory, self.assertLogs(self.logger, level="ERROR"):
            utility = ImportService(
                app_config(),
                FakeHomeAssistant({electricity.statistic_id: rows(17)}),
                utility_hauzer,
                Path(directory) / "state.json",
                discover=lambda *args: DiscoveryResult((electricity,), {}),
            )
            worker.start()
            try:
                self.assertTrue(retry_started.wait(2))
                self.assertTrue(utility.run_cycle(NOW).success)
                self.assertTrue(utility.run_cycle(NOW + timedelta(minutes=5)).success)
                self.assertEqual(len(utility_hauzer.batches), 2)
                self.assertTrue(worker.is_alive())
            finally:
                release_retry.set()
                worker.join(timeout=2)
                stop.set()
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 4)
        self.assertEqual(failures, [])

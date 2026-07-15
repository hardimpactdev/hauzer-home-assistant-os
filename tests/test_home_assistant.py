from datetime import datetime, timezone
import json
import unittest

from hauzer_utility_exporter.home_assistant import (
    HomeAssistantClient,
    HomeAssistantError,
    SupervisorWebSocket,
)


class FakeSocket:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = [json.dumps(response) for response in responses]
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def recv(self) -> str:
        return self.responses.pop(0)

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def close(self) -> None:
        self.closed = True


class HomeAssistantClientTest(unittest.TestCase):
    def test_client_exposes_energy_preferences_and_metadata(self) -> None:
        energy_preferences = {"energy_sources": []}
        metadata = [{"statistic_id": "sensor.grid", "has_sum": True}]
        websocket_calls: list[dict[str, object]] = []

        def websocket_command(command: dict[str, object]) -> object:
            websocket_calls.append(command)
            if command["type"] == "energy/get_prefs":
                return energy_preferences
            return metadata

        client = HomeAssistantClient(
            "supervisor-secret",
            websocket_command=websocket_command,
        )

        self.assertEqual(client.energy_preferences(), energy_preferences)
        self.assertEqual(client.statistics_metadata(), metadata)
        self.assertEqual(
            websocket_calls,
            [
                {"type": "energy/get_prefs"},
                {"type": "recorder/list_statistic_ids", "statistic_type": "sum"},
            ],
        )

    def test_rest_states_use_only_the_internal_supervisor_url(self) -> None:
        calls: list[tuple[str, dict[str, str], float]] = []

        def request_json(
            url: str,
            headers: dict[str, str],
            timeout: float,
        ) -> object:
            calls.append((url, headers, timeout))
            return [{"entity_id": "sensor.water"}]

        client = HomeAssistantClient(
            "supervisor-secret",
            request_json=request_json,
            websocket_command=lambda command: {},
        )

        self.assertEqual(client.states(), [{"entity_id": "sensor.water"}])
        self.assertEqual(calls[0][0], "http://supervisor/core/api/states")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer supervisor-secret")
        self.assertEqual(calls[0][2], 20.0)

    def test_statistics_request_uses_completed_period_parameters(self) -> None:
        calls: list[dict[str, object]] = []

        def websocket_command(command: dict[str, object]) -> object:
            calls.append(command)
            return {"sensor.grid": []}

        client = HomeAssistantClient(
            "supervisor-secret",
            websocket_command=websocket_command,
        )
        start = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc)

        result = client.statistics_during_period(("sensor.grid",), start, end)

        self.assertEqual(result, {"sensor.grid": []})
        self.assertEqual(calls[0]["type"], "recorder/statistics_during_period")
        self.assertEqual(calls[0]["period"], "5minute")
        self.assertEqual(calls[0]["statistic_ids"], ["sensor.grid"])
        self.assertEqual(calls[0]["start_time"], start.isoformat())
        self.assertEqual(calls[0]["end_time"], end.isoformat())

    def test_websocket_authenticates_once_and_uses_monotonic_command_ids(self) -> None:
        sockets = [
            FakeSocket(
                [
                    {"type": "auth_required"},
                    {"type": "auth_ok"},
                    {"id": 1, "type": "result", "success": True, "result": {"one": 1}},
                ]
            ),
            FakeSocket(
                [
                    {"type": "auth_required"},
                    {"type": "auth_ok"},
                    {"id": 2, "type": "result", "success": True, "result": {"two": 2}},
                ]
            ),
        ]

        adapter = SupervisorWebSocket(
            "supervisor-secret",
            socket_factory=lambda url, timeout: sockets.pop(0),
        )

        self.assertEqual(adapter.command({"type": "first"}), {"one": 1})
        first = adapter.last_socket
        self.assertEqual(adapter.command({"type": "second"}), {"two": 2})
        second = adapter.last_socket

        self.assertEqual(first.sent[0], {"type": "auth", "access_token": "supervisor-secret"})
        self.assertEqual(first.sent[1], {"id": 1, "type": "first"})
        self.assertNotIn("access_token", first.sent[1])
        self.assertEqual(second.sent[1], {"id": 2, "type": "second"})

    def test_websocket_errors_never_expose_the_supervisor_token(self) -> None:
        socket = FakeSocket(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "id": 1,
                    "type": "result",
                    "success": False,
                    "error": {"message": "supervisor-secret is invalid"},
                },
            ]
        )
        adapter = SupervisorWebSocket(
            "supervisor-secret",
            socket_factory=lambda url, timeout: socket,
        )

        with self.assertRaisesRegex(HomeAssistantError, "command failed") as raised:
            adapter.command({"type": "energy/get_prefs"})

        self.assertNotIn("supervisor-secret", str(raised.exception))

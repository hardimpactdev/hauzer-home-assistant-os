from datetime import datetime
import json
from typing import Callable, Optional
from urllib.request import Request, urlopen


CORE_API_URL = "http://supervisor/core/api"
CORE_WEBSOCKET_URL = "ws://supervisor/core/websocket"
REQUEST_TIMEOUT = 20.0


class HomeAssistantError(RuntimeError):
    """Raised when Home Assistant cannot complete an exporter request."""


def _default_request_json(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> object:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _default_socket_factory(url: str, timeout: float) -> object:
    from websocket import create_connection

    return create_connection(url, timeout=timeout)


class SupervisorWebSocket:
    def __init__(
        self,
        supervisor_token: str,
        socket_factory: Callable[[str, float], object] = _default_socket_factory,
    ) -> None:
        self._supervisor_token = supervisor_token
        self._socket_factory = socket_factory
        self._next_command_id = 1
        self.last_socket: Optional[object] = None

    def command(self, command: dict[str, object]) -> object:
        socket = self._socket_factory(CORE_WEBSOCKET_URL, REQUEST_TIMEOUT)
        self.last_socket = socket

        try:
            self._expect_message(socket, "auth_required")
            self._send(
                socket,
                {"type": "auth", "access_token": self._supervisor_token},
            )
            self._expect_message(socket, "auth_ok")

            command_id = self._next_command_id
            self._next_command_id += 1
            self._send(socket, {"id": command_id, **command})
            response = self._receive(socket)

            if (
                response.get("type") != "result"
                or response.get("id") != command_id
                or response.get("success") is not True
            ):
                raise HomeAssistantError("Home Assistant command failed.")

            return response.get("result")
        except HomeAssistantError:
            raise
        except Exception as error:
            raise HomeAssistantError("Home Assistant WebSocket request failed.") from error
        finally:
            socket.close()

    @staticmethod
    def _send(socket: object, message: dict[str, object]) -> None:
        socket.send(json.dumps(message))

    @staticmethod
    def _receive(socket: object) -> dict[str, object]:
        message = json.loads(socket.recv())
        if not isinstance(message, dict):
            raise HomeAssistantError("Home Assistant returned an invalid response.")
        return message

    def _expect_message(self, socket: object, expected_type: str) -> None:
        if self._receive(socket).get("type") != expected_type:
            raise HomeAssistantError("Home Assistant authentication failed.")


class HomeAssistantClient:
    def __init__(
        self,
        supervisor_token: str,
        request_json: Callable[[str, dict[str, str], float], object] = _default_request_json,
        websocket_command: Optional[Callable[[dict[str, object]], object]] = None,
    ) -> None:
        self._supervisor_token = supervisor_token
        self._request_json = request_json
        self._websocket_command = websocket_command or SupervisorWebSocket(
            supervisor_token,
        ).command

    def energy_preferences(self) -> dict[str, object]:
        result = self._websocket_command({"type": "energy/get_prefs"})
        if not isinstance(result, dict):
            raise HomeAssistantError("Home Assistant returned invalid energy preferences.")
        return result

    def states(self) -> list[dict[str, object]]:
        result = self._request_json(
            f"{CORE_API_URL}/states",
            {"Authorization": f"Bearer {self._supervisor_token}"},
            REQUEST_TIMEOUT,
        )
        if not isinstance(result, list):
            raise HomeAssistantError("Home Assistant returned invalid states.")
        return result

    def statistics_metadata(self) -> list[dict[str, object]]:
        result = self._websocket_command(
            {"type": "recorder/list_statistic_ids", "statistic_type": "sum"},
        )
        if not isinstance(result, list):
            raise HomeAssistantError("Home Assistant returned invalid statistic metadata.")
        return result

    def statistics_during_period(
        self,
        statistic_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict[str, object]]]:
        result = self._websocket_command(
            {
                "type": "recorder/statistics_during_period",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "statistic_ids": list(statistic_ids),
                "period": "5minute",
                "types": ["sum"],
            },
        )
        if not isinstance(result, dict):
            raise HomeAssistantError("Home Assistant returned invalid statistics.")
        return result

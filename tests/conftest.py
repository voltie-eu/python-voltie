"""Fixtures for the Voltie client tests."""

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
import json
from types import TracebackType
from typing import Any, Self
from unittest.mock import patch

import pytest
from yarl import URL

from voltie import VoltieCharger

HOST = "192.168.1.234"
BASE = f"http://{HOST}:5059"
CHARGER_ID = "000000009d104335"


def status_payload(**overrides: Any) -> dict[str, Any]:
    """Return a /status response as documented in the HTTP API specification."""
    payload: dict[str, Any] = {
        "charger_id": CHARGER_ID,
        "system_time": 1708335549,
        "sw_ver": 1003025,
        "fw_ver": 199,
        "evse_state": 1,
        "is_car_connected": False,
        "charge_enabled": True,
        "is_charging": False,
        "autostart": False,
        "mains_voltage": 233,
        "phases": 3,
        "phases_used": 0,
        "current_hw_limit": 32,
        "current_offered": 32,
        "charge_current": 0,
        "charge_power": 0,
        "first_cdr": 1,
        "last_cdr": 445,
        "cdr": None,
        "response_time_ms": 0,
        "error_code": 0,
    }
    payload.update(overrides)
    return payload


def cdr_payload(**overrides: Any) -> dict[str, Any]:
    """Return a session record as the firmware sends it."""
    payload: dict[str, Any] = {
        "cdr_id": 446,
        "s_start": 1708335000,
        "chg_energy": 5.852,
        "chg_time": 3120,
        "idle_time": 240,
        "avg_power": 6.75,
        "max_power": 7.2,
        "idtag": "0A1B2C3D",
        "idtag_name": "John Doe",
    }
    payload.update(overrides)
    return payload


@dataclass
class Call:
    """A request the client sent."""

    kwargs: dict[str, Any]


@dataclass
class _Reply:
    status: int
    body: bytes
    exception: BaseException | None
    repeat: bool


class _Response:
    def __init__(self, reply: _Reply) -> None:
        self.status = reply.status
        self._body = reply.body

    async def read(self) -> bytes:
        return self._body


class _RequestContext:
    def __init__(self, reply: _Reply) -> None:
        self._reply = reply

    async def __aenter__(self) -> _Response:
        if self._reply.exception is not None:
            raise self._reply.exception
        return _Response(self._reply)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


@dataclass
class FakeSession:
    """Stands in for aiohttp.ClientSession, answering from registered replies."""

    replies: dict[tuple[str, URL], list[_Reply]] = field(
        default_factory=lambda: defaultdict(list)
    )
    requests: dict[tuple[str, URL], list[Call]] = field(
        default_factory=lambda: defaultdict(list)
    )
    closed: bool = False

    def _add(
        self,
        method: str,
        url: str,
        *,
        payload: Any = None,
        body: str | None = None,
        status: int = 200,
        exception: BaseException | None = None,
        repeat: bool = False,
    ) -> None:
        raw = body.encode() if body is not None else json.dumps(payload).encode()
        self.replies[(method, URL(url))].append(_Reply(status, raw, exception, repeat))

    def get(self, url: str, **kwargs: Any) -> None:
        """Register a reply to a GET request."""
        self._add("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> None:
        """Register a reply to a POST request."""
        self._add("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> None:
        """Register a reply to a PUT request."""
        self._add("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> None:
        """Register a reply to a DELETE request."""
        self._add("DELETE", url, **kwargs)

    def request(self, method: str, url: URL, **kwargs: Any) -> _RequestContext:
        """Answer a request with the next registered reply."""
        if params := kwargs.get("params"):
            url = url.with_query(params)
        key = (method, url)
        self.requests[key].append(Call(kwargs))
        queue = self.replies[key]
        if not queue:
            msg = f"No reply registered for {method} {url}"
            raise AssertionError(msg)
        reply = queue[0] if queue[0].repeat else queue.pop(0)
        return _RequestContext(reply)

    async def close(self) -> None:
        """Close the session."""
        self.closed = True

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """Leave the async context."""
        await self.close()


@pytest.fixture
def mock_api() -> Iterator[FakeSession]:
    """Return a fake HTTP session, also used by clients that open their own."""
    session = FakeSession()
    with patch("voltie.charger.aiohttp.ClientSession", return_value=session):
        yield session


@pytest.fixture
def charger(mock_api: FakeSession) -> VoltieCharger:
    """Return a client without retries, so failures surface at once."""
    return VoltieCharger(HOST, session=mock_api, retries=0)  # type: ignore[arg-type]

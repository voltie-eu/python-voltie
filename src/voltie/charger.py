"""Asynchronous client for the Voltie charger's local HTTP API."""

import asyncio
import base64
from collections.abc import Mapping
from http import HTTPStatus
import json
import logging
import re
from typing import Any, Self

import aiohttp
from yarl import URL

from .const import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    AccessMode,
    DlmMode,
    Endpoint,
    ForceSinglePhase,
)
from .exceptions import (
    VoltieAuthenticationError,
    VoltieCommandError,
    VoltieConnectionError,
    VoltieUnsupportedError,
)
from .models import (
    CONFIG_KEYS,
    ChargingSession,
    Config,
    Power,
    RfidStatus,
    RfidTagList,
    Status,
)

_LOGGER = logging.getLogger(__name__)

# Error codes of the HTTP API; 0 is success.
_ERROR_MESSAGES: Mapping[int, str] = {
    1: "command not possible in the current state",
    5: "incorrect message format or parameter",
    23: "not the main unit of its prepaid RFID cluster",
    24: "unknown command",
}
# Only this code means the firmware cannot do what was asked. Code 23 comes
# from current firmware too: the request must go to another unit instead.
_UNSUPPORTED_ERROR_CODE = 24

# Fields some firmware versions leave out of a status response now and then.
# The last value seen stands in for a missing one, so a single short response
# does not make the charger look unknown for a whole poll interval.
_STICKY_STATUS_FIELDS = (
    "evse_state",
    "is_car_connected",
    "is_charging",
    "charge_enabled",
)

_COLOR_RE = re.compile(r"#?(?P<hex>[0-9A-Fa-f]{6})")


class VoltieCharger:
    """A Voltie charger, reached over its local HTTP API."""

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
        session: aiohttp.ClientSession | None = None,
        request_timeout: float = DEFAULT_TIMEOUT,
        retries: int = 1,
    ) -> None:
        """Initialize the client.

        Credentials are only needed when HTTP API authentication is enabled in
        the Voltie app. Read requests are retried `retries` times when the
        charger cannot be reached; commands are never retried.
        """
        self.host = host
        self.port = port
        self._headers: dict[str, str] = {}
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"
        self._session = session
        self._close_session = False
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._retries = retries
        self._last_status: dict[str, Any] = {}

    @property
    def url(self) -> URL:
        """The base URL of the charger's HTTP API."""
        return URL.build(scheme="http", host=self.host, port=self.port)

    async def _request(
        self,
        method: str,
        endpoint: Endpoint,
        *,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request, retrying reads that fail to connect."""
        attempts = self._retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                return await self._request_once(method, endpoint, params, body)
            except VoltieConnectionError as err:
                if attempt + 1 == attempts:
                    raise
                _LOGGER.debug("Retrying %s %s after: %s", method, endpoint, err)
                await asyncio.sleep(1)
        raise AssertionError  # pragma: no cover

    async def _request_once(
        self,
        method: str,
        endpoint: Endpoint,
        params: Mapping[str, str] | None,
        body: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._close_session = True

        try:
            async with self._session.request(
                method,
                self.url / endpoint,
                headers=self._headers,
                params=params,
                json=body,
                timeout=self._timeout,
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise VoltieAuthenticationError(
                        "The charger rejected the HTTP API credentials"
                    )
                if response.status in (
                    HTTPStatus.NOT_FOUND,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                ):
                    raise VoltieUnsupportedError(
                        f"The charger's firmware has no /{endpoint} endpoint"
                    )
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise VoltieConnectionError(
                        f"The charger answered /{endpoint} with HTTP {response.status}"
                    )
                raw = await response.read()
        except TimeoutError as err:
            raise VoltieConnectionError(
                f"Timed out waiting for the charger to answer /{endpoint}"
            ) from err
        except aiohttp.ClientError as err:
            raise VoltieConnectionError(
                f"Could not talk to the charger: {err}"
            ) from err

        try:
            payload = json.loads(raw) if raw else {}
        except ValueError as err:
            raise VoltieConnectionError(
                f"The charger answered /{endpoint} with something other than JSON"
            ) from err
        if not isinstance(payload, dict):
            raise VoltieConnectionError(
                f"The charger answered /{endpoint} with an unexpected response"
            )

        _raise_for_error_code(endpoint, payload)
        return payload

    async def status(self) -> Status:
        """Return the charger's live status."""
        payload = await self._request("GET", Endpoint.STATUS)
        if not payload.get("charger_id"):
            raise VoltieConnectionError("The charger did not report its ID")
        for key in _STICKY_STATUS_FIELDS:
            if payload.get(key) is None and key in self._last_status:
                payload[key] = self._last_status[key]
        self._last_status = {
            key: payload[key]
            for key in _STICKY_STATUS_FIELDS
            if payload.get(key) is not None
        }
        return Status.from_dict(payload)

    async def power(self) -> Power:
        """Return the per-phase measurements."""
        return Power.from_dict(await self._request("GET", Endpoint.POWER))

    async def config(self) -> Config:
        """Return the charger's settings."""
        return Config.from_dict(await self._request("GET", Endpoint.CONFIG))

    async def set_config(
        self,
        *,
        autostart_enabled: bool | None = None,
        current_limit: int | None = None,
        display_enabled: bool | None = None,
        front_led_enabled: bool | None = None,
        rear_led_enabled: bool | None = None,
        buzzer_enabled: bool | None = None,
        force_single_phase: ForceSinglePhase | None = None,
        dlm_mode: DlmMode | None = None,
        dlm_current_limit: int | None = None,
        dlm_eco_start_current: int | None = None,
        grid_stop_voltage: int | None = None,
        grid_min_voltage: int | None = None,
        grid_max_voltage: int | None = None,
        grid_increase_delay: int | None = None,
        grid_decrease_delay: int | None = None,
        out_of_service: bool | None = None,
        access_mode: AccessMode | None = None,
    ) -> None:
        """Change settings; the ones left at None stay as they are.

        The charger drops a value it does not accept (out of range, not
        supported by the hardware, or not changeable in the current state) and
        only reports how many it took, so a shortfall raises.
        """
        values = {
            "autostart_enabled": autostart_enabled,
            "current_limit": current_limit,
            "display_enabled": display_enabled,
            "front_led_enabled": front_led_enabled,
            "rear_led_enabled": rear_led_enabled,
            "buzzer_enabled": buzzer_enabled,
            "force_single_phase": force_single_phase,
            "dlm_mode": dlm_mode,
            "dlm_current_limit": dlm_current_limit,
            "dlm_eco_start_current": dlm_eco_start_current,
            "grid_stop_voltage": grid_stop_voltage,
            "grid_min_voltage": grid_min_voltage,
            "grid_max_voltage": grid_max_voltage,
            "grid_increase_delay": grid_increase_delay,
            "grid_decrease_delay": grid_decrease_delay,
            "out_of_service": out_of_service,
            "access_mode": access_mode,
        }
        body = {
            CONFIG_KEYS[name]: int(value) if not isinstance(value, bool) else value
            for name, value in values.items()
            if value is not None
        }
        if not body:
            raise ValueError("No setting to change")
        result = await self._request("PUT", Endpoint.CONFIG, body=body)
        accepted = result.get("accepted")
        if isinstance(accepted, int) and accepted < len(body):
            raise VoltieCommandError(
                f"The charger refused {len(body) - accepted} of {len(body)} settings,"
                " a value outside the range its firmware allows or a setting it"
                " cannot change at the moment"
            )

    async def start(
        self, *, id_tag: str | None = None, name: str | None = None
    ) -> None:
        """Allow charging, optionally recording a tag and name in the session."""
        params = {
            key: value for key, value in (("id_tag", id_tag), ("name", name)) if value
        }
        await self._request("GET", Endpoint.START, params=params or None)

    async def stop(self) -> None:
        """Stop charging."""
        await self._request("GET", Endpoint.STOP)

    async def session(self, cdr_id: int) -> ChargingSession | None:
        """Return a stored session record, or None if the charger has none."""
        payload = await self._request(
            "GET", Endpoint.CDR, params={"cdr_id": str(cdr_id)}
        )
        return ChargingSession.from_dict(payload.get("cdr"))

    async def api_version(self) -> int | None:
        """Return the major HTTP API version, or None if it is not reported.

        Firmware from before the version endpoint existed reports none.
        """
        try:
            payload = await self._request("GET", Endpoint.API_VERSION)
        except VoltieUnsupportedError:
            return None
        value = payload.get("api_files_version")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return int(value)

    async def _command(
        self,
        endpoint: Endpoint,
        command: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {"command": command}
        if params:
            body["params"] = dict(params)
        await self._request("POST", endpoint, body=body)

    async def display_text(
        self,
        message: str,
        *,
        repeat_count: int | None = None,
        clear_first: bool | None = None,
    ) -> None:
        """Scroll a message (1 to 100 characters) across the display."""
        params: dict[str, Any] = {"message": message}
        if repeat_count is not None:
            params["repeat_count"] = repeat_count
        if clear_first is not None:
            params["clear_first"] = clear_first
        await self._command(Endpoint.EXTRAS, "display_scroll_text", params)

    async def set_rear_led(
        self,
        *,
        brightness: float,
        color: str,
        duration: int | None = None,
    ) -> None:
        """Set the rear LED for `duration` seconds (at most an hour).

        The color is a hex value such as #FF8800; brightness runs from 0 to 1.
        """
        if (match := _COLOR_RE.fullmatch(color.strip())) is None:
            raise ValueError(f"{color!r} is not a hex RGB color")
        params: dict[str, Any] = {
            "brightness": brightness,
            "color_rgb": f"#{match['hex'].upper()}",
        }
        if duration is not None:
            params["duration_sec"] = duration
        await self._command(Endpoint.EXTRAS, "rear_led_set", params)

    async def reboot(self) -> None:
        """Restart the charger; it drops off the network for a few seconds."""
        await self._command(Endpoint.EXTRAS, "charger_reboot")

    async def rfid_status(self) -> RfidStatus:
        """Return the RFID reader state; needs HTTP API version 5."""
        payload = await self._request("GET", Endpoint.RFID_STATUS)
        status = payload.get("rfid_status")
        if not isinstance(status, Mapping):
            raise VoltieConnectionError("The charger did not report its RFID status")
        return RfidStatus.from_dict(status)

    async def rfid_tags(
        self, *, first_item: int | None = None, max_count: int | None = None
    ) -> RfidTagList:
        """Return stored RFID tags; max_count 0 returns all of them."""
        params = {
            key: str(value)
            for key, value in (("first_item", first_item), ("max_count", max_count))
            if value is not None
        }
        return RfidTagList.from_dict(
            await self._request("GET", Endpoint.RFID, params=params or None)
        )

    async def add_rfid_tag(
        self,
        tag_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        comment: str | None = None,
    ) -> None:
        """Store a new RFID tag; the ID is 8 to 20 hexadecimal characters."""
        await self._request(
            "POST",
            Endpoint.RFID,
            body=_rfid_body(tag_id, name=name, enabled=enabled, comment=comment),
        )

    async def modify_rfid_tag(
        self,
        tag_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        comment: str | None = None,
    ) -> None:
        """Change a stored RFID tag; fields left at None stay as they are."""
        await self._request(
            "PUT",
            Endpoint.RFID,
            body=_rfid_body(tag_id, name=name, enabled=enabled, comment=comment),
        )

    async def delete_rfid_tag(self, tag_id: str) -> None:
        """Remove a stored RFID tag."""
        await self._request("DELETE", Endpoint.RFID, params={"id": tag_id})

    async def rfid_learn(
        self, *, duration: int | None = None, count_max: int | None = None
    ) -> None:
        """Store the tags presented to the reader for `duration` seconds.

        Without arguments the firmware defaults apply: 30 seconds, no limit on
        the number of tags.
        """
        params = {
            key: value
            for key, value in (("timeout_sec", duration), ("count_max", count_max))
            if value is not None
        }
        await self._command(Endpoint.RFID_EXTRAS, "rfid_learn", params)

    async def rfid_learn_cancel(self) -> None:
        """End RFID learn mode."""
        await self._command(Endpoint.RFID_EXTRAS, "rfid_learn_cancel")

    async def close(self) -> None:
        """Close the HTTP session, if this client opened it."""
        if self._session is not None and self._close_session:
            await self._session.close()

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """Leave the async context, closing the session this client opened."""
        await self.close()


def _raise_for_error_code(endpoint: Endpoint, payload: Mapping[str, Any]) -> None:
    raw_code = payload.get("error_code")
    if raw_code is None:
        # Some failures answer with a status text instead of an error code.
        if isinstance(status := payload.get("status"), str) and status:
            raise VoltieConnectionError(
                f"The charger could not answer /{endpoint}: {status}"
            )
        return
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = None
    if code is None or isinstance(raw_code, bool):
        raise VoltieConnectionError(
            f"The charger answered /{endpoint} with error code {raw_code!r}"
        )
    if code == 0:
        return
    message = f"The charger refused /{endpoint}: " + _ERROR_MESSAGES.get(
        code, f"error code {code}"
    )
    if code == _UNSUPPORTED_ERROR_CODE:
        raise VoltieUnsupportedError(message, code=code)
    raise VoltieCommandError(message, code=code)


def _rfid_body(
    tag_id: str,
    *,
    name: str | None,
    enabled: bool | None,
    comment: str | None,
) -> dict[str, Any]:
    # The charger reads an absent field as "leave unchanged".
    fields = {"name": name, "enabled": enabled, "comment": comment}
    return {"id": tag_id} | {
        key: value for key, value in fields.items() if value is not None
    }

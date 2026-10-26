"""Tests for the HTTP client."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from yarl import URL

from voltie import (
    AccessMode,
    DlmMode,
    ForceSinglePhase,
    VoltieAuthenticationError,
    VoltieCharger,
    VoltieCommandError,
    VoltieConnectionError,
    VoltieUnsupportedError,
)

from .conftest import (
    BASE,
    CHARGER_ID,
    HOST,
    FakeSession,
    cdr_payload,
    status_payload,
)


def ack(**overrides: Any) -> dict[str, Any]:
    """Return a command acknowledgement."""
    return {"response_time_ms": 0, "error_code": 0, **overrides}


def sent_json(mock_api: FakeSession, method: str, url: str) -> Any:
    """Return the JSON body of the last request to the URL."""
    return mock_api.requests[(method, URL(url))][-1].kwargs["json"]


async def test_status(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(f"{BASE}/status", payload=status_payload())
    status = await charger.status()
    assert status.charger_id == CHARGER_ID
    assert status.software_version_string == "1.3.25"
    assert status.firmware_version_string == "1.99"
    assert status.mains_voltage == 233
    assert status.phases == 3
    assert status.session is None
    assert status.session_known
    assert not status.meter_supported
    assert status.evse_state is not None
    assert not status.problem


async def test_status_with_session_and_meter(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    mock_api.get(
        f"{BASE}/status",
        payload=status_payload(evse_state=3, cdr=cdr_payload(), meter_kwh=1523.4),
    )
    status = await charger.status()
    assert status.session is not None
    assert status.session.started_at == datetime(2024, 2, 19, 9, 30, tzinfo=UTC)
    assert status.session.energy == 5.852
    assert status.meter_energy == 1523.4


async def test_status_without_charger_id(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    mock_api.get(f"{BASE}/status", payload=status_payload(charger_id=""))
    with pytest.raises(VoltieConnectionError):
        await charger.status()


async def test_status_keeps_dropped_fields(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    """A field the firmware leaves out keeps its last value."""
    mock_api.get(f"{BASE}/status", payload=status_payload(evse_state=3))
    payload = status_payload()
    del payload["evse_state"]
    payload["is_charging"] = None
    mock_api.get(f"{BASE}/status", payload=payload)
    await charger.status()
    status = await charger.status()
    assert status.evse_state_code == 3
    assert status.charging is False


async def test_port_in_url(mock_api: FakeSession) -> None:
    mock_api.get(f"http://{HOST}:8080/status", payload=status_payload())
    charger = VoltieCharger(HOST, port=8080, session=mock_api)  # type: ignore[arg-type]
    assert (await charger.status()).charger_id == CHARGER_ID
    assert str(charger.url) == f"http://{HOST}:8080"


async def test_credentials_are_sent(mock_api: FakeSession) -> None:
    mock_api.get(f"{BASE}/status", payload=status_payload())
    charger = VoltieCharger(
        HOST,
        username="u",
        password="p",
        session=mock_api,  # type: ignore[arg-type]
    )
    await charger.status()
    request = mock_api.requests[("GET", URL(f"{BASE}/status"))][0]
    assert request.kwargs["headers"] == {"Authorization": "Basic dTpw"}


@pytest.mark.parametrize("status", [401, 403])
async def test_authentication_error(
    mock_api: FakeSession, charger: VoltieCharger, status: int
) -> None:
    mock_api.get(f"{BASE}/status", status=status)
    with pytest.raises(VoltieAuthenticationError):
        await charger.status()


@pytest.mark.parametrize("status", [404, 405])
async def test_missing_endpoint(
    mock_api: FakeSession, charger: VoltieCharger, status: int
) -> None:
    mock_api.get(f"{BASE}/rfid/status", status=status)
    with pytest.raises(VoltieUnsupportedError):
        await charger.rfid_status()


async def test_server_error(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(f"{BASE}/status", status=500)
    with pytest.raises(VoltieConnectionError, match="HTTP 500"):
        await charger.status()


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), aiohttp.ClientConnectionError("refused")],
)
async def test_connection_errors(
    mock_api: FakeSession, charger: VoltieCharger, error: Exception
) -> None:
    mock_api.get(f"{BASE}/status", exception=error)
    with pytest.raises(VoltieConnectionError):
        await charger.status()


async def test_reads_are_retried(mock_api: FakeSession) -> None:
    mock_api.get(f"{BASE}/status", exception=TimeoutError())
    mock_api.get(f"{BASE}/status", payload=status_payload())
    charger = VoltieCharger(HOST, session=mock_api)  # type: ignore[arg-type]
    with patch("voltie.charger.asyncio.sleep") as sleep:
        assert (await charger.status()).charger_id == CHARGER_ID
    sleep.assert_awaited_once_with(1)


async def test_commands_are_not_retried(mock_api: FakeSession) -> None:
    mock_api.post(f"{BASE}/extras", exception=TimeoutError())
    charger = VoltieCharger(HOST, session=mock_api)  # type: ignore[arg-type]
    with pytest.raises(VoltieConnectionError):
        await charger.reboot()


@pytest.mark.parametrize(
    "body",
    ["<html>nope</html>", "[1, 2]"],
)
async def test_unusable_response(
    mock_api: FakeSession, charger: VoltieCharger, body: str
) -> None:
    mock_api.get(f"{BASE}/status", body=body)
    with pytest.raises(VoltieConnectionError):
        await charger.status()


async def test_empty_response_is_success(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    mock_api.get(f"{BASE}/stop", body="")
    await charger.stop()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (1, VoltieCommandError),
        (5, VoltieCommandError),
        (23, VoltieCommandError),
        (99, VoltieCommandError),
        (24, VoltieUnsupportedError),
    ],
)
async def test_error_codes(
    mock_api: FakeSession,
    charger: VoltieCharger,
    code: int,
    expected: type[Exception],
) -> None:
    mock_api.post(f"{BASE}/extras", payload=ack(error_code=code))
    with pytest.raises(expected) as err:
        await charger.reboot()
    assert isinstance(err.value, VoltieCommandError)
    assert err.value.code == code


@pytest.mark.parametrize("code", ["0", 0])
async def test_success_codes(
    mock_api: FakeSession, charger: VoltieCharger, code: object
) -> None:
    mock_api.post(f"{BASE}/extras", payload=ack(error_code=code))
    await charger.reboot()


@pytest.mark.parametrize("code", ["junk", True, [1]])
async def test_malformed_error_code(
    mock_api: FakeSession, charger: VoltieCharger, code: object
) -> None:
    mock_api.post(f"{BASE}/extras", payload=ack(error_code=code))
    with pytest.raises(VoltieConnectionError):
        await charger.reboot()


@pytest.mark.parametrize(
    "payload",
    [{"status": "internal timeout"}, {"error_code": None, "status": "busy"}],
)
async def test_status_text_without_error_code(
    mock_api: FakeSession, charger: VoltieCharger, payload: dict[str, Any]
) -> None:
    mock_api.get(f"{BASE}/power", payload=payload)
    with pytest.raises(VoltieConnectionError):
        await charger.power()


async def test_status_text_with_error_code(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    """A normal payload that has a status field as data is not an error."""
    mock_api.get(f"{BASE}/status", payload=status_payload(status="charging"))
    await charger.status()


async def test_power(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(
        f"{BASE}/power",
        payload={
            "power_stat": {
                "voltage1": 231.461,
                "current1": 15.9,
                "power1": 3.68,
                "dlm_valid": True,
                "dlm_current1": -14.186,
                "ipm_valid": 0,
            },
            "error_code": 0,
        },
    )
    power = await charger.power()
    assert power.voltage_l1 == 231.461
    assert power.voltage_l2 is None
    assert power.current_l1 == 15.9
    assert power.power_l1 == 3.68
    assert power.dlm_valid is True
    assert power.dlm_current_l1 == -14.186
    assert power.ipm_valid is False


async def test_config(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(
        f"{BASE}/config",
        payload=ack(
            conf_current_limit=16,
            conf_dlm_mode=1,
            conf_access_mode=1,
            conf_force_single_phase=2,
            conf_autostart_enabled=True,
        ),
    )
    config = await charger.config()
    assert config.current_limit == 16
    assert config.dlm_mode is DlmMode.DYNAMIC
    assert config.access_mode is AccessMode.HOME_CHARGER_RFID
    assert config.force_single_phase is ForceSinglePhase.NOT_SUPPORTED
    assert config.autostart_enabled is True
    assert config.buzzer_enabled is None
    assert config.supported == {
        "current_limit",
        "dlm_mode",
        "access_mode",
        "force_single_phase",
        "autostart_enabled",
    }


async def test_set_config(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.put(f"{BASE}/config", payload=ack(accepted=3))
    await charger.set_config(
        current_limit=16, dlm_mode=DlmMode.ECO, buzzer_enabled=False
    )
    assert sent_json(mock_api, "PUT", f"{BASE}/config") == {
        "conf_current_limit": 16,
        "conf_dlm_mode": 2,
        "conf_buzzer_enabled": False,
    }


async def test_set_config_partly_accepted(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    mock_api.put(f"{BASE}/config", payload=ack(accepted=1))
    with pytest.raises(VoltieCommandError, match="1 of 2"):
        await charger.set_config(current_limit=16, dlm_mode=DlmMode.OFF)


async def test_set_config_nothing(charger: VoltieCharger) -> None:
    with pytest.raises(ValueError, match="No setting"):
        await charger.set_config()


async def test_start_and_stop(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(f"{BASE}/start", payload=ack())
    mock_api.get(f"{BASE}/start?id_tag=0A1B2C3D&name=Guest", payload=ack())
    mock_api.get(f"{BASE}/stop", payload=ack())
    await charger.start()
    await charger.start(id_tag="0A1B2C3D", name="Guest")
    await charger.stop()
    assert ("GET", URL(f"{BASE}/start?id_tag=0A1B2C3D&name=Guest")) in (
        mock_api.requests
    )


async def test_session(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(
        f"{BASE}/cdr?cdr_id=446",
        payload=ack(cdr=cdr_payload(s_end=1708338360, closed_after_restart=True)),
    )
    mock_api.get(f"{BASE}/cdr?cdr_id=1", payload=ack(cdr=None))
    session = await charger.session(446)
    assert session is not None
    assert session.cdr_id == 446
    assert session.ended_at == datetime(2024, 2, 19, 10, 26, tzinfo=UTC)
    assert session.charge_time == 3120
    assert session.id_tag_name == "John Doe"
    assert session.closed_after_restart
    assert await charger.session(1) is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"api_files_version": 5}, 5), ({"api_files_version": "5"}, None), ({}, None)],
)
async def test_api_version(
    mock_api: FakeSession,
    charger: VoltieCharger,
    payload: dict[str, Any],
    expected: int | None,
) -> None:
    mock_api.get(f"{BASE}/apiver", payload=payload)
    assert await charger.api_version() == expected


async def test_api_version_missing_endpoint(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    mock_api.get(f"{BASE}/apiver", status=404)
    assert await charger.api_version() is None


async def test_extras_commands(mock_api: FakeSession, charger: VoltieCharger) -> None:
    url = f"{BASE}/extras"
    mock_api.post(url, payload=ack(), repeat=True)

    await charger.display_text("hi", repeat_count=3, clear_first=True)
    assert sent_json(mock_api, "POST", url) == {
        "command": "display_scroll_text",
        "params": {"message": "hi", "repeat_count": 3, "clear_first": True},
    }

    await charger.display_text("hi")
    assert sent_json(mock_api, "POST", url) == {
        "command": "display_scroll_text",
        "params": {"message": "hi"},
    }

    await charger.set_rear_led(brightness=0.5, color="ff8800", duration=60)
    assert sent_json(mock_api, "POST", url) == {
        "command": "rear_led_set",
        "params": {"brightness": 0.5, "color_rgb": "#FF8800", "duration_sec": 60},
    }

    await charger.set_rear_led(brightness=1, color="#00FF00")
    assert sent_json(mock_api, "POST", url) == {
        "command": "rear_led_set",
        "params": {"brightness": 1, "color_rgb": "#00FF00"},
    }

    await charger.reboot()
    assert sent_json(mock_api, "POST", url) == {"command": "charger_reboot"}


async def test_rear_led_bad_color(charger: VoltieCharger) -> None:
    with pytest.raises(ValueError, match="not a hex RGB color"):
        await charger.set_rear_led(brightness=1, color="orange")


async def test_rfid_status(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(
        f"{BASE}/rfid/status",
        payload=ack(
            rfid_status={
                "list_count": 5,
                "list_capacity": 200,
                "list_format_ver": 3,
                "list_hash": "A3F21B8C",
                "reader_enabled": True,
                "reader_working": True,
                "learn_in_progress": False,
                "learn_to_sec": 0,
            }
        ),
    )
    status = await charger.rfid_status()
    assert status.list_count == 5
    assert status.list_hash == "A3F21B8C"
    assert status.learn_in_progress is False


async def test_rfid_status_without_block(
    mock_api: FakeSession, charger: VoltieCharger
) -> None:
    mock_api.get(f"{BASE}/rfid/status", payload=ack(list_count=5))
    with pytest.raises(VoltieConnectionError):
        await charger.rfid_status()


async def test_rfid_tags(mock_api: FakeSession, charger: VoltieCharger) -> None:
    mock_api.get(
        f"{BASE}/rfid?first_item=0&max_count=10",
        payload=ack(
            rfid_list=[
                {
                    "id": "0A1B2C3D",
                    "name": "John Doe",
                    "last_use": 1708336326,
                    "enabled": True,
                    "comment": "",
                },
                {"name": "no id"},
                "junk",
            ],
            rfid_count=12,
            list_hash="A3F21B8C",
        ),
    )
    mock_api.get(f"{BASE}/rfid", payload=ack(rfid_list=None, rfid_count=3))
    tags = await charger.rfid_tags(first_item=0, max_count=10)
    assert tags.total == 12
    assert tags.list_hash == "A3F21B8C"
    assert len(tags.tags) == 1
    assert tags.tags[0].id == "0A1B2C3D"
    assert tags.tags[0].comment is None
    assert tags.tags[0].last_used == datetime(2024, 2, 19, 9, 52, 6, tzinfo=UTC)

    empty = await charger.rfid_tags()
    assert empty.tags == []
    assert empty.total == 0


async def test_rfid_tag_changes(mock_api: FakeSession, charger: VoltieCharger) -> None:
    url = f"{BASE}/rfid"
    mock_api.post(url, payload=ack())
    mock_api.put(url, payload=ack())
    mock_api.delete(f"{url}?id=0A1B2C3D", payload=ack())

    await charger.add_rfid_tag("0A1B2C3D", name="John", enabled=True, comment="x")
    assert sent_json(mock_api, "POST", url) == {
        "id": "0A1B2C3D",
        "name": "John",
        "enabled": True,
        "comment": "x",
    }

    await charger.modify_rfid_tag("0A1B2C3D", enabled=False)
    assert sent_json(mock_api, "PUT", url) == {"id": "0A1B2C3D", "enabled": False}

    await charger.delete_rfid_tag("0A1B2C3D")
    assert ("DELETE", URL(f"{url}?id=0A1B2C3D")) in mock_api.requests


async def test_rfid_learn(mock_api: FakeSession, charger: VoltieCharger) -> None:
    url = f"{BASE}/rfid/extras"
    mock_api.post(url, payload=ack(), repeat=True)

    await charger.rfid_learn()
    assert sent_json(mock_api, "POST", url) == {"command": "rfid_learn"}

    await charger.rfid_learn(duration=60, count_max=2)
    assert sent_json(mock_api, "POST", url) == {
        "command": "rfid_learn",
        "params": {"timeout_sec": 60, "count_max": 2},
    }

    await charger.rfid_learn_cancel()
    assert sent_json(mock_api, "POST", url) == {"command": "rfid_learn_cancel"}


async def test_own_session(mock_api: FakeSession) -> None:
    """Without a session the client opens one, and closes it again."""
    mock_api.get(f"{BASE}/status", payload=status_payload())
    async with VoltieCharger(HOST) as charger:
        await charger.status()
        session = charger._session
        assert session is not None
    assert session.closed


async def test_shared_session_stays_open(mock_api: FakeSession) -> None:
    mock_api.get(f"{BASE}/status", payload=status_payload())
    async with VoltieCharger(HOST, session=mock_api) as charger:  # type: ignore[arg-type]
        await charger.status()
    assert not mock_api.closed

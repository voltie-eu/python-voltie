"""Tests for response parsing."""

from typing import Any

import pytest

from voltie import (
    ChargingSession,
    Config,
    EvseState,
    Power,
    RfidTag,
    RfidTagList,
    Status,
)

from .conftest import cdr_payload, status_payload


@pytest.mark.parametrize(
    ("code", "state", "problem"),
    [
        (0, EvseState.UNKNOWN, False),
        (1, EvseState.EV_NOT_CONNECTED, False),
        (3, EvseState.EV_CONNECTED_CHARGING, False),
        (7, EvseState.NO_GROUND, True),
        (13, EvseState.EV_FAULT, True),
        (18, EvseState.DISABLED, False),
        (99, None, True),
        (None, None, None),
        ("3", None, None),
        (True, None, None),
    ],
)
def test_evse_state(code: Any, state: EvseState | None, problem: bool | None) -> None:
    status = Status.from_dict(status_payload(evse_state=code))
    assert status.evse_state is state
    assert status.problem is problem


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), (3, 3), (3.0, 3), (2, None), (True, None), ("3", None), (None, None)],
)
def test_phases(value: Any, expected: int | None) -> None:
    assert Status.from_dict(status_payload(phases=value)).phases == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5.5, 5.5),
        (1, 1.0),
        (float("nan"), None),
        (float("inf"), None),
        ("12", None),
        (False, None),
        ({}, None),
    ],
)
def test_numbers(value: Any, expected: float | None) -> None:
    assert Status.from_dict(status_payload(charge_power=value)).charge_power == expected


@pytest.mark.parametrize(
    ("payload", "meter_energy", "supported"),
    [
        ({"meter_kwh": 1523.4}, 1523.4, True),
        ({"meter_kwh": -1}, None, True),
        ({"meter_kwh": None}, None, True),
        ({}, None, False),
    ],
)
def test_meter(
    payload: dict[str, Any], meter_energy: float | None, supported: bool
) -> None:
    status = Status.from_dict(status_payload(**payload))
    assert status.meter_energy == meter_energy
    assert status.meter_supported is supported


@pytest.mark.parametrize(
    ("payload", "has_session", "known"),
    [
        ({"cdr": None}, False, True),
        ({"cdr": cdr_payload()}, True, True),
        ({"cdr": cdr_payload(s_start=0)}, False, False),
        ({"cdr": "junk"}, False, False),
    ],
)
def test_session_known(payload: dict[str, Any], has_session: bool, known: bool) -> None:
    status = Status.from_dict(status_payload(**payload))
    assert (status.session is not None) is has_session
    assert status.session_known is known


def test_session_missing() -> None:
    payload = status_payload()
    del payload["cdr"]
    assert not Status.from_dict(payload).session_known


def test_version_strings_missing() -> None:
    status = Status.from_dict(status_payload(sw_ver=None, fw_ver="1"))
    assert status.software_version_string is None
    assert status.firmware_version_string is None


def test_session_values() -> None:
    session = ChargingSession.from_dict(
        cdr_payload(chg_energy=-1, chg_time=12.0, idle_time="1", idtag="")
    )
    assert session is not None
    assert session.energy is None
    assert session.charge_time == 12
    assert session.idle_time is None
    assert session.id_tag is None
    assert not session.closed_after_restart


@pytest.mark.parametrize("value", [None, [], "x", {"cdr_id": 1}])
def test_not_a_session(value: Any) -> None:
    assert ChargingSession.from_dict(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (0, False), (1, True), (2, None), ("true", None)],
)
def test_config_booleans(value: Any, expected: bool | None) -> None:
    assert Config.from_dict({"conf_buzzer_enabled": value}).buzzer_enabled is expected


def test_config_unknown_enum() -> None:
    config = Config.from_dict({"conf_dlm_mode": 9, "conf_access_mode": "1"})
    assert config.dlm_mode is None
    assert config.access_mode is None
    assert config.supported == {"dlm_mode", "access_mode"}


def test_config_fractional_integer() -> None:
    assert Config.from_dict({"conf_current_limit": 16.5}).current_limit is None


def test_power_without_block() -> None:
    assert Power.from_dict({"error_code": 0}).voltage_l1 is None


def test_rfid_tag_without_id() -> None:
    assert RfidTag.from_dict({"id": ""}) is None


def test_rfid_list_count_below_page() -> None:
    tags = RfidTagList.from_dict({"rfid_list": [{"id": "0A1B2C3D"}], "rfid_count": 0})
    assert tags.total == 1

"""Typed views of the charger's HTTP API responses.

The firmware leaves fields out depending on hardware and version, and now and
then reports a value of the wrong type. Every field is therefore optional, and
a value that does not have the documented type reads as None rather than
raising: one odd field must not make the whole response unusable.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
from typing import Any, Self

from .const import (
    NON_PROBLEM_STATES,
    AccessMode,
    DlmMode,
    EvseState,
    ForceSinglePhase,
)


def _float(value: Any) -> float | None:
    """Return a finite number as float, anything else as None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    """Return a whole number as int, anything else as None."""
    number = _float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _non_negative(value: Any) -> float | None:
    number = _float(value)
    return number if number is not None and number >= 0 else None


def _bool(value: Any) -> bool | None:
    """Return a boolean, accepting the 0 and 1 some firmware sends."""
    if isinstance(value, bool):
        return value
    if (number := _int(value)) in (0, 1):
        return bool(number)
    return None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: Any) -> datetime | None:
    """Return a Unix timestamp as an aware datetime; 0 means not set."""
    seconds = _float(value)
    if not seconds or seconds < 0:
        return None
    return datetime.fromtimestamp(seconds, UTC)


def _enum[EnumT: (EvseState, DlmMode, AccessMode, ForceSinglePhase)](
    enum: type[EnumT], value: Any
) -> EnumT | None:
    if (number := _int(value)) is None:
        return None
    try:
        return enum(number)
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class ChargingSession:
    """A charging session (charge detail record, CDR).

    The charger keeps one record per session, from plugging in to unplugging.
    The active session is part of the status; closed ones are read by ID.
    """

    cdr_id: int | None
    started_at: datetime
    ended_at: datetime | None
    energy: float | None
    """Energy delivered in kWh."""
    charge_time: int | None
    """Seconds spent charging."""
    idle_time: int | None
    """Seconds connected without charging."""
    average_power: float | None
    """Average power in kW."""
    max_power: float | None
    """Highest power in kW."""
    id_tag: str | None
    id_tag_name: str | None
    closed_after_restart: bool
    """The session was closed by a charger restart rather than by unplugging."""

    @classmethod
    def from_dict(cls, data: Any) -> Self | None:
        """Parse a record; None if it is not one, or has not started."""
        if not isinstance(data, Mapping):
            return None
        # The firmware reports a record as active only once its start is set.
        if (started_at := _timestamp(data.get("s_start"))) is None:
            return None
        charge_time = _non_negative(data.get("chg_time"))
        idle_time = _non_negative(data.get("idle_time"))
        return cls(
            cdr_id=_int(data.get("cdr_id")),
            started_at=started_at,
            ended_at=_timestamp(data.get("s_end")),
            energy=_non_negative(data.get("chg_energy")),
            charge_time=None if charge_time is None else int(charge_time),
            idle_time=None if idle_time is None else int(idle_time),
            average_power=_non_negative(data.get("avg_power")),
            max_power=_non_negative(data.get("max_power")),
            id_tag=_str(data.get("idtag")),
            id_tag_name=_str(data.get("idtag_name")),
            closed_after_restart=data.get("closed_after_restart") is True,
        )


@dataclass(frozen=True, kw_only=True)
class Status:
    """Live status of the charger (GET /status)."""

    charger_id: str
    software_version: int | None
    """Decimal-packed charger software version, for example 1003025."""
    firmware_version: int | None
    """Decimal-packed EVSE controller firmware version, for example 199."""
    evse_state_code: int | None
    car_connected: bool | None
    charge_enabled: bool | None
    charging: bool | None
    mains_voltage: float | None
    phases: int | None
    """Phases the charger is wired to: 1 or 3."""
    phases_used: int | None
    """Phases the current session draws from; 0 while not charging."""
    current_hw_limit: float | None
    current_offered: float | None
    charge_current: float | None
    charge_power: float | None
    """Charging power in kW."""
    last_cdr: int | None
    """ID of the most recently closed session record."""
    session: ChargingSession | None
    """The active session, if there is one."""
    session_known: bool
    """Whether the response said anything about the active session.

    False when the response left the session out or sent a malformed one: that
    says nothing, unlike an explicit null, which means there is no session.
    """
    meter_energy: float | None
    """Lifetime energy reading in kWh, from the MID meter where one is fitted."""
    meter_supported: bool
    """Whether this firmware reports the lifetime energy reading at all."""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Parse a status response."""
        raw_session = data.get("cdr", ...)
        session = ChargingSession.from_dict(raw_session)
        phases = _int(data.get("phases"))
        return cls(
            charger_id=str(data["charger_id"]),
            software_version=_int(data.get("sw_ver")),
            firmware_version=_int(data.get("fw_ver")),
            evse_state_code=_int(data.get("evse_state")),
            car_connected=_bool(data.get("is_car_connected")),
            charge_enabled=_bool(data.get("charge_enabled")),
            charging=_bool(data.get("is_charging")),
            mains_voltage=_float(data.get("mains_voltage")),
            phases=phases if phases in (1, 3) else None,
            phases_used=_int(data.get("phases_used")),
            current_hw_limit=_float(data.get("current_hw_limit")),
            current_offered=_float(data.get("current_offered")),
            charge_current=_float(data.get("charge_current")),
            charge_power=_float(data.get("charge_power")),
            last_cdr=_int(data.get("last_cdr")),
            session=session,
            session_known=raw_session is None or session is not None,
            # -1 is the firmware's "no fresh reading".
            meter_energy=_non_negative(data.get("meter_kwh")),
            meter_supported="meter_kwh" in data,
        )

    @property
    def evse_state(self) -> EvseState | None:
        """The EVSE state; None if unknown to this library or not reported."""
        if self.evse_state_code is None:
            return None
        try:
            return EvseState(self.evse_state_code)
        except ValueError:
            return None

    @property
    def problem(self) -> bool | None:
        """Whether the EVSE reports a fault; None if the state is not reported."""
        if self.evse_state_code is None:
            return None
        return self.evse_state_code not in NON_PROBLEM_STATES

    @property
    def software_version_string(self) -> str | None:
        """Software version as dotted text, for example 1.3.25."""
        if (value := self.software_version) is None:
            return None
        return f"{value // 1_000_000}.{value // 1_000 % 1_000}.{value % 1_000}"

    @property
    def firmware_version_string(self) -> str | None:
        """EVSE controller firmware version as text, for example 1.99."""
        if (value := self.firmware_version) is None:
            return None
        return f"{value // 100}.{value % 100:02d}"


@dataclass(frozen=True, kw_only=True)
class Power:
    """Per-phase measurements (GET /power).

    DLM values come from the dynamic load management meter on the building's
    supply, IPM values from a load management meter connected over IP.
    """

    voltage_l1: float | None
    voltage_l2: float | None
    voltage_l3: float | None
    current_l1: float | None
    current_l2: float | None
    current_l3: float | None
    power_l1: float | None
    """Power in kW."""
    power_l2: float | None
    power_l3: float | None
    dlm_valid: bool | None
    dlm_current_l1: float | None
    dlm_current_l2: float | None
    dlm_current_l3: float | None
    ipm_valid: bool | None
    ipm_current_l1: float | None
    ipm_current_l2: float | None
    ipm_current_l3: float | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Parse a power response."""
        stat = data.get("power_stat")
        if not isinstance(stat, Mapping):
            stat = {}
        return cls(
            voltage_l1=_float(stat.get("voltage1")),
            voltage_l2=_float(stat.get("voltage2")),
            voltage_l3=_float(stat.get("voltage3")),
            current_l1=_float(stat.get("current1")),
            current_l2=_float(stat.get("current2")),
            current_l3=_float(stat.get("current3")),
            power_l1=_float(stat.get("power1")),
            power_l2=_float(stat.get("power2")),
            power_l3=_float(stat.get("power3")),
            dlm_valid=_bool(stat.get("dlm_valid")),
            dlm_current_l1=_float(stat.get("dlm_current1")),
            dlm_current_l2=_float(stat.get("dlm_current2")),
            dlm_current_l3=_float(stat.get("dlm_current3")),
            ipm_valid=_bool(stat.get("ipm_valid")),
            ipm_current_l1=_float(stat.get("ipm_current1")),
            ipm_current_l2=_float(stat.get("ipm_current2")),
            ipm_current_l3=_float(stat.get("ipm_current3")),
        )


# Attribute name to API key. Hardware- and firmware-dependent keys are left
# out of the response altogether, which is how a caller tells whether this
# charger has the setting at all.
CONFIG_KEYS: Mapping[str, str] = {
    "autostart_enabled": "conf_autostart_enabled",
    "current_limit": "conf_current_limit",
    "display_enabled": "conf_disp_enabled",
    "front_led_enabled": "conf_front_led_enabled",
    "rear_led_enabled": "conf_rear_led_enabled",
    "buzzer_enabled": "conf_buzzer_enabled",
    "force_single_phase": "conf_force_single_phase",
    "dlm_mode": "conf_dlm_mode",
    "dlm_current_limit": "conf_dlm_current_limit",
    "dlm_eco_start_current": "conf_dlm_eco_startcurr",
    "grid_stop_voltage": "conf_grid_u_stop",
    "grid_min_voltage": "conf_grid_u_min",
    "grid_max_voltage": "conf_grid_u_max",
    "grid_increase_delay": "conf_grid_t_up",
    "grid_decrease_delay": "conf_grid_t_dn",
    "out_of_service": "conf_out_of_service",
    "access_mode": "conf_access_mode",
}


@dataclass(frozen=True, kw_only=True)
class Config:
    """Charger settings (GET /config).

    A setting is None when the charger did not report it, which older firmware
    and some hardware do for the settings they lack.
    """

    autostart_enabled: bool | None = None
    current_limit: int | None = None
    """Maximum charging current in A."""
    display_enabled: bool | None = None
    front_led_enabled: bool | None = None
    rear_led_enabled: bool | None = None
    buzzer_enabled: bool | None = None
    force_single_phase: ForceSinglePhase | None = None
    dlm_mode: DlmMode | None = None
    dlm_current_limit: int | None = None
    """Building supply current limit in A."""
    dlm_eco_start_current: int | None = None
    """Solar surplus current in A, fed to the grid, at which eco mode starts."""
    grid_stop_voltage: int | None = None
    grid_min_voltage: int | None = None
    grid_max_voltage: int | None = None
    grid_increase_delay: int | None = None
    """Seconds before grid control raises the current."""
    grid_decrease_delay: int | None = None
    """Seconds before grid control lowers the current."""
    out_of_service: bool | None = None
    access_mode: AccessMode | None = None
    supported: frozenset[str] = field(default_factory=frozenset)
    """Names of the settings this charger reported, whatever their value."""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Parse a config response."""
        return cls(
            autostart_enabled=_bool(data.get("conf_autostart_enabled")),
            current_limit=_int(data.get("conf_current_limit")),
            display_enabled=_bool(data.get("conf_disp_enabled")),
            front_led_enabled=_bool(data.get("conf_front_led_enabled")),
            rear_led_enabled=_bool(data.get("conf_rear_led_enabled")),
            buzzer_enabled=_bool(data.get("conf_buzzer_enabled")),
            force_single_phase=_enum(
                ForceSinglePhase, data.get("conf_force_single_phase")
            ),
            dlm_mode=_enum(DlmMode, data.get("conf_dlm_mode")),
            dlm_current_limit=_int(data.get("conf_dlm_current_limit")),
            dlm_eco_start_current=_int(data.get("conf_dlm_eco_startcurr")),
            grid_stop_voltage=_int(data.get("conf_grid_u_stop")),
            grid_min_voltage=_int(data.get("conf_grid_u_min")),
            grid_max_voltage=_int(data.get("conf_grid_u_max")),
            grid_increase_delay=_int(data.get("conf_grid_t_up")),
            grid_decrease_delay=_int(data.get("conf_grid_t_dn")),
            out_of_service=_bool(data.get("conf_out_of_service")),
            access_mode=_enum(AccessMode, data.get("conf_access_mode")),
            supported=frozenset(
                name for name, key in CONFIG_KEYS.items() if key in data
            ),
        )


@dataclass(frozen=True, kw_only=True)
class RfidStatus:
    """State of the RFID reader and its tag list (GET /rfid/status)."""

    list_count: int | None
    list_capacity: int | None
    list_format_version: int | None
    list_hash: str | None
    """Changes whenever the stored tag list changes."""
    reader_enabled: bool | None
    reader_working: bool | None
    learn_in_progress: bool | None
    learn_remaining: int | None
    """Seconds left in learn mode."""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Parse the rfid_status block of a response."""
        return cls(
            list_count=_int(data.get("list_count")),
            list_capacity=_int(data.get("list_capacity")),
            list_format_version=_int(data.get("list_format_ver")),
            list_hash=_str(data.get("list_hash")),
            reader_enabled=_bool(data.get("reader_enabled")),
            reader_working=_bool(data.get("reader_working")),
            learn_in_progress=_bool(data.get("learn_in_progress")),
            learn_remaining=_int(data.get("learn_to_sec")),
        )


@dataclass(frozen=True, kw_only=True)
class RfidTag:
    """An RFID tag stored on the charger."""

    id: str
    name: str | None
    enabled: bool | None
    comment: str | None
    last_used: datetime | None

    @classmethod
    def from_dict(cls, data: Any) -> Self | None:
        """Parse a stored tag; None if it is not one."""
        if not isinstance(data, Mapping) or not (tag_id := _str(data.get("id"))):
            return None
        return cls(
            id=tag_id,
            name=_str(data.get("name")),
            enabled=_bool(data.get("enabled")),
            comment=_str(data.get("comment")),
            last_used=_timestamp(data.get("last_use")),
        )


@dataclass(frozen=True, kw_only=True)
class RfidTagList:
    """A page of the stored RFID tag list (GET /rfid)."""

    tags: list[RfidTag]
    total: int
    """Tags stored in total, which can exceed the tags on this page."""
    list_hash: str | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Parse a tag list response."""
        raw_tags = data.get("rfid_list")
        if not isinstance(raw_tags, list):
            return cls(tags=[], total=0, list_hash=_str(data.get("list_hash")))
        tags = [tag for raw in raw_tags if (tag := RfidTag.from_dict(raw))]
        total = _int(data.get("rfid_count"))
        return cls(
            tags=tags,
            total=total if total is not None and total >= len(tags) else len(tags),
            list_hash=_str(data.get("list_hash")),
        )

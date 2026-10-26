"""Constants and enumerations of the Voltie charger HTTP API."""

from enum import IntEnum, StrEnum

DEFAULT_PORT = 5059
DEFAULT_TIMEOUT = 10.0


class EvseState(IntEnum):
    """State of the charger's EVSE (supply equipment) controller.

    The values follow the firmware's internal state enum, which is more detailed
    than the list in the HTTP API specification.
    """

    UNKNOWN = 0
    EV_NOT_CONNECTED = 1
    EV_CONNECTED_NOT_CHARGING = 2
    EV_CONNECTED_CHARGING = 3
    EV_CONNECTED_CHARGING_VENTILATION = 4
    DIODE_CHECK_FAILED = 5
    GFCI_FAULT = 6
    NO_GROUND = 7
    STUCK_RELAY = 8
    GFI_SELF_TEST_FAILURE = 9
    OVER_TEMPERATURE = 10
    OVER_CURRENT = 11
    I2C_BUS_ERROR = 12
    EV_FAULT = 13
    OVER_HUMIDITY = 14
    PHASE_MISCONNECTED = 15
    OVERVOLTAGE = 16
    UNDERVOLTAGE = 17
    DISABLED = 18
    BOOTING = 19
    MID_METER_MISSING = 20
    POWER_BOARD_UNKNOWN = 21
    SLEEPING = 22  # An old power-saving state that the firmware no longer uses
    SHUTDOWN = 23
    UNDETERMINED = 24
    FIRMWARE_UPDATE = 25
    PE_N_FAULT = 26


# Every state not listed here is a fault, including codes that newer firmware
# may add. A vehicle fault counts as a problem too, since charging is stuck
# either way. UNKNOWN is what the charger reports before its first valid EVSE
# reading, so counting it would raise a false alarm on every start.
NON_PROBLEM_STATES = frozenset(
    {
        EvseState.UNKNOWN,
        EvseState.EV_NOT_CONNECTED,
        EvseState.EV_CONNECTED_NOT_CHARGING,
        EvseState.EV_CONNECTED_CHARGING,
        EvseState.EV_CONNECTED_CHARGING_VENTILATION,
        EvseState.DISABLED,
        EvseState.BOOTING,
        EvseState.SLEEPING,
        EvseState.UNDETERMINED,
        EvseState.FIRMWARE_UPDATE,
    }
)


class DlmMode(IntEnum):
    """Dynamic load management mode."""

    OFF = 0
    DYNAMIC = 1
    ECO = 2
    GREEN = 3
    GRID_CONTROL = 4


class AccessMode(IntEnum):
    """Who may start a charging session."""

    HOME_CHARGER = 0
    HOME_CHARGER_RFID = 1


class ForceSinglePhase(IntEnum):
    """Single-phase charging setting.

    Only OFF and ON can be written. The other two values are reported by the
    charger when its hardware cannot switch phases or has not determined yet
    whether it can.
    """

    OFF = 0
    ON = 1
    NOT_SUPPORTED = 2
    UNDETERMINED = 3


class Endpoint(StrEnum):
    """HTTP API endpoints."""

    API_VERSION = "apiver"
    CDR = "cdr"
    CONFIG = "config"
    EXTRAS = "extras"
    POWER = "power"
    RFID = "rfid"
    RFID_EXTRAS = "rfid/extras"
    RFID_STATUS = "rfid/status"
    START = "start"
    STATUS = "status"
    STOP = "stop"

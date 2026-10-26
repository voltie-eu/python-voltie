"""Exceptions raised by the Voltie charger client."""


class VoltieError(Exception):
    """Base exception for everything this library raises."""


class VoltieConnectionError(VoltieError):
    """The charger could not be reached or sent an unusable response."""


class VoltieAuthenticationError(VoltieError):
    """The charger rejected the HTTP API credentials."""


class VoltieCommandError(VoltieError):
    """The charger understood the request but refused it."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        """Initialize the error with the charger's error code, if it sent one."""
        super().__init__(message)
        self.code = code


class VoltieUnsupportedError(VoltieCommandError):
    """The charger's firmware does not implement the endpoint or command."""

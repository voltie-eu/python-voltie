# Changelog

## 1.0.0

First release, extracted from the Voltie Charger Home Assistant integration.

- Typed client for the local HTTP API (version 4 and 5): status, power, settings, start and stop,
  session records, display text, rear LED, reboot and RFID tag management.
- Typed models that read an unexpected value as `None` instead of failing the whole response.
- `SessionTracker` to detect the end of a charging session.
- Exceptions: `VoltieConnectionError`, `VoltieAuthenticationError`, `VoltieCommandError` and
  `VoltieUnsupportedError`, all derived from `VoltieError`.

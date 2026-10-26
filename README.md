# python-voltie

Asynchronous Python client for the local HTTP API of [Voltie](https://voltie.eu) EV chargers.

It talks to the charger directly on your network, without the Voltie cloud. It powers the
[Voltie Charger integration](https://www.home-assistant.io/integrations/voltie_charger) of Home Assistant.

## Requirements

- Python 3.12 or newer.
- A Voltie charger with its HTTP API enabled in the Voltie app. If you set a username and
  password for the HTTP API there, pass them to the client.
- RFID tag management needs HTTP API version 5 or newer.

## Installation

```bash
pip install voltie
```

## Usage

```python
import asyncio

from voltie import DlmMode, VoltieCharger


async def main() -> None:
    async with VoltieCharger(
        "192.168.1.50", username="api", password="secret"
    ) as charger:
        status = await charger.status()
        print(status.charger_id, status.evse_state, status.charge_power)

        config = await charger.config()
        if "current_limit" in config.supported:
            await charger.set_config(current_limit=16, dlm_mode=DlmMode.ECO)

        await charger.display_text("Hello from Python")


asyncio.run(main())
```

Pass your own `aiohttp.ClientSession` with `session=` to share connections; the client then
leaves it open.

### What it covers

| Method | Endpoint | Returns |
| --- | --- | --- |
| `status()` | `GET /status` | `Status`, including the active `ChargingSession` |
| `power()` | `GET /power` | `Power`: per-phase voltage, current, power, load-management meters |
| `config()` / `set_config(...)` | `GET` / `PUT /config` | `Config`; settings the charger lacks read as `None` and are missing from `Config.supported` |
| `start(...)` / `stop()` | `GET /start`, `/stop` | |
| `session(cdr_id)` | `GET /cdr` | a closed `ChargingSession`, or `None` |
| `api_version()` | `GET /apiver` | major API version |
| `display_text(...)`, `set_rear_led(...)`, `reboot()` | `POST /extras` | |
| `rfid_status()`, `rfid_tags(...)`, `add_rfid_tag(...)`, `modify_rfid_tag(...)`, `delete_rfid_tag(...)`, `rfid_learn(...)`, `rfid_learn_cancel()` | `/rfid`, `/rfid/status`, `/rfid/extras` | |

`SessionTracker` follows the active session across `status()` readings and returns the closed
record of a session once it ends.

### Errors

All exceptions derive from `VoltieError`:

- `VoltieConnectionError`: the charger is unreachable or sent an unusable response.
- `VoltieAuthenticationError`: the HTTP API credentials were rejected.
- `VoltieCommandError`: the charger refused the request; `code` holds its error code.
- `VoltieUnsupportedError`: a `VoltieCommandError` for an endpoint or command the firmware lacks.

Read requests are retried once when the charger cannot be reached (`retries=`); commands are
never retried.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e . --group dev
pytest
mypy src tests
ruff check . && ruff format --check .
```

Releases are published to PyPI by the `release` workflow when a GitHub release is created.
The release tag must equal the version in `pyproject.toml`, and every release lists its changes
in [CHANGELOG.md](CHANGELOG.md).

## License

Apache License 2.0, see [LICENSE](LICENSE).

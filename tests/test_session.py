"""Tests for finished-session detection."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from voltie import ChargingSession, SessionTracker, Status, VoltieConnectionError

from .conftest import cdr_payload, status_payload

STARTED_AT = datetime(2024, 2, 19, 9, 30, tzinfo=UTC)


def status(**overrides: object) -> Status:
    """Return a parsed status."""
    return Status.from_dict(status_payload(**overrides))


def closed_record(**overrides: object) -> ChargingSession:
    """Return a closed session record."""
    record = ChargingSession.from_dict(
        cdr_payload(s_end=1708338360, chg_energy=6.1, **overrides)
    )
    assert record is not None
    return record


async def test_reports_closed_record() -> None:
    charger = AsyncMock()
    charger.session.return_value = closed_record()
    tracker = SessionTracker(charger)

    assert await tracker.update(status(evse_state=3, cdr=cdr_payload())) is None
    assert await tracker.update(status(evse_state=3, cdr=cdr_payload())) is None
    finished = await tracker.update(status(cdr=None, last_cdr=446))

    assert finished is not None
    assert finished.energy == 6.1
    assert finished.ended_at is not None
    charger.session.assert_awaited_once_with(446)
    # Reported once only.
    assert await tracker.update(status(cdr=None, last_cdr=446)) is None


async def test_new_session_ends_the_previous_one() -> None:
    charger = AsyncMock()
    charger.session.return_value = closed_record()
    tracker = SessionTracker(charger)

    await tracker.update(status(evse_state=3, cdr=cdr_payload()))
    finished = await tracker.update(
        status(evse_state=3, cdr=cdr_payload(cdr_id=447, s_start=1708340000))
    )
    assert finished is not None
    assert finished.started_at == STARTED_AT


async def test_unknown_session_changes_nothing() -> None:
    charger = AsyncMock()
    tracker = SessionTracker(charger)

    await tracker.update(status(evse_state=3, cdr=cdr_payload()))
    payload = status_payload()
    del payload["cdr"]
    assert await tracker.update(Status.from_dict(payload)) is None
    assert await tracker.update(status(cdr="junk")) is None
    charger.session.assert_not_awaited()


async def test_falls_back_to_snapshot() -> None:
    """Without a matching closed record the last snapshot is reported."""
    charger = AsyncMock()
    charger.session.side_effect = [
        VoltieConnectionError("down"),
        closed_record(s_start=1),
    ]
    tracker = SessionTracker(charger)

    await tracker.update(status(evse_state=3, cdr=cdr_payload(cdr_id=440)))
    finished = await tracker.update(status(cdr=None, last_cdr=446))
    assert finished is not None
    assert finished.cdr_id == 440
    assert finished.ended_at is None
    assert [call.args for call in charger.session.await_args_list] == [(446,), (440,)]


async def test_record_ids_are_deduplicated() -> None:
    charger = AsyncMock()
    charger.session.return_value = None
    tracker = SessionTracker(charger)

    await tracker.update(status(evse_state=3, cdr=cdr_payload()))
    await tracker.update(status(cdr=None, last_cdr=446))
    charger.session.assert_awaited_once_with(446)


async def test_other_charger_resets() -> None:
    charger = AsyncMock()
    tracker = SessionTracker(charger)

    await tracker.update(status(evse_state=3, cdr=cdr_payload()))
    assert await tracker.update(status(charger_id="other", cdr=None)) is None
    charger.session.assert_not_awaited()

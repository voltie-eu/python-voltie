"""Detection of finished charging sessions."""

import logging

from .charger import VoltieCharger
from .exceptions import VoltieError
from .models import ChargingSession, Status

_LOGGER = logging.getLogger(__name__)


class SessionTracker:
    """Follows the active session across status readings and reports its end.

    The status only carries the active session, so a session has ended once a
    reading shows no session or one with a different start. Its final numbers
    are then read from the closed record. A session is identified by its start
    time, since the firmware renumbers an active record when it resynchronizes
    its counter with the Voltie cloud.

    Nothing is persisted: a caller that must report sessions ending while it
    was not running compares the record of `Status.last_cdr` with the last
    session it reported.
    """

    def __init__(self, charger: VoltieCharger) -> None:
        """Initialize the tracker."""
        self._charger = charger
        self._session: ChargingSession | None = None
        self._charger_id: str | None = None

    async def update(self, status: Status) -> ChargingSession | None:
        """Take a status reading; return the session it shows to have ended."""
        if not status.session_known:
            return None

        if self._charger_id != status.charger_id:
            # A different charger answers at this address now.
            self._session = None
            self._charger_id = status.charger_id

        tracked = self._session
        self._session = status.session
        if tracked is None or (
            status.session is not None
            and status.session.started_at == tracked.started_at
        ):
            return None
        return await self._final_record(tracked, status)

    async def _final_record(
        self, tracked: ChargingSession, status: Status
    ) -> ChargingSession:
        """Prefer the closed record over the last snapshot of the session.

        The snapshot misses up to a poll interval of the session and has no
        end time. last_cdr names the most recently closed record, normally
        this one; the tracked ID covers a renumbering in between.
        """
        candidates = dict.fromkeys(
            cdr_id
            for cdr_id in (status.last_cdr, tracked.cdr_id)
            if cdr_id is not None and cdr_id > 0
        )
        for cdr_id in candidates:
            try:
                record = await self._charger.session(cdr_id)
            except VoltieError as err:
                _LOGGER.debug("Could not read session record %s: %s", cdr_id, err)
                continue
            if record is not None and record.started_at == tracked.started_at:
                return record
        return tracked

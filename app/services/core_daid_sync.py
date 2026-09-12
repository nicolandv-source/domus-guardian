from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.adapters.core_identity import CoreIdentityAdapter
from app.repositories.core_daid_links import CoreDaidLinkRepository
from app.services.device_grouping import DeviceGrouping


logger = logging.getLogger(__name__)

MAX_DAID_SYNC_ATTEMPTS = 3


class CoreDaidSyncService:
    """Owns the two-step, fail-open path from a physical HA device to a
    DOMUS Core DAID: ``sync_pending`` discovers devices, ``reconcile_with_core``
    calls Core for each one still owed a DAID. Core being unreachable never
    raises out of either method — Guardian's own function never depends on it
    (ADR-0011, domus-platform)."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        repository: CoreDaidLinkRepository,
        grouping: DeviceGrouping,
        core_identity_adapter: CoreIdentityAdapter,
        max_attempts: int = MAX_DAID_SYNC_ATTEMPTS,
    ) -> None:
        self._session_factory = session_factory
        self._links = repository
        self._grouping = grouping
        self._core = core_identity_adapter
        self._max_attempts = max_attempts

    def sync_pending(self) -> int:
        """Create a ``pending`` link for every physical device Guardian
        currently knows about that doesn't have one yet. Read-only against
        ``DeviceGrouping`` — never writes back into it."""
        created = 0
        with self._session_factory() as session:
            for snapshot in self._grouping.all_snapshots():
                _, was_created = self._links.ensure_pending(
                    session, snapshot.device_id
                )
                if was_created:
                    created += 1
            session.commit()
        return created

    async def reconcile_with_core(self) -> int:
        """Call Core for each link still owed a DAID. One device's failure
        (Core down, timeout, 5xx) never stops the rest of the batch."""
        confirmed = 0
        with self._session_factory() as session:
            for link in self._links.due_for_reconcile(session, self._max_attempts):
                try:
                    daid = await self._core.create_identity()
                except Exception:
                    logger.exception(
                        "core_daid_reconcile_failed device_id_ha=%s",
                        link.device_id_ha,
                    )
                    self._links.mark_attempt_failed(session, link)
                    continue
                self._links.mark_confirmed(session, link, daid)
                confirmed += 1
            session.commit()
        return confirmed

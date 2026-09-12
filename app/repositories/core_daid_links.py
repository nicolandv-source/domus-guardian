from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CoreDaidLink


class CoreDaidLinkRepository:
    def get(self, session: Session, device_id_ha: str) -> CoreDaidLink | None:
        return session.get(CoreDaidLink, device_id_ha)

    def ensure_pending(
        self, session: Session, device_id_ha: str
    ) -> tuple[CoreDaidLink, bool]:
        """Create a ``pending`` link for a device Guardian has just seen.

        A no-op when a link already exists, whatever its status — bringing a
        ``failed`` row back for another try is the reconcile worker's job
        (bounded by ``attempts``), not re-discovery's.
        """
        existing = self.get(session, device_id_ha)
        if existing is not None:
            return existing, False
        link = CoreDaidLink(device_id_ha=device_id_ha)
        session.add(link)
        session.flush()
        return link, True

    def due_for_reconcile(
        self, session: Session, max_attempts: int
    ) -> list[CoreDaidLink]:
        """Links still owed a DAID, same bounded-retry shape as
        ``NotificationRepository.failed_for_retry``."""
        return list(
            session.scalars(
                select(CoreDaidLink).where(
                    CoreDaidLink.daid_status.in_(("pending", "failed")),
                    CoreDaidLink.attempts < max_attempts,
                )
            )
        )

    def mark_confirmed(self, session: Session, link: CoreDaidLink, daid: str) -> None:
        link.daid = daid
        link.daid_status = "confirmed"
        session.flush()

    def mark_attempt_failed(self, session: Session, link: CoreDaidLink) -> None:
        link.attempts += 1
        link.daid_status = "failed"
        session.flush()

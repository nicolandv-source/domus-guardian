from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import MaintenanceWindow


class MaintenanceRepository:
    def expire_due(self, session: Session, now: datetime) -> list[MaintenanceWindow]:
        due = session.scalars(
            select(MaintenanceWindow).where(
                MaintenanceWindow.active.is_(True),
                MaintenanceWindow.ends_at.is_not(None),
                MaintenanceWindow.ends_at <= now,
            )
        ).all()
        for window in due:
            window.active = False
            window.ended_at = window.ends_at
        session.flush()
        return due

    def get_active(
        self, session: Session, device_id: str, now: datetime
    ) -> MaintenanceWindow | None:
        self.expire_due(session, now)
        return session.scalar(
            select(MaintenanceWindow).where(
                MaintenanceWindow.device_id == device_id,
                MaintenanceWindow.active.is_(True),
                or_(MaintenanceWindow.ends_at.is_(None), MaintenanceWindow.ends_at > now),
            )
        )

    def list(self, session: Session, now: datetime) -> list[MaintenanceWindow]:
        self.expire_due(session, now)
        return session.scalars(
            select(MaintenanceWindow).order_by(MaintenanceWindow.started_at.desc())
        ).all()

    def activate(
        self,
        session: Session,
        *,
        device_id: str,
        reason: str,
        started_at: datetime,
        ends_at: datetime | None,
    ) -> MaintenanceWindow:
        existing = session.scalar(
            select(MaintenanceWindow).where(MaintenanceWindow.device_id == device_id)
        )
        if existing is None:
            existing = MaintenanceWindow(device_id=device_id)
            session.add(existing)
        existing.reason = reason
        existing.started_at = started_at
        existing.ends_at = ends_at
        existing.active = True
        existing.ended_at = None
        session.flush()
        return existing

    def deactivate(
        self, session: Session, device_id: str, now: datetime
    ) -> MaintenanceWindow | None:
        window = session.scalar(
            select(MaintenanceWindow).where(MaintenanceWindow.device_id == device_id)
        )
        if window is None:
            return None
        window.active = False
        window.ended_at = now
        session.flush()
        return window

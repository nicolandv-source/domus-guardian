from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident, Notification


class NotificationRepository:
    def get(self, session: Session, notification_id: int) -> Notification | None:
        return session.get(Notification, notification_id)

    def get_for_event(
        self, session: Session, incident_id: int, channel: str, event_type: str
    ) -> Notification | None:
        return session.scalar(
            select(Notification).where(
                Notification.incident_id == incident_id,
                Notification.channel == channel,
                Notification.event_type == event_type,
            )
        )

    def create_once(
        self,
        session: Session,
        *,
        incident: Incident,
        channel: str,
        event_type: str,
        category: str,
        title: str,
        message: str,
    ) -> tuple[Notification, bool]:
        existing = self.get_for_event(session, incident.id, channel, event_type)
        if existing is not None:
            return existing, False
        notification = Notification(
            incident_id=incident.id,
            channel=channel,
            event_type=event_type,
            notification_id=f"domus_incident_{incident.id}",
            category=category,
            title=title,
            message=message,
        )
        session.add(notification)
        session.flush()
        return notification, True

    def mark_sent(
        self, session: Session, notification: Notification, now: datetime
    ) -> None:
        notification.status = "sent"
        notification.sent_at = now
        notification.error_message = None
        notification.attempts += 1
        session.flush()

    def mark_failed(
        self, session: Session, notification: Notification, error: str
    ) -> None:
        notification.status = "failed"
        notification.error_message = error[:500]
        notification.attempts += 1
        session.flush()

    def mark_suppressed(self, session: Session, notification: Notification) -> None:
        notification.status = "suppressed"
        notification.error_message = "notification cooldown"
        session.flush()

    def has_recent_open_delivery(
        self,
        session: Session,
        incident_key: str,
        since: datetime,
    ) -> bool:
        return (
            session.scalar(
                select(Notification.id)
                .join(Incident)
                .where(
                    Incident.entity_id == incident_key,
                    Notification.channel == "ha_persistent",
                    Notification.event_type == "opened",
                    Notification.status == "sent",
                    Notification.sent_at >= since,
                )
                .limit(1)
            )
            is not None
        )

    def failed_for_retry(
        self, session: Session, max_attempts: int
    ) -> list[Notification]:
        return list(
            session.scalars(
                select(Notification).where(
                    Notification.channel == "ha_persistent",
                    Notification.status == "failed",
                    Notification.attempts < max_attempts,
                )
            )
        )

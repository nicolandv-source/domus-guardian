from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.adapters.home_assistant_notify import HomeAssistantNotifyAdapter
from app.core.event_bus import EventBus
from app.models import Incident
from app.repositories.notifications import NotificationRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationPolicy:
    notify_important_incidents: bool = True
    cooldown: timedelta = timedelta(minutes=10)
    max_attempts: int = 3

    def should_notify(self, severity: str) -> bool:
        return severity == "critical" or (
            severity == "warning" and self.notify_important_incidents
        )


class NotificationEngine:
    """Persists and dispatches non-duplicated incident notifications."""

    def __init__(
        self,
        event_bus: EventBus,
        session_factory: Callable[[], Session],
        repository: NotificationRepository,
        adapter: HomeAssistantNotifyAdapter,
        policy: NotificationPolicy,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._repository = repository
        self._adapter = adapter
        self._policy = policy

    def subscribe(self) -> None:
        self._event_bus.subscribe("incident_opened", self._on_incident_opened)
        self._event_bus.subscribe("incident_resolved", self._on_incident_resolved)

    def unsubscribe(self) -> None:
        self._event_bus.unsubscribe("incident_opened", self._on_incident_opened)
        self._event_bus.unsubscribe("incident_resolved", self._on_incident_resolved)

    def _on_incident_opened(self, payload: dict[str, object]) -> None:
        self._schedule("opened", payload)

    def _on_incident_resolved(self, payload: dict[str, object]) -> None:
        self._schedule("resolved", payload)

    def _schedule(self, event_type: str, payload: dict[str, object]) -> None:
        asyncio.get_running_loop().create_task(
            self.dispatch(event_type, payload),
            name=f"notification-{event_type}-{payload.get('incident_id')}",
        )

    async def dispatch(self, event_type: str, payload: dict[str, object]) -> None:
        incident_id = int(payload["incident_id"])
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                incident = session.get(Incident, incident_id)
                if incident is None:
                    logger.warning(
                        "Incidente non trovato per notifica: %s", incident_id
                    )
                    return
                title, message = self._render(event_type, incident)
                category = self._category(incident.severity, event_type)
                log_record, log_created = self._repository.create_once(
                    session,
                    incident=incident,
                    channel="log",
                    event_type=event_type,
                    category=category,
                    title=title,
                    message=message,
                )
                if log_created:
                    self._repository.mark_sent(session, log_record, now)
                    logger.info(
                        "Notifica DOMUS %s: incidente %s", event_type, incident.id
                    )

                if not self._policy.should_notify(incident.severity):
                    return
                notification, created = self._repository.create_once(
                    session,
                    incident=incident,
                    channel="ha_persistent",
                    event_type=event_type,
                    category=category,
                    title=title,
                    message=message,
                )
                if not created:
                    return
                if event_type == "opened" and self._repository.has_recent_open_delivery(
                    session,
                    incident.entity_id,
                    now - self._policy.cooldown,
                ):
                    self._repository.mark_suppressed(session, notification)
                    return
                notification_id = notification.id

        await self._deliver(notification_id)

    async def retry_failed(self) -> int:
        with self._session_factory() as session:
            retry_ids = [
                notification.id
                for notification in self._repository.failed_for_retry(
                    session, self._policy.max_attempts
                )
            ]
        for notification_id in retry_ids:
            await self._deliver(notification_id)
        return len(retry_ids)

    async def _deliver(self, notification_id: int) -> None:
        with self._session_factory() as session:
            notification = self._repository.get(session, notification_id)
            if notification is None:
                return
            payload = (
                notification.notification_id,
                notification.title,
                notification.message,
            )

        try:
            await self._adapter.upsert_persistent_notification(*payload)
        except Exception as exc:
            with self._session_factory() as session:
                with session.begin():
                    notification = self._repository.get(session, notification_id)
                    if notification is not None:
                        self._repository.mark_failed(
                            session, notification, type(exc).__name__
                        )
            logger.warning(
                "Invio notifica Home Assistant fallito: %s", type(exc).__name__
            )
            return

        with self._session_factory() as session:
            with session.begin():
                notification = self._repository.get(session, notification_id)
                if notification is not None:
                    self._repository.mark_sent(
                        session, notification, datetime.now(timezone.utc)
                    )
        logger.info("Notifica Home Assistant inviata: %s", notification_id)

    @staticmethod
    def _category(severity: str, event_type: str) -> str:
        return "info" if event_type == "resolved" else severity

    @staticmethod
    def _render(event_type: str, incident: Incident) -> tuple[str, str]:
        name = incident.title.removesuffix(" non disponibile")
        if event_type == "resolved":
            return (
                f"[DOMUS · RISOLTO] {name}",
                f"{name} è tornato disponibile. Incidente #{incident.id} risolto.",
            )
        label = {"critical": "CRITICO", "warning": "ATTENZIONE", "info": "INFO"}.get(
            incident.severity, "INFO"
        )
        return (
            f"[DOMUS · {label}] {incident.title}",
            f"{incident.description or 'Dispositivo non disponibile.'}\n"
            f"Incidente #{incident.id} aperto.",
        )

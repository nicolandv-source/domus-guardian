from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.adapters.home_assistant_notify import HomeAssistantNotifyAdapter
from app.core.event_bus import EventBus
from app.models import Incident, Notification
from app.repositories.incidents import STALENESS_KIND
from app.repositories.notifications import NotificationRepository


logger = logging.getLogger(__name__)

_INCIDENT_TITLE_SUFFIXES = (" non disponibile", " silenzioso")


@dataclass(frozen=True)
class NotificationPolicy:
    notify_important_incidents: bool = True
    cooldown: timedelta = timedelta(minutes=10)
    max_attempts: int = 3
    # A ``pending`` outbox row older than this was persisted but never
    # delivered nor failed: the process was interrupted mid-flight. Long
    # enough to clear the batch window plus normal delivery latency without
    # racing an in-flight send.
    outbox_stale_after: timedelta = timedelta(minutes=2)
    # How long a resolved incident's HA persistent-notification card stays
    # visible (showing "RISOLTO") before being auto-dismissed from the panel.
    auto_dismiss_after: timedelta = timedelta(minutes=30)

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
        loop: asyncio.AbstractEventLoop | None = None,
        batch_window: timedelta = timedelta(seconds=8),
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._repository = repository
        self._adapter = adapter
        self._policy = policy
        self._loop = loop
        self._batch_window = batch_window
        self._pending: dict[str, list[dict[str, object]]] = {}
        self._batch_tasks: dict[str, asyncio.Task[None]] = {}

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
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._enqueue(event_type, payload), self._loop
            )
            return
        # This fallback is for small synchronous embeddings that do not supply
        # the application loop.  FastAPI supplies one at lifespan startup.
        asyncio.run(self.dispatch(event_type, payload))

    async def _enqueue(self, event_type: str, payload: dict[str, object]) -> None:
        """Collect same-moment incidents so a shared outage yields one alert.

        A single flaky Zigbee bridge dropping can open a dozen incidents
        within a second or two; delivering each as its own persistent
        notification (and each cascading into Telegram, etc.) drowns the
        signal. Events of the same type arriving within ``_batch_window``
        of the first one are grouped into a single delivery.
        """
        bucket = self._pending.setdefault(event_type, [])
        bucket.append(payload)
        if event_type not in self._batch_tasks:
            self._batch_tasks[event_type] = asyncio.create_task(
                self._flush_after_delay(event_type)
            )

    async def _flush_after_delay(self, event_type: str) -> None:
        await asyncio.sleep(self._batch_window.total_seconds())
        payloads = self._pending.pop(event_type, [])
        self._batch_tasks.pop(event_type, None)
        if not payloads:
            return
        # One correlation ID for the whole batch: these incidents are
        # persisted and delivered together as a single outbox round.
        correlation_id = uuid.uuid4().hex
        notification_ids = []
        for payload in payloads:
            notification_id = await self._record(event_type, payload, correlation_id)
            if notification_id is not None:
                notification_ids.append(notification_id)
        if not notification_ids:
            return
        if len(notification_ids) == 1:
            await self._deliver(notification_ids[0])
        else:
            await self._deliver_batch(event_type, notification_ids)

    async def dispatch(self, event_type: str, payload: dict[str, object]) -> None:
        """Record and immediately deliver a single incident notification.

        Bypasses batching; used for direct/synchronous callers (retries,
        tests) where the caller wants one notification for one incident.
        """
        notification_id = await self._record(event_type, payload, uuid.uuid4().hex)
        if notification_id is not None:
            await self._deliver(notification_id)

    async def _record(
        self,
        event_type: str,
        payload: dict[str, object],
        correlation_id: str,
    ) -> Optional[int]:
        incident_id = int(payload["incident_id"])
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                incident = session.get(Incident, incident_id)
                if incident is None:
                    logger.warning(
                        "Incidente non trovato per notifica: %s", incident_id
                    )
                    return None
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
                    correlation_id=correlation_id,
                )
                if log_created:
                    self._repository.mark_sent(session, log_record, now)
                    logger.info(
                        "Notifica DOMUS %s: incidente %s correlation_id=%s",
                        event_type,
                        incident.id,
                        correlation_id,
                    )

                if not self._policy.should_notify(incident.severity):
                    return None
                notification, created = self._repository.create_once(
                    session,
                    incident=incident,
                    channel="ha_persistent",
                    event_type=event_type,
                    category=category,
                    title=title,
                    message=message,
                    correlation_id=correlation_id,
                )
                if not created:
                    return None
                if event_type == "opened" and self._repository.has_recent_open_delivery(
                    session,
                    incident.entity_id,
                    now - self._policy.cooldown,
                ):
                    self._repository.mark_suppressed(session, notification)
                    return None
                return notification.id

    async def _deliver_batch(
        self, event_type: str, notification_ids: list[int]
    ) -> None:
        with self._session_factory() as session:
            notifications = [
                notification
                for notification in (
                    self._repository.get(session, notification_id)
                    for notification_id in notification_ids
                )
                if notification is not None
            ]
        if not notifications:
            return

        title, message = self._render_batch(event_type, notifications)
        batch_id = f"domus_batch_{event_type}_{int(datetime.now(timezone.utc).timestamp())}"
        try:
            await self._adapter.upsert_persistent_notification(
                batch_id, title, message
            )
        except Exception as exc:
            with self._session_factory() as session:
                with session.begin():
                    for notification in notifications:
                        row = self._repository.get(session, notification.id)
                        if row is not None:
                            self._repository.mark_failed(
                                session, row, type(exc).__name__
                            )
            logger.warning(
                "Invio notifica batch Home Assistant fallito: %s", type(exc).__name__
            )
            return

        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                for notification in notifications:
                    row = self._repository.get(session, notification.id)
                    if row is not None:
                        # The batch id is what actually reached Home Assistant;
                        # each row otherwise keeps its own per-incident id,
                        # which cleanup could never use to dismiss the card.
                        self._repository.mark_delivered_as(session, row, batch_id)
                        self._repository.mark_sent(session, row, now)
        logger.info(
            "Notifica batch Home Assistant inviata: %s dispositivi (%s)",
            len(notifications),
            event_type,
        )

    async def retry_failed(self) -> int:
        """Sweep the outbox: retry failed deliveries and recover stuck ones.

        ``failed`` rows are known delivery failures within their attempt
        budget. ``pending`` rows older than the outbox staleness window were
        persisted but never delivered nor failed — the process was
        interrupted between the two. Both are outstanding outbox work; this
        is the persist-before-send guarantee's other half, without which a
        row like that would sit forever with no notification ever sent.
        """
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            retry_ids = [
                notification.id
                for notification in self._repository.failed_for_retry(
                    session, self._policy.max_attempts
                )
            ]
            stale_ids = [
                notification.id
                for notification in self._repository.stale_pending(
                    session, now - self._policy.outbox_stale_after
                )
            ]
        if stale_ids:
            logger.warning(
                "Notifiche pending bloccate recuperate dall'outbox: %s", len(stale_ids)
            )
        retry_ids.extend(stale_ids)
        for notification_id in retry_ids:
            await self._deliver(notification_id)
        return len(retry_ids)

    async def dismiss_resolved_notifications(self) -> int:
        """Clear resolved incidents' cards from the HA panel; keep their history.

        Rows are never deleted here (or anywhere): ``/api/v1/notifications``
        stays a full audit trail. This only calls
        ``persistent_notification.dismiss`` so a resolved incident stops
        being an "active" card once its grace period has passed, and marks
        the row dismissed so the sweep does not repeat the call.
        """
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            candidates = self._repository.resolved_pending_dismissal(
                session, now - self._policy.auto_dismiss_after
            )
            by_notification_id: dict[str, list[int]] = {}
            for notification in candidates:
                by_notification_id.setdefault(notification.notification_id, []).append(
                    notification.id
                )

        dismissed = 0
        for notification_id, row_ids in by_notification_id.items():
            try:
                await self._adapter.dismiss_persistent_notification(notification_id)
            except Exception as exc:
                logger.warning(
                    "Rimozione notifica Home Assistant fallita: %s (%s)",
                    notification_id,
                    type(exc).__name__,
                )
                continue
            now = datetime.now(timezone.utc)
            with self._session_factory() as session:
                with session.begin():
                    for row_id in row_ids:
                        row = self._repository.get(session, row_id)
                        if row is not None:
                            self._repository.mark_dismissed(session, row, now)
            dismissed += len(row_ids)
        return dismissed

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
    def _device_name(title: str) -> str:
        for suffix in _INCIDENT_TITLE_SUFFIXES:
            if title.endswith(suffix):
                return title.removesuffix(suffix)
        return title

    @classmethod
    def _render_batch(
        cls, event_type: str, notifications: list[Notification]
    ) -> tuple[str, str]:
        count = len(notifications)
        names = [
            cls._device_name(notification.title.split("] ", 1)[-1])
            for notification in notifications
        ]
        if event_type == "resolved":
            title = f"[DOMUS · RISOLTO] {count} dispositivi risolti"
        else:
            title = f"[DOMUS · ATTENZIONE] {count} dispositivi segnalati"
        message = "\n".join(f"- {name}" for name in names)
        return title, message

    @staticmethod
    def _category(severity: str, event_type: str) -> str:
        return "info" if event_type == "resolved" else severity

    @classmethod
    def _render(cls, event_type: str, incident: Incident) -> tuple[str, str]:
        name = cls._device_name(incident.title)
        if event_type == "resolved":
            verb = (
                "ha ripreso a comunicare"
                if incident.kind == STALENESS_KIND
                else "è tornato disponibile"
            )
            return (
                f"[DOMUS · RISOLTO] {name}",
                f"{name} {verb}. Incidente #{incident.id} risolto.",
            )
        label = {"critical": "CRITICO", "warning": "ATTENZIONE", "info": "INFO"}.get(
            incident.severity, "INFO"
        )
        return (
            f"[DOMUS · {label}] {incident.title}",
            f"{incident.description or 'Dispositivo non disponibile.'}\n"
            f"Incidente #{incident.id} aperto.",
        )

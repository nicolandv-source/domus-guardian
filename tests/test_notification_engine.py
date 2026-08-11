from __future__ import annotations

from datetime import timedelta
import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.event_bus import EventBus
from app.database import Base
from app.models import Device, Incident, Notification
from app.repositories.notifications import NotificationRepository
from app.services.notification_engine import NotificationEngine, NotificationPolicy


class FakeNotifyAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.failure: Exception | None = None

    async def upsert_persistent_notification(
        self, notification_id: str, title: str, message: str
    ) -> None:
        if self.failure is not None:
            raise self.failure
        self.calls.append((notification_id, title, message))


def make_engine(
    severity: str = "critical",
    policy: NotificationPolicy | None = None,
) -> tuple[NotificationEngine, sessionmaker, FakeNotifyAdapter, Incident]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        device = Device(entity_id="switch.test", domain="switch", name="Test switch")
        session.add(device)
        session.flush()
        incident = Incident(
            device_id=device.id,
            entity_id="physical-test",
            kind="availability",
            severity=severity,
            status="open",
            title="Test switch non disponibile",
            description="Test availability incident.",
        )
        session.add(incident)
        session.flush()
    adapter = FakeNotifyAdapter()
    notification_engine = NotificationEngine(
        EventBus(),
        factory,
        NotificationRepository(),
        adapter,
        policy or NotificationPolicy(cooldown=timedelta(minutes=10)),
    )
    return notification_engine, factory, adapter, incident


@pytest.mark.asyncio
async def test_open_and_resolved_incident_update_one_persistent_notification() -> None:
    engine, factory, adapter, incident = make_engine()

    await engine.dispatch("opened", {"incident_id": incident.id})
    await engine.dispatch("opened", {"incident_id": incident.id})

    with factory.begin() as session:
        session.get(Incident, incident.id).status = "resolved"

    await engine.dispatch("resolved", {"incident_id": incident.id})

    with factory() as session:
        notifications = session.scalars(select(Notification)).all()

    assert len(notifications) == 4  # log + persistent for opened and resolved
    assert len(adapter.calls) == 2
    assert adapter.calls[0][0] == adapter.calls[1][0] == f"domus_incident_{incident.id}"
    assert "RISOLTO" in adapter.calls[1][1]


@pytest.mark.asyncio
async def test_important_notifications_can_be_disabled() -> None:
    policy = NotificationPolicy(notify_important_incidents=False)
    engine, factory, adapter, incident = make_engine("warning", policy)

    await engine.dispatch("opened", {"incident_id": incident.id})

    with factory() as session:
        notifications = session.scalars(select(Notification)).all()

    assert len(notifications) == 1
    assert notifications[0].channel == "log"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_open_notification_cooldown_suppresses_same_device() -> None:
    engine, factory, adapter, incident = make_engine()
    await engine.dispatch("opened", {"incident_id": incident.id})

    with factory.begin() as session:
        second_incident = Incident(
            entity_id=incident.entity_id,
            kind="availability",
            severity="critical",
            status="open",
            title="Test switch non disponibile",
            description="Second availability incident.",
        )
        session.add(second_incident)
        session.flush()
        second_id = second_incident.id

    await engine.dispatch("opened", {"incident_id": second_id})

    with factory() as session:
        suppressed = session.scalar(
            select(Notification).where(
                Notification.incident_id == second_id,
                Notification.channel == "ha_persistent",
            )
        )

    assert len(adapter.calls) == 1
    assert suppressed.status == "suppressed"


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_without_exposing_error() -> None:
    engine, factory, adapter, incident = make_engine()
    adapter.failure = RuntimeError("network unavailable")

    await engine.dispatch("opened", {"incident_id": incident.id})

    with factory() as session:
        notification = session.scalar(
            select(Notification).where(Notification.channel == "ha_persistent")
        )
        assert notification.status == "failed"
        assert notification.error_message == "RuntimeError"

    adapter.failure = None
    assert await engine.retry_failed() == 1

    with factory() as session:
        notification = session.scalar(
            select(Notification).where(Notification.channel == "ha_persistent")
        )
        assert notification.status == "sent"
        assert notification.attempts == 2


@pytest.mark.asyncio
async def test_resolved_event_from_sync_thread_uses_application_loop() -> None:
    event_bus = EventBus()
    engine, factory, adapter, incident = make_engine()
    engine._event_bus = event_bus
    engine._loop = asyncio.get_running_loop()
    engine._batch_window = timedelta(seconds=0)
    engine.subscribe()

    with factory.begin() as session:
        session.get(Incident, incident.id).status = "resolved"

    await asyncio.to_thread(
        event_bus.publish, "incident_resolved", {"incident_id": incident.id}
    )
    for _ in range(50):
        if adapter.calls:
            break
        await asyncio.sleep(0.02)

    assert len(adapter.calls) == 1
    assert "RISOLTO" in adapter.calls[0][1]
    assert event_bus.metrics().handler_failures == 0


@pytest.mark.asyncio
async def test_simultaneous_incidents_are_batched_into_one_notification() -> None:
    event_bus = EventBus()
    engine, factory, adapter, incident = make_engine()
    engine._event_bus = event_bus
    engine._loop = asyncio.get_running_loop()
    engine._batch_window = timedelta(seconds=0.05)
    engine.subscribe()

    with factory.begin() as session:
        second_incident = Incident(
            entity_id="physical-second",
            kind="availability",
            severity="warning",
            status="open",
            title="Second device non disponibile",
            description="Second incident.",
        )
        session.add(second_incident)
        session.flush()
        second_id = second_incident.id

    event_bus.publish("incident_opened", {"incident_id": incident.id})
    event_bus.publish("incident_opened", {"incident_id": second_id})

    for _ in range(50):
        if adapter.calls:
            break
        await asyncio.sleep(0.02)

    assert len(adapter.calls) == 1
    notification_id, title, message = adapter.calls[0]
    assert notification_id.startswith("domus_batch_opened_")
    assert "2 dispositivi" in title
    assert "Test switch" in message
    assert "Second device" in message

    with factory() as session:
        notifications = session.scalars(
            select(Notification).where(Notification.channel == "ha_persistent")
        ).all()
    assert len(notifications) == 2
    assert all(notification.status == "sent" for notification in notifications)

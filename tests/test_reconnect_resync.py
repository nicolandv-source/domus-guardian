"""End-to-end reconnect coverage: WebSocket client -> adapter -> DeviceService.

Unit tests elsewhere cover the WebSocket handshake and DeviceService's
availability logic in isolation. Neither proves the thing item #9 on the
roadmap actually asks for: that a real disconnect/reconnect cycle, replayed
through the full pipeline, leaves no gap (a state change missed while
offline is still picked up) and no duplicate (the same physical device
does not end up as two rows, two incidents, or two notifications just
because the connection cycled).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.event_bus import EventBus
from app.database import Base
from app.ha.websocket import HomeAssistantWebSocketClient
from app.models import Device, Incident
from app.adapters.home_assistant import HomeAssistantAdapter
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService


BASE_TIME = datetime(2026, 8, 22, 9, tzinfo=timezone.utc)


class FakeWebSocket:
    """One connection cycle: handshake, registry snapshot, get_states, subscribe.

    ``events`` is exhausted immediately (StopAsyncIteration) unless given,
    matching a connection that synced and then dropped before any live
    event arrived — exactly the case a reconnect resync must cover.
    """

    def __init__(
        self,
        *,
        entity_registry: list[dict[str, object]],
        device_registry: list[dict[str, object]],
        states: list[dict[str, object]],
        events: list[str] | None = None,
    ) -> None:
        self.messages = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps(
                {"id": 1, "type": "result", "success": True, "result": entity_registry}
            ),
            json.dumps(
                {"id": 2, "type": "result", "success": True, "result": device_registry}
            ),
            json.dumps({"id": 3, "type": "result", "success": True, "result": states}),
            json.dumps({"id": 4, "type": "result", "success": True}),
        ]
        self.events = list(events or [])
        self.sent: list[dict[str, object]] = []

    async def recv(self):
        return self.messages.pop(0)

    async def send(self, message):
        self.sent.append(json.loads(message))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.events:
            return self.events.pop(0)
        raise StopAsyncIteration


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_exc):
        return None


def make_pipeline() -> tuple[HomeAssistantAdapter, DeviceService, sessionmaker, EventBus]:
    # The real WebSocket client publishes via ``asyncio.to_thread`` (the
    # event-loop-blocking fix), so DB writes triggered downstream happen on a
    # worker thread, not the test's own thread. Plain sqlite ``:memory:``
    # pooling is thread-local and would silently hand that thread an empty
    # database; StaticPool + check_same_thread=False shares the one
    # connection across threads instead.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    event_bus = EventBus()
    service = DeviceService(
        factory,
        DeviceRepository(),
        IncidentRepository(),
        DeviceGrouping(),
        # A zero-second debounce window: DeviceService.handle_state_changed
        # times debounce commits off the wall clock (not the event's own
        # time_fired, which this test controls independently), so a later
        # flush_debounce() call is due as soon as any real time has passed.
        DeviceDebouncer(timedelta(seconds=0)),
        event_bus=event_bus,
    )
    adapter = HomeAssistantAdapter(event_bus, service)
    adapter.subscribe()
    return adapter, service, factory, event_bus


ENTITY_REGISTRY = [{"entity_id": "binary_sensor.door", "device_id": "door-1"}]
DEVICE_REGISTRY = [{"id": "door-1", "connections": []}]


def state(value: str, when: datetime) -> dict[str, object]:
    return {
        "entity_id": "binary_sensor.door",
        "state": value,
        "attributes": {"friendly_name": "Porta ingresso"},
        "last_updated": when.isoformat(),
    }


async def run_one_connection(event_bus: EventBus, websocket: FakeWebSocket) -> None:
    """One ``run_once()`` cycle against a fresh fake connection.

    Mirrors what ``run_forever`` does on each reconnect: a new
    ``HomeAssistantWebSocketClient`` per call is equivalent here since all
    state that must survive a reconnect (registry mappings, device rows,
    incidents) lives in the shared event bus's subscribers, not in the
    WebSocket client itself.
    """
    client = HomeAssistantWebSocketClient(
        "ws://test", "secret", event_bus, lambda *_a, **_k: FakeConnection(websocket)
    )
    await client.run_once()


@pytest.mark.asyncio
async def test_reconnect_resync_has_no_gap_and_no_duplicate() -> None:
    adapter, service, factory, event_bus = make_pipeline()

    # Connection 1: device is available.
    first = FakeWebSocket(
        entity_registry=ENTITY_REGISTRY,
        device_registry=DEVICE_REGISTRY,
        states=[state("on", BASE_TIME)],
    )
    await run_one_connection(event_bus, first)

    with factory() as session:
        assert len(session.scalars(select(Device)).all()) == 1
        assert session.scalars(select(Incident)).all() == []

    # The connection drops. While Guardian is offline, HA sees the door go
    # unavailable — no event for it is ever delivered, only the next
    # reconnect's full snapshot.
    second = FakeWebSocket(
        entity_registry=ENTITY_REGISTRY,
        device_registry=DEVICE_REGISTRY,
        states=[state("unavailable", BASE_TIME + timedelta(minutes=5))],
    )
    await run_one_connection(event_bus, second)
    service.flush_debounce()

    with factory() as session:
        devices = session.scalars(select(Device)).all()
        incidents = session.scalars(select(Incident)).all()
    assert len(devices) == 1  # no duplicate device row from the second connect
    assert len(incidents) == 1  # the missed transition was not lost
    assert incidents[0].status == "open"

    # A third connect resync finds it recovered.
    third = FakeWebSocket(
        entity_registry=ENTITY_REGISTRY,
        device_registry=DEVICE_REGISTRY,
        states=[state("on", BASE_TIME + timedelta(minutes=10))],
    )
    await run_one_connection(event_bus, third)
    service.flush_debounce()

    with factory() as session:
        devices = session.scalars(select(Device)).all()
        incidents = session.scalars(select(Incident)).all()
    assert len(devices) == 1
    assert len(incidents) == 1  # resolved in place, not a second incident
    assert incidents[0].status == "resolved"

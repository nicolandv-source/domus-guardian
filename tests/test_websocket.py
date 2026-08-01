import asyncio
import json

import pytest

from app.core.event_bus import EventBus
from app.ha.websocket import HomeAssistantWebSocketClient


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps(
                {
                    "id": 1,
                    "type": "result",
                    "success": True,
                    "result": [
                        {
                            "entity_id": "sensor.domus_test",
                            "device_id": "physical-test",
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "id": 2,
                    "type": "result",
                    "success": True,
                    "result": [
                        {
                            "entity_id": "sensor.domus_test",
                            "state": "on",
                            "attributes": {},
                        }
                    ],
                }
            ),
            json.dumps({"id": 3, "type": "result", "success": True}),
        ]
        self.events = [
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "event_type": "state_changed",
                        "data": {"entity_id": "sensor.domus_test"},
                    },
                }
            )
        ]
        self.sent = []

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
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_):
        return None


@pytest.mark.asyncio
async def test_authenticates_subscribes_and_publishes_event() -> None:
    websocket = FakeWebSocket()
    received = []
    bus = EventBus()
    bus.subscribe("state_changed", received.append)

    connect_kwargs = {}

    def connect_factory(*_, **kwargs):
        connect_kwargs.update(kwargs)
        return FakeConnection(websocket)

    client = HomeAssistantWebSocketClient(
        "ws://home-assistant.test/api/websocket",
        "secret",
        bus,
        connect_factory,
    )
    await client.run_once()

    assert websocket.sent == [
        {"type": "auth", "access_token": "secret"},
        {"id": 1, "type": "config/entity_registry/list"},
        {"id": 2, "type": "get_states"},
        {"id": 3, "type": "subscribe_events", "event_type": "state_changed"},
    ]
    assert connect_kwargs["additional_headers"] == {
        "Authorization": "Bearer secret"
    }
    assert received[0]["data"]["entity_id"] == "sensor.domus_test"


@pytest.mark.asyncio
async def test_reconnects_after_a_transient_websocket_failure(monkeypatch) -> None:
    client = HomeAssistantWebSocketClient("ws://test", "secret", EventBus())
    calls = 0

    async def fake_run_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("connection dropped")
        raise asyncio.CancelledError

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(client, "run_once", fake_run_once)
    monkeypatch.setattr(asyncio, "sleep", no_wait)

    with pytest.raises(asyncio.CancelledError):
        await client.run_forever()

    assert calls == 2

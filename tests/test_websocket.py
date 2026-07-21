import json

import pytest

from app.core.event_bus import EventBus
from app.ha.websocket import HomeAssistantWebSocketClient


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1, "type": "result", "success": True}),
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

    def connect_factory(*_, **__):
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
        {"id": 1, "type": "subscribe_events", "event_type": "state_changed"},
    ]
    assert received[0]["data"]["entity_id"] == "sensor.domus_test"

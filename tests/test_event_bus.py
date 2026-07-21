from app.core.event_bus import EventBus


def test_subscribe_publish_unsubscribe() -> None:
    bus = EventBus()
    received = []

    def handler(payload):
        received.append(payload)

    bus.subscribe("state_changed", handler)
    bus.publish("state_changed", {"entity_id": "sensor.test"})
    bus.unsubscribe("state_changed", handler)
    bus.publish("state_changed", {"entity_id": "sensor.ignored"})

    assert received == [{"entity_id": "sensor.test"}]

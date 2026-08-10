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


def test_take_recent_handler_failures_resets_after_read() -> None:
    bus = EventBus()

    def failing_handler(_payload):
        raise RuntimeError("boom")

    bus.subscribe("state_changed", failing_handler)
    bus.publish("state_changed", {})
    bus.publish("state_changed", {})

    assert bus.take_recent_handler_failures() == 2
    assert bus.take_recent_handler_failures() == 0

    bus.publish("state_changed", {})

    assert bus.take_recent_handler_failures() == 1
    assert bus.metrics().handler_failures == 3

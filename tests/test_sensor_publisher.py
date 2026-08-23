from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.sensor_publisher import SensorPublisher


class FakeStateAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.failure_for: set[str] = set()

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, object]
    ) -> None:
        if entity_id in self.failure_for:
            raise RuntimeError("HA unreachable")
        self.calls.append((entity_id, state, attributes))


def full_snapshot(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "score": 92,
        "status": "warning",
        "active_incidents": 2,
        "critical_incidents": 0,
        "warning_incidents": 2,
        "offline_devices": 2,
        "offline_devices_weighted": 2.0,
        "watchdog_status": "healthy",
        "watchdog_issues": [],
        "last_websocket_event_at": datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_publishes_all_five_expected_sensors() -> None:
    adapter = FakeStateAdapter()
    publisher = SensorPublisher(adapter, lambda: full_snapshot())

    await publisher.publish()

    entity_ids = [call[0] for call in adapter.calls]
    assert entity_ids == [
        "sensor.domus_guardian_health_score",
        "sensor.domus_guardian_open_incidents",
        "sensor.domus_guardian_degraded_devices",
        "sensor.domus_guardian_last_sync",
        "sensor.domus_guardian_watchdog",
    ]


@pytest.mark.asyncio
async def test_health_score_and_watchdog_states_reflect_the_snapshot() -> None:
    adapter = FakeStateAdapter()
    publisher = SensorPublisher(
        adapter, lambda: full_snapshot(score=57, watchdog_status="degraded")
    )

    await publisher.publish()

    by_entity = {call[0]: (call[1], call[2]) for call in adapter.calls}
    score_state, score_attrs = by_entity["sensor.domus_guardian_health_score"]
    assert score_state == "57"
    assert score_attrs["unit_of_measurement"] == "%"
    watchdog_state, _ = by_entity["sensor.domus_guardian_watchdog"]
    assert watchdog_state == "degraded"


@pytest.mark.asyncio
async def test_last_sync_uses_iso_timestamp_when_available() -> None:
    adapter = FakeStateAdapter()
    when = datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)
    publisher = SensorPublisher(
        adapter, lambda: full_snapshot(last_websocket_event_at=when)
    )

    await publisher.publish()

    state, attrs = next(
        (call[1], call[2])
        for call in adapter.calls
        if call[0] == "sensor.domus_guardian_last_sync"
    )
    assert state == when.isoformat()
    assert attrs["device_class"] == "timestamp"


@pytest.mark.asyncio
async def test_last_sync_falls_back_to_unknown_before_first_event() -> None:
    adapter = FakeStateAdapter()
    publisher = SensorPublisher(
        adapter, lambda: full_snapshot(last_websocket_event_at=None)
    )

    await publisher.publish()

    state = next(
        call[1] for call in adapter.calls if call[0] == "sensor.domus_guardian_last_sync"
    )
    assert state == "unknown"


@pytest.mark.asyncio
async def test_one_sensor_failure_does_not_block_the_others() -> None:
    adapter = FakeStateAdapter()
    adapter.failure_for = {"sensor.domus_guardian_open_incidents"}
    publisher = SensorPublisher(adapter, lambda: full_snapshot())

    await publisher.publish()

    entity_ids = [call[0] for call in adapter.calls]
    assert "sensor.domus_guardian_open_incidents" not in entity_ids
    # every other sensor still went through despite the one failure
    assert len(entity_ids) == 4

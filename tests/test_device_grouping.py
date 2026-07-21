from __future__ import annotations

from datetime import datetime, timezone

from app.dto import StateChangedDTO
from app.services.device_grouping import DeviceGrouping


def dto(entity_id: str, state: str, device_id: str | None = None) -> StateChangedDTO:
    return StateChangedDTO(
        entity_id=entity_id,
        state=state,
        domain=entity_id.partition(".")[0],
        friendly_name=None,
        time_fired=datetime.now(timezone.utc),
        device_id=device_id,
    )


def test_group_is_available_when_any_entity_is_available() -> None:
    grouping = DeviceGrouping()
    grouping.register_entity_mapping("switch.relay", "physical-1")
    grouping.register_entity_mapping("sensor.signal", "physical-1")

    grouping.update(dto("switch.relay", "on"))
    state = grouping.update(dto("sensor.signal", "unavailable"))

    assert state.device_id == "physical-1"
    assert state.entity_ids == ("sensor.signal", "switch.relay")
    assert state.is_available is True


def test_group_is_unavailable_only_when_all_entities_are_unavailable_or_unknown() -> (
    None
):
    grouping = DeviceGrouping()
    grouping.update(dto("switch.relay", "unavailable", "physical-1"))
    state = grouping.update(dto("sensor.signal", "unknown", "physical-1"))

    assert state.is_available is False


def test_fallback_group_uses_entity_id() -> None:
    state = DeviceGrouping().update(dto("sensor.no_registry_mapping", "on"))
    assert state.device_id == "sensor.no_registry_mapping"

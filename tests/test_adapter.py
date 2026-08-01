from app.adapters.home_assistant import HomeAssistantAdapter
from app.core.event_bus import EventBus


def test_maps_home_assistant_state_changed_event() -> None:
    dto = HomeAssistantAdapter.to_dto(
        {
            "time_fired": "2026-07-21T12:00:00+00:00",
            "data": {
                "entity_id": "binary_sensor.test_porta",
                "old_state": {"state": "off"},
                "new_state": {
                    "state": "on",
                    "attributes": {
                        "friendly_name": "Porta test",
                        "device_id": "physical-test",
                    },
                },
            },
        }
    )

    assert dto is not None
    assert dto.entity_id == "binary_sensor.test_porta"
    assert dto.domain == "binary_sensor"
    assert dto.old_state == "off"
    assert dto.state == "on"
    assert dto.friendly_name == "Porta test"
    assert dto.device_id == "physical-test"


def test_ignores_helper_entity_without_registry_device() -> None:
    class Service:
        def __init__(self) -> None:
            self.received = []

        def is_physical_entity(self, _entity_id: str) -> bool:
            return False

        def handle_state_changed(self, dto) -> None:
            self.received.append(dto)

    service = Service()
    adapter = HomeAssistantAdapter(EventBus(), service)
    adapter.handle_state_changed(
        {
            "data": {
                "entity_id": "binary_sensor.presenza",
                "new_state": {"state": "unavailable", "attributes": {}},
            }
        }
    )
    assert service.received == []

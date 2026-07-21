from app.adapters.home_assistant import HomeAssistantAdapter


def test_maps_home_assistant_state_changed_event() -> None:
    dto = HomeAssistantAdapter.to_dto(
        {
            "time_fired": "2026-07-21T12:00:00+00:00",
            "data": {
                "entity_id": "binary_sensor.test_porta",
                "old_state": {"state": "off"},
                "new_state": {
                    "state": "on",
                    "attributes": {"friendly_name": "Porta test"},
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

from app.adapters.home_assistant import HomeAssistantAdapter
from app.core.event_bus import EventBus
from app.services.entity_monitoring_policy import EntityMonitoringPolicy


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


class RecordingService:
    def __init__(self) -> None:
        self.received = []

    def is_physical_entity(self, _entity_id: str) -> bool:
        return True

    def handle_state_changed(self, dto) -> None:
        self.received.append(dto)


def state_changed_event(entity_id: str) -> dict:
    return {
        "data": {
            "entity_id": entity_id,
            "new_state": {"state": "on", "attributes": {}},
        }
    }


def test_default_monitoring_policy_preserves_existing_behavior(monkeypatch) -> None:
    monkeypatch.delenv("GUARDIAN_MONITORED_DOMAINS", raising=False)
    monkeypatch.delenv("GUARDIAN_EXCLUDED_ENTITY_PATTERNS", raising=False)
    service = RecordingService()
    adapter = HomeAssistantAdapter(EventBus(), service, EntityMonitoringPolicy())

    adapter.handle_state_changed(state_changed_event("sensor.indoor_temperature"))

    assert [dto.entity_id for dto in service.received] == ["sensor.indoor_temperature"]


def test_monitoring_policy_rejects_domain_outside_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("GUARDIAN_MONITORED_DOMAINS", "light, switch")
    service = RecordingService()
    adapter = HomeAssistantAdapter(EventBus(), service, EntityMonitoringPolicy())

    adapter.handle_state_changed(state_changed_event("sensor.indoor_temperature"))

    assert service.received == []


def test_monitoring_policy_excluded_pattern_overrides_allowed_domain(monkeypatch) -> None:
    monkeypatch.setenv("GUARDIAN_MONITORED_DOMAINS", "sensor")
    monkeypatch.setenv("GUARDIAN_EXCLUDED_ENTITY_PATTERNS", "sensor.domus_finance_*")
    service = RecordingService()
    adapter = HomeAssistantAdapter(EventBus(), service, EntityMonitoringPolicy())

    adapter.handle_state_changed(state_changed_event("sensor.domus_finance_balance"))

    assert service.received == []


def test_monitoring_policy_allows_eligible_entity_to_reach_service(monkeypatch) -> None:
    monkeypatch.setenv("GUARDIAN_MONITORED_DOMAINS", "binary_sensor")
    service = RecordingService()
    adapter = HomeAssistantAdapter(EventBus(), service, EntityMonitoringPolicy())

    adapter.handle_state_changed(state_changed_event("binary_sensor.front_door"))

    assert [dto.entity_id for dto in service.received] == ["binary_sensor.front_door"]

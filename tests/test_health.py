from pathlib import Path

from app.models import Device
from app.services.health_engine import HealthEngine, calculate_health_score_status
from app.services.health_weights import HealthWeights


class FakeDeviceService:
    def __init__(self, devices: list[dict[str, object]]) -> None:
        self._devices = devices

    def diagnostics(self, _profile_for):
        return self._devices


def weights() -> HealthWeights:
    return HealthWeights.from_file(
        Path(__file__).parents[1] / "app" / "config" / "health_weights.json"
    )


def test_health_is_critical_for_unavailable_device_incident() -> None:
    score, status = calculate_health_score_status(
        database_connected=True,
        critical_incidents=1,
        warning_incidents=0,
        offline_devices=1,
    )

    assert score == 70
    assert status == "critical"


def test_health_returns_healthy_after_resolution() -> None:
    score, status = calculate_health_score_status(
        database_connected=True,
        critical_incidents=0,
        warning_incidents=0,
        offline_devices=0,
    )

    assert score == 100
    assert status == "healthy"


def test_optional_devices_have_minimal_health_impact() -> None:
    devices = [
        {
            "last_state": "available",
            "category": "important",
            "weight": 1.0,
            "include_in_score": True,
        }
        for _ in range(20)
    ]
    devices.extend(
        {
            "last_state": "unavailable",
            "category": "optional",
            "weight": 0.05,
            "include_in_score": True,
        }
        for _ in range(15)
    )

    snapshot = HealthEngine(FakeDeviceService(devices), weights()).snapshot(True)
    assert snapshot.offline_devices == 15
    assert snapshot.score >= 95
    assert snapshot.status == "warning"


def test_critical_devices_have_high_health_impact() -> None:
    devices = [
        {
            "last_state": "available",
            "category": "important",
            "weight": 1.0,
            "include_in_score": True,
        }
        for _ in range(20)
    ]
    devices.extend(
        {
            "last_state": "unavailable",
            "category": "critical",
            "weight": 20.0,
            "include_in_score": True,
        }
        for _ in range(2)
    )

    snapshot = HealthEngine(FakeDeviceService(devices), weights()).snapshot(True)
    assert snapshot.offline_devices == 2
    assert snapshot.score < 80
    assert snapshot.status == "critical"


def test_profile_rules_use_domain_device_class_and_name() -> None:
    policy = weights()

    tv = Device(entity_id="media_player.tv", domain="media_player", name="TV")
    door = Device(
        entity_id="binary_sensor.door",
        domain="binary_sensor",
        name="Porta ingresso",
        device_class="door",
    )
    main_light = Device(
        entity_id="light.centrale",
        domain="light",
        name="Luce Centrale",
    )

    assert policy.profile_for([tv]).category == "optional"
    assert policy.profile_for([door]).category == "critical"
    assert policy.profile_for([main_light]).category == "critical"

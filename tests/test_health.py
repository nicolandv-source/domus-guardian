from datetime import datetime, timedelta, timezone

from app.services.device_debounce import DeviceDebouncer
from app.services.health_engine import HealthEngine, calculate_health_score_status


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


def test_health_uses_only_stable_device_states() -> None:
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    debouncer = DeviceDebouncer(timedelta(seconds=30))
    health = HealthEngine(debouncer)

    debouncer.process_state_change("physical-1", True, now)
    debouncer.process_state_change("physical-1", False, now + timedelta(seconds=1))

    assert health.snapshot(database_connected=True).offline_devices == 0

    debouncer.flush_due(now + timedelta(seconds=31))
    snapshot = health.snapshot(database_connected=True)
    assert snapshot.offline_devices == 1
    assert snapshot.score == 70
    assert snapshot.status == "critical"

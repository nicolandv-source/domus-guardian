from app.main import calculate_health_score_status


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

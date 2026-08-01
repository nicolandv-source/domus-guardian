from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import main
from app.main import app
from app.models import Device, Incident
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService
from app.services.health_engine import HealthEngine
from app.services.health_weights import HealthWeights
from pathlib import Path


BASE_TIME = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def make_service() -> tuple[DeviceService, sessionmaker]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return (
        DeviceService(
            factory,
            DeviceRepository(),
            IncidentRepository(),
            DeviceGrouping(),
            DeviceDebouncer(timedelta(seconds=30)),
        ),
        factory,
    )


def test_maintenance_rest_api_lists_activates_and_deactivates() -> None:
    service, _ = make_service()
    app.state.device_service = service
    client = TestClient(app)

    response = client.put(
        "/api/v1/maintenance/box-3",
        json={"reason": "Spento volontariamente"},
    )
    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["reason"] == "Spento volontariamente"

    listed = client.get("/api/v1/maintenance")
    assert listed.status_code == 200
    assert [item["device_id"] for item in listed.json()] == ["box-3"]

    deactivated = client.delete("/api/v1/maintenance/box-3")
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False


def test_maintenance_api_rejects_past_expiry() -> None:
    service, _ = make_service()
    app.state.device_service = service
    client = TestClient(app)

    response = client.put(
        "/api/v1/maintenance/box-3",
        json={
            "reason": "Scadenza non valida",
            "ends_at": (BASE_TIME - timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422


def test_incidents_api_applies_status_severity_and_pagination(monkeypatch) -> None:
    service, factory = make_service()
    with factory.begin() as session:
        device = Device(entity_id="switch.test", domain="switch", name="Test")
        session.add(device)
        session.flush()
        session.add_all(
            [
                Incident(
                    device_id=device.id, entity_id="one", kind="availability",
                    severity="critical", status="open", title="One",
                ),
                Incident(
                    device_id=device.id, entity_id="two", kind="availability",
                    severity="warning", status="open", title="Two",
                ),
                Incident(
                    device_id=device.id, entity_id="three", kind="availability",
                    severity="critical", status="resolved", title="Three",
                ),
            ]
        )
    monkeypatch.setattr(main, "SessionLocal", factory)
    app.state.device_service = service
    client = TestClient(app)

    response = client.get("/api/v1/incidents?status=open&severity=critical&limit=1&offset=0")

    assert response.status_code == 200
    assert [item["entity_id"] for item in response.json()] == ["one"]


def test_health_and_open_incidents_are_consistent_after_restart(monkeypatch) -> None:
    service, factory = make_service()
    with factory.begin() as session:
        device = Device(
            entity_id="binary_sensor.offline", domain="binary_sensor",
            is_available=False, name="Offline",
        )
        session.add(device)
        session.flush()
        session.add(
            Incident(
                device_id=device.id, entity_id="physical-offline", kind="availability",
                severity="critical", status="open", title="Offline non disponibile",
            )
        )

    class Watchdog:
        def snapshot(self):
            return type("Snapshot", (), {"status": "healthy"})()

    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "ping_database", lambda: {"connected": True})
    app.state.device_service = service
    weights = HealthWeights.from_file(
        Path(__file__).parents[1] / "app" / "config" / "health_weights.json"
    )
    app.state.health_engine = HealthEngine(service, weights)
    app.state.health_weights = weights
    app.state.watchdog = Watchdog()
    client = TestClient(app)

    health = client.get("/api/v1/ha/health").json()
    incidents = client.get("/api/v1/incidents?status=open").json()

    assert health["active_incidents"] == len(incidents) == 1
    assert health["offline_devices"] == 1

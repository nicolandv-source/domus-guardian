from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService


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

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.dto import StateChangedDTO
from app.models import Device, Incident
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_service import DeviceService


def make_service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return (
        DeviceService(factory, DeviceRepository(), IncidentRepository()),
        factory,
    )


def event(state: str) -> StateChangedDTO:
    return StateChangedDTO(
        entity_id="binary_sensor.domus_test",
        state=state,
        domain="binary_sensor",
        friendly_name="DOMUS Test",
        time_fired=datetime.now(timezone.utc),
    )


def test_upserts_device_and_opens_single_incident() -> None:
    service, factory = make_service()
    service.handle_state_changed(event("unavailable"))
    service.handle_state_changed(event("unavailable"))

    with factory() as session:
        device = session.scalar(select(Device))
        incidents = session.scalars(select(Incident)).all()

    assert device is not None
    assert device.state == "unavailable"
    assert device.is_available is False
    assert len(incidents) == 1
    assert incidents[0].status == "open"
    assert incidents[0].severity == "critical"


def test_available_state_resolves_incident() -> None:
    service, factory = make_service()
    service.handle_state_changed(event("unavailable"))
    service.handle_state_changed(event("on"))

    with factory() as session:
        device = session.scalar(select(Device))
        incident = session.scalar(select(Incident))

    assert device is not None
    assert device.is_available is True
    assert incident is not None
    assert incident.status == "resolved"
    assert incident.resolved_at is not None

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.dto import StateChangedDTO
from app.models import Device, Incident
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService


BASE_TIME = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def make_service() -> tuple[DeviceService, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = DeviceService(
        factory,
        DeviceRepository(),
        IncidentRepository(),
        DeviceGrouping(),
        DeviceDebouncer(timedelta(seconds=30)),
    )
    return service, factory


def event(
    entity_id: str,
    state: str,
    device_id: str = "sonoff-device-1",
) -> StateChangedDTO:
    return StateChangedDTO(
        entity_id=entity_id,
        state=state,
        domain=entity_id.partition(".")[0],
        friendly_name=entity_id,
        time_fired=BASE_TIME,
        device_id=device_id,
    )


def test_grouped_entities_open_one_incident_after_debounce() -> None:
    service, factory = make_service()

    service.handle_state_changed(event("switch.sonoff_relay", "on"), BASE_TIME)
    service.handle_state_changed(
        event("sensor.sonoff_rssi", "unavailable"),
        BASE_TIME + timedelta(seconds=1),
    )
    service.handle_state_changed(
        event("switch.sonoff_relay", "unavailable"),
        BASE_TIME + timedelta(seconds=2),
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=32))

    with factory() as session:
        devices = session.scalars(select(Device)).all()
        incidents = session.scalars(select(Incident)).all()

    assert len(devices) == 2
    assert len(incidents) == 1
    assert incidents[0].entity_id == "sonoff-device-1"
    assert incidents[0].status == "open"


def test_short_flap_does_not_open_incident() -> None:
    service, factory = make_service()

    service.handle_state_changed(event("switch.sonoff_relay", "on"), BASE_TIME)
    service.handle_state_changed(
        event("switch.sonoff_relay", "unavailable"),
        BASE_TIME + timedelta(seconds=1),
    )
    service.handle_state_changed(
        event("switch.sonoff_relay", "on"),
        BASE_TIME + timedelta(seconds=10),
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=60))

    with factory() as session:
        incidents = session.scalars(select(Incident)).all()

    assert incidents == []
    assert service.diagnostics()[0]["last_state"] == "available"
    assert service.diagnostics()[0]["pending_state"] is None


def test_stable_recovery_resolves_group_incident_once() -> None:
    service, factory = make_service()

    service.handle_state_changed(event("switch.sonoff_relay", "on"), BASE_TIME)
    service.handle_state_changed(
        event("switch.sonoff_relay", "unavailable"),
        BASE_TIME + timedelta(seconds=1),
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=31))

    service.handle_state_changed(
        event("switch.sonoff_relay", "on"),
        BASE_TIME + timedelta(seconds=32),
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=62))

    with factory() as session:
        incident = session.scalar(select(Incident))

    assert incident is not None
    assert incident.status == "resolved"
    assert incident.resolved_at is not None

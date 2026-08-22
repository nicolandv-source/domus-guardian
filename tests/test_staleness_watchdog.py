from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.dto import StateChangedDTO
from app.models import Incident
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService
from app.services.health_weights import HealthWeights


BASE_TIME = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


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
    device_id: str,
    time_fired: datetime,
) -> StateChangedDTO:
    return StateChangedDTO(
        entity_id=entity_id,
        state=state,
        domain=entity_id.partition(".")[0],
        friendly_name=entity_id,
        time_fired=time_fired,
        device_id=device_id,
    )


def weights() -> HealthWeights:
    from pathlib import Path

    return HealthWeights.from_file(
        Path(__file__).parents[1] / "app" / "config" / "health_weights.json"
    )


def test_profile_carries_staleness_minutes_by_category_and_rule_override() -> None:
    from app.models import Device

    policy = weights()
    door = Device(
        entity_id="binary_sensor.door", domain="binary_sensor", device_class="door"
    )
    living_room_light = Device(entity_id="light.soggiorno", domain="light")
    sensor = Device(entity_id="sensor.temp_salotto", domain="sensor")
    tv = Device(entity_id="media_player.tv", domain="media_player")

    assert policy.profile_for([door]).staleness_minutes == 30  # rule override
    assert policy.profile_for([living_room_light]).staleness_minutes == 720  # category default
    assert policy.profile_for([sensor]).staleness_minutes == 1440  # rule override
    assert policy.profile_for([tv]).staleness_minutes is None  # optional: never checked


def test_silent_available_device_past_threshold_opens_staleness_incident() -> None:
    service, factory = make_service()
    service.handle_state_changed(
        event("sensor.temp_salotto", "on", "temp-1", BASE_TIME), BASE_TIME
    )

    opened, resolved = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(days=2)
    )

    assert len(opened) == 1
    assert opened[0].kind == "staleness"
    assert opened[0].entity_id == "temp-1"
    assert opened[0].severity == "info"  # "important" category, one notch softer
    assert resolved == []


def test_silent_device_within_threshold_does_not_open_incident() -> None:
    service, factory = make_service()
    service.handle_state_changed(
        event("sensor.temp_salotto", "on", "temp-1", BASE_TIME), BASE_TIME
    )

    opened, _ = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(hours=1)
    )

    assert opened == []


def test_optional_category_devices_are_never_flagged_stale() -> None:
    service, factory = make_service()
    service.handle_state_changed(
        event("media_player.tv", "on", "tv-1", BASE_TIME), BASE_TIME
    )

    opened, _ = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(days=30)
    )

    assert opened == []


def test_fresh_event_resolves_open_staleness_incident() -> None:
    service, factory = make_service()
    service.handle_state_changed(
        event("sensor.temp_salotto", "on", "temp-1", BASE_TIME), BASE_TIME
    )
    opened, _ = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(days=2)
    )
    assert len(opened) == 1

    service.handle_state_changed(
        event(
            "sensor.temp_salotto",
            "on",
            "temp-1",
            BASE_TIME + timedelta(days=2, minutes=1),
        ),
        BASE_TIME + timedelta(days=2, minutes=1),
    )
    _, resolved = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(days=2, minutes=2)
    )

    assert [incident.id for incident in resolved] == [opened[0].id]
    with factory() as session:
        incident = session.get(Incident, opened[0].id)
    assert incident.status == "resolved"


def test_maintenance_suppresses_staleness_incident() -> None:
    service, factory = make_service()
    service.handle_state_changed(
        event("sensor.temp_salotto", "on", "temp-1", BASE_TIME), BASE_TIME
    )
    service.activate_maintenance(
        "temp-1", "Sensore in test", None, BASE_TIME + timedelta(hours=1)
    )

    opened, _ = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(days=2)
    )

    assert opened == []


def test_already_unavailable_device_is_left_to_availability_path() -> None:
    service, factory = make_service()
    service.handle_state_changed(
        event("sensor.temp_salotto", "unavailable", "temp-1", BASE_TIME), BASE_TIME
    )

    opened, _ = service.check_stale_devices(
        weights().profile_for, BASE_TIME + timedelta(days=2)
    )

    assert opened == []

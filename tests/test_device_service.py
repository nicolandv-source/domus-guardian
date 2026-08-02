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


def test_maintenance_resolves_existing_incident_and_suppresses_new_one() -> None:
    service, factory = make_service()

    service.handle_state_changed(event("switch.box3", "on", "box-3"), BASE_TIME)
    service.handle_state_changed(
        event("switch.box3", "unavailable", "box-3"), BASE_TIME + timedelta(seconds=1)
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=31))

    window = service.activate_maintenance(
        "box-3", "Spento volontariamente", BASE_TIME + timedelta(days=1), BASE_TIME + timedelta(seconds=32)
    )
    service.handle_state_changed(
        event("switch.box3", "on", "box-3"), BASE_TIME + timedelta(seconds=33)
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=63))
    service.handle_state_changed(
        event("switch.box3", "unavailable", "box-3"), BASE_TIME + timedelta(seconds=64)
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=94))

    with factory() as session:
        incidents = session.scalars(select(Incident)).all()

    assert window.active is True
    assert len(incidents) == 1
    assert incidents[0].status == "resolved"


def test_expired_maintenance_restores_availability_monitoring() -> None:
    service, factory = make_service()
    service.handle_state_changed(event("switch.box3", "on", "box-3"), BASE_TIME)
    service.activate_maintenance(
        "box-3", "Finestra breve", BASE_TIME + timedelta(seconds=10), BASE_TIME
    )
    service.handle_state_changed(
        event("switch.box3", "unavailable", "box-3"), BASE_TIME + timedelta(seconds=11)
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=41))

    with factory() as session:
        incident = session.scalar(select(Incident))

    assert incident is not None
    assert incident.status == "open"


def test_tts_and_stt_service_entities_do_not_create_availability_incidents() -> None:
    service, factory = make_service()
    service.handle_state_changed(event("tts.google_translate", "unavailable"), BASE_TIME)
    service.handle_state_changed(event("stt.whisper", "unavailable"), BASE_TIME)
    service.flush_debounce(BASE_TIME + timedelta(seconds=60))

    with factory() as session:
        incidents = session.scalars(select(Incident)).all()

    assert incidents == []
    assert service.diagnostics() == []


def test_unavailable_dlna_without_sibling_opens_availability_incident() -> None:
    service, factory = make_service()
    service.register_entity_mapping(
        "media_player.camera_da_letto", "bedroom-tv", "dlna_dmr"
    )
    service.handle_state_changed(
        StateChangedDTO(
            entity_id="media_player.camera_da_letto",
            state="unavailable",
            domain="media_player",
            friendly_name="Google TV camera",
            device_id="bedroom-tv",
            time_fired=BASE_TIME,
        ),
        BASE_TIME,
    )
    service.flush_debounce(BASE_TIME + timedelta(seconds=30))

    with factory() as session:
        incidents = session.scalars(select(Incident)).all()

    assert len(incidents) == 1
    assert incidents[0].status == "open"
    assert service.diagnostics()[0]["entity_ids"] == (
        "media_player.camera_da_letto",
    )


def test_reconciliation_keeps_real_offline_and_resolves_invalid_history() -> None:
    service, factory = make_service()
    with factory.begin() as session:
        available = Device(
            entity_id="fan.helper", domain="fan", is_available=True, name="Helper"
        )
        unavailable = Device(
            entity_id="binary_sensor.real", domain="binary_sensor", is_available=False,
            name="Sensore reale",
        )
        session.add_all([available, unavailable])
        session.flush()
        session.add_all(
            [
                Incident(
                    device_id=available.id, entity_id="fan.helper", kind="availability",
                    severity="warning", status="open", title="Helper non disponibile",
                ),
                Incident(
                    device_id=unavailable.id, entity_id="physical-offline", kind="availability",
                    severity="critical", status="open", title="Sensore non disponibile",
                ),
            ]
        )

    resolved = service.reconcile_open_incidents(BASE_TIME)

    assert [incident.entity_id for incident in resolved] == ["fan.helper"]
    with factory() as session:
        incidents = {item.entity_id: item.status for item in session.scalars(select(Incident))}
    assert incidents == {"fan.helper": "resolved", "physical-offline": "open"}


def test_reconciliation_resolves_historical_tts_stt_incidents_keyed_by_device_id() -> None:
    service, factory = make_service()
    with factory.begin() as session:
        excluded = [
            Device(entity_id="tts.google_translate", domain="tts", is_available=False),
            Device(entity_id="tts.cloud", domain="tts", is_available=False),
            Device(entity_id="stt.whisper", domain="stt", is_available=False),
            Device(entity_id="stt.piper", domain="stt", is_available=False),
        ]
        real = Device(
            entity_id="binary_sensor.real", domain="binary_sensor", is_available=False,
            name="Sensore reale",
        )
        session.add_all([*excluded, real])
        session.flush()
        session.add_all(
            [
                Incident(
                    device_id=device.id,
                    entity_id=f"historical-device-{index}",
                    kind="availability",
                    severity="warning",
                    status="open",
                    title=f"{device.entity_id} non disponibile",
                )
                for index, device in enumerate(excluded, start=1)
            ]
            + [
                Incident(
                    device_id=real.id,
                    entity_id="historical-real-device",
                    kind="availability",
                    severity="critical",
                    status="open",
                    title="Sensore reale non disponibile",
                )
            ]
        )

    resolved = service.reconcile_open_incidents(BASE_TIME)

    assert {incident.entity_id for incident in resolved} == {
        f"historical-device-{index}" for index in range(1, 5)
    }
    with factory() as session:
        incidents = {incident.entity_id: incident for incident in session.scalars(select(Incident))}
    assert all(
        incidents[f"historical-device-{index}"].status == "resolved"
        and incidents[f"historical-device-{index}"].resolved_at is not None
        for index in range(1, 5)
    )
    assert incidents["historical-real-device"].status == "open"
    resolved_at = {
        key: incident.resolved_at
        for key, incident in incidents.items()
        if key.startswith("historical-device-")
    }

    assert service.reconcile_open_incidents(BASE_TIME + timedelta(minutes=1)) == []
    with factory() as session:
        incidents = {incident.entity_id: incident for incident in session.scalars(select(Incident))}
    assert all(
        incidents[f"historical-device-{index}"].resolved_at
        == resolved_at[f"historical-device-{index}"]
        for index in range(1, 5)
    )


def test_reconciliation_resolves_dlna_duplicate_with_available_sibling() -> None:
    service, factory = make_service()
    service.register_entity_mapping("media_player.tv", "kitchen-tv", "dlna_dmr")
    service.register_entity_mapping("media_player.tv_ue55au7170uxzt", "kitchen-tv", "samsungtv")
    service.handle_state_changed(event("media_player.tv", "unavailable", "kitchen-tv"), BASE_TIME)
    service.handle_state_changed(
        event("media_player.tv_ue55au7170uxzt", "on", "kitchen-tv"), BASE_TIME
    )
    with factory.begin() as session:
        dlna = session.scalar(select(Device).where(Device.entity_id == "media_player.tv"))
        assert dlna is not None
        session.add(Incident(device_id=dlna.id, entity_id="kitchen-tv", kind="availability", severity="warning", status="open", title="TV non disponibile"))

    resolved = service.reconcile_open_incidents(BASE_TIME)

    assert [incident.entity_id for incident in resolved] == ["kitchen-tv"]


def test_reconciliation_resolves_dlna_duplicate_with_off_but_available_sibling() -> None:
    service, factory = make_service()
    service.register_entity_mapping("media_player.75_qled", "living-tv", "dlna_dmr")
    service.register_entity_mapping("media_player.75_qled_qe75q60dauxxh_2", "living-tv", "samsungtv")
    service.handle_state_changed(event("media_player.75_qled", "unavailable", "living-tv"), BASE_TIME)
    service.handle_state_changed(
        event("media_player.75_qled_qe75q60dauxxh_2", "off", "living-tv"), BASE_TIME
    )
    with factory.begin() as session:
        dlna = session.scalar(select(Device).where(Device.entity_id == "media_player.75_qled"))
        assert dlna is not None
        session.add(Incident(device_id=dlna.id, entity_id="living-tv", kind="availability", severity="warning", status="open", title="TV non disponibile"))

    resolved = service.reconcile_open_incidents(BASE_TIME)

    assert [incident.entity_id for incident in resolved] == ["living-tv"]


def test_reconciliation_keeps_dlna_when_non_dlna_sibling_is_unavailable() -> None:
    service, factory = make_service()
    service.register_entity_mapping("media_player.tv", "kitchen-tv", "dlna_dmr")
    service.register_entity_mapping("media_player.tv_ue55au7170uxzt", "kitchen-tv", "samsungtv")
    service.handle_state_changed(event("media_player.tv", "unavailable", "kitchen-tv"), BASE_TIME)
    service.handle_state_changed(
        event("media_player.tv_ue55au7170uxzt", "unavailable", "kitchen-tv"), BASE_TIME
    )
    with factory.begin() as session:
        dlna = session.scalar(select(Device).where(Device.entity_id == "media_player.tv"))
        assert dlna is not None
        session.add(Incident(device_id=dlna.id, entity_id="kitchen-tv", kind="availability", severity="warning", status="open", title="TV non disponibile"))

    assert service.reconcile_open_incidents(BASE_TIME) == []


def test_dlna_reconciliation_is_idempotent_across_two_starts() -> None:
    service, factory = make_service()
    service.register_entity_mapping("media_player.tv", "kitchen-tv", "dlna_dmr")
    service.register_entity_mapping("media_player.tv_ue55au7170uxzt", "kitchen-tv", "samsungtv")
    service.handle_state_changed(event("media_player.tv", "unavailable", "kitchen-tv"), BASE_TIME)
    service.handle_state_changed(
        event("media_player.tv_ue55au7170uxzt", "off", "kitchen-tv"), BASE_TIME
    )
    with factory.begin() as session:
        dlna = session.scalar(select(Device).where(Device.entity_id == "media_player.tv"))
        assert dlna is not None
        session.add(Incident(device_id=dlna.id, entity_id="kitchen-tv", kind="availability", severity="warning", status="open", title="TV non disponibile"))

    first = service.reconcile_open_incidents(BASE_TIME)
    second = service.reconcile_open_incidents(BASE_TIME + timedelta(minutes=1))

    assert len(first) == 1
    assert second == []

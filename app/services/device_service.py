from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.dto import StateChangedDTO
from app.core.event_bus import EventBus
from app.models import Device, Incident, MaintenanceWindow
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.repositories.maintenance import MaintenanceRepository
from app.services.device_debounce import DeviceDebouncer, DeviceStateChange
from app.services.device_grouping import DeviceGrouping
from app.services.health_weights import DeviceProfile


logger = logging.getLogger(__name__)


class DeviceService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        device_repository: DeviceRepository,
        incident_repository: IncidentRepository,
        grouping: DeviceGrouping,
        debouncer: DeviceDebouncer,
        event_bus: EventBus | None = None,
        profile_for: Callable[[list[Device]], DeviceProfile] | None = None,
        maintenance_repository: MaintenanceRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._devices = device_repository
        self._incidents = incident_repository
        self._grouping = grouping
        self._debouncer = debouncer
        self._event_bus = event_bus
        self._profile_for = profile_for
        self._maintenance = maintenance_repository or MaintenanceRepository()

    def register_entity_mapping(
        self,
        entity_id: str,
        device_id: str | None,
        platform: str | None = None,
    ) -> None:
        self._grouping.register_entity_mapping(entity_id, device_id, platform)

    def register_device_registry_entry(self, entry: dict[str, Any]) -> None:
        self._grouping.register_device_registry_entry(entry)

    def is_physical_entity(self, entity_id: str) -> bool:
        return self._grouping.is_physical_entity(entity_id)

    def handle_state_changed(
        self,
        dto: StateChangedDTO,
        now: datetime | None = None,
    ) -> DeviceStateChange | None:
        now = now or datetime.now(timezone.utc)
        events: list[tuple[str, dict[str, object]]] = []
        with self._session_factory() as session:
            with session.begin():
                self._devices.upsert(session, dto)
                if dto.domain in {"tts", "stt"}:
                    return None
                # Keep each physical registry entity's state for sibling-aware
                # reconciliation during the initial Home Assistant snapshot.
                self._grouping.update(dto)
                grouped = self._grouping.snapshot_for_entity(dto.entity_id)
                if grouped is None:
                    return None
                change = self._debouncer.process_state_change(
                    grouped.device_id,
                    grouped.is_available,
                    now,
                )
                if change is not None:
                    events.extend(self._apply_change(session, change))
        self._publish_events(events)
        return change

    def reconcile_open_incidents(
        self, now: datetime | None = None
    ) -> list[Incident]:
        """Resolve only availability incidents contradicted by persisted HA state.

        State snapshots are written before availability processing, so this is
        safe during startup and after every WebSocket state refresh.  It never
        deletes history and deliberately leaves genuinely unavailable devices
        (and active maintenance windows) untouched.
        """
        now = now or datetime.now(timezone.utc)
        events: list[tuple[str, dict[str, object]]] = []
        resolved: list[Incident] = []
        with self._session_factory() as session:
            with session.begin():
                for incident in self._incidents.list_open_availability(session):
                    source_entity_id = self._incident_source_entity_id(incident)
                    grouped = (
                        self._grouping.snapshot_for_entity(source_entity_id)
                        if source_entity_id
                        else self._grouping.snapshot(incident.entity_id)
                    )
                    group_id = grouped.device_id if grouped is not None else incident.entity_id
                    if self._maintenance.get_active(session, group_id, now):
                        continue

                    available = (
                        grouped.is_available
                        if grouped is not None
                        else self._incident_device_available(incident)
                    )
                    # Historical availability incidents for UI helpers and
                    # excluded service integrations are invalid: neither can
                    # enter physical-device monitoring now.  Resolve them but
                    # never delete them, so the incident audit trail remains.
                    is_helper = (
                        grouped is None
                        and incident.device is not None
                        and incident.entity_id == incident.device.entity_id
                        and not self._grouping.is_physical_entity(incident.entity_id)
                    )
                    is_excluded_history = self._is_excluded_incident(incident)
                    if available or is_helper or is_excluded_history:
                        incident.status = "resolved"
                        incident.resolved_at = now
                        resolved.append(incident)
                session.flush()
                events.extend(
                    ("incident_resolved", self._incident_payload(incident))
                    for incident in resolved
                )
        self._publish_events(events)
        return resolved

    def check_stale_devices(
        self,
        profile_for: Callable[[list[Device]], DeviceProfile],
        now: datetime | None = None,
    ) -> tuple[list[Incident], list[Incident]]:
        """Flag devices that stopped reporting state without HA marking them unavailable.

        Independent from the availability debounce: silence is judged from
        ``Device.last_seen_at`` against a per-category threshold from
        ``health_weights.json``, not from state. A device already flagged
        unavailable is left to the availability path; this only watches
        devices HA still calls available.
        """
        now = now or datetime.now(timezone.utc)
        opened: list[Incident] = []
        resolved: list[Incident] = []
        with self._session_factory() as session:
            with session.begin():
                grouped_devices = self._grouping.all_snapshots()
                entity_ids = {
                    entity_id
                    for grouped in grouped_devices
                    for entity_id in grouped.entity_ids
                }
                known_devices = {
                    device.entity_id: device
                    for device in session.scalars(
                        select(Device).where(Device.entity_id.in_(entity_ids))
                    )
                }
                for grouped in grouped_devices:
                    devices = [
                        known_devices[entity_id]
                        for entity_id in grouped.entity_ids
                        if entity_id in known_devices
                    ]
                    if not devices:
                        continue
                    if self._maintenance.get_active(session, grouped.device_id, now):
                        resolved.extend(
                            self._incidents.resolve_staleness(
                                session, [grouped.device_id], now
                            )
                        )
                        continue

                    profile = profile_for(devices)
                    last_seen = max(
                        (
                            self._as_aware(device.last_seen_at)
                            for device in devices
                            if device.last_seen_at
                        ),
                        default=None,
                    )
                    is_stale = (
                        profile.staleness_minutes is not None
                        and grouped.is_available
                        and last_seen is not None
                        and now - last_seen > timedelta(minutes=profile.staleness_minutes)
                    )
                    if not is_stale:
                        resolved.extend(
                            self._incidents.resolve_staleness(
                                session, [grouped.device_id], now
                            )
                        )
                        continue

                    representative = next(
                        (
                            device
                            for device in devices
                            if device.entity_id == grouped.representative_entity_id
                        ),
                        devices[0],
                    )
                    minutes_silent = int((now - last_seen).total_seconds() // 60)
                    incident, created = self._incidents.open_staleness(
                        session,
                        representative,
                        grouped.device_id,
                        self._staleness_severity(profile.category),
                        minutes_silent=minutes_silent,
                        threshold_minutes=profile.staleness_minutes,
                    )
                    if created:
                        logger.warning(
                            "Incidente silenzio aperto: %s (%s minuti)",
                            grouped.device_id,
                            minutes_silent,
                        )
                        opened.append(incident)
        events = [("incident_opened", self._incident_payload(incident)) for incident in opened]
        events.extend(
            ("incident_resolved", self._incident_payload(incident)) for incident in resolved
        )
        self._publish_events(events)
        return opened, resolved

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        # SQLite (used in tests) does not round-trip tzinfo on DateTime(timezone=True)
        # columns; PostgreSQL does. Treat a naive read as UTC, matching how it was written.
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _staleness_severity(category: str) -> str:
        # One notch softer than availability: silence is inferred, not
        # confirmed offline by Home Assistant, so it should never read as
        # equally certain as a reported ``unavailable`` state.
        return {"critical": "warning", "important": "info"}.get(category, "info")

    def flush_debounce(
        self,
        now: datetime | None = None,
    ) -> list[DeviceStateChange]:
        now = now or datetime.now(timezone.utc)
        changes = self._debouncer.flush_due(now)
        events: list[tuple[str, dict[str, object]]] = []
        for change in changes:
            with self._session_factory() as session:
                with session.begin():
                    events.extend(self._apply_change(session, change))
        self._publish_events(events)
        return changes

    def diagnostics(
        self,
        profile_for: Callable[[list[Device]], DeviceProfile] | None = None,
    ) -> list[dict[str, object]]:
        debounce = self._debouncer.diagnostics()
        grouped_devices = self._grouping.all_snapshots()
        entity_ids = {
            entity_id for grouped in grouped_devices for entity_id in grouped.entity_ids
        }
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            known_devices = {
                device.entity_id: device
                for device in session.scalars(
                    select(Device).where(Device.entity_id.in_(entity_ids))
                )
            }
            maintenance_by_device = {
                grouped.device_id: self._maintenance.get_active(
                    session, grouped.device_id, now
                )
                is not None
                for grouped in grouped_devices
            }
        result = []
        for grouped in grouped_devices:
            state = debounce.get(grouped.device_id)
            item: dict[str, object] = {
                "device_id": grouped.device_id,
                "entity_ids": grouped.entity_ids,
                "last_state": self._state_label(state.last_state) if state else None,
                "pending_state": self._state_label(state.pending_state)
                if state and state.pending_state is not None
                else None,
                "debounce_until": state.debounce_until if state else None,
                "last_change_time": state.last_change_time if state else None,
                "maintenance_active": maintenance_by_device[grouped.device_id],
            }
            if profile_for is not None:
                profile = profile_for(
                    [
                        known_devices[entity_id]
                        for entity_id in grouped.entity_ids
                        if entity_id in known_devices
                    ]
                )
                item.update(
                    {
                        "category": profile.category,
                        "weight": profile.weight,
                        "include_in_score": profile.include_in_score,
                    }
                )
            result.append(item)
        return result

    def activate_maintenance(
        self,
        device_id: str,
        reason: str,
        ends_at: datetime | None = None,
        now: datetime | None = None,
    ) -> MaintenanceWindow:
        now = now or datetime.now(timezone.utc)
        events: list[tuple[str, dict[str, object]]] = []
        with self._session_factory() as session:
            with session.begin():
                window = self._maintenance.activate(
                    session, device_id=device_id, reason=reason, started_at=now, ends_at=ends_at
                )
                grouped = self._grouping.snapshot(device_id)
                keys = [device_id, *(grouped.entity_ids if grouped else ())]
                resolved = self._incidents.resolve_availability(session, keys, now)
                events.extend(("incident_resolved", self._incident_payload(item)) for item in resolved)
        self._publish_events(events)
        return window

    def deactivate_maintenance(
        self, device_id: str, now: datetime | None = None
    ) -> MaintenanceWindow | None:
        now = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                return self._maintenance.deactivate(session, device_id, now)

    def list_maintenance(self, now: datetime | None = None) -> list[MaintenanceWindow]:
        now = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                return self._maintenance.list(session, now)

    def _apply_change(
        self, session: Session, change: DeviceStateChange
    ) -> list[tuple[str, dict[str, object]]]:
        grouped = self._grouping.snapshot(change.device_id)
        if grouped is None:
            return []
        if self._maintenance.get_active(session, change.device_id, change.changed_at):
            logger.info("Disponibilità esclusa per manutenzione: %s", change.device_id)
            return []
        device = self._devices.get_by_entity_id(
            session,
            grouped.representative_entity_id,
        )
        if device is None:
            logger.warning("Device di riferimento non trovato: %s", change.device_id)
            return []

        if change.new_state:
            resolved = self._incidents.resolve_availability(
                session,
                [change.device_id, *grouped.entity_ids],
                change.changed_at,
            )
            if resolved:
                logger.warning("Incidente risolto: %s", change.device_id)
            return [
                ("incident_resolved", self._incident_payload(incident))
                for incident in resolved
            ]

        severity = self._incident_severity(session, grouped.entity_ids)
        incident, created = self._incidents.open_availability(
            session,
            device,
            change.device_id,
            severity,
        )
        if created:
            logger.warning("Incidente aperto: %s", incident.entity_id)
            return [("incident_opened", self._incident_payload(incident))]
        return []

    def _incident_severity(self, session: Session, entity_ids: tuple[str, ...]) -> str:
        if self._profile_for is None:
            return "critical"
        devices = list(
            session.scalars(select(Device).where(Device.entity_id.in_(entity_ids)))
        )
        category = self._profile_for(devices).category
        return {"critical": "critical", "important": "warning"}.get(category, "info")

    def _publish_events(self, events: list[tuple[str, dict[str, object]]]) -> None:
        if self._event_bus is None:
            return
        for event_type, payload in events:
            self._event_bus.publish(event_type, payload)

    @staticmethod
    def _incident_payload(incident: Incident) -> dict[str, object]:
        return {
            "incident_id": incident.id,
            "incident_key": incident.entity_id,
            "severity": incident.severity,
        }

    @staticmethod
    def _state_label(state: bool | None) -> str | None:
        if state is None:
            return None
        return "available" if state else "unavailable"

    @staticmethod
    def _is_excluded_incident(incident: Incident) -> bool:
        device = incident.device
        identities = [incident.entity_id]
        # Grouped availability incidents use the Home Assistant device_id as
        # their incident key.  Historical rows can therefore not be classified
        # from the key alone: use their linked entity as a second identity.
        if device is not None:
            identities.append(device.entity_id)
        return any(
            entity_id.partition(".")[0] in {"tts", "stt"} for entity_id in identities
        )

    @staticmethod
    def _incident_source_entity_id(incident: Incident) -> str | None:
        if incident.device is not None:
            return incident.device.entity_id
        if incident.entity_id.startswith("media_player."):
            return incident.entity_id
        return None

    def _incident_device_available(self, incident: Incident) -> bool:
        return incident.device is not None and incident.device.is_available

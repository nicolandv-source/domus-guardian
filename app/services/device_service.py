from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.dto import StateChangedDTO
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer, DeviceStateChange
from app.services.device_grouping import DeviceGrouping


logger = logging.getLogger(__name__)


class DeviceService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        device_repository: DeviceRepository,
        incident_repository: IncidentRepository,
        grouping: DeviceGrouping,
        debouncer: DeviceDebouncer,
    ) -> None:
        self._session_factory = session_factory
        self._devices = device_repository
        self._incidents = incident_repository
        self._grouping = grouping
        self._debouncer = debouncer

    def register_entity_mapping(self, entity_id: str, device_id: str | None) -> None:
        self._grouping.register_entity_mapping(entity_id, device_id)

    def handle_state_changed(
        self,
        dto: StateChangedDTO,
        now: datetime | None = None,
    ) -> DeviceStateChange | None:
        now = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            with session.begin():
                self._devices.upsert(session, dto)
                grouped = self._grouping.update(dto)
                change = self._debouncer.process_state_change(
                    grouped.device_id,
                    grouped.is_available,
                    now,
                )
                if change is not None:
                    self._apply_change(session, change)
        return change

    def flush_debounce(
        self,
        now: datetime | None = None,
    ) -> list[DeviceStateChange]:
        now = now or datetime.now(timezone.utc)
        changes = self._debouncer.flush_due(now)
        for change in changes:
            with self._session_factory() as session:
                with session.begin():
                    self._apply_change(session, change)
        return changes

    def diagnostics(self) -> list[dict[str, object]]:
        debounce = self._debouncer.diagnostics()
        result = []
        for grouped in self._grouping.all_snapshots():
            state = debounce.get(grouped.device_id)
            result.append(
                {
                    "device_id": grouped.device_id,
                    "entity_ids": grouped.entity_ids,
                    "last_state": self._state_label(state.last_state)
                    if state
                    else None,
                    "pending_state": self._state_label(state.pending_state)
                    if state and state.pending_state is not None
                    else None,
                    "debounce_until": state.debounce_until if state else None,
                    "last_change_time": state.last_change_time if state else None,
                }
            )
        return result

    def _apply_change(self, session: Session, change: DeviceStateChange) -> None:
        grouped = self._grouping.snapshot(change.device_id)
        if grouped is None:
            return
        device = self._devices.get_by_entity_id(
            session,
            grouped.representative_entity_id,
        )
        if device is None:
            logger.warning("Device di riferimento non trovato: %s", change.device_id)
            return

        if change.new_state:
            resolved = self._incidents.resolve_availability(
                session,
                [change.device_id, *grouped.entity_ids],
                change.changed_at,
            )
            if resolved:
                logger.warning("Incidente risolto: %s", change.device_id)
            return

        incident, created = self._incidents.open_availability(
            session,
            device,
            change.device_id,
        )
        if created:
            logger.warning("Incidente aperto: %s", incident.entity_id)

    @staticmethod
    def _state_label(state: bool | None) -> str | None:
        if state is None:
            return None
        return "available" if state else "unavailable"

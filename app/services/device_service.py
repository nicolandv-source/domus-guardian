import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.dto import StateChangedDTO
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository


logger = logging.getLogger(__name__)


class DeviceService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        device_repository: DeviceRepository,
        incident_repository: IncidentRepository,
    ) -> None:
        self._session_factory = session_factory
        self._devices = device_repository
        self._incidents = incident_repository

    def handle_state_changed(self, dto: StateChangedDTO) -> None:
        with self._session_factory() as session:
            with session.begin():
                device = self._devices.upsert(session, dto)
                if dto.is_available:
                    incident = self._incidents.resolve_availability(
                        session,
                        dto.entity_id,
                        dto.time_fired,
                    )
                    if incident is not None:
                        logger.warning("Incidente risolto: %s", dto.entity_id)
                else:
                    incident, created = self._incidents.open_availability(
                        session,
                        device,
                    )
                    if created:
                        logger.warning("Incidente aperto: %s", incident.entity_id)

        logger.info(
            "Device aggiornato: %s state=%s available=%s",
            dto.entity_id,
            dto.state,
            dto.is_available,
        )

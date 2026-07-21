from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dto import StateChangedDTO
from app.models import Device


class DeviceRepository:
    def get_by_entity_id(self, session: Session, entity_id: str) -> Device | None:
        return session.scalar(select(Device).where(Device.entity_id == entity_id))

    def upsert(self, session: Session, dto: StateChangedDTO) -> Device:
        device = self.get_by_entity_id(session, dto.entity_id)
        if device is None:
            device = Device(entity_id=dto.entity_id, domain=dto.domain)
            session.add(device)

        device.name = dto.friendly_name or device.name
        device.state = dto.state
        device.is_available = dto.is_available
        device.last_seen_at = dto.time_fired
        session.flush()
        return device

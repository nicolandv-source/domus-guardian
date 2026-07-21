from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, Incident


AVAILABILITY_KIND = "availability"


class IncidentRepository:
    def get_open_availability(
        self,
        session: Session,
        entity_id: str,
    ) -> Incident | None:
        return session.scalar(
            select(Incident).where(
                Incident.entity_id == entity_id,
                Incident.kind == AVAILABILITY_KIND,
                Incident.status == "open",
            )
        )

    def open_availability(
        self,
        session: Session,
        device: Device,
    ) -> tuple[Incident, bool]:
        existing = self.get_open_availability(session, device.entity_id)
        if existing is not None:
            return existing, False

        incident = Incident(
            device_id=device.id,
            entity_id=device.entity_id,
            kind=AVAILABILITY_KIND,
            severity="critical",
            status="open",
            title=f"{device.name or device.entity_id} non disponibile",
            description="Home Assistant ha segnalato lo stato unavailable.",
        )
        session.add(incident)
        session.flush()
        return incident, True

    def resolve_availability(
        self,
        session: Session,
        entity_id: str,
        resolved_at: datetime,
    ) -> Incident | None:
        incident = self.get_open_availability(session, entity_id)
        if incident is None:
            return None
        incident.status = "resolved"
        incident.resolved_at = resolved_at
        session.flush()
        return incident

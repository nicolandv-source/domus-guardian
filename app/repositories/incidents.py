from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, Incident


AVAILABILITY_KIND = "availability"


class IncidentRepository:
    def get_open_availability(
        self,
        session: Session,
        incident_key: str,
    ) -> Incident | None:
        return session.scalar(
            select(Incident).where(
                Incident.entity_id == incident_key,
                Incident.kind == AVAILABILITY_KIND,
                Incident.status == "open",
            )
        )

    def open_availability(
        self,
        session: Session,
        device: Device,
        incident_key: str,
    ) -> tuple[Incident, bool]:
        existing = self.get_open_availability(session, incident_key)
        if existing is not None:
            return existing, False

        incident = Incident(
            device_id=device.id,
            entity_id=incident_key,
            kind=AVAILABILITY_KIND,
            severity="critical",
            status="open",
            title=f"{device.name or incident_key} non disponibile",
            description="Home Assistant ha segnalato lo stato unavailable.",
        )
        session.add(incident)
        session.flush()
        return incident, True

    def resolve_availability(
        self,
        session: Session,
        incident_keys: Iterable[str],
        resolved_at: datetime,
    ) -> list[Incident]:
        incidents = session.scalars(
            select(Incident).where(
                Incident.entity_id.in_(set(incident_keys)),
                Incident.kind == AVAILABILITY_KIND,
                Incident.status == "open",
            )
        ).all()
        for incident in incidents:
            incident.status = "resolved"
            incident.resolved_at = resolved_at
        session.flush()
        return incidents

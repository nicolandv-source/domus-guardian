from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, Incident


AVAILABILITY_KIND = "availability"
STALENESS_KIND = "staleness"


class IncidentRepository:
    def list_open_availability(self, session: Session) -> list[Incident]:
        return self._list_open(session, AVAILABILITY_KIND)

    def list_open_staleness(self, session: Session) -> list[Incident]:
        return self._list_open(session, STALENESS_KIND)

    def get_open_availability(
        self,
        session: Session,
        incident_key: str,
    ) -> Incident | None:
        return self._get_open(session, incident_key, AVAILABILITY_KIND)

    def get_open_staleness(
        self,
        session: Session,
        incident_key: str,
    ) -> Incident | None:
        return self._get_open(session, incident_key, STALENESS_KIND)

    def open_availability(
        self,
        session: Session,
        device: Device,
        incident_key: str,
        severity: str = "critical",
    ) -> tuple[Incident, bool]:
        return self._open(
            session,
            device,
            incident_key,
            AVAILABILITY_KIND,
            severity,
            title=f"{device.name or incident_key} non disponibile",
            description="Home Assistant ha segnalato lo stato unavailable.",
        )

    def open_staleness(
        self,
        session: Session,
        device: Device,
        incident_key: str,
        severity: str,
        *,
        minutes_silent: int,
        threshold_minutes: int,
    ) -> tuple[Incident, bool]:
        return self._open(
            session,
            device,
            incident_key,
            STALENESS_KIND,
            severity,
            title=f"{device.name or incident_key} silenzioso",
            description=(
                f"Nessun aggiornamento di stato da {minutes_silent} minuti "
                f"(soglia attesa per questo tipo di dispositivo: {threshold_minutes} minuti). "
                "Home Assistant non lo segnala come unavailable: potrebbe essere solo silenzioso, "
                "non necessariamente offline."
            ),
        )

    def resolve_availability(
        self,
        session: Session,
        incident_keys: Iterable[str],
        resolved_at: datetime,
    ) -> list[Incident]:
        return self._resolve(session, incident_keys, AVAILABILITY_KIND, resolved_at)

    def resolve_staleness(
        self,
        session: Session,
        incident_keys: Iterable[str],
        resolved_at: datetime,
    ) -> list[Incident]:
        return self._resolve(session, incident_keys, STALENESS_KIND, resolved_at)

    def _list_open(self, session: Session, kind: str) -> list[Incident]:
        return session.scalars(
            select(Incident).where(
                Incident.kind == kind,
                Incident.status == "open",
            )
        ).all()

    def _get_open(
        self, session: Session, incident_key: str, kind: str
    ) -> Incident | None:
        return session.scalar(
            select(Incident).where(
                Incident.entity_id == incident_key,
                Incident.kind == kind,
                Incident.status == "open",
            )
        )

    def _open(
        self,
        session: Session,
        device: Device,
        incident_key: str,
        kind: str,
        severity: str,
        *,
        title: str,
        description: str,
    ) -> tuple[Incident, bool]:
        existing = self._get_open(session, incident_key, kind)
        if existing is not None:
            return existing, False

        incident = Incident(
            device_id=device.id,
            entity_id=incident_key,
            kind=kind,
            severity=severity,
            status="open",
            title=title,
            description=description,
        )
        session.add(incident)
        session.flush()
        return incident, True

    def _resolve(
        self,
        session: Session,
        incident_keys: Iterable[str],
        kind: str,
        resolved_at: datetime,
    ) -> list[Incident]:
        incidents = session.scalars(
            select(Incident).where(
                Incident.entity_id.in_(set(incident_keys)),
                Incident.kind == kind,
                Incident.status == "open",
            )
        ).all()
        for incident in incidents:
            incident.status = "resolved"
            incident.resolved_at = resolved_at
        session.flush()
        return incidents

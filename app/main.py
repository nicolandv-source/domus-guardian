from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select

from app.adapters.home_assistant import HomeAssistantAdapter
from app.core.event_bus import EventBus
from app.database import SessionLocal, ping_database
from app.ha.websocket import HomeAssistantWebSocketClient
from app.models import Device, Incident
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_service import DeviceService
from app.settings import get_settings


logger = logging.getLogger(__name__)
logging.getLogger("app.ha.websocket").setLevel(logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    event_bus = EventBus()
    service = DeviceService(
        session_factory=SessionLocal,
        device_repository=DeviceRepository(),
        incident_repository=IncidentRepository(),
    )
    adapter = HomeAssistantAdapter(event_bus, service)
    adapter.subscribe()

    websocket_client = HomeAssistantWebSocketClient(
        url=settings.ha_ws_url,
        token=settings.ha_token,
        event_bus=event_bus,
    )
    websocket_task = asyncio.create_task(
        websocket_client.run_forever(),
        name="home-assistant-websocket",
    )
    app.state.websocket_client = websocket_client
    app.state.websocket_task = websocket_task

    try:
        yield
    finally:
        adapter.unsubscribe()
        websocket_task.cancel()
        with suppress(asyncio.CancelledError):
            await websocket_task
        logger.info("Servizi DOMUS Guardian arrestati")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)


def calculate_health_score_status(
    *,
    database_connected: bool,
    critical_incidents: int,
    warning_incidents: int,
    offline_devices: int,
) -> tuple[int, str]:
    score = 100
    if not database_connected:
        score -= 40
    score -= critical_incidents * 25
    score -= warning_incidents * 10
    score -= offline_devices * 5
    score = max(0, score)

    if not database_connected or critical_incidents:
        status = "critical"
    elif warning_incidents or offline_devices:
        status = "warning"
    else:
        status = "healthy"
    return score, status


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }


@app.get("/api/v1/db/ping")
def database_ping() -> dict[str, object]:
    try:
        return ping_database()
    except Exception as exc:
        logger.exception("Verifica connessione PostgreSQL fallita")
        raise HTTPException(
            status_code=503,
            detail="Connessione PostgreSQL non disponibile",
        ) from exc


@app.get("/api/v1/ha/ping")
async def home_assistant_ping() -> dict[str, object]:
    if not settings.ha_token:
        raise HTTPException(status_code=500, detail="HA_TOKEN non disponibile")

    headers = {
        "Authorization": f"Bearer {settings.ha_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.ha_request_timeout_seconds,
            verify=settings.ha_verify_ssl,
        ) as client:
            response = await client.get(
                f"{settings.ha_url}/api/config",
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("Verifica connessione Home Assistant fallita")
        raise HTTPException(
            status_code=502,
            detail="Connessione Home Assistant non disponibile",
        ) from exc

    config = response.json()
    return {
        "connected": True,
        "home_assistant_version": config.get("version"),
        "location_name": config.get("location_name"),
    }


@app.get("/api/v1/devices")
def list_devices() -> list[dict[str, object]]:
    with SessionLocal() as session:
        devices = session.scalars(select(Device).order_by(Device.entity_id)).all()
        return [
            {
                "entity_id": device.entity_id,
                "name": device.name,
                "domain": device.domain,
                "state": device.state,
                "is_available": device.is_available,
                "last_seen_at": device.last_seen_at,
            }
            for device in devices
        ]


@app.get("/api/v1/incidents")
def list_incidents() -> list[dict[str, object]]:
    with SessionLocal() as session:
        incidents = session.scalars(
            select(Incident).order_by(Incident.opened_at.desc())
        ).all()
        return [
            {
                "id": incident.id,
                "entity_id": incident.entity_id,
                "kind": incident.kind,
                "severity": incident.severity,
                "status": incident.status,
                "title": incident.title,
                "opened_at": incident.opened_at,
                "resolved_at": incident.resolved_at,
            }
            for incident in incidents
        ]


@app.get("/api/v1/ha/health")
def health() -> dict[str, object]:
    database_connected = False
    database_error: str | None = None
    active_incidents = 0
    critical_incidents = 0
    warning_incidents = 0
    offline_devices = 0

    try:
        ping_database()
        database_connected = True
        with SessionLocal() as session:
            active_incidents = (
                session.scalar(
                    select(func.count())
                    .select_from(Incident)
                    .where(Incident.status == "open")
                )
                or 0
            )
            critical_incidents = (
                session.scalar(
                    select(func.count())
                    .select_from(Incident)
                    .where(
                        Incident.status == "open",
                        Incident.severity == "critical",
                    )
                )
                or 0
            )
            warning_incidents = (
                session.scalar(
                    select(func.count())
                    .select_from(Incident)
                    .where(
                        Incident.status == "open",
                        Incident.severity == "warning",
                    )
                )
                or 0
            )
            offline_devices = (
                session.scalar(
                    select(func.count())
                    .select_from(Device)
                    .where(Device.is_available.is_(False))
                )
                or 0
            )
    except Exception as exc:
        logger.exception("Calcolo health DOMUS fallito")
        database_error = type(exc).__name__

    score, status = calculate_health_score_status(
        database_connected=database_connected,
        critical_incidents=critical_incidents,
        warning_incidents=warning_incidents,
        offline_devices=offline_devices,
    )

    return {
        "score": score,
        "status": status,
        "database_connected": database_connected,
        "database_error": database_error,
        "active_incidents": active_incidents,
        "critical_incidents": critical_incidents,
        "warning_incidents": warning_incidents,
        "offline_devices": offline_devices,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

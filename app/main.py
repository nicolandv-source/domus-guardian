from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.adapters.home_assistant import HomeAssistantAdapter
from app.core.event_bus import EventBus
from app.database import SessionLocal, ping_database
from app.ha.websocket import HomeAssistantWebSocketClient
from app.models import Device, Incident
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService
from app.services.health_engine import HealthEngine
from app.services.health_weights import HealthWeights
from app.settings import get_settings


logger = logging.getLogger(__name__)
logging.getLogger("app.ha.websocket").setLevel(logging.INFO)
settings = get_settings()


def debounce_window() -> timedelta:
    seconds = int(os.getenv("DEVICE_DEBOUNCE_SECONDS", "45"))
    return timedelta(seconds=max(5, min(seconds, 300)))


def health_weights() -> HealthWeights:
    return HealthWeights.from_file(
        Path(__file__).parent / "config" / "health_weights.json"
    )


async def run_debounce_worker(service: DeviceService) -> None:
    while True:
        changes = service.flush_debounce()
        if changes:
            logger.info("Applicati %s cambi stabilizzati", len(changes))
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    event_bus = EventBus()
    grouping = DeviceGrouping()
    debouncer = DeviceDebouncer(debounce_window())
    service = DeviceService(
        session_factory=SessionLocal,
        device_repository=DeviceRepository(),
        incident_repository=IncidentRepository(),
        grouping=grouping,
        debouncer=debouncer,
    )
    weights = health_weights()
    health_engine = HealthEngine(service, weights)
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
    debounce_task = asyncio.create_task(
        run_debounce_worker(service),
        name="device-debounce-worker",
    )
    app.state.device_service = service
    app.state.health_engine = health_engine
    app.state.health_weights = weights
    app.state.websocket_task = websocket_task
    app.state.debounce_task = debounce_task

    try:
        yield
    finally:
        adapter.unsubscribe()
        for task in (websocket_task, debounce_task):
            task.cancel()
        for task in (websocket_task, debounce_task):
            with suppress(asyncio.CancelledError):
                await task
        logger.info("Servizi DOMUS Guardian arrestati")


app = FastAPI(
    title=settings.app_name,
    version=os.getenv("APP_VERSION", settings.app_version),
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": os.getenv("APP_VERSION", settings.app_version),
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


@app.get("/api/v1/devices/debounced")
def list_debounced_devices() -> list[dict[str, object]]:
    return app.state.device_service.diagnostics(app.state.health_weights.profile_for)


@app.get("/api/v1/health/weights")
def list_health_weights() -> list[dict[str, object]]:
    return list_debounced_devices()


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
    try:
        ping_database()
        database_connected = True
    except Exception as exc:
        logger.exception("Calcolo health DOMUS fallito")
        database_error = type(exc).__name__

    snapshot = app.state.health_engine.snapshot(database_connected)
    return {
        "score": snapshot.score,
        "status": snapshot.status,
        "database_connected": database_connected,
        "database_error": database_error,
        "active_incidents": snapshot.active_incidents,
        "critical_incidents": snapshot.critical_incidents,
        "warning_incidents": snapshot.warning_incidents,
        "offline_devices": snapshot.offline_devices,
        "total_weight": snapshot.total_weight,
        "offline_devices_weighted": snapshot.offline_weight,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

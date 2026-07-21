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
from app.adapters.home_assistant_notify import HomeAssistantNotifyAdapter
from app.core.event_bus import EventBus
from app.database import SessionLocal, ping_database
from app.ha.websocket import HomeAssistantWebSocketClient
from app.models import Device, Incident, Notification
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.repositories.notifications import NotificationRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService
from app.services.health_engine import HealthEngine
from app.services.health_weights import HealthWeights
from app.services.notification_engine import NotificationEngine, NotificationPolicy
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


def notification_policy() -> NotificationPolicy:
    return NotificationPolicy(
        notify_important_incidents=os.getenv(
            "NOTIFY_IMPORTANT_INCIDENTS", "true"
        ).lower()
        in {"1", "true", "yes"},
        cooldown=timedelta(
            minutes=max(
                1, min(int(os.getenv("NOTIFICATION_COOLDOWN_MINUTES", "10")), 120)
            )
        ),
    )


async def run_debounce_worker(service: DeviceService) -> None:
    while True:
        changes = service.flush_debounce()
        if changes:
            logger.info("Applicati %s cambi stabilizzati", len(changes))
        await asyncio.sleep(1)


async def run_notification_retry_worker(engine: NotificationEngine) -> None:
    while True:
        await asyncio.sleep(60)
        retries = await engine.retry_failed()
        if retries:
            logger.info("Ritentate %s notifiche DOMUS", retries)


@asynccontextmanager
async def lifespan(app: FastAPI):
    event_bus = EventBus()
    grouping = DeviceGrouping()
    debouncer = DeviceDebouncer(debounce_window())
    weights = health_weights()
    service = DeviceService(
        session_factory=SessionLocal,
        device_repository=DeviceRepository(),
        incident_repository=IncidentRepository(),
        grouping=grouping,
        debouncer=debouncer,
        event_bus=event_bus,
        profile_for=weights.profile_for,
    )
    health_engine = HealthEngine(service, weights)
    adapter = HomeAssistantAdapter(event_bus, service)
    adapter.subscribe()
    notification_engine = NotificationEngine(
        event_bus=event_bus,
        session_factory=SessionLocal,
        repository=NotificationRepository(),
        adapter=HomeAssistantNotifyAdapter(
            base_url=settings.ha_url,
            token=settings.ha_token,
            timeout_seconds=settings.ha_request_timeout_seconds,
        ),
        policy=notification_policy(),
    )
    notification_engine.subscribe()

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
    notification_retry_task = asyncio.create_task(
        run_notification_retry_worker(notification_engine),
        name="notification-retry-worker",
    )
    app.state.device_service = service
    app.state.health_engine = health_engine
    app.state.health_weights = weights
    app.state.notification_engine = notification_engine
    app.state.websocket_task = websocket_task
    app.state.debounce_task = debounce_task
    app.state.notification_retry_task = notification_retry_task

    try:
        yield
    finally:
        adapter.unsubscribe()
        notification_engine.unsubscribe()
        for task in (websocket_task, debounce_task, notification_retry_task):
            task.cancel()
        for task in (websocket_task, debounce_task, notification_retry_task):
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


def notification_to_dict(notification: Notification) -> dict[str, object]:
    return {
        "id": notification.id,
        "incident_id": notification.incident_id,
        "channel": notification.channel,
        "event_type": notification.event_type,
        "notification_id": notification.notification_id,
        "category": notification.category,
        "title": notification.title,
        "message": notification.message,
        "status": notification.status,
        "attempts": notification.attempts,
        "sent_at": notification.sent_at,
        "error_message": notification.error_message,
        "created_at": notification.created_at,
    }


@app.get("/api/v1/notifications")
def list_notifications() -> list[dict[str, object]]:
    with SessionLocal() as session:
        notifications = session.scalars(
            select(Notification).order_by(Notification.created_at.desc())
        ).all()
        return [notification_to_dict(notification) for notification in notifications]


@app.get("/api/v1/notifications/{notification_id}")
def get_notification(notification_id: int) -> dict[str, object]:
    with SessionLocal() as session:
        notification = session.get(Notification, notification_id)
        if notification is None:
            raise HTTPException(status_code=404, detail="Notifica non trovata")
        return notification_to_dict(notification)


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

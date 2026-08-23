from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.adapters.home_assistant import HomeAssistantAdapter
from app.adapters.home_assistant_notify import HomeAssistantNotifyAdapter
from app.core.event_bus import EventBus
from app.database import SessionLocal, ping_database, reset_database_pool
from app.ha.websocket import HomeAssistantWebSocketClient
from app.models import Device, Incident, MaintenanceWindow, Notification
from app.repositories.devices import DeviceRepository
from app.repositories.incidents import IncidentRepository
from app.repositories.notifications import NotificationRepository
from app.services.device_debounce import DeviceDebouncer
from app.services.device_grouping import DeviceGrouping
from app.services.device_service import DeviceService
from app.services.entity_monitoring_policy import EntityMonitoringPolicy
from app.services.health_engine import HealthEngine
from app.services.health_weights import HealthWeights
from app.services.notification_engine import NotificationEngine, NotificationPolicy
from app.services.watchdog import WatchdogService
from app.settings import get_settings


logger = logging.getLogger(__name__)
logging.getLogger("app.ha.websocket").setLevel(logging.INFO)
settings = get_settings()
APP_RELEASE_VERSION = settings.app_version


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
        outbox_stale_after=timedelta(
            seconds=max(
                30,
                min(int(os.getenv("NOTIFICATION_OUTBOX_STALE_SECONDS", "120")), 3600),
            )
        ),
    )


def notification_batch_window() -> timedelta:
    seconds = int(os.getenv("NOTIFICATION_BATCH_WINDOW_SECONDS", "8"))
    return timedelta(seconds=max(0, min(seconds, 60)))


def staleness_check_interval_seconds() -> float:
    seconds = int(os.getenv("STALENESS_CHECK_INTERVAL_SECONDS", "300"))
    return max(30, min(seconds, 3600))


def watchdog_options() -> tuple[int, timedelta, int, int, float]:
    """Read watchdog options without requiring a settings.py migration."""
    interval_seconds = max(
        10, min(int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "60")), 3600)
    )
    stale_minutes = max(
        1, min(int(os.getenv("WATCHDOG_WEBSOCKET_STALE_MINUTES", "10")), 1440)
    )
    memory_threshold_mb = max(
        64, min(int(os.getenv("WATCHDOG_MEMORY_THRESHOLD_MB", "512")), 4096)
    )
    retry_attempts = max(1, min(int(os.getenv("WATCHDOG_DATABASE_RETRY_ATTEMPTS", "3")), 5))
    retry_backoff_seconds = max(
        0.0, min(float(os.getenv("WATCHDOG_DATABASE_RETRY_BACKOFF_SECONDS", "1")), 30.0)
    )
    return (
        interval_seconds,
        timedelta(minutes=stale_minutes),
        memory_threshold_mb,
        retry_attempts,
        retry_backoff_seconds,
    )


async def run_debounce_worker(
    service: DeviceService, *, interval_seconds: float = 1.0
) -> None:
    """Apply due debounced state changes without letting one DB error kill it."""
    while True:
        try:
            changes = service.flush_debounce()
            if changes:
                logger.info("Applicati %s cambi stabilizzati", len(changes))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("device_debounce_worker_failed")
        await asyncio.sleep(interval_seconds)


async def run_notification_retry_worker(
    engine: NotificationEngine, *, interval_seconds: float = 60.0
) -> None:
    """Retry transient notification failures while keeping the worker alive."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            retries = await engine.retry_failed()
            if retries:
                logger.info("Ritentate %s notifiche DOMUS", retries)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification_retry_worker_failed")


async def run_reconciliation_worker(
    service: DeviceService, *, interval_seconds: float = 60.0
) -> None:
    """Periodically remove only availability incidents contradicted by HA state."""
    while True:
        try:
            service.reconcile_open_incidents()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("incident_reconciliation_worker_failed")
        await asyncio.sleep(interval_seconds)


async def run_staleness_worker(
    service: DeviceService,
    weights: HealthWeights,
    *,
    interval_seconds: float = 300.0,
) -> None:
    """Periodically flag devices silent past their category's threshold."""
    while True:
        try:
            service.check_stale_devices(weights.profile_for)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("device_staleness_worker_failed")
        await asyncio.sleep(interval_seconds)


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
    adapter = HomeAssistantAdapter(
        event_bus,
        service,
        monitoring_policy=EntityMonitoringPolicy(),
    )
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
        loop=asyncio.get_running_loop(),
        batch_window=notification_batch_window(),
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
    reconciliation_task = asyncio.create_task(
        run_reconciliation_worker(service),
        name="incident-reconciliation-worker",
    )
    staleness_task = asyncio.create_task(
        run_staleness_worker(
            service, weights, interval_seconds=staleness_check_interval_seconds()
        ),
        name="device-staleness-worker",
    )
    (
        watchdog_interval,
        websocket_stale_after,
        watchdog_memory_threshold,
        watchdog_database_retry_attempts,
        watchdog_database_retry_backoff_seconds,
    ) = watchdog_options()
    watchdog = WatchdogService(
        database_check=ping_database,
        database_recover=reset_database_pool,
        websocket=websocket_client,
        event_bus=event_bus,
        interval_seconds=watchdog_interval,
        websocket_stale_after=websocket_stale_after,
        memory_threshold_mb=watchdog_memory_threshold,
        database_retry_attempts=watchdog_database_retry_attempts,
        database_retry_backoff_seconds=watchdog_database_retry_backoff_seconds,
    )
    watchdog_task = asyncio.create_task(
        watchdog.run_forever(),
        name="domus-watchdog",
    )
    app.state.device_service = service
    app.state.health_engine = health_engine
    app.state.health_weights = weights
    app.state.notification_engine = notification_engine
    app.state.websocket_task = websocket_task
    app.state.debounce_task = debounce_task
    app.state.notification_retry_task = notification_retry_task
    app.state.reconciliation_task = reconciliation_task
    app.state.staleness_task = staleness_task
    app.state.watchdog = watchdog
    app.state.watchdog_task = watchdog_task

    try:
        yield
    finally:
        adapter.unsubscribe()
        notification_engine.unsubscribe()
        for task in (
            websocket_task,
            debounce_task,
            notification_retry_task,
            reconciliation_task,
            staleness_task,
            watchdog_task,
        ):
            task.cancel()
        for task in (
            websocket_task,
            debounce_task,
            notification_retry_task,
            reconciliation_task,
            staleness_task,
            watchdog_task,
        ):
            with suppress(asyncio.CancelledError):
                await task
        logger.info("Servizi DOMUS Guardian arrestati")


app = FastAPI(
    title=settings.app_name,
    version=APP_RELEASE_VERSION,
    lifespan=lifespan,
)

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class MaintenanceRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    ends_at: Optional[datetime] = None


def maintenance_to_dict(window: MaintenanceWindow) -> dict[str, object]:
    return {
        "device_id": window.device_id,
        "reason": window.reason,
        "started_at": window.started_at,
        "ends_at": window.ends_at,
        "active": window.active,
        "ended_at": window.ended_at,
    }


@app.get("/")
def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/v1/status")
def application_status() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": APP_RELEASE_VERSION,
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


@app.get("/api/v1/maintenance")
def list_maintenance() -> list[dict[str, object]]:
    return [
        maintenance_to_dict(window)
        for window in app.state.device_service.list_maintenance()
    ]


@app.put("/api/v1/maintenance/{device_id}")
def activate_maintenance(
    device_id: str, request: MaintenanceRequest
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    if request.ends_at is not None and request.ends_at <= now:
        raise HTTPException(status_code=422, detail="La scadenza deve essere futura")
    window = app.state.device_service.activate_maintenance(
        device_id, request.reason, request.ends_at, now
    )
    return maintenance_to_dict(window)


@app.delete("/api/v1/maintenance/{device_id}")
def deactivate_maintenance(device_id: str) -> dict[str, object]:
    window = app.state.device_service.deactivate_maintenance(device_id)
    if window is None:
        raise HTTPException(status_code=404, detail="Manutenzione non trovata")
    return maintenance_to_dict(window)


@app.get("/api/v1/health/weights")
def list_health_weights() -> list[dict[str, object]]:
    return list_debounced_devices()


@app.get("/api/v1/incidents")
def list_incidents(
    status: str | None = Query(default=None, max_length=32),
    severity: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, object]]:
    with SessionLocal() as session:
        statement = select(Incident).order_by(Incident.opened_at.desc())
        if status is not None:
            statement = statement.where(Incident.status == status)
        if severity is not None:
            statement = statement.where(Incident.severity == severity)
        incidents = session.scalars(statement.offset(offset).limit(limit)).all()
        return [
            {
                "id": incident.id,
                "entity_id": incident.entity_id,
                "kind": incident.kind,
                "severity": incident.severity,
                "status": incident.status,
                "title": incident.title,
                "description": incident.description,
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
        "correlation_id": notification.correlation_id,
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

    # Keep persistent incidents and health in agreement even immediately after
    # a process restart, when in-memory debounce state has not been rebuilt.
    app.state.device_service.reconcile_open_incidents()
    with SessionLocal() as session:
        incidents = IncidentRepository().list_open_availability(session)
        snapshot = app.state.health_engine.snapshot_from_incidents(
            database_connected,
            incidents,
            app.state.device_service.diagnostics(app.state.health_weights.profile_for),
        )
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
        "watchdog_status": app.state.watchdog.snapshot().status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/watchdog/health")
def watchdog_health() -> dict[str, object]:
    snapshot = app.state.watchdog.snapshot()
    return {
        "status": snapshot.status,
        "last_check": snapshot.last_check,
        "issues": list(snapshot.issues),
        "actions_taken": snapshot.actions_taken,
        "actions": list(snapshot.actions),
        "database_latency_ms": snapshot.database_latency_ms,
        "websocket_connected": snapshot.websocket_connected,
        "last_websocket_event_at": snapshot.last_websocket_event_at,
        "memory_mb": snapshot.memory_mb,
        "active_tasks": snapshot.active_tasks,
        "event_bus_pending_handlers": snapshot.event_bus_pending_handlers,
        "event_bus_handler_failures": snapshot.event_bus_handler_failures,
        "event_loop_delay_ms": snapshot.event_loop_delay_ms,
    }

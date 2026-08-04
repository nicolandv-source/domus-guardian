from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import websockets

from app.core.event_bus import EventBus


logger = logging.getLogger(__name__)


class HomeAssistantWebSocketClient:
    """Home Assistant event client with observable, reconnectable state."""

    def __init__(
        self,
        url: str,
        token: str,
        event_bus: EventBus,
        connect_factory: Callable[..., Any] = websockets.connect,
    ) -> None:
        self._url = url
        self._token = token
        self._event_bus = event_bus
        self._connect_factory = connect_factory
        self._websocket: Any | None = None
        self._connected = False
        self._connected_at: datetime | None = None
        self._last_event_at: datetime | None = None
        self._last_message_at: datetime | None = None

    def status(self) -> dict[str, object]:
        return {
            "connected": self._connected,
            "connected_at": self._connected_at,
            "last_event_at": self._last_event_at,
            "last_message_at": self._last_message_at,
        }

    async def request_reconnect(self) -> bool:
        """Close a live connection; ``run_forever`` will reconnect it safely."""
        websocket = self._websocket
        if websocket is None:
            return False
        await websocket.close(code=1012, reason="DOMUS watchdog reconnect")
        return True

    async def run_once(self) -> None:
        if not self._token:
            raise RuntimeError("Token Home Assistant non disponibile")

        logger.info("Connessione WebSocket Home Assistant")
        try:
            async with self._connect_factory(
                self._url,
                # The Supervisor proxy validates its bearer token at the HTTP
                # upgrade boundary. Home Assistant Core then performs the
                # standard WebSocket `auth` exchange below.
                additional_headers={
                    "Authorization": f"Bearer {self._token}",
                },
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
                # HA's entity registry can exceed the library default (1 MiB).
                # It is received only from the trusted Supervisor proxy.
                max_size=None,
            ) as websocket:
                self._websocket = websocket
                auth_required = json.loads(await websocket.recv())
                if auth_required.get("type") != "auth_required":
                    raise RuntimeError("Handshake WebSocket Home Assistant inatteso")

                await websocket.send(
                    json.dumps({"type": "auth", "access_token": self._token})
                )
                auth_result = json.loads(await websocket.recv())
                if auth_result.get("type") != "auth_ok":
                    raise RuntimeError(
                        "Autenticazione WebSocket Home Assistant fallita"
                    )
                logger.info("WebSocket Home Assistant autenticato")

                await websocket.send(
                    json.dumps({"id": 1, "type": "config/entity_registry/list"})
                )
                registry_response = json.loads(await websocket.recv())
                physical_entity_ids: set[str] | None = None
                if registry_response.get("type") == "result" and registry_response.get(
                    "success"
                ):
                    registry_entries = registry_response.get("result") or []
                    physical_entity_ids = {
                        entry["entity_id"]
                        for entry in registry_entries
                        if entry.get("entity_id") and entry.get("device_id")
                    }
                    await asyncio.to_thread(
                        self._event_bus.publish,
                        "entity_registry_loaded",
                        {"entries": registry_entries},
                    )
                else:
                    logger.warning(
                        "Registro entità HA non disponibile; uso fallback entity_id"
                    )

                await websocket.send(
                    json.dumps({"id": 2, "type": "config/device_registry/list"})
                )
                device_registry_response = json.loads(await websocket.recv())
                if device_registry_response.get("type") == "result" and device_registry_response.get(
                    "success"
                ):
                    await asyncio.to_thread(
                        self._event_bus.publish,
                        "device_registry_loaded",
                        {"entries": device_registry_response.get("result") or []},
                    )
                else:
                    logger.warning("Registro dispositivi HA non disponibile")

                await websocket.send(json.dumps({"id": 3, "type": "get_states"}))
                states_response = json.loads(await websocket.recv())
                if states_response.get("type") == "result" and states_response.get(
                    "success"
                ):
                    for state in states_response.get("result") or []:
                        entity_id = state.get("entity_id")
                        if not entity_id:
                            continue
                        if (
                            physical_entity_ids is not None
                            and entity_id not in physical_entity_ids
                        ):
                            continue
                        await asyncio.to_thread(
                            self._event_bus.publish,
                            "state_changed",
                            {
                                "time_fired": state.get("last_updated"),
                                "data": {
                                    "entity_id": entity_id,
                                    "old_state": None,
                                    "new_state": state,
                                },
                            },
                        )
                    await asyncio.to_thread(
                        self._event_bus.publish, "ha_state_snapshot_loaded", {}
                    )
                    logger.info("Stati iniziali Home Assistant caricati")
                else:
                    logger.warning(
                        "Stati iniziali HA non disponibili; attendo eventi live"
                    )

                await websocket.send(
                    json.dumps(
                        {
                            "id": 4,
                            "type": "subscribe_events",
                            "event_type": "state_changed",
                        }
                    )
                )
                subscription = json.loads(await websocket.recv())
                if subscription.get("type") != "result" or not subscription.get(
                    "success"
                ):
                    raise RuntimeError("Sottoscrizione state_changed fallita")

                self._connected = True
                self._connected_at = datetime.now(timezone.utc)
                logger.info("Sottoscrizione state_changed attiva")

                async for raw_message in websocket:
                    self._last_message_at = datetime.now(timezone.utc)
                    message = json.loads(raw_message)
                    if message.get("type") != "event":
                        continue
                    event = message.get("event") or {}
                    if event.get("event_type") == "state_changed":
                        self._last_event_at = datetime.now(timezone.utc)
                    await asyncio.to_thread(
                        self._event_bus.publish, "state_changed", event
                    )
        finally:
            self._connected = False
            self._websocket = None

    async def run_forever(self) -> None:
        retry_seconds = 2
        while True:
            try:
                await self.run_once()
                retry_seconds = 2
            except asyncio.CancelledError:
                logger.info("WebSocket Home Assistant arrestato")
                raise
            except Exception:
                logger.exception(
                    "WebSocket Home Assistant disconnesso; nuovo tentativo tra %ss",
                    retry_seconds,
                )
                await asyncio.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 30)

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import websockets

from app.core.event_bus import EventBus


logger = logging.getLogger(__name__)


class HomeAssistantWebSocketClient:
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

    async def run_once(self) -> None:
        if not self._token:
            raise RuntimeError("Token Home Assistant non disponibile")

        logger.info("Connessione WebSocket Home Assistant")
        async with self._connect_factory(
            self._url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            # The entity registry can exceed the websockets default 1 MiB limit.
            max_size=10 * 1024 * 1024,
        ) as websocket:
            auth_required = json.loads(await websocket.recv())
            if auth_required.get("type") != "auth_required":
                raise RuntimeError("Handshake WebSocket Home Assistant inatteso")

            await websocket.send(
                json.dumps({"type": "auth", "access_token": self._token})
            )
            auth_result = json.loads(await websocket.recv())
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError("Autenticazione WebSocket Home Assistant fallita")
            logger.info("WebSocket Home Assistant autenticato")

            await websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "config/entity_registry/list",
                    }
                )
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
                self._event_bus.publish(
                    "entity_registry_loaded",
                    {"entries": registry_entries},
                )
            else:
                logger.warning(
                    "Registro entità HA non disponibile; uso fallback entity_id"
                )

            await websocket.send(
                json.dumps(
                    {
                        "id": 2,
                        "type": "get_states",
                    }
                )
            )
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
                    self._event_bus.publish(
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
                logger.info("Stati iniziali Home Assistant caricati")
            else:
                logger.warning("Stati iniziali HA non disponibili; attendo eventi live")

            await websocket.send(
                json.dumps(
                    {
                        "id": 3,
                        "type": "subscribe_events",
                        "event_type": "state_changed",
                    }
                )
            )
            subscription = json.loads(await websocket.recv())
            if subscription.get("type") != "result" or not subscription.get("success"):
                raise RuntimeError("Sottoscrizione state_changed fallita")
            logger.info("Sottoscrizione state_changed attiva")

            async for raw_message in websocket:
                message = json.loads(raw_message)
                if message.get("type") != "event":
                    continue
                event = message.get("event") or {}
                self._event_bus.publish("state_changed", event)

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

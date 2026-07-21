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

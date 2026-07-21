import asyncio
import time

from fastapi.testclient import TestClient

from app.ha.websocket import HomeAssistantWebSocketClient
from app.main import app


def test_lifespan_starts_and_cancels_websocket(monkeypatch) -> None:
    started: list[bool] = []
    cancelled: list[bool] = []

    async def fake_run_forever(self) -> None:
        started.append(True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(
        HomeAssistantWebSocketClient,
        "run_forever",
        fake_run_forever,
    )

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        for _ in range(20):
            if started:
                break
            time.sleep(0.01)
        assert started == [True]

    assert cancelled == [True]

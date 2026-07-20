import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="DOMUS Guardian",
    version="0.1.0",
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "DOMUS Guardian",
        "status": "running",
    }


@app.get("/api/v1/ha/health")
def health() -> dict[str, object]:
    return {
        "score": 100,
        "status": "healthy",
        "active_incidents": 0,
        "critical_incidents": 0,
        "warning_incidents": 0,
        "offline_devices": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/ha/ping")
async def home_assistant_ping() -> dict[str, object]:
    token = os.getenv("HA_TOKEN")
    base_url = os.getenv("HA_URL", "http://supervisor/core")

    if not token:
        raise HTTPException(
            status_code=500,
            detail="HA_TOKEN non disponibile",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/api/config",
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Connessione Home Assistant fallita: {exc}",
        ) from exc

    config = response.json()

    return {
        "connected": True,
        "home_assistant_version": config.get("version"),
        "location_name": config.get("location_name"),
    }

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings


def test_root_serves_domus_dashboard() -> None:
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "DOMUS Guardian" in response.text
    assert "Sicurezza" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_application_status_remains_available_as_api() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["version"] == get_settings().app_version


def test_dashboard_does_not_show_online_when_status_api_is_unavailable() -> None:
    dashboard = (Path(__file__).parents[1] / "app" / "web" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "json('/api/v1/status',null)" in dashboard
    assert "const serviceState=status?'Online':'Non raggiungibile'" in dashboard
    assert "health?.watchdog_status??'—'" in dashboard

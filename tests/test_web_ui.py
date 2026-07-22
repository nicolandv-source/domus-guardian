from fastapi.testclient import TestClient

from app.main import app


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

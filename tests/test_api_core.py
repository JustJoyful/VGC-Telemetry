from fastapi.testclient import TestClient

from app.main import app
from core.config import get_active_regulation


def test_health_check_dynamic_regulation():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    active_reg = get_active_regulation()
    assert data["status"] == "ok"
    assert data["regulation"] == active_reg["regulation_id"]
    assert data["format"] == "Pokémon Champions VGC"
    assert "timestamp" in data


def test_root_endpoint_redirects_or_returns_health():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_openapi_schema_metadata():
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "VGC Telemetry API"
    assert "paths" in schema

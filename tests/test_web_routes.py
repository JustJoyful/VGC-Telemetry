# ruff: noqa: F811
from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from tests.v2_helpers import v2_client  # noqa: F401


def test_explorer_home_is_html_without_changing_health_or_api(v2_client):
    client, _, _, _ = v2_client
    before = client.get("/v2/status").json()
    page = client.get("/explorer")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "VGC Telemetry" in page.text and 'href="/explorer/api"' in page.text
    assert 'href="/docs"' in client.get("/explorer/api").text
    for path in ("/", "/health"):
        health = client.get(path).json()
        assert datetime.fromisoformat(health.pop("timestamp"))
        assert health == {
            "status": "ok",
            "regulation": "M-C",
            "format": "Pokémon Champions VGC",
        }
    assert client.get("/docs").status_code == 200
    assert client.get("/v2/status").json() == before


def test_explorer_errors_render_safe_html(v2_client):
    client, _, _, _ = v2_client
    response = client.get("/explorer", params={"limit": "<script>alert(1)</script>"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Invalid query" in response.text and 'href="/explorer' in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "Traceback" not in response.text
    assert client.get("/v2/status?limit=invalid").status_code == 422


def test_unknown_web_path_does_not_change_api_error_contract():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

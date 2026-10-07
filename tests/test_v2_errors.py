import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_unknown_v2_route_uses_safe_envelope_without_changing_v1(monkeypatch):
    from app.main import app
    from app.middleware.rate_limit import DynamoDBRateLimitMiddleware

    monkeypatch.setattr(DynamoDBRateLimitMiddleware, "check_rate_limit", lambda *_: (True, 60, 60))
    with TestClient(app) as client:
        response = client.get("/v2/unknown")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert response.json()["error"]["request_id"]
        assert client.get("/v1/unknown").json() == {"detail": "Not Found"}
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_cursor"),
        (404, "not_found"),
        (409, "ambiguous_population"),
        (410, "cursor_expired"),
        (422, "query_too_broad"),
        (503, "data_unavailable"),
        (500, "internal_error"),
    ],
)
def test_safe_typed_errors_and_internal_failures(status, code):
    from app.v2.errors import V2Error, install_errors

    app = FastAPI()
    install_errors(app)

    @app.get("/v2/test")
    def endpoint():
        if status == 500:
            raise RuntimeError("private-bucket/secret-key")
        raise V2Error(status, code, "Safe public message")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v2/test")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private-bucket" not in response.text
    assert response.json()["error"]["request_id"]


def test_v2_rate_limit_envelope_and_headers(monkeypatch):
    from app.main import app
    from app.middleware.rate_limit import DynamoDBRateLimitMiddleware

    monkeypatch.setattr(DynamoDBRateLimitMiddleware, "check_rate_limit", lambda *_: (False, 0, 12))
    with TestClient(app) as client:
        response = client.get("/v2/unknown")
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert response.headers["Retry-After"] == "12"
        assert "detail" in client.get("/v1/unknown").json()

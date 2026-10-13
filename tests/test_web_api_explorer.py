# ruff: noqa: F811
from tests.v2_helpers import v2_client  # noqa: F401
from tests.web_helpers import Document


def test_api_page_uses_theme_tokens_and_preserves_fixed_read_allowlist(v2_client, monkeypatch):
    from app.web import data

    client, _, _, _ = v2_client
    original = data.api_example

    def escaped_preview(reader, example):
        result = original(reader, example)
        result["response_json"] = '{"name": "<script>attack()</script>"}'
        return result

    monkeypatch.setattr(data, "api_example", escaped_preview)
    page = client.get("/explorer/api")
    assert page.status_code == 200
    document = Document(page.text)
    assert any("api-code" in attrs.get("class", "") for tag, attrs in document.tags if tag == "pre")
    assert any("dex-card" in attrs.get("class", "") for tag, attrs in document.tags if tag == "nav")
    for example in ("status", "meta", "cores", "teams", "pokemon", "tournaments"):
        assert f"/explorer/api?example={example}" in page.text
    for params in ({"url": "https://example.com"}, {"method": "POST"}, {"headers": "secret"}):
        assert client.get("/explorer/api", params=params).status_code == 422
    assert "<script>attack()</script>" not in page.text
    assert "&lt;script&gt;attack()&lt;/script&gt;" in page.text


def test_api_explorer_lists_allowlisted_openapi_get_operations(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer/api?example=pokemon")
    assert page.status_code == 200
    schema = client.get("/openapi.json").json()
    for path in [
        "/v2/status",
        "/v2/meta",
        "/v2/cores",
        "/v2/teams",
        "/v2/pokemon",
        "/v2/tournaments",
    ]:
        assert "get" in schema["paths"][path]
        assert path in page.text
    assert "/docs" in [a["href"] for a, _ in Document(page.text).links]
    assert "champions-mc-3661ce4" in page.text
    assert "snapshot" in page.text and "limit=3" in page.text
    assert "/ingestion" not in page.text and "/internal" not in page.text


def test_response_preview_uses_fixed_query_allowlist(v2_client):
    client, _, _, _ = v2_client
    for params in [
        {"url": "https://example.com"},
        {"method": "POST"},
        {"headers": "secret"},
        {"credentials": "secret"},
        {"example": "https://example.com"},
        {"limit": "10000"},
    ]:
        assert client.get("/explorer/api", params=params).status_code == 422
    for example in ["status", "meta", "cores", "teams", "pokemon", "tournaments"]:
        page = client.get("/explorer/api", params={"example": example})
        assert page.status_code == 200 and "Response preview" in page.text


def test_api_explorer_escapes_response_and_exposes_no_secrets(v2_client, monkeypatch):
    from app.web import data

    client, _, _, _ = v2_client
    original = data.api_example

    def malicious(reader, example):
        result = original(reader, example)
        result["response_json"] = '{"name": "<script>attack()</script>"}'
        return result

    monkeypatch.setattr(data, "api_example", malicious)
    page = client.get("/explorer/api?example=teams")
    assert page.status_code == 200
    assert "<script>attack()</script>" not in page.text
    assert "&lt;script&gt;attack()&lt;/script&gt;" in page.text
    assert "test-only-config-secret" not in page.text
    assert "AWS_SECRET_ACCESS_KEY" not in page.text

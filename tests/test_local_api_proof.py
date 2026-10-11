import httpx


def test_proof_reads_v2_items_and_checks_provenance(monkeypatch):
    from scripts.local.cli import api_proof

    def respond(request):
        if request.url.path == "/v2/teams/limitless:team":
            return httpx.Response(
                200,
                json={
                    "provenance": [{"url": "https://example.test"}],
                    "pokemon": [{"stat_points": None}],
                },
            )
        if request.url.path == "/v2/teams":
            return httpx.Response(
                200, json={"items": [{"team_id": "limitless:team"}], "snapshot": "snapshot"}
            )
        return httpx.Response(200, json={"items": [], "snapshot": "snapshot"})

    real = httpx.Client
    monkeypatch.setattr(
        "scripts.local.cli.httpx.Client",
        lambda **kwargs: real(transport=httpx.MockTransport(respond), **kwargs),
    )
    report = api_proof("http://api:8080")
    assert report[-1]["provenance_checked"]
    assert next(r for r in report if r["path"].startswith("/v2/teams?"))["rows"] == 1

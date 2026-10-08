# ruff: noqa: F811
from tests.v2_helpers import v2_client  # noqa: F401


def test_correction_between_pages_retains_old_teams(v2_client):
    from datetime import UTC, datetime

    from tests.phase2_helpers import publish_input
    from tests.test_public_snapshot import finish, held

    client, value, table, s3 = v2_client
    first = client.get("/v2/teams", params={"limit": 1}).json()
    removed = first["items"][0]["team_id"]
    bucket = __import__("boto3").resource("s3").Bucket(value.bucket)
    publish_input(
        table,
        bucket,
        mutate=lambda t: setattr(t, "teams", []),
        captured_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    finish(table, s3, held(table))
    last = client.get("/v2/teams", params={"limit": 1, "cursor": first["next_cursor"]})
    assert last.status_code == 200, last.text
    assert last.json()["snapshot"] == first["snapshot"]
    assert len(last.json()["items"]) == 1
    assert last.json()["next_cursor"] is None
    assert (
        client.get("/v2/teams/" + removed, params={"snapshot": first["snapshot"]}).status_code
        == 200
    )
    assert client.get("/v2/teams/" + removed).status_code == 404
    assert client.get("/v2/teams").json()["items"] == []


def test_teams_filters_partial_units_pagination_and_read_only(v2_client, monkeypatch):
    client, value, table, _ = v2_client

    def forbidden(**kwargs):
        raise AssertionError("HTTP must not scan or write public data")

    monkeypatch.setattr(table, "scan", forbidden)
    monkeypatch.setattr(table, "put_item", forbidden)
    monkeypatch.setattr(table, "update_item", forbidden)
    first = client.get("/v2/teams", params={"limit": 1}).json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    last = client.get("/v2/teams", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert len(last["items"]) == 1
    assert last["next_cursor"] is None
    assert first["items"][0]["team_id"] != last["items"][0]["team_id"]
    assert (
        client.get(
            "/v2/teams", params={"limit": 1, "cursor": first["next_cursor"], "eligibility": "all"}
        ).status_code
        == 400
    )
    assert len(client.get("/v2/teams", params={"eligibility": "all"}).json()["items"]) == 6
    for params in [
        {"player_id": "limitless:axtheb"},
        {"species_id": "floette", "form_id": "floetteeternal"},
        {"mega_variant_id": "floettemega"},
        {"placement_max": 1},
        {"tournament_id": value.event_id, "placement_min": 1, "placement_max": 1},
    ]:
        response = client.get("/v2/teams", params=params)
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 1
    team = client.get(
        "/v2/teams/" + first["items"][0]["team_id"], params={"snapshot": first["snapshot"]}
    ).json()
    assert team["pokemon"][0]["stat_points"] is None
    assert "PK" not in team and "SK" not in team
    assert client.get("/v2/teams", params={"form_id": "rotomwash"}).status_code == 422
    assert (
        client.get(
            "/v2/teams", params={"species_id": "incineroar", "form_id": "floetteeternal"}
        ).status_code
        == 422
    )

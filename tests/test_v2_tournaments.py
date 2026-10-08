# ruff: noqa: F811
from tests.v2_helpers import v2_client  # noqa: F401


def test_event_context_and_strict_filters(v2_client):
    client, value, _, _ = v2_client
    page = client.get("/v2/tournaments", params={"limit": 1})
    assert page.status_code == 200, page.text
    data = page.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["coverage"]["standings_entries"] == 6
    assert data["items"][0]["cut"]["participant_count"] == 2
    assert data["coverage"]["eligible_complete_rosters"] == 2
    detail = client.get("/v2/tournaments/" + value.event_id, params={"snapshot": data["snapshot"]})
    assert detail.status_code == 200
    assert detail.json()["generated_at"] is not None
    standings = client.get(
        f"/v2/tournaments/{value.event_id}/standings", params={"limit": 100}
    ).json()
    assert len(standings["items"]) == 6
    assert sum(t["eligibility"]["status"] == "included" for t in standings["items"]) == 2
    matches = client.get(f"/v2/tournaments/{value.event_id}/matches", params={"phase": 2}).json()
    assert len(matches["items"]) == 1
    for params in [
        {"source": "limitless"},
        {"regulation": "M-C"},
        {"date_from": "2026-10-03", "date_to": "2026-10-03"},
        {"match_mode": "BO3"},
        {"cut_status": "verified_complete"},
    ]:
        assert len(client.get("/v2/tournaments", params=params).json()["items"]) == 1
    assert client.get("/v2/tournaments", params={"typo": "bad"}).status_code == 422
    assert client.get("/v2/tournaments/limitless:missing").status_code == 404
